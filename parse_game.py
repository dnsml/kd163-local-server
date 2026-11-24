#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 [u16BE len][u16BE cmd][u32BE seq][payload] 切分游戏协议流"""
import os, sys, glob, struct

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(HERE, "capture")


def parse_stream(data, label=""):
    out = []
    pos = 0
    n = len(data)
    while pos + 8 <= n:
        total, cmd, seq = struct.unpack_from(">HHI", data, pos)
        # len 字段语义：实测 = payload + 6
        if total < 6 or total > 60000:
            # 尝试找下一个候选包头
            pos += 1
            continue
        end = pos + 2 + total
        if end > n:
            break
        payload = data[pos + 8:end]
        out.append((pos, total, cmd, seq, payload))
        pos = end
    return out, pos


def hexdump(b, width=16, limit=256):
    b = b[:limit]
    lines = []
    for i in range(0, len(b), width):
        chunk = b[i:i + width]
        hx = " ".join("%02x" % x for x in chunk)
        asc = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        lines.append("    %04x  %-*s  %s" % (i, width * 3, hx, asc))
    return "\n".join(lines)


def utf8_strings(b, minlen=2):
    """提取可打印的 UTF-8 片段"""
    res = []
    cur = bytearray()
    i = 0
    while i < len(b):
        c = b[i]
        if c < 0x80:
            if 32 <= c < 127:
                cur.append(c); i += 1; continue
            else:
                if len(cur) >= minlen: res.append(cur.decode("ascii", "replace"))
                cur = bytearray(); i += 1; continue
        # 多字节
        ln = 2 if 0xC0 <= c < 0xE0 else 3 if 0xE0 <= c < 0xF0 else 4 if 0xF0 <= c < 0xF8 else 0
        if ln and i + ln <= len(b):
            try:
                s = b[i:i + ln].decode("utf-8")
                cur.extend(b[i:i + ln]); i += ln; continue
            except Exception:
                pass
        if len(cur) >= minlen: res.append(cur.decode("utf-8", "replace"))
        cur = bytearray(); i += 1
    if len(cur) >= minlen: res.append(cur.decode("utf-8", "replace"))
    return res


def main():
    files = sorted(glob.glob(os.path.join(CAP, "*game*.bin")))
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    if not files:
        print("没有 game dump 文件")
        return
    allcmds = {}
    for f in files:
        data = open(f, "rb").read()
        pkts, endpos = parse_stream(data)
        print("=" * 90)
        print("%s  %d 字节  解析出 %d 个包  剩余 %d 字节" % (os.path.basename(f), len(data), len(pkts), len(data) - endpos))
        print("=" * 90)
        for pos, total, cmd, seq, payload in pkts:
            print("off=%-6d len=%-5d cmd=%-5d (0x%04x) seq=%-4d payload=%d" % (pos, total, cmd, cmd, seq, len(payload)))
            if payload:
                print(hexdump(payload, limit=192))
                ss = utf8_strings(payload, 3)
                if ss:
                    print("    strings: %r" % (ss[:8],))
            allcmds.setdefault(cmd, []).append((os.path.basename(f), seq, len(payload)))
        print()

    print("=" * 90)
    print("cmd 汇总")
    print("%-8s %-8s %-6s %s" % ("cmd", "hex", "次数", "payload 长度"))
    print("-" * 60)
    for cmd in sorted(allcmds):
        rows = allcmds[cmd]
        print("%-8d 0x%04x %-6d %s" % (cmd, cmd, len(rows), [r[2] for r in rows][:8]))


if __name__ == "__main__":
    main()
