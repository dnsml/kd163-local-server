import struct, sys
p = "work/libgame.so"
d = open(p,'rb').read()
TEXT_OFF = 0x2ec5a8
TEXT_END = 0x2ec5a8 + 0x3b2714

# 目标 cmd 值
TARGETS = [817,818,819,820,837,838,178,181,182,29,28,30,1922,1931,1932]

def arm_movw_const(rd, imm16):
    imm4 = (imm16 >> 12) & 0xF
    imm12 = imm16 & 0xFFF
    return 0xE3000000 | (imm4 << 16) | (rd << 12) | imm12

def thumb_movw(rd, imm16):
    imm4 = (imm16 >> 12) & 0xF
    i = (imm16 >> 11) & 1
    imm3 = (imm16 >> 8) & 7
    imm8 = imm16 & 0xFF
    hw1 = 0xF240 | (i << 10) | imm4
    hw2 = (imm3 << 12) | (rd << 8) | imm8
    return struct.pack('<HH', hw1, hw2)

hits = []
for val in TARGETS:
    for rd in range(16):
        # ARM
        w = arm_movw_const(rd, val)
        pat = struct.pack('<I', w)
        i = TEXT_OFF
        cnt = 0
        while True:
            i = d.find(pat, i, TEXT_END)
            if i < 0: break
            # 检查 4 字节对齐 & 附近有 movt? 只记录
            hits.append((val, 'arm', rd, i))
            i += 4
            cnt += 1
            if cnt > 200: break
        # Thumb
        tp = thumb_movw(rd, val)
        i = TEXT_OFF
        cnt = 0
        while True:
            i = d.find(tp, i, TEXT_END)
            if i < 0: break
            hits.append((val, 'thumb', rd, i))
            i += 2
            cnt += 1
            if cnt > 200: break

from collections import Counter
c = Counter((v,k) for v,k,rd,off in hits)
for (v,k),n in sorted(c.items()):
    print("cmd=%d %-5s hits=%d" % (v,k,n))
print("--- 明细（只列 817-838）---")
for v,k,rd,off in hits:
    if 817 <= v <= 838:
        print("cmd=%d %-5s rd=%d off=0x%08x" % (v,k,rd,off))
