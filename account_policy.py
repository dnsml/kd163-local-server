# -*- coding: utf-8 -*-
"""账号白名单。

为什么需要
----------
本服没有密码体系：`/auth` 只要带个用户名就发令牌，游戏登录（cmd=17）也只认账号串。
放到公网等于「知道账号就能登任何人」。白名单是最小成本的封口：只放行名单里的账号。

三种状态
--------
1. **未配置**（没有 accounts.json、也没有环境变量）→ 全部放行，行为与以前完全一致（开发用）。
2. **已配置** → 只放行名单里的账号；`/auth` 直接 403，游戏登录（17/20）也会被拦。
3. **名单为空**（文件存在但 allow 是空数组）→ 全部拒绝，并在启动日志里告警。
   这是故意的：配错了宁可锁门，也不要静默变成「谁都能进」。

配置来源（优先级从高到低）
--------------------------
- 环境变量 `KD_ALLOW_ACCOUNTS="1784366,900000102"`（`*` 表示放行所有）
- 文件 `accounts.json`（可用 `KD_ACCOUNTS_FILE` 换路径）：

```json
{"allow": ["1784366", "900000102"], "updated_at": "2026-11-26 22:30:00"}
```

文件改了不用重启：每次校验都看 mtime，改了立即生效。

维护命令
--------
    python work/allow_accounts.py list
    python work/allow_accounts.py add 1784366 900000102
    python work/allow_accounts.py remove 123
    python work/allow_accounts.py sync      # 用库里/磁盘上现有角色生成名单
    python work/allow_accounts.py off       # 删掉 accounts.json = 关闭白名单
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.environ.get("KD_ACCOUNTS_FILE") or os.path.join(HERE, "accounts.json")

_cache = {"mtime": None, "allow": None, "src": "off"}
_warned = [False]


def _parse(text):
    text = (text or "").strip()
    if not text:
        return set()
    text = text.replace(";", ",").replace(" ", ",")
    return {x.strip() for x in text.split(",") if x.strip()}


def load(force=False):
    """返回 (allow_set 或 None, 来源)。None = 未配置 = 全部放行。"""
    env = os.environ.get("KD_ALLOW_ACCOUNTS")
    if env is not None and env.strip():
        if env.strip() == "*":
            return None, "env(*)"
        return _parse(env), "env"
    try:
        m = os.path.getmtime(FILE)
    except OSError:
        return None, "off"
    if not force and _cache["mtime"] == m:
        return _cache["allow"], _cache["src"]
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
        raw = d.get("allow") if isinstance(d, dict) else d
        allow = {str(x).strip() for x in (raw or []) if str(x).strip()}
        _cache.update(mtime=m, allow=allow, src="file")
        return allow, "file"
    except Exception as e:
        # 配置坏了就锁门：宁可全部拒绝，也不能悄悄退化成「谁都能进」
        if not _warned[0]:
            print("[account_policy] ！！accounts.json 解析失败（%s），白名单按「全部拒绝」处理"
                  % e, flush=True)
            _warned[0] = True
        _cache.update(mtime=m, allow=set(), src="file(坏)")
        return set(), "file(坏)"


def allowed(acct):
    """这个账号能不能登录。未配置白名单时恒为 True。"""
    acct = str(acct or "").strip()
    if not acct:
        return False
    allow, _ = load()
    if allow is None:
        return True
    return acct in allow


def describe():
    """启动日志用的一句话。"""
    allow, src = load()
    if allow is None:
        return "关闭（所有账号可登录；配 accounts.json 或 KD_ALLOW_ACCOUNTS 开启）"
    if not allow:
        return "已开启但名单为空 —— 所有账号都会被拒绝（%s）" % src
    return "已开启：%d 个账号（%s）%s" % (
        len(allow), src, "、".join(sorted(allow)[:8]) + ("…" if len(allow) > 8 else ""))


def save(allow):
    """写回 accounts.json（排序、带时间戳），返回路径。"""
    data = {"allow": sorted({str(x).strip() for x in allow if str(x).strip()}),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    load(force=True)
    return FILE
