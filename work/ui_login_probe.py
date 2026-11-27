#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 uiautomator dump：打印登录对话框的账号框 / 密码框 / 登录按钮。

用法：python work/ui_login_probe.py logs/ui.xml
输出（每行一个 key=value，没找到就不输出）：
    acct=<账号框文字>
    passlen=<密码框字符个数>
    login=<x> <y>
    cancel=<x> <y>
    start=<x> <y>        # 「开始游戏」按钮（没有就不输出）
"""
import re
import sys


def center(bounds):
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
    return (x1 + x2) // 2, (y1 + y2) // 2


def main(path):
    try:
        s = open(path, encoding="utf-8").read()
    except Exception:
        return
    edits = []
    for t in re.findall(r"<node[^>]*>", s):
        cls = re.search(r'class="([^"]*)"', t)
        txt = re.search(r'text="([^"]*)"', t)
        bd = re.search(r'bounds="([^"]*)"', t)
        if not (cls and bd):
            continue
        text = txt.group(1) if txt else ""
        if "EditText" in cls.group(1):
            edits.append((text, bd.group(1)))
        elif text in ("登录", "取消", "开始游戏", "开始旅程"):
            x, y = center(bd.group(1))
            key = {"登录": "login", "取消": "cancel"}.get(text, "start")
            print("%s=%d %d" % (key, x, y))
    if edits:
        print("acct=%s" % edits[0][0])
    if len(edits) > 1:
        print("passlen=%d" % len(edits[1][0]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/ui.xml")
