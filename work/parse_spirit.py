# -*- coding: utf-8 -*-
"""扫 relay.log / server.log，解出妖灵部位映射。

1410 每条 7B：[u8 序号-1][varint 妖灵id][u8 部位][01][01]
1412 应答  ：[u8 槽位][varint 妖灵id][00][00]
1411 请求  ：[u8 槽位]
1413 请求  ：[u8 第几只]

用法：
  python work/parse_spirit.py logs/relay.log
  python work/parse_spirit.py logs/server.log
"""
import re
import sys

LINE = re.compile(r"^\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s+(?:C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)(?: hex=(\S*))?")
PUSH = re.compile(r"^\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s+.*push cmd=(\d+)\s+len=(\d+).*hex=(\S*)")


def vread(b, i):
    """返回 (值, 新下标)。协议变长：cc+1B / cd+2B / ce+4B / 其余单字节。"""
    if i >= len(b):
        return None, i
    p = b[i]
    if p == 0xCC and i + 1 < len(b):
        return b[i + 1], i + 2
    if p == 0xCD and i + 2 < len(b):
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if p == 0xCE and i + 4 < len(b):
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    return p, i + 1


def parse_1410(h, when):
    b = bytes.fromhex(h)
    if not b.startswith(b"\xcc\x80"):
        return
    n = b[2]
    i = 3
    out = []
    for _ in range(n):
        idx = b[i]
        iid, i = vread(b, i + 1)
        if iid is None or i >= len(b):
            break
        part = b[i]
        i += 3                     # 部位 + 01 + 01
        out.append((idx + 1, iid, part))
    if out:
        print("%s 1410  %s" % (when, "  ".join(
            "第%d只 id=%-6d 部位=%d" % (a, b2, c) for a, b2, c in out)))


def main(path):
    seen = set()
    for L in open(path, encoding="utf-8", errors="replace"):
        m = LINE.match(L) or PUSH.match(L)
        if not m:
            continue
        g = m.groups()
        if len(g) == 5:
            when, cmd, seq, ln, h = g
        else:                      # push 行：时间, cmd, len, hex
            when, cmd, ln, h = g
        cmd = int(cmd)
        if cmd != 1410 or not h:
            continue
        key = (when, h)
        if key in seen:
            continue
        seen.add(key)
        parse_1410(h, when)


if __name__ == "__main__":
    for p in sys.argv[1:] or ["logs/relay.log"]:
        print("==", p)
        main(p)
