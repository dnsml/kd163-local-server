# -*- coding: utf-8 -*-
"""网页后台的管理员账号（admin.json）与会话 token。

规则
----
- 后台只绑 `127.0.0.1`（默认）时：**不强制登录**，行为和以前一样，方便本机开发；
- 绑到 `0.0.0.0` / 局域网 / 公网时：**必须**有 admin.json，没有就自动生成一个
  `admin` + 12 位随机密码，写进 admin.json 并打印在控制台（只显示这一次）；
- 会话 token 只存在内存里，后台进程重启 = 所有人重新登录（简单、安全）。

维护
----
    python work/admin_password.py set admin 你的密码
    python work/admin_password.py show        # 只看用户名和文件位置，不显示密码
    python work/admin_password.py disable     # 删掉 admin.json（只建议本机用）
"""
import json
import os
import secrets
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.environ.get("KD_ADMIN_FILE") or os.path.join(HERE, "admin.json")

TOKEN_TTL = 12 * 3600          # 12 小时
_tokens = {}                   # token -> {"user": str, "exp": float}
_fails = {}                    # ip -> [失败次数, 锁定到什么时候]


def _hash(pwd):
    from account_policy import hash_password
    return hash_password(pwd)


def _check(pwd, stored):
    from account_policy import check_password
    return bool(check_password(pwd, stored))


def configured():
    return os.path.exists(FILE)


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def set_user(user, password):
    user = str(user or "").strip() or "admin"
    data = {"user": user, "pass": _hash(password),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, FILE)
    try:
        os.chmod(FILE, 0o600)
    except OSError:
        pass
    return FILE


def disable():
    if os.path.exists(FILE):
        os.remove(FILE)


def ensure(host, force=False):
    """启动时调用。返回 (user, password) 表示刚生成了新密码；None 表示不需要/已有。

    force=True：即使绑的是 127.0.0.1 也生成（反向代理暴露后台时用 KD_ADMIN_REQUIRE=1）。
    """
    if configured():
        return None
    h = (host or "").strip().lower()
    if not force and h in ("127.0.0.1", "localhost", "::1"):
        return None             # 本机不强制登录
    pwd = "".join(secrets.choice(
        "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(12))
    set_user("admin", pwd)
    return "admin", pwd


def verify(user, password):
    d = load()
    if not d.get("user") or not d.get("pass"):
        return False
    if not secrets.compare_digest(str(user or ""), str(d["user"])):
        return False
    return _check(password or "", d["pass"])


def issue(user):
    tok = secrets.token_urlsafe(32)
    _tokens[tok] = {"user": str(user), "exp": time.time() + TOKEN_TTL}
    # 顺手清过期
    now = time.time()
    for k in [k for k, v in _tokens.items() if v["exp"] < now]:
        _tokens.pop(k, None)
    return tok


def check(tok):
    if not tok:
        return None
    v = _tokens.get(tok)
    if not v:
        return None
    if v["exp"] < time.time():
        _tokens.pop(tok, None)
        return None
    return v["user"]


def revoke(tok):
    _tokens.pop(tok or "", None)


def wait_seconds(ip):
    """当前 IP 还要等多少秒才能再试登录（0 = 可以试）。"""
    rec = _fails.get(ip or "?")
    now = time.time()
    if rec and rec[1] > now:
        return int(rec[1] - now) + 1
    return 0


def throttle(ip, ok):
    """登录失败限速：同一 IP 连错 5 次锁 60 秒。返回还需要等多少秒（0=可以继续）。"""
    ip = ip or "?"
    now = time.time()
    rec = _fails.get(ip)
    if rec and rec[1] > now:
        return int(rec[1] - now)
    if ok:
        _fails.pop(ip, None)
        return 0
    if not rec:
        rec = [0, 0.0]
    rec[0] += 1
    if rec[0] >= 5:
        rec = [0, now + 60]
    _fails[ip] = rec
    return 0
