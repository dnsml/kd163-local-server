#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 relay.log 里 947 的数值变化，与 1413/1414 事件对齐成时间线。

输出形如：
    23:39:22 -> 23:39:24  事件 1413(00)    idx11 0->35  idx12 802->854
这样能直接看出「装一只某系妖灵，947 里哪个数字涨了多少」。

用法：python work/probe_947_attrs.py [日志路径]
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "logs", "relay.log")
sys.path.insert(0, ROOT)
import pet_proto  # noqa: E402

EV = re.compile(r"\[(\d\d:\d\d:\d\d)\] (C->S|S->C) cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)(?: hex=([0-9a-f]*))?")
WATCH = {947, 1410, 1411, 1412, 1413, 1414}


def vread(b, i):
    c = b[i]
    if c < 0x80:
        return c, i + 1
    if c == 0xCC:
        return b[i + 1], i + 2
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    raise ValueError("bad varint")


def head_nums(b):
    """947 头部数值：名字之后到 cd2710(skin) 之前。"""
    n = b[1] - 0xA0
    i = 2 + n
    j = b.find(b"\xcd\x27\x10")
    if j < 0:
        j = len(b)
    out = []
    while i < j:
        try:
            v, i = vread(b, i)
        except Exception:
            break
        out.append(v)
    return out


def slots_of(b):
    """947 的 8 个妖灵格（有=6B cd+id+部位+0101，空=4B 零）。"""
    j = b.find(b"\xcd\x27\x10")
    if j < 0:
        return {}
    p = j + 4
    if p < len(b) and b[p] == 0x08:
        p += 1
    out = {}
    for s in range(1, 9):
        if b[p:p + 1] == b"\xcd" and p + 6 <= len(b):
            out[s] = int.from_bytes(b[p + 1:p + 3], "big")
            p += 6
        else:
            p += 4
    return out


def main():
    pet_proto.load()
    last = None      # (t, nums, slots)
    pending = []     # 中间发生的事件
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            m = EV.search(ln)
            if not m:
                continue
            t, d, cmd, seq, n, hx = m.groups()
            cmd = int(cmd)
            if cmd not in WATCH:
                continue
            b = bytes.fromhex(hx or "")
            if cmd == 947:
                try:
                    nums = head_nums(b)
                    sl = slots_of(b)
                except Exception:
                    continue
                if last and nums != last[1]:
                    diffs = []
                    L = max(len(nums), len(last[1]))
                    for i in range(L):
                        a = last[1][i] if i < len(last[1]) else None
                        c = nums[i] if i < len(nums) else None
                        if a != c:
                            diffs.append("idx%d %s->%s" % (i, a, c))
                    ev = " ".join(pending) if pending else "-"
                    print("%s -> %s   %-42s %s"
                          % (last[0], t, ev, "  ".join(diffs)))
                    ids = " ".join("%d:%s" % (s, pet_proto.name_of(v)) for s, v in sorted(sl.items()))
                    print("      上阵: %s" % (ids or "空"))
                last = (t, nums, sl)
                pending = []
            else:
                if cmd in (1411, 1412, 1413, 1414) and d == "C->S":
                    pending.append("%d(%s)" % (cmd, hx))
                elif cmd == 1410:
                    pending.append("1410(%s)" % (hx or ""))


if __name__ == "__main__":
    main()
