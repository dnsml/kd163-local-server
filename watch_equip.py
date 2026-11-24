#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盯 relay.log，一旦出现 819/820（装备格查询/应答）就把 payload 解出来。

用法： python watch_equip.py [--log logs/relay.log] [--from N]
--from N 表示从第 N 行之后开始看（默认从文件末尾开始）。
ctrl-c 退出。
"""
import argparse
import re
import sys
import time

WATCH = (819, 820, 821)
LINE = re.compile(r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")


def vint(b, i):
    """解析一条 varint，返回 (值, 新下标, 类型)。"""
    if i >= len(b):
        return None, i, "eof"
    c = b[i]
    if c == 0xCE:
        if i + 5 > len(b):
            return None, i, "short"
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5, "u32"
    if c == 0xCD:
        if i + 3 > len(b):
            return None, i, "short"
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3, "u16"
    if c == 0xCC:
        if i + 2 > len(b):
            return None, i, "short"
        return b[i + 1], i + 2, "u8"
    if c <= 0x7F:
        return c, i + 1, "tiny"
    return None, i + 1, "str0x%02x" % c


def dump(payload, title):
    print("\n" + "=" * 62)
    print(title)
    print("raw(%d) = %s" % (len(payload), payload.hex()))
    print("idx : 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f")
    for off in range(0, len(payload), 16):
        chunk = payload[off:off + 16]
        print("%04x: %s" % (off, " ".join("%02x" % x for x in chunk)))
    # 顺序 varint 扫描（粗解，遇到字符串前缀会跳过 1 字节继续）
    i, out = 0, []
    while i < len(payload):
        v, ni, kind = vint(payload, i)
        if v is None:
            i = ni
            continue
        out.append("[%d]%s=%d" % (i, kind, v))
        i = ni
    print("varint 扫: " + " ".join(out))
    print("=" * 62 + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="logs/relay.log")
    ap.add_argument("--from", dest="start", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="从文件开头扫一遍已有的")
    args = ap.parse_args()

    with open(args.log, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, 2)
        pos = f.tell()
        if args.all:
            f.seek(0)
            pos = 0

    print("盯 %s，等待 819/820 ..." % args.log)
    while True:
        with open(args.log, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(pos)
            lines = f.readlines()
            pos = f.tell()
        for raw in lines:
            m = LINE.search(raw)
            if not m:
                if "连接" in raw:
                    print("[%s] %s" % (m.group(1) if m else "-", raw.strip()))
                continue
            t, dirn, cmd, seq, ln, hx = m.groups()
            cmd = int(cmd)
            if cmd not in WATCH:
                continue
            dump(bytes.fromhex(hx), "[%s] %s cmd=%d seq=%s len=%s" % (t, dirn, cmd, seq, ln))
        time.sleep(0.4)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
