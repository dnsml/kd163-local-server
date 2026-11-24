# -*- coding: utf-8 -*-
"""列出真服 relay.log 里所有不同的 cmd=29 面板包，按等级/强化看字段变化。"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.path.dirname(HERE), "logs", "relay.log")


def dec(p):
    out, i, n = [], 0, len(p)
    while i < n:
        c = p[i]
        if c == 0xCC:
            out.append(p[i + 1] if i + 1 < n else 0)
            i += 2
        elif c == 0xCD:
            out.append(int.from_bytes(p[i + 1:i + 3], "big"))
            i += 3
        elif c == 0xCE:
            out.append(int.from_bytes(p[i + 1:i + 5], "big"))
            i += 5
        else:
            out.append(c)
            i += 1
    return out


PAT = re.compile(r"\[(\d\d:\d\d:\d\d)\] S->C cmd=29\s+seq=\d+\s+len=\d+\s+hex=(\S+)")


def main():
    seen = set()
    for ln in open(LOG, encoding="utf-8", errors="replace"):
        m = PAT.search(ln)
        if not m:
            continue
        t = dec(bytes.fromhex(m.group(2)))
        key = tuple(t)
        if key in seen:
            continue
        seen.add(key)
        print("%s n=%2d %s" % (m.group(1), len(t), t))
    print("共 %d 种" % len(seen))


if __name__ == "__main__":
    main()
