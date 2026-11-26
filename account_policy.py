# -*- coding: utf-8 -*-
"""账号白名单 + 账号密码。

两类账号
--------
1. **有密码的账号**（后台「新建游戏账号」生成的，账号/密码都是随机的）：
   `/auth` 会校验密码，错了直接 401，客户端进不了游戏。
2. **只有白名单、没有密码的老账号**（`allow` 列表里的，比如主号）：
   行为与以前一致 —— 只看名单，不校验密码（客户端那边密码填什么都行）。

文件格式（accounts.json）
-------------------------
```json
{
  "allow": ["1784366"],
  "accounts": {
    "k7m2pq9x": {"pass": "pbkdf2_sha256$120000$<salt>$<hash>",
                 "created_at": "2026-11-26 23:00:00", "note": ""}
  }
}
```
- 两种条目都算「允许登录」；
- 没有这个文件、也没有 `KD_ALLOW_ACCOUNTS` → 全部放行（开发默认）；
- 文件存在但既没 allow 也没 accounts → 全部拒绝（fail closed）；
- 文件坏了 → 全部拒绝，并在日志里告警。

命令行
------
    python work/allow_accounts.py sync|add|remove|list|off
    python work/admin_password.py set admin 新密码
"""
import hashlib
import json
import os
import secrets
import string
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.environ.get("KD_ACCOUNTS_FILE") or os.path.join(HERE, "accounts.json")

# 账号字母表：去掉了 0/O/1/I/l 这类容易抄错的字符
ACCT_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
# 密码字母表：大小写 + 数字，同样去歧义
PASS_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ"
PBKDF2_ROUNDS = 120000
# 生成账号时的默认密码。按运营要求统一成 123（弱密码，公网部署要清楚风险）。
DEFAULT_PASSWORD = os.environ.get("KD_DEFAULT_PASSWORD") or "123"

_cache = {"mtime": None, "allow": None, "accounts": None, "src": "off", "open": False}
_warned = [False]


# ---------------- 密码哈希 ----------------

def hash_password(pwd, rounds=None):
    rounds = int(rounds or PBKDF2_ROUNDS)
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", str(pwd).encode("utf-8"), salt, rounds)
    return "pbkdf2_sha256$%d$%s$%s" % (rounds, salt.hex(), h.hex())


def check_password(pwd, stored):
    """校验密码。stored 为空/不是我们的格式 → 返回 None（表示这号没设密码）。"""
    if not stored or not isinstance(stored, str):
        return None
    try:
        algo, rounds, salt, want = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return None
        got = hashlib.pbkdf2_hmac("sha256", str(pwd).encode("utf-8"),
                                  bytes.fromhex(salt), int(rounds)).hex()
        return secrets.compare_digest(got, want)
    except Exception:
        return False


# ---------------- 读写 ----------------

def _parse_env(text):
    text = (text or "").strip()
    if not text:
        return set()
    text = text.replace(";", ",").replace(" ", ",")
    return {x.strip() for x in text.split(",") if x.strip()}


def load(force=False):
    """返回 (allow_set 或 None, 来源)。allow=None 表示「未配置，全放行」。"""
    env = os.environ.get("KD_ALLOW_ACCOUNTS")
    if env is not None and env.strip():
        if env.strip() == "*":
            return None, "env(*)"
        return _parse_env(env), "env"
    try:
        m = os.path.getmtime(FILE)
    except OSError:
        _cache.update(mtime=None, allow=None, accounts={}, src="off", open=False)
        return None, "off"
    if not force and _cache["mtime"] == m:
        # 命中缓存也要尊重 open：否则 set_open(True) 之后 describe/allowed
        # 还会拿缓存里的 allow 集合，表现为「刚放开却仍然全拒」。
        if _cache.get("open"):
            return None, "file(open)"
        return _cache["allow"], _cache["src"]
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            raw_allow = d.get("allow") or []
            raw_acc = d.get("accounts") or {}
            is_open = bool(d.get("open"))
        else:                       # 允许最简写成 ["a","b"]
            raw_allow, raw_acc, is_open = d or [], {}, False
        allow = {str(x).strip() for x in raw_allow if str(x).strip()}
        accounts = {}
        for k, v in (raw_acc or {}).items():
            k = str(k).strip()
            if not k:
                continue
            accounts[k] = v if isinstance(v, dict) else {}
        _cache.update(mtime=m, allow=allow, accounts=accounts, src="file", open=is_open)
        if is_open:
            return None, "file(open)"
        return allow, "file"
    except Exception as e:
        if not _warned[0]:
            print("[account_policy] ！！accounts.json 解析失败（%s），按「全部拒绝」处理"
                  % e, flush=True)
            _warned[0] = True
        _cache.update(mtime=m, allow=set(), accounts={}, src="file(坏)", open=False)
        return set(), "file(坏)"


def registry():
    """{账号: {pass, created_at, note}}。未配置时返回 {}。"""
    load()
    return dict(_cache["accounts"] or {})


def allowed(acct):
    """能不能登录（白名单 + 已建账号）。未配置白名单时恒 True。"""
    acct = str(acct or "").strip()
    if not acct:
        return False
    allow, _ = load()
    if allow is None:
        return True
    return acct in allow or acct in (_cache["accounts"] or {})


def needs_password(acct):
    """这个账号是否设了密码（老白名单账号没有）。"""
    load()
    return bool((_cache["accounts"] or {}).get(str(acct or "").strip(), {}).get("pass"))


def verify(acct, password):
    """返回 True/False；None 表示这号没设密码（不校验）。"""
    load()
    ent = (_cache["accounts"] or {}).get(str(acct or "").strip())
    if not ent or not ent.get("pass"):
        return None
    return bool(check_password(password or "", ent["pass"]))


def describe():
    allow, src = load()
    n_pw = sum(1 for v in (_cache["accounts"] or {}).values() if v.get("pass"))
    n_acc = len(_cache["accounts"] or {})
    if allow is None:
        if src == "file(open)":
            return ("开放：任何账号都能登录；已有 %d 个账号（%d 个有密码）仍校验密码"
                    % (n_acc, n_pw))
        return "关闭（所有账号可登录；配 accounts.json 或 KD_ALLOW_ACCOUNTS 开启）"
    total = len(allow) + n_acc
    if not total:
        return "已开启但名单为空 —— 所有账号都会被拒绝（%s）" % src
    return "已开启：%d 个账号（其中 %d 个有密码）· %s" % (total, n_pw, src)


# ---------------- 写 ----------------

def _write(allow, accounts, is_open=None):
    data = {
        "allow": sorted({str(x).strip() for x in allow if str(x).strip()}),
        "accounts": accounts,
        # 默认收紧：任何一次「改名单/加账号」都回到白名单模式；
        # set_open(True) 会显式传 is_open=True 保持开放。
        "open": False if is_open is None else bool(is_open),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, FILE)
    load(force=True)
    return FILE


def save(allow, accounts=None, is_open=None):
    """写回名单。accounts/is_open 不传就保留文件里现有的。"""
    load(force=True)
    cur_accounts = dict(_cache["accounts"] or {})
    if accounts is not None:
        cur_accounts = accounts
    return _write(allow, cur_accounts, is_open=is_open)


def set_open(flag):
    """开放/收紧：开放时任何账号都能登录，但**账号表里的密码仍然校验**。"""
    load(force=True)
    _write(set(_cache["allow"] or []), dict(_cache["accounts"] or {}), is_open=flag)
    return describe()


def new_account(acct=None, password=None):
    """生成（或指定）账号密码并写盘。返回 (账号, 密码)。"""
    load(force=True)
    accounts = dict(_cache["accounts"] or {})
    allow = set(_cache["allow"] or [])
    while True:
        a = acct or "".join(secrets.choice(ACCT_ALPHABET) for _ in range(8))
        if a not in accounts and a not in allow:
            break
        if acct:
            break
    p = password or DEFAULT_PASSWORD
    accounts[a] = {"pass": hash_password(p),
                   "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write(allow, accounts)
    return a, p


def reset_password(acct, password=None):
    """重置密码（默认也是 123），返回 (账号, 新密码)；账号不存在返回 (acct, None)。"""
    load(force=True)
    accounts = dict(_cache["accounts"] or {})
    acct = str(acct or "").strip()
    if acct not in accounts:
        return acct, None
    p = password or DEFAULT_PASSWORD
    accounts[acct]["pass"] = hash_password(p)
    accounts[acct]["pass_reset_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write(set(_cache["allow"] or []), accounts)
    return acct, p


def set_password(acct, password):
    """把某个账号的密码直接设成指定值（账号必须已在账号表里）。"""
    load(force=True)
    accounts = dict(_cache["accounts"] or {})
    acct = str(acct or "").strip()
    if acct not in accounts:
        return False
    accounts[acct]["pass"] = hash_password(password)
    _write(set(_cache["allow"] or []), accounts)
    return True


def set_all_passwords(password):
    """把账号表里所有账号的密码统一改成 password，返回改了几个。"""
    load(force=True)
    accounts = dict(_cache["accounts"] or {})
    if not accounts:
        return 0
    for a in accounts:
        accounts[a]["pass"] = hash_password(password)
    _write(set(_cache["allow"] or []), accounts)
    return len(accounts)


def remove_account(acct):
    """从账号表和白名单里都删掉（角色存档不动）。"""
    load(force=True)
    acct = str(acct or "").strip()
    accounts = dict(_cache["accounts"] or {})
    allow = set(_cache["allow"] or [])
    had = acct in accounts
    accounts.pop(acct, None)
    allow.discard(acct)
    _write(allow, accounts)
    return had


def set_note(acct, note):
    load(force=True)
    accounts = dict(_cache["accounts"] or {})
    if acct in accounts:
        accounts[acct]["note"] = str(note or "")
        _write(set(_cache["allow"] or []), accounts)
        return True
    return False
