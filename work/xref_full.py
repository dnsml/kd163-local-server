# -*- coding: utf-8 -*-
"""全量扫描 libgame.so 里「PIC 字符串引用」：ldr rX,[pc,#imm] + add rX,pc。

为什么需要这个：so/xref_pc.py 对 .rodata 里的字符串返回 refs=0（它可能只认
某几种编码），导致 create_role.xml / loading %d/%d 这些关键串「看起来没人引用」。
这里是暴力版：逐 2 字节解码，遇到 ldr reg,[pc,#imm] 就读字面量，算出
target = align4(addr+4) + literal，再跟字符串表比对。

用法：
    python work/xref_full.py 0x729f5f 0x729fb4
    python work/xref_full.py --str "create_role.xml"
"""
import struct
import sys

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from capstone.arm import ARM_OP_MEM, ARM_REG_PC

SO = "so/libgame.so"
TEXT_LO, TEXT_HI = 0x2EC4A8, 0x69E312


def cstr_at(raw, a, n=80):
    if a <= 0 or a >= len(raw):
        return None
    out = bytearray()
    for c in raw[a:a + n]:
        if c == 0:
            break
        out.append(c)
    if len(out) < 3:
        return None
    try:
        return out.decode("utf-8")
    except Exception:
        return None


def find_strings(raw, needles):
    """返回 {字符串起始地址: 文本}。"""
    out = {}
    for s in needles:
        b = s.encode()
        i = raw.find(b)
        while i >= 0:
            # 前面必须是 0 或者非可打印，才算独立串头
            if i == 0 or not (32 <= raw[i - 1] < 127):
                out[i] = s
            i = raw.find(b, i + 1)
    return out


def main():
    raw = open(SO, "rb").read()
    if len(sys.argv) > 1 and sys.argv[1] == "--str":
        targets = find_strings(raw, sys.argv[2:])
        if not targets:
            print("字符串没找到")
            return
        for a, s in sorted(targets.items()):
            print("目标 %s  %r" % (hex(a), s))
    else:
        targets = {}
        for a in sys.argv[1:]:
            v = int(a, 16)
            targets[v] = cstr_at(raw, v) or "?"

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.detail = True
    hits = {a: [] for a in targets}
    i = TEXT_LO
    while i < TEXT_HI - 4:
        for ins in md.disasm(raw[i:i + 4], i):
            if (ins.mnemonic.startswith("ldr") and len(ins.operands) > 1
                    and ins.operands[1].type == ARM_OP_MEM
                    and ins.operands[1].mem.base == ARM_REG_PC):
                lit = ins.address + 4 + (ins.operands[1].mem.disp & 0xFFFFFFFF)
                if 0 <= lit < len(raw) - 4:
                    val = struct.unpack_from("<I", raw, lit)[0]
                    pc = (ins.address + 4) & 0xFFFFFFFC
                    tgt = (pc + val) & 0xFFFFFFFF
                    if tgt in hits:
                        hits[tgt].append(ins.address)
            break
        i += 2

    for a in sorted(targets):
        hs = hits[a]
        print("%s %-28r refs=%d %s"
              % (hex(a), targets[a], len(hs), [hex(h) for h in hs]))


if __name__ == "__main__":
    main()
