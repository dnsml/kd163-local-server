# -*- coding: utf-8 -*-
"""AXML 结构 dump：打印 string pool 索引 + 每个元素的属性（ns/name/值）。

用法：python work/axml_dump.py AndroidManifest.xml
"""
import struct
import sys

RES_STRING_POOL = 0x0001
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_RESOURCE_MAP = 0x0180
UTF8_FLAG = 0x100

TYPES = {
    0x00: "NULL", 0x01: "REF", 0x02: "ATTR", 0x03: "STRING",
    0x04: "FLOAT", 0x05: "DIM", 0x06: "FRAC", 0x10: "INT_DEC",
    0x11: "INT_HEX", 0x12: "INT_BOOL",
}


def read_pool(m, base=8):
    t, hsz, sz = struct.unpack_from("<HHI", m, base)
    assert t == RES_STRING_POOL, "no string pool at %d" % base
    cnt, sty, flags, sstart, sstyle = struct.unpack_from("<5I", m, base + 8)
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
            n2 = m[p]
            p += 1
            if n2 & 0x80:
                n2 = ((n2 & 0x7F) << 8) | m[p]
                p += 1
            strings.append(m[p:p + n2].decode("utf-8", "replace"))
        else:
            n, = struct.unpack_from("<H", m, p)
            p += 2
            if n & 0x8000:
                n2, = struct.unpack_from("<H", m, p)
                p += 2
                n = ((n & 0x7FFF) << 16) | n2
            strings.append(m[p:p + n * 2].decode("utf-16-le", "replace"))
    return base, sz, cnt, flags, strings


def dump(path):
    m = open(path, "rb").read()
    ftype, fhsz, fsize = struct.unpack_from("<HHI", m, 0)
    print("file: type=0x%04x header=%d size=%d actual=%d" % (ftype, fhsz, fsize, len(m)))
    base, psz, cnt, flags, strings = read_pool(m)
    print("string pool: %d strings, utf8=%s" % (cnt, bool(flags & UTF8_FLAG)))
    pos = base + psz
    depth = 0
    while pos + 8 <= len(m):
        t, hsz, sz = struct.unpack_from("<HHI", m, pos)
        if sz <= 0:
            break
        if t == RES_XML_RESOURCE_MAP:
            n = (sz - hsz) // 4
            print("  [resource map] %d ids" % n)
        elif t == RES_XML_START_ELEMENT:
            # node header: lineNumber(4) comment(4) ns(4) name(4) attrStart(2) attrSize(2) attrCount(2) ...
            ns_i, name_i, a_start, a_size, a_count = struct.unpack_from("<IIHHH", m, pos + hsz)
            print("%s<%s%s>" % ("  " * depth, strings[name_i],
                                "" if ns_i == 0xFFFFFFFF else " ns=" + strings[ns_i]))
            ap = pos + hsz + a_start
            for k in range(a_count):
                a_ns, a_name, a_raw, v_size, v_res0, v_type, v_data = \
                    struct.unpack_from("<IIIHBBI", m, ap + k * a_size)
                an = strings[a_name]
                ans = "" if a_ns == 0xFFFFFFFF else strings[a_ns].rsplit("/", 1)[-1] + ":"
                if v_type == 0x03:
                    val = repr(strings[v_data])
                elif v_type == 0x01:
                    val = "REF@0x%08x" % v_data
                else:
                    val = "%s(0x%x)" % (TYPES.get(v_type, hex(v_type)), v_data)
                raw = "" if a_raw == 0xFFFFFFFF else " raw=%r" % strings[a_raw]
                print("%s  %s%s = %s%s" % ("  " * depth, ans, an, val, raw))
            depth += 1
        elif t == RES_XML_END_ELEMENT:
            depth = max(0, depth - 1)
        pos += sz


if __name__ == "__main__":
    dump(sys.argv[1])
