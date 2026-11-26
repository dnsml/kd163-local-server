# -*- coding: utf-8 -*-
"""维护账号白名单（accounts.json）。

用法：
    python work/allow_accounts.py list            # 看当前策略 + 库里每个账号是否放行
    python work/allow_accounts.py sync            # 用「库里 + 磁盘存档」现有账号生成名单（推荐第一步）
    python work/allow_accounts.py add 1784366     # 追加账号
    python work/allow_accounts.py remove 123      # 移除账号
    python work/allow_accounts.py off             # 删除 accounts.json = 关闭白名单（开发用）

注意：
- 白名单**一旦存在就生效**：名单外的账号 `/auth` 403、登录 17/20 也被拒；
- `add/remove` 会在现有文件上改；没有文件时从空开始，会自动提示先跑 sync；
- 改完不用重启游戏服，`account_policy` 每次校验都会看文件 mtime。
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import account_policy as AP


def existing_accounts():
    """现有角色：SQLite 优先，其次老 JSON。返回 [(acct, 描述)]。"""
    rows = []
    seen = set()
    try:
        import state_store
        for r in state_store.accounts():
            acct = str(r["acct"])
            seen.add(acct)
            rows.append((acct, "Lv%s %s" % (r["level"], r["name"] or "?")))
    except Exception as e:
        print("（读库失败：%s）" % e)
    for p in sorted(glob.glob(os.path.join(HERE, "player_state*.json"))):
        b = os.path.basename(p)
        if b == "player_state.json":
            acct = "1784366"
        elif b.startswith("player_state_") and b.endswith(".json"):
            acct = b[len("player_state_"):-len(".json")]
        else:
            continue
        if acct in seen:
            continue
        seen.add(acct)
        rows.append((acct, "仅 JSON 存档"))
    rows.sort(key=lambda kv: (kv[0].isdigit(), kv[0]))
    return rows


def current_allow():
    allow, src = AP.load(force=True)
    return allow, src


def cmd_list(_args):
    allow, src = current_allow()
    print("白名单文件：%s" % AP.FILE)
    print("策略：%s" % AP.describe())
    if allow is None:
        print("（未配置 = 所有账号可登录）\n")
    rows = existing_accounts()
    print("现有角色 %d 个：" % len(rows))
    for acct, desc in rows:
        mark = "✓" if (allow is None or acct in (allow or set())) else "×"
        print("  %s %-14s %s" % (mark, acct, desc))
    if allow:
        missing = sorted(allow - {a for a, _ in rows})
        if missing:
            print("\n名单里但库里没有角色的账号（可能是还没建角/已删除）：%s" % "、".join(missing))
    return 0


def cmd_sync(_args):
    rows = existing_accounts()
    if not rows:
        print("库里和磁盘上都没有角色，拒绝生成空名单（会锁死所有人）")
        return 2
    p = AP.save([a for a, _ in rows])
    print("已写入 %s：%d 个账号" % (p, len(rows)))
    print("白名单**从现在开始生效**，名单外账号将无法登录。")
    return 0


def cmd_add(args):
    allow, _ = current_allow()
    if allow is None:
        print("当前未开启白名单。add 会新建名单（只放行你加的账号）。")
        print("如果这是第一次开启，建议先跑：python work/allow_accounts.py sync")
        allow = set()
    allow |= {str(a).strip() for a in args.accounts if str(a).strip()}
    p = AP.save(allow)
    print("已写入 %s：%d 个账号（%s）" % (p, len(allow), "、".join(sorted(allow))))
    return 0


def cmd_remove(args):
    allow, _ = current_allow()
    if allow is None:
        print("当前没开白名单，没有可移除的账号")
        return 1
    for a in args.accounts:
        allow.discard(str(a).strip())
    p = AP.save(allow)
    print("已写入 %s：剩余 %d 个账号" % (p, len(allow)))
    if not allow:
        print("！！名单为空 = 所有账号都会被拒绝登录；不想锁服就 add 或 off")
    return 0


def cmd_off(_args):
    """放开白名单（写 open=true），**不删文件** —— 文件里还有账号密码。"""
    if not os.path.exists(AP.FILE):
        print("没有 %s：本来就是「谁都能登录」的状态，也没有账号密码记录" % AP.FILE)
    else:
        print("已放开：%s" % AP.set_open(True))
    if os.environ.get("KD_ALLOW_ACCOUNTS"):
        print("注意：环境变量 KD_ALLOW_ACCOUNTS 仍然生效（%s）" % os.environ["KD_ALLOW_ACCOUNTS"])
    print("已有账号的密码仍然校验；想彻底删掉账号表请手工删 accounts.json。")
    return 0


def cmd_passwd(args):
    if not AP.set_password(args.acct, args.password):
        print("账号 %s 不在账号表里（老白名单账号没有密码）" % args.acct)
        return 1
    print("已把 %s 的密码设为 %s" % (args.acct, args.password))
    return 0


def cmd_passwd_all(args):
    n = AP.set_all_passwords(args.password)
    if not n:
        print("账号表是空的：先用后台「新建游戏账号」或 sync 建账号")
        return 1
    print("已把 %d 个账号的密码统一设为 %s" % (n, args.password))
    return 0


def main():
    ap = argparse.ArgumentParser(description="账号白名单维护")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    sub.add_parser("sync")
    sub.add_parser("off")
    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("accounts", nargs="+")
    p = sub.add_parser("passwd")
    p.add_argument("acct")
    p.add_argument("password")
    p = sub.add_parser("passwd-all")
    p.add_argument("password", nargs="?", default="123")
    a = ap.parse_args()
    return {"list": cmd_list, "sync": cmd_sync, "add": cmd_add,
            "remove": cmd_remove, "off": cmd_off,
            "passwd": cmd_passwd, "passwd-all": cmd_passwd_all}.get(a.cmd, cmd_list)(a)


if __name__ == "__main__":
    sys.exit(main())
