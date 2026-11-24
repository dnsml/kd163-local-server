# -*- coding: utf-8 -*-
"""把 relay.log 末尾 N 行里的「非心跳/非战斗」命令筛出来，用来找新玩法入口。"""
import re
import sys

RE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] (C->S|S->C) cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)(?: hex=(\S*))?")
SKIP = {4, 115, 146, 148, 29, 28, 30, 150, 149, 210, 116, 1, 178}


def main(path, n):
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()[-int(n):]
    for L in lines:
        m = RE.match(L)
        if not m:
            continue
        t, d, c, s, ln, h = m.groups()
        if int(c) in SKIP:
            continue
        print("%-10s %-4s cmd=%-6s seq=%-5s len=%-5s %s" % (t, d, c, s, ln, (h or "")[:80]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/relay.log",
         sys.argv[2] if len(sys.argv) > 2 else 400)
