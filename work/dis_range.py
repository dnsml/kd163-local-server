# -*- coding: utf-8 -*-
"""反汇编指定虚拟地址区间，输出带目标解析的 asm。

用法: python work/dis_range.py 0x2ed400 0x2ee000 out.asm
"""
import sys
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

SO = "so/libgame.so"


def main():
    lo = int(sys.argv[1], 16)
    hi = int(sys.argv[2], 16)
    out = sys.argv[3] if len(sys.argv) > 3 else None
    raw = open(SO, "rb").read()
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.detail = False
    lines = []
    for ins in md.disasm(raw[lo:hi], lo):
        lines.append("%08x  %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
    txt = "\n".join(lines)
    if out:
        open(out, "w", encoding="utf-8").write(txt)
        print("wrote %s  %d insns" % (out, len(lines)))
    else:
        print(txt)


main()
