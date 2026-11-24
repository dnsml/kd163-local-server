import struct, sys
d = open("work/libgame.so",'rb').read()
TEXT_OFF, TEXT_SIZE = 0x2ec5a8, 0x3b2714
TEXT_END = TEXT_OFF + TEXT_SIZE

def scan(target):
    res = []
    # Thumb-1 LDR literal: 01001 Rt imm8  (hw1)
    i = TEXT_OFF
    while i < TEXT_END - 4:
        hw1 = struct.unpack_from('<H', d, i)[0]
        if (hw1 & 0xF800) == 0x4800:
            rt = hw1 & 7
            imm8 = (hw1 >> 8) & 0xFF
            pc = (i + 4) & ~3
            if pc + imm8*4 == target:
                res.append((i, 'thumb1', rt))
        # Thumb-2 LDR.W literal: hw1==0xF8DF
        if hw1 == 0xF8DF:
            hw2 = struct.unpack_from('<H', d, i+2)[0]
            rt = (hw2 >> 12) & 0xF
            imm12 = hw2 & 0xFFF
            pc = (i + 4) & ~3
            if pc + imm12 == target:
                res.append((i, 'thumb2', rt))
        i += 2
    # ARM LDR literal: cond 010 P U B W L Rn Rt imm12
    i = TEXT_OFF
    while i < TEXT_END - 4:
        w = struct.unpack_from('<I', d, i)[0]
        if (w & 0x0F7F0000) == 0x051F0000:  # ldr Rt,[pc,#imm]
            imm12 = w & 0xFFF
            u = (w >> 23) & 1
            rt = (w >> 12) & 0xF
            pc = i + 8
            addr = pc + imm12 if u else pc - imm12
            if addr == target:
                res.append((i, 'arm', rt))
        i += 4
    return res

for t in [int(x,16) for x in sys.argv[1:]]:
    print("== target 0x%x ==" % t)
    for off, kind, rt in scan(t):
        print("   %s  at 0x%08x  r%d" % (kind, off, rt))
