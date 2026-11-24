# -*- coding: utf-8 -*-
"""把一段函数里的 ldr rX,[pc,#imm] 字面量解析出来，并尝试读成字符串。

PIC 语义：ldr rX,[pc,#imm] 取的是「相对偏移」，随后 add rX,pc 得到绝对地址。
所以这里两种都打印：literal 原值、加 pc 后的目标地址、（若有）目标处的字符串。
"""
import sys
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

SO = "so/libgame.so"


def cstr(raw, a, n=64):
    if a <= 0 or a >= len(raw):
        return None
    b = raw[a:a + n]
    out = []
    for ch in b:
        if ch == 0:
            break
        if 32 <= ch < 127 or ch > 127:
            out.append(ch)
        else:
            return None
    s = bytes(out)
    if len(s) < 3:
        return None
    try:
        return s.decode("utf-8", "replace")
    except Exception:
        return None


def main():
    lo = int(sys.argv[1], 16)
    hi = int(sys.argv[2], 16)
    raw = open(SO, "rb").read()
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.skipdata = True
    for ins in md.disasm(raw[lo:hi], lo):
        line = "%08x  %-8s %s" % (ins.address, ins.mnemonic, ins.op_str)
        if ins.mnemonic.startswith("ldr") and "[pc" in ins.op_str:
            # 取后面的 #imm
            try:
                imm = int(ins.op_str.split("#")[-1].rstrip("]"), 0)
            except Exception:
                imm = 0
            lit_addr = ((ins.address + 4) & ~3) + imm
            import struct
            if lit_addr + 4 <= len(raw):
                lit = struct.unpack_from("<I", raw, lit_addr)[0]
                tgt = (lit + ins.address + 4) & 0xFFFFFFFF
                s1 = cstr(raw, tgt)
                s2 = cstr(raw, lit)
                line += "   ; lit=%08x tgt=%08x %s %s" % (
                    lit, tgt,
                    ("str(tgt)=%r" % s1) if s1 else "",
                    ("str(lit)=%r" % s2) if s2 and not s1 else "",
                )
        print(line)


main()
