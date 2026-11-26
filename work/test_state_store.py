# -*- coding: utf-8 -*-
"""SQLite 存档后端回归（state_store.py）。

用法：
    python work/test_state_store.py

覆盖：
 1. 新建行 / 读回：整份 dict 逐字段一致（含中文名、背包、装备、妖灵）
 2. rev 乐观锁：正常自增；旧 rev 写回打冲突但仍落盘（最后写入者胜）；严格模式抛 Stale
 3. 两个账号互不影响（A 存档不会出现在 B 上，反之亦然）
 4. 「重启」一致性：换一条连接重新 load，内容和 rev 都对得上
 5. 老 JSON 懒迁移：库里没有、磁盘有 player_state_<acct>.json → 读档时自动进库
 6. 落盘闸门：未登录（authed=False）的连接不许在库里凭空建行
 7. 快照：写盘前在 backups/ 留一份，10 分钟内不重复
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 必须在 import local_server3 / state_store 之前指好库，否则会写到正式库
_TMP = tempfile.mkdtemp(prefix="kd_store_test_")
os.environ["KD_STATE_DB"] = os.path.join(_TMP, "test.db")
os.environ["KD_STATE_BACKEND"] = "sqlite"

import local_server3 as L       # noqa: E402
import state_store as S         # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + extra) if extra else ""))


def new_conn(acct):
    p = L.new_player()
    p.acct = acct
    p.cid = 1
    return p


def sample(name="甲", level=7, coin=123):
    return {"name": name, "sex": 2, "has_char": True, "level": level, "exp": 5,
            "need": 300, "coin": coin, "class": 6, "bind_yuanbao": 20,
            "tongqian": 30, "yuanbao": 40, "yueli": 0, "vip_level": 1,
            "vip_until": 0.0, "mp_add": 0, "wing": None, "wardrobe": [1, 2],
            "quest_active": 100101, "quest_ready": False, "quest_done": [1, 2, 3],
            "title": 1019, "scene": 101, "pos_x": 30, "pos_y": 9,
            "equip": {"1": 11235, "8": 11235}, "enhance": {"8": 3},
            "bag": {"0": [20190, 10], "5": [21001, 1]},
            "pets": [10000], "spirit_bag": {"1101": {"idx": 1, "slot": 0}},
            "spirit_slot": {"1": 1101}, "ver": 2}


def main():
    S.init()
    print("测试库：%s" % S.DB_FILE)

    print("\n[1] 新建 / 读回")
    d = sample()
    rev = S.save("acc_a", d, 0)
    d2, rev2 = S.load("acc_a")
    check("首次写入 rev=1", rev == 1 and rev2 == 1, "rev=%s/%s" % (rev, rev2))
    check("整份内容一致", d2 == d, "" if d2 == d else "字段有差异")
    rows = {r["acct"]: r for r in S.accounts()}
    check("列字段同步（等级/货币/背包件数）",
          rows["acc_a"]["level"] == 7 and rows["acc_a"]["gold"] == 40
          and rows["acc_a"]["copper"] == 30 and rows["acc_a"]["bag"] == 2,
          str(rows.get("acc_a")))

    print("\n[2] rev 乐观锁")
    d["level"] = 8
    r3 = S.save("acc_a", d, rev2)
    check("正常写 rev 自增", r3 == rev2 + 1, "rev=%s" % r3)
    d_old = dict(d, level=9)
    r4 = S.save("acc_a", d_old, rev2)          # 故意用旧 rev
    check("旧 rev 仍落盘（最后写入者胜）",
          r4 == r3 + 1 and S.load("acc_a")[0]["level"] == 9, "rev=%s" % r4)
    S.STRICT = True
    try:
        S.save("acc_a", d, rev2)
        check("严格模式抛 Stale", False, "没抛异常")
    except S.Stale:
        check("严格模式抛 Stale", True)
    finally:
        S.STRICT = False

    print("\n[3] 账号隔离")
    S.save("acc_b", sample("乙", 3, 0), 0)
    da, _ = S.load("acc_a")
    db, _ = S.load("acc_b")
    check("A 不受 B 影响", da["name"] == "甲" and db["name"] == "乙")
    check("B 的货币独立", db["coin"] == 0 and da["coin"] == 123)
    check("账号数 = 2", S.count() == 2, "count=%d" % S.count())

    print("\n[4] 重启一致性（新连接重新读）")
    p = new_conn("acc_a")
    p.authed = True
    with L.player_ctx(p):
        L.load_state()
        check("读回 Lv9", p.level == 9, "Lv%d" % p.level)
        check("读回昵称/称号/宠物", p.name == "甲" and p.title == 1019 and p.pets == [10000])
        check("读回背包 2 件", len(p.bag) == 2 and p.bag.get(0) == [20190, 10], str(p.bag))
        check("rev 与库一致", p.rev == S.load("acc_a")[1], "rev=%d" % p.rev)

    print("\n[5] 老 JSON 懒迁移")
    jp = os.path.join(_TMP, "player_state_legacy1.json")
    legacy = sample("老档", 21, 777)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(legacy, f, ensure_ascii=False)
    pj = new_conn("legacy1")
    pj.authed = True
    old_path = L.state_path
    L.state_path = lambda pl=None: jp
    try:
        with L.player_ctx(pj):
            L.load_state()
            check("JSON 内容读进来了", pj.name == "老档" and pj.level == 21)
            check("懒迁移已入库", S.exists("legacy1"))
            back, _ = S.load("legacy1")
            check("库内容与 JSON 一致", back == legacy)
    finally:
        L.state_path = old_path

    print("\n[6] 落盘闸门")
    pghost = new_conn("ghost1")
    with L.player_ctx(pghost):
        L.save_state()                      # authed=False / has_char=False
        check("未登录连接不建行", not S.exists("ghost1"))

    print("\n[7] SQL 注入 / 非法账号")
    evil = "x'; DROP TABLE player;--"
    try:
        S.save(evil, sample("注入", 3, 0), 0)
        check("恶意账号被拒绝", False, "竟然写进去了")
    except ValueError:
        check("恶意账号被拒绝", True)
    check("非法账号读不到", S.load(evil)[0] is None)
    check("player 表还在", S.count() >= 2, "count=%d" % S.count())
    check("账号列表里没有注入账号", all(r["acct"] != evil for r in S.accounts()))

    print("\n[8] 写前快照")
    sn = S.snapshot("acc_a", S.load("acc_a")[0], force=True)
    check("生成了快照文件", bool(sn) and os.path.exists(sn), str(sn))
    again = S.snapshot("acc_a", S.load("acc_a")[0])
    check("10 分钟内不重复备份", again is None, str(again))

    print("\n" + "=" * 56)
    print("通过 %d 项 / 失败 %d 项" % (len(OK), len(BAD)))
    for b in BAD:
        print("  失败：%s" % b)
    print("=" * 56)
    return 0 if not BAD else 1


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
