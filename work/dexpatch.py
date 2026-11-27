# -*- coding: utf-8 -*-
"""最小 DEX 字符串补丁器（只改字符串常量，重建 string_data 区并修正所有绝对偏移）。

用法：
    python work/dexpatch.py <in.dex> <out.dex> old=new [old=new ...]

设计要点：
- string_data_item 区在 DEX 中是一段连续块，所有引用它的地方只有
  string_id_item.string_data_off。重建该块后，其后所有 section 整体平移 delta，
  需要修正的绝对偏移只有：
    header.map_off / data_size / file_size
    map_list 每个 item.offset
    class_def_item.{class_data_off, static_values_off, annotations_off}
    class_data_item 里 encoded_method.code_off
    code_item.debug_info_off
    annotations_directory_item.class_annotations_off
  其余（type_list / annotation_set / annotation_set_ref_list / field/method 注解）
  都是相对自身起始的偏移，不用改。
- 最后重算 SHA-1 signature 与 adler32 checksum。
"""
import struct
import sys
import zlib
import hashlib


def uleb_read(b, o):
    r = 0
    s = 0
    while True:
        x = b[o]
        o += 1
        r |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return r, o


def uleb_write(v):
    out = bytearray()
    while True:
        x = v & 0x7F
        v >>= 7
        if v:
            out.append(x | 0x80)
        else:
            out.append(x)
            return bytes(out)


def uleb_len(v):
    return len(uleb_write(v))


def read_string(b, off):
    n, o = uleb_read(b, off)
    e = b.index(b"\x00", o)
    return n, b[o:e], e + 1


def parse_map(b):
    map_off, = struct.unpack_from("<I", b, 0x34)
    n, = struct.unpack_from("<I", b, map_off)
    items = []
    for i in range(n):
        t, _, sz, off = struct.unpack_from("<HHII", b, map_off + 4 + i * 12)
        items.append([t, sz, off])
    return map_off, items


def patch(src, dst, repl):
    b = bytearray(open(src, "rb").read())
    if b[:4] != b"dex\n":
        raise SystemExit("不是 dex 文件")

    file_size, = struct.unpack_from("<I", b, 0x20)
    map_off, map_items = parse_map(b)
    sids_size, sids_off = struct.unpack_from("<II", b, 0x38)
    cd_size, cd_off = struct.unpack_from("<II", b, 0x60)
    data_size, data_off = struct.unpack_from("<II", b, 0x68)

    # ---- 1. 读出全部字符串，应用替换 ----
    sids = [struct.unpack_from("<I", b, sids_off + 4 * i)[0] for i in range(sids_size)]
    strings = []
    changed = 0
    for off in sids:
        n, s, end = read_string(b, off)
        ns = s
        for old, new in repl.items():
            if old in ns:
                ns = ns.replace(old, new)
        if ns != s:
            changed += 1
        # utf16 长度：BMP 内 ASCII/字符按 1 计，4 字节序列按 2 计
        u16 = 0
        i = 0
        while i < len(ns):
            c = ns[i]
            if c < 0x80:
                i += 1
                u16 += 1
            elif c >> 5 == 0x6:
                i += 2
                u16 += 1
            elif c >> 4 == 0xE:
                i += 3
                u16 += 1
            else:
                i += 4
                u16 += 2
        strings.append((u16, ns))

    # ---- 2. 重建 string_data 块 ----
    old_start = min(sids)
    # 区的结束位置取 map 中下一个 section 的起点（原文件可能在末尾带对齐 padding，
    # 不能只按最后一个字符串的结束位置算，否则残留 padding 会破坏对齐）
    old_end = min([off for t, sz, off in map_items if off > old_start] or [len(b)])
    block = bytearray()
    new_offs = []
    for u16, s in strings:
        new_offs.append(old_start + len(block))
        block += uleb_write(u16) + s + b"\x00"
    old_span = old_end - old_start
    if len(block) <= old_span:
        # 新串总长不超过原区：直接补零到原长度，delta=0，
        # 后续所有 section 偏移完全不变，只需改 string_ids 与校验和。
        block += b"\x00" * (old_span - len(block))
        delta = 0
    else:
        while len(block) % 4:
            block += b"\x00"
        delta = len(block) - old_span

    # ---- 3. 拼接新文件 ----
    out = bytearray()
    out += b[:old_start]
    out += block
    out += b[old_end:]
    assert len(out) == file_size + delta, (len(out), file_size, delta)

    def adj(v):
        # 只平移落在 string_data 之后的偏移
        return v + delta if v >= old_end else v

    # ---- 4. 修 header ----
    struct.pack_into("<I", out, 0x20, len(out))
    struct.pack_into("<I", out, 0x34, adj(map_off))
    struct.pack_into("<I", out, 0x68, data_size + delta)

    # ---- 5. 修 string_ids ----
    for i, o in enumerate(new_offs):
        struct.pack_into("<I", out, sids_off + 4 * i, o)

    # ---- 6. 修 class_def ----
    code_offs = set()
    for i in range(cd_size):
        base = cd_off + 32 * i
        # interfaces_off, annotations_off, class_data_off, static_values_off
        for fo in (12, 20, 24, 28):
            v, = struct.unpack_from("<I", out, base + fo)
            if v:
                struct.pack_into("<I", out, base + fo, adj(v))

    # ---- 7. 按 map 逐段修内部绝对偏移 ----
    new_map_off = adj(map_off)
    for idx, (t, sz, off) in enumerate(map_items):
        struct.pack_into("<I", out, new_map_off + 4 + idx * 12 + 8, adj(off))

    for t, sz, off in map_items:
        noff = adj(off)
        if t == 0x2000:  # class_data_item
            o = noff
            for _ in range(sz):
                sf, o = uleb_read(out, o)
                inf, o = uleb_read(out, o)
                dm, o = uleb_read(out, o)
                vm, o = uleb_read(out, o)
                for _ in range(sf + inf):
                    _, o = uleb_read(out, o)
                    _, o = uleb_read(out, o)
                for cnt in (dm, vm):
                    for _ in range(cnt):
                        _, o = uleb_read(out, o)
                        _, o = uleb_read(out, o)
                        o_start = o
                        co, o = uleb_read(out, o)
                        if co:
                            nco = adj(co)
                            nb = uleb_write(nco)
                            if len(nb) == (o - o_start):
                                out[o_start:o] = nb
                            else:
                                raise SystemExit("code_off uleb 长度变化，需扩展实现")
                            code_offs.add(nco)
        elif t == 0x2006:  # annotations_directory_item
            o = noff
            i = 0
            while i < sz:
                cao, fs, ms_, ps = struct.unpack_from("<IIII", out, o)
                if cao:
                    struct.pack_into("<I", out, o, adj(cao))
                o += 16 + 8 * (fs + ms_ + ps)
                i += 1

    # ---- 8. 修 code_item.debug_info_off ----
    for co in code_offs:
        dbg, = struct.unpack_from("<I", out, co + 8)
        if dbg:
            struct.pack_into("<I", out, co + 8, adj(dbg))

    # ---- 9. 重算签名/校验 ----
    sig = hashlib.sha1(bytes(out[32:])).digest()
    out[12:32] = sig
    chk = zlib.adler32(bytes(out[12:])) & 0xFFFFFFFF
    struct.pack_into("<I", out, 8, chk)

    open(dst, "wb").write(bytes(out))
    return changed, delta


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    repl = {}
    for kv in sys.argv[3:]:
        k, v = kv.split("=", 1)
        repl[k.encode()] = v.encode()
    n, delta = patch(src, dst, repl)
    print("changed strings:", n, "delta bytes:", delta)
