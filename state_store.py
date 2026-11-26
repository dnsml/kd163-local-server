# -*- coding: utf-8 -*-
"""角色存档的 SQLite 后端。

为什么换库
----------
以前每个账号一个 `player_state*.json`：`player_state.json` 给主号，其它账号
`player_state_<账号>.json`。能跑，但有三个硬伤：

1. 落盘是「整份 JSON 覆盖」。两个连接同时在线（同一个号双开、后台代改）时，
   后写的把先写的整份盖掉，谁也不知道自己丢了什么。
2. 账号 → 文件名的映射是字符串拼接。账号里出现 `/`、`..`、空格就会写到别处；
   主号还特殊化成 `player_state.json`，读的时候得记住这个例外。
3. 想统计「有多少角色、最高多少级、谁在线」只能把目录里所有文件全读一遍。

现在改成一张表：一行一个账号，高频字段（等级/货币/位置/称号）拆成列方便后台
查询，其余复杂结构（背包/装备/妖灵/任务）继续以 JSON 存在 `data` 列里。
读档/落盘的业务代码几乎不用改，只是把 open()/os.replace() 换成 load()/save()。

并发
----
`rev` 是乐观锁版本号：读出来时是多少，写回去就带上多少。写的时候发现库里的
rev 已经变了（另一个连接先写了），默认打日志后仍然落盘（最后写入者胜，本地服
同一账号双开本来就是同一个玩家），设 `KD_STATE_STRICT=1` 则抛 STALE 让调用方
重读。不会出现「半份 JSON」——SQLite 事务本身保证原子性。

开关
----
`KD_STATE_BACKEND=sqlite`（默认）走本模块；`=json` 回退到旧的单文件存档，
保留给「库坏了要救急」的场景。两条路径的 state_dict() 完全相同，互相可读。
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("KD_STATE_DB") or os.path.join(HERE, "server_state.db")


def _selfcheck():
    """是不是 work/test_*.py / probe_*.py 这种自检脚本。

    自检脚本会大量 create_character / save_state（账号名是 t1、zzz 之类），
    默认写正式库等于往玩家列表里塞垃圾角色，万一断言里用了主号还会改真存档。
    这里直接给它们一个一次性的临时库；要指定别的库仍然设 KD_STATE_DB。
    """
    argv0 = os.path.abspath(sys.argv[0] or "")
    parts = argv0.replace("\\", "/").split("/")
    base = os.path.basename(argv0)
    return ("work" in parts[:-1]
            and (base.startswith("test_") or base.startswith("probe_")))


TEST_MODE = False
if not os.environ.get("KD_STATE_DB") and _selfcheck():
    TEST_MODE = True
    DB_FILE = os.path.join(
        tempfile.gettempdir(),
        "kd_test_state_%s.db" % os.path.splitext(os.path.basename(sys.argv[0]))[0])
BACKUP_DIR = os.path.join(HERE, "backups")
BACKUP_EVERY = 600.0        # 同一角色最多 10 分钟留一份快照
BACKUP_KEEP = 20            # 每个角色保留最近 20 份

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    -- 展示用编号（后台「ID」列）。不是主键，主键仍是账号；老库启动时自动补列+回填。
    id          INTEGER,
    acct        TEXT PRIMARY KEY,
    name        TEXT,
    level       INTEGER NOT NULL DEFAULT 0,
    cls         INTEGER NOT NULL DEFAULT 6,
    sex         INTEGER NOT NULL DEFAULT 1,
    exp         INTEGER NOT NULL DEFAULT 0,
    exp_need    INTEGER NOT NULL DEFAULT 0,
    scene       INTEGER NOT NULL DEFAULT 101,
    x           INTEGER NOT NULL DEFAULT 21,
    y           INTEGER NOT NULL DEFAULT 7,
    bind_copper INTEGER NOT NULL DEFAULT 0,
    bind_gold   INTEGER NOT NULL DEFAULT 0,
    copper      INTEGER NOT NULL DEFAULT 0,
    gold        INTEGER NOT NULL DEFAULT 0,
    vip         INTEGER NOT NULL DEFAULT 0,
    title       INTEGER NOT NULL DEFAULT 0,
    bag_count   INTEGER NOT NULL DEFAULT 0,
    rev         INTEGER NOT NULL DEFAULT 0,
    data        TEXT NOT NULL,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS online (
    acct   TEXT PRIMARY KEY,
    cid    INTEGER,
    since  TEXT,
    seen   TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_LOG = [None]          # 由 local_server3 注入它的 log()，避免循环 import
_LOCK = threading.Lock()
_READY = [False]
STRICT = os.environ.get("KD_STATE_STRICT", "") not in ("", "0")

# 账号只允许这些字符（与客户端 account_from_login / 后台 ACCT_RE 一致）。
# SQL 本身全部参数化，这里是第二道防线：非法账号连库都不碰，避免任何拼接类漏洞。
_ACCT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _check_acct(acct):
    acct = str(acct or "")
    if not _ACCT_RE.match(acct):
        raise ValueError("账号含非法字符（只允许字母/数字/_ - .，1~64 位）")
    return acct


class Stale(Exception):
    """写回时 rev 对不上（另一个连接先写了这份存档）。"""


def set_logger(fn):
    _LOG[0] = fn


def log(msg):
    fn = _LOG[0]
    if fn:
        try:
            fn(msg)
            return
        except Exception:
            pass
    print(msg, flush=True)


def backend():
    """当前后端：sqlite / json。"""
    return (os.environ.get("KD_STATE_BACKEND") or "sqlite").strip().lower()


def enabled():
    return backend() == "sqlite"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _connect():
    con = sqlite3.connect(DB_FILE, timeout=10.0, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")     # 读写不互斥，掉电最多丢最后一条事务
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init():
    """建表（幂等）。第一次调用才真正动磁盘。"""
    if _READY[0]:
        return
    with _LOCK:
        if _READY[0]:
            return
        os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)
        if TEST_MODE:                       # 自检脚本每次从空库开始
            for p in (DB_FILE, DB_FILE + "-wal", DB_FILE + "-shm"):
                try:
                    os.remove(p)
                except OSError:
                    pass
        con = _connect()
        try:
            con.executescript(SCHEMA)
            _migrate_id(con)
        finally:
            con.close()
        _READY[0] = True
        log("[state_store] SQLite 就绪 %s%s"
            % (os.path.basename(DB_FILE), "（自检临时库）" if TEST_MODE else ""))


def _migrate_id(con):
    """老库补 id 列并按账号顺序回填。幂等，每次启动跑一遍也不贵。"""
    cols = [r[1] for r in con.execute("PRAGMA table_info(player)").fetchall()]
    if "id" not in cols:
        con.execute("ALTER TABLE player ADD COLUMN id INTEGER")
        log("[state_store] player 表补 id 列")
    rows = con.execute("SELECT acct FROM player WHERE id IS NULL OR id=0 ORDER BY acct"
                       ).fetchall()
    if not rows:
        return
    nxt = int(con.execute("SELECT COALESCE(MAX(id),0) FROM player").fetchone()[0]) + 1
    for (acct,) in rows:
        con.execute("UPDATE player SET id=? WHERE acct=?", (nxt, acct))
        nxt += 1
    log("[state_store] 回填账号编号 %d 个" % len(rows))


# ---------------- 结构映射 ----------------

def _cols(d):
    """存档 dict -> 表里的高频列。缺字段一律按「空角色」补 0。"""
    bag = d.get("bag") or {}
    return (
        str(d.get("name") or ""),
        int(d.get("level") or 0),
        int(d.get("class") or d.get("cls") or 6),
        int(d.get("sex") or 1),
        int(d.get("exp") or 0),
        int(d.get("need") or 0),
        int(d.get("scene") or 101),
        int(d.get("pos_x") or 21),
        int(d.get("pos_y") or 7),
        int(d.get("coin") or 0),            # coin 就是绑定铜钱（老存档字段名）
        int(d.get("bind_yuanbao") or 0),
        int(d.get("tongqian") or 0),
        int(d.get("yuanbao") or 0),
        int(d.get("vip_level") or 0),
        int(d.get("title") or 0),
        len(bag),
    )


_UPDATE = ("UPDATE player SET name=?, level=?, cls=?, sex=?, exp=?, exp_need=?, "
           "scene=?, x=?, y=?, bind_copper=?, bind_gold=?, copper=?, gold=?, "
           "vip=?, title=?, bag_count=?, rev=rev+1, data=?, updated_at=? "
           "WHERE acct=?")


def load(acct):
    """读一个账号的存档。返回 (dict, rev)；没有这一行返回 (None, 0)。"""
    init()
    acct = str(acct or "")
    if not acct or not _ACCT_RE.match(acct):
        if acct:
            log("[state_store] 拒绝读取非法账号 %r" % acct[:40])
        return None, 0
    con = _connect()
    try:
        row = con.execute("SELECT data, rev FROM player WHERE acct=?",
                          (acct,)).fetchone()
    finally:
        con.close()
    if not row:
        return None, 0
    try:
        d = json.loads(row[0])
    except Exception as e:
        log("[state_store] 账号 %s 的 data 不是合法 JSON（%s），当作无存档" % (acct, e))
        return None, 0
    return d, int(row[1] or 0)


def save(acct, data, rev=0):
    """写回一个账号的存档，返回新的 rev。

    rev=0 表示「我不管之前是什么版本」（建角/迁移/强制覆盖）。
    rev>0 且与库里不一致：默认记日志后照写（最后写入者胜），
    KD_STATE_STRICT=1 时抛 Stale。
    """
    init()
    acct = str(acct or "")
    if not acct:
        raise ValueError("save() 需要账号，空账号会把数据写进无名行")
    acct = _check_acct(acct)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    cols = _cols(data)
    now = _now()
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT rev FROM player WHERE acct=?", (acct,)).fetchone()
        if row is None:
            nid = int(con.execute(
                "SELECT COALESCE(MAX(id),0)+1 FROM player").fetchone()[0])
            con.execute(
                "INSERT INTO player (id, acct, name, level, cls, sex, exp, exp_need,"
                " scene, x, y, bind_copper, bind_gold, copper, gold, vip, title,"
                " bag_count, rev, data, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, acct) + cols + (1, payload, now, now))
            con.execute("COMMIT")
            return 1
        cur_rev = int(row[0] or 0)
        if rev and cur_rev != rev:
            log("[state_store] 账号 %s rev 冲突：读的是 %d，库里是 %d（%s）"
                % (acct, rev, cur_rev, "拒写" if STRICT else "仍按最后写入者落盘"))
            if STRICT:
                con.execute("ROLLBACK")
                raise Stale("acct=%s rev=%d cur=%d" % (acct, rev, cur_rev))
        con.execute(_UPDATE, cols + (payload, now, acct))
        con.execute("COMMIT")
        return cur_rev + 1
    except Stale:
        raise
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def updated(acct):
    """这一行最后一次写入的时间（epoch 秒）；没有这行返回 0。

    用途：写库万一失败会回退写老 JSON，下次读档靠「JSON 比库里新」判断
    是不是要用 JSON 覆盖回来，避免那份兜底悄悄被忽略。
    """
    init()
    con = _connect()
    try:
        row = con.execute("SELECT updated_at FROM player WHERE acct=?",
                          (str(acct or ""),)).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return 0.0
    try:
        return time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def exists(acct):
    init()
    acct = str(acct or "")
    if not acct or not _ACCT_RE.match(acct):
        return False
    con = _connect()
    try:
        return con.execute("SELECT 1 FROM player WHERE acct=? LIMIT 1",
                           (acct,)).fetchone() is not None
    finally:
        con.close()


def delete(acct):
    init()
    acct = str(acct or "")
    if not _ACCT_RE.match(acct):
        return
    con = _connect()
    try:
        con.execute("DELETE FROM player WHERE acct=?", (acct,))
    finally:
        con.close()


def count():
    init()
    con = _connect()
    try:
        return int(con.execute("SELECT COUNT(*) FROM player").fetchone()[0])
    finally:
        con.close()


def accounts():
    """后台用的账号列表（不含背包等大字段）。"""
    init()
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, acct, name, level, cls, gold, copper, bind_gold, bind_copper,"
            " bag_count, rev, updated_at FROM player ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    keys = ("id", "acct", "name", "level", "cls", "gold", "copper", "bind_gold",
            "bind_copper", "bag", "rev", "updated_at")
    return [dict(zip(keys, r)) for r in rows]


# ---------------- 在线表（后台/运维用，正式在线判断仍走 GAME_CONNS） ----------------

def online_set(acct, cid):
    init()
    acct = str(acct or "")
    if not _ACCT_RE.match(acct):
        return
    now = _now()
    con = _connect()
    try:
        con.execute("INSERT INTO online (acct, cid, since, seen) VALUES (?,?,?,?)"
                    " ON CONFLICT(acct) DO UPDATE SET cid=excluded.cid,"
                    " seen=excluded.seen", (acct, int(cid or 0), now, now))
    except Exception as e:
        log("[state_store] online_set 失败 %s" % e)
    finally:
        con.close()


def online_clear(acct):
    init()
    acct = str(acct or "")
    if not _ACCT_RE.match(acct):
        return
    con = _connect()
    try:
        con.execute("DELETE FROM online WHERE acct=?", (acct,))
    except Exception:
        pass
    finally:
        con.close()


def online_list():
    init()
    con = _connect()
    try:
        rows = con.execute("SELECT acct, cid, since, seen FROM online ORDER BY since"
                           ).fetchall()
    finally:
        con.close()
    return [{"acct": r[0], "cid": r[1], "since": r[2], "seen": r[3]} for r in rows]


# ---------------- 快照（写前兜底，与旧 backup_state 同规则） ----------------

def snapshot(acct, data, force=False):
    """把一份存档 dict 写进 backups/（10 分钟节流、每人留 20 份）。"""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        base = ("player_state_%s" % acct) if acct else "player_state"
        base = base.replace("/", "_").replace("\\", "_")
        pat = re.compile(r"^%s_\d{8}_\d{6}\.json$" % re.escape(base))
        pat_hit = [f for f in os.listdir(BACKUP_DIR) if pat.match(f)]
        pat_hit.sort()
        now = time.time()
        if pat_hit and not force:
            last = os.path.join(BACKUP_DIR, pat_hit[-1])
            if now - os.path.getmtime(last) < BACKUP_EVERY:
                return None
        dst = os.path.join(BACKUP_DIR, "%s_%s.json"
                           % (base, time.strftime("%Y%m%d_%H%M%S")))
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
        for f in pat_hit[:-BACKUP_KEEP + 1]:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
            except OSError:
                pass
        log("[state] 已备份存档 -> backups/%s" % os.path.basename(dst))
        return dst
    except Exception as e:
        log("[state] 备份失败：%s" % e)
        return None
