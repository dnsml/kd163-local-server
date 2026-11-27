# -*- coding: utf-8 -*-
"""最小 AXML 补丁器：只重建字符串池（string pool），其余 chunk 原样拼接。

用法：python work/axmlpatch.py in.xml out.xml old=new ...
"""
import struct
import sys

RES_STRING_POOL = 0x0001
UTF8_FLAG = 0x100


def read_pool(m):
    t, hsz, sz = struct.unpack_from("<HHI", m, 8)
    if t != RES_STRING_POOL:
        raise SystemExit("第一个 chunk 不是 string pool")
    cnt, sty, flags, sstart, sstyle = struct.unpack_from("<5I", m, 8 + 8)
    base = 8
    offs = list(struct.unpack_from("<%dI" % cnt, m, base + hsz))
    data0 = base + sstart
    strings = []
    utf8 = bool(flags & UTF8_FLAG)
    for o in offs:
        p = data0 + o
        if utf8:
            n = m[p]
            p += 1
            if n & 0x80:
                p += 1
            m2 = m[p]
            p += 1
            if m2 & 0x80:
                m2 = ((m2 & 0x7F) << 8) | m[p]
                p += 1
            strings.append(m[p:p + m2].decode("utf-8", "replace"))
        else:
            n, = struct.unpack_from("<H", m, p)
            p += 2
            if n & 0x8000:
                n2, = struct.unpack_from("<H", m, p)
                p += 2
                n = ((n & 0x7FFF) << 16) | n2
            strings.append(m[p:p + n * 2].decode("utf-16-le", "replace"))
    return 8, sz, cnt, sty, flags, sstart, sstyle, strings


def build_pool(strings, utf8=False):
    cnt = len(strings)
    body = bytearray()
    offs = []
    for s in strings:
        offs.append(len(body))
        if utf8:
            raw = s.encode("utf-8")
            body += bytes([len(raw)]) + bytes([len(s)]) + raw + b"\x00"
        else:
            raw = s.encode("utf-16-le")
            body += struct.pack("<H", len(s)) + raw + b"\x00\x00"
        while len(body) % 4:
            body += b"\x00"
    hsz = 28
    sstart = hsz + 4 * cnt
    size = sstart + len(body)
    out = bytearray()
    out += struct.pack("<HHI", RES_STRING_POOL, hsz, size)
    out += struct.pack("<5I", cnt, 0, 0, sstart, 0)
    for o in offs:
        out += struct.pack("<I", o)
    out += body
    return bytes(out)


def patch(src, dst, repl):
    m = open(src, "rb").read()
    ftype, fhsz, fsize = struct.unpack_from("<HHI", m, 0)
    base, psz, cnt, sty, flags, sstart, sstyle, strings = read_pool(m)
    new = [s
           for s in strings]
    changed = 0
    for i, s in enumerate(strings):
        ns = s
        for a, b in repl.items():
            ns = ns.replace(a, b)
        if ns != s:
            changed += 1
        new[i] = ns
    rest = m[base + psz:]
    pool = build_pool(new, utf8=bool(flags & UTF8_FLAG))
    out = bytearray()
    out += struct.pack("<HHI", ftype, fhsz, fhsz + len(pool) + len(rest))
    out += pool
    out += rest
    open(dst, "wb").write(bytes(out))
    return changed, len(out) - len(m)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    repl = {}
    for kv in sys.argv[3:]:
        k, v = kv.split("=", 1)
        repl[k] = v
    n, d = patch(src, dst, repl)
    print("changed strings:", n, "delta:", d)
