# -*- coding: utf-8 -*-
"""维护网页后台的管理员账号（admin.json）。

用法：
    python work/admin_password.py set admin 新密码   # 指定用户名/密码
    python work/admin_password.py set                # 用户名默认 admin，密码随机生成并打印
    python work/admin_password.py show               # 看用户名、文件位置（不显示密码）
    python work/admin_password.py disable            # 删掉 admin.json（只建议本机用）

说明：
- 后台绑 127.0.0.1 且没有 admin.json 时**不要求登录**；
- 绑 0.0.0.0 / 局域网启动时，若没有 admin.json 会自动生成并打印一次；
- 密码只存 pbkdf2 哈希，忘了只能重设。
"""
import argparse
import os
import secrets
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import admin_auth as A


def cmd_set(args):
    user = args.user or "admin"
    pwd = args.password
    if not pwd:
        pwd = "".join(secrets.choice(
            "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(12))
        print("已生成随机密码（只显示这一次）：")
    A.set_user(user, pwd)
    print("用户名：%s" % user)
    print("密码  ：%s" % pwd)
    print("文件  ：%s" % A.FILE)
    if os.environ.get("KD_ADMIN_FILE"):
        print("注意：这次写的是 KD_ADMIN_FILE 指定的路径")
    print("新密码下次登录生效（不用重启）；已登录的会话仍然有效，直到退出或 12 小时过期。")
    return 0


def cmd_show(_args):
    d = A.load()
    print("文件：%s（%s）" % (A.FILE, "存在" if A.configured() else "不存在"))
    print("用户名：%s" % (d.get("user") or "（未设置）"))
    print("更新时间：%s" % (d.get("updated_at") or "—"))
    print("是否需要登录：%s" % ("是" if A.configured() else "本机否 / 对外是（启动时自动生成）"))
    return 0


def cmd_disable(_args):
    A.disable()
    print("已删除 %s：后台不再要求登录（仅建议本机绑定使用）" % A.FILE)
    return 0


def main():
    ap = argparse.ArgumentParser(description="后台管理员账号维护")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("set")
    p.add_argument("user", nargs="?", default="admin")
    p.add_argument("password", nargs="?", default="")
    sub.add_parser("show")
    sub.add_parser("disable")
    a = ap.parse_args()
    return {"set": cmd_set, "show": cmd_show, "disable": cmd_disable}.get(a.cmd, cmd_show)(a)


if __name__ == "__main__":
    sys.exit(main())
