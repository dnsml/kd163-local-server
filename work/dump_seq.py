#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按时间窗打印 818/29/822 结构（用于反推属性包）。

用法: python work/dump_seq.py 19:14:00 19:30:00 [cmd1,cmd2]
"""
import re
import sys
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "logs", "relay.log")
REC = re.compile(r"\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")


def vread(b, i):
    c = b[i]
    if c == 0xCC:
        return b[i + 1], i + 2
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    return c, i + 1


def toks(b):
    o, i = [], 0
    while i < len(b):
        v, i = vread(b, i)
        o.append(v)
    return o


def main():
    t0 = sys.argv[1] if len(sys.argv) > 1 else "00:00:00"
    t1 = sys.argv[2] if len(sys.argv) > 2 else "99:99:99"
    want = {int(x) for x in sys.argv[3].split(",")} if len(sys.argv) > 3 else {818, 29, 822, 31, 819, 817, 820}
    for line in open(LOG, encoding="utf-8", errors="replace"):
        m = REC.search(line)
        if not m:
            continue
        t, d, c, s, ln, hx = m.groups()
        if not (t0 <= t <= t1):
            continue
        c = int(c)
        if c not in want:
            continue
        b = bytes.fromhex(hx)
        if c == 818:
            n = b[0] if b else 0
            print("%s %s 818 n=%d len=%d" % (t, d, n, len(b)))
            for k in range(n):
                rec = b[1 + 28 * k:1 + 28 * (k + 1)]
                if len(rec) < 28:
                    break
                iid, _ = vread(rec, 1)
                print("    slot=%d iid=%d lv=%d rest=%s" % (rec[0], iid, rec[6], rec[7:20].hex()))
        elif c in (29, 28):
            print("%s %s %d len=%d %s" % (t, d, c, len(b), toks(b)))
        else:
            print("%s %s %d len=%d %s" % (t, d, c, len(b), hx))


if __name__ == "__main__":
    main()
