# -*- coding: utf-8 -*-
"""解析 151 开战包，按字段打印偏移，用于对齐本地构造。"""
import sys


def vread(b, i):
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


def sread(b, i):
    """0xA0|n 长度前缀字符串。"""
    if i >= len(b):
        return None, None, i
    n = b[i] & 0x0F if (b[i] & 0xF0) == 0xA0 else None
    if n is None:
        return None, None, i
    raw = b[i + 1:i + 1 + n]
    try:
        s = raw.decode("utf-8")
    except Exception:
        s = repr(raw)
    return n, s, i + 1 + n


def dump(h, label):
    b = bytes.fromhex(h)
    print("=" * 60)
    print("%s  len=%d" % (label, len(b)))
    i = 0
    print("  [0] %02x" % b[0])
    print("  [1] %02x" % b[1])
    print("  [2] %02x  <- class/形象" % b[2])
    n, s, i = sread(b, 3)
    print("  [3] len=%s name=%r -> next %d" % (n, s, i))
    print("  [%d] %02x" % (i, b[i]))
    i += 1
    hp, i = vread(b, i)
    hp2, i = vread(b, i)
    mp, i = vread(b, i)
    mp2, i = vread(b, i)
    print("  hp=%s/%s mp=%s/%s -> next %d" % (hp, hp2, mp, mp2, i))
    print("  --- 之后原始: %s" % b[i:].hex())
    return b, i


if __name__ == "__main__":
    samples = {
        "真服 23:45:03 锁妖塔(带熊猫)": "010c01a6e8908ce696b00100cd0a06cd0a06cd026ccd026c01ace58a9fe5a4abe7868ae78cab0fcd0a34cd0a34cd271001ce00205a6dcd0ee3cd0ee30000020107",
        "真服 23:40:37(不带宠物?)": "010c01a6e8908ce696b00100cd08fecd08fecd0258cd025801ace58a9fe5a4abe7868ae78cab0fcd0a34cd0a34cd2710",
        "本地模板 OPEN_TMPL": "010c06a6e8908ce696b001004c4c2f2f00a00000000001ce0001b1fd50500000020107",
    }
    for k, v in samples.items():
        try:
            dump(v, k)
        except Exception as e:
            print("解析失败 %s: %s" % (k, e))
