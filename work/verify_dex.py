# -*- coding: utf-8 -*-
"""DEX 结构自检：校验 header/校验和/各 section/字符串/class_data/code_item 一致性。"""
import struct
import sys
import zlib
import hashlib


def uleb(b, o):
    r = 0
    s = 0
    while True:
        x = b[o]
        o += 1
        r |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return r, o


def mutf8_len(s):
    n = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c < 0x80:
            i += 1
            n += 1
        elif c >> 5 == 6:
            i += 2
            n += 1
        elif c >> 4 == 0xE:
            i += 3
            n += 1
        else:
            i += 4
            n += 2
    return n


def check(path):
    b = open(path, "rb").read()
    errs = []
    assert b[:4] == b"dex\n", "magic"
    ver = b[4:8]
    file_size, = struct.unpack_from("<I", b, 0x20)
    if file_size != len(b):
        errs.append("file_size %d != %d" % (file_size, len(b)))
    chk, = struct.unpack_from("<I", b, 8)
    if chk != (zlib.adler32(b[12:]) & 0xFFFFFFFF):
        errs.append("checksum mismatch")
    if b[12:32] != hashlib.sha1(b[32:]).digest():
        errs.append("signature mismatch")
    map_off, = struct.unpack_from("<I", b, 0x34)
    if map_off % 4:
        errs.append("map_off 未 4 字节对齐: %d" % map_off)
    n, = struct.unpack_from("<I", b, map_off)
    items = []
    prev = -1
    for i in range(n):
        t, _, sz, off = struct.unpack_from("<HHII", b, map_off + 4 + i * 12)
        items.append((t, sz, off))
        if off <= prev:
            errs.append("map item %d offset 非递增 %d" % (i, off))
        prev = off
        if off + 4 > len(b):
            errs.append("map item %d offset 越界" % i)
    sids_size, sids_off = struct.unpack_from("<II", b, 0x38)
    str_end = {}
    for i in range(sids_size):
        off, = struct.unpack_from("<I", b, sids_off + 4 * i)
        if off >= len(b):
            errs.append("string_id %d 越界" % i)
            continue
        u16, o = uleb(b, off)
        e = b.index(b"\x00", o)
        s = b[o:e]
        if mutf8_len(s) != u16:
            errs.append("string_id %d utf16 长度不符 %d != %d (%r)" % (i, u16, mutf8_len(s), s[:40]))
        str_end[i] = e + 1
    # class_def
    cds, cdo = struct.unpack_from("<II", b, 0x60)
    ncode = ncd = 0
    ctype = {t: (sz, off) for t, sz, off in items}
    for i in range(cds):
        base = cdo + 32 * i
        cidx, flags, sup, ifo, src, ann, cdoff, svo = struct.unpack_from("<8I", b, base)
        if cidx >= struct.unpack_from("<I", b, 0x40)[0]:
            errs.append("class_def %d class_idx 越界" % i)
        if cdoff:
            ncd += 1
            o = cdoff
            sf, o = uleb(b, o)
            inf, o = uleb(b, o)
            dm, o = uleb(b, o)
            vm, o = uleb(b, o)
            for _ in range(sf + inf):
                _, o = uleb(b, o)
                _, o = uleb(b, o)
            for cnt in (dm, vm):
                for _ in range(cnt):
                    _, o = uleb(b, o)
                    _, o = uleb(b, o)
                    co, o = uleb(b, o)
                    if co:
                        ncode += 1
                        if co + 16 > len(b):
                            errs.append("code_off 越界 %d" % co)
                            continue
                        dbg, = struct.unpack_from("<I", b, co + 8)
                        ins, = struct.unpack_from("<I", b, co + 12)
                        if co + 16 + ins * 2 > len(b):
                            errs.append("code_item insns 越界 %d" % co)
                        if dbg and (dbg < ctype.get(0x2003, (0, 0))[1] or
                                    dbg > ctype.get(0x2003, (0, len(b)))[1] + 4 * 1024 * 1024):
                            errs.append("debug_info_off 可疑 %d" % dbg)
    print("%s: size=%d strings=%d classes=%d class_data=%d code=%d" %
          (path, len(b), sids_size, cds, ncd, ncode))
    print("  map sections:", ["%s(%d@%d)" % (hex(t), sz, off) for t, sz, off in items])
    if errs:
        print("  ERRORS:")
        for e in errs[:20]:
            print("   -", e)
        return False
    print("  OK")
    return True


if __name__ == "__main__":
    ok = True
    for p in sys.argv[1:]:
        ok &= check(p)
    sys.exit(0 if ok else 1)
