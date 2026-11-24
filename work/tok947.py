# -*- coding: utf-8 -*-
"""从 relay.log 抽取 947/962/1410/1412 样本，逐字节结构对比。"""
import os
import re

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "relay.log")


def toks(b):
    i = 0
    out = []
    while i < len(b):
        c = b[i]
        if c == 0xCC and i + 1 < len(b):
            out.append(("cc", b[i + 1]))
            i += 2
        elif c == 0xCD and i + 2 < len(b):
            out.append(("cd", int.from_bytes(b[i + 1:i + 3], "big")))
            i += 3
        elif c == 0xCE and i + 4 < len(b):
            out.append(("ce", int.from_bytes(b[i + 1:i + 5], "big")))
            i += 5
        else:
            out.append(("b", c))
            i += 1
    return out


def main():
    rows = []
    for ln in open(LOG, encoding="utf-8", errors="ignore"):
        m = re.search(r'\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s+S->C cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=(\S*)', ln)
        if not m:
            continue
        t, cmd, seq, ln_, hx = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
        if cmd not in (947, 962, 1410, 1412):
            continue
        b = bytes.fromhex(hx)
        rows.append((t, cmd, seq, ln_, b))

    for t, cmd, seq, ln_, b in rows:
        if t < "21:03:30" or t > "21:04:10":
            continue
        print("[%s] cmd=%d len=%d" % (t, cmd, ln_))
        print("   ", toks(b))

    print("\n=== 947 固定块长度统计 ===")
    for t, cmd, seq, ln_, b in rows:
        if cmd != 947:
            continue
        i = b.find(b"\xcd\x27\x10")  # 10000
        if i < 0:
            continue
        tail = b.rfind(b"\x03\xce")
        print("[%s] total=%d i10000=%d tail=%d  block=%d"
              % (t, len(b), i, tail, tail - (i + 4)))


if __name__ == "__main__":
    main()
