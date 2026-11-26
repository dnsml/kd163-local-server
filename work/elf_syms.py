# -*- coding: utf-8 -*-
"""极简 ELF32 符号表读取（只为了在 libgame.so 里找函数地址，不依赖 pyelftools）。

用法：
    python work/elf_syms.py <so>                 # 列出全部符号（太多）
    python work/elf_syms.py <so> PetView         # 只列名字里含关键字的
"""
import struct
import sys


def load(path):
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF":
        raise SystemExit("不是 ELF")
    is64 = d[4] == 2
    if is64:
        e_shoff = struct.unpack_from("<Q", d, 0x28)[0]
        e_shentsize = struct.unpack_from("<H", d, 0x3A)[0]
        e_shnum = struct.unpack_from("<H", d, 0x3C)[0]
        e_shstrndx = struct.unpack_from("<H", d, 0x3E)[0]
    else:
        e_shoff = struct.unpack_from("<I", d, 0x20)[0]
        e_shentsize = struct.unpack_from("<H", d, 0x2E)[0]
        e_shnum = struct.unpack_from("<H", d, 0x30)[0]
        e_shstrndx = struct.unpack_from("<H", d, 0x32)[0]

    sh = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        if is64:
            name, typ, flags, addr, off, size, link, info, align, entsize = \
                struct.unpack_from("<IIQQQQIIQQ", d, o)
        else:
            name, typ, flags, addr, off, size, link, info, align, entsize = \
                struct.unpack_from("<IIIIIIIIII", d, o)
        sh.append(dict(name=name, typ=typ, addr=addr, off=off, size=size,
                       link=link, entsize=entsize))

    def cstr(base, n):
        e = d.index(b"\x00", base + n)
        return d[base + n:e].decode("ascii", "ignore")

    syms = []
    for sec in sh:
        if sec["typ"] not in (2, 11):        # SYMTAB / DYNSYM
            continue
        strtab = sh[sec["link"]]["off"]
        ent = sec["entsize"] or (24 if is64 else 16)
        for k in range(sec["size"] // ent):
            o = sec["off"] + k * ent
            if is64:
                st_name, st_info, st_other, st_shndx, st_value, st_size = \
                    struct.unpack_from("<IBBHQQ", d, o)
            else:
                st_name, st_value, st_size, st_info, st_other, st_shndx = \
                    struct.unpack_from("<IIIBBH", d, o)
            if st_name:
                syms.append((cstr(strtab, st_name), st_value, st_size))
    return d, sh, syms


if __name__ == "__main__":
    path = sys.argv[1]
    kw = sys.argv[2] if len(sys.argv) > 2 else ""
    _d, _sh, syms = load(path)
    hit = [s for s in syms if kw in s[0]]
    print("符号 %d 条，命中 %d" % (len(syms), len(hit)))
    for nm, v, sz in sorted(hit, key=lambda x: x[1]):
        print("%08x %5d %s" % (v, sz, nm[:120]))
