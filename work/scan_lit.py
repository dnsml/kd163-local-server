import struct
d = open("work/libgame.so",'rb').read()
TEXT_OFF, TEXT_SIZE = 0x2ec5a8, 0x3b2714
TEXT_END = TEXT_OFF + TEXT_SIZE

# 段表（从 rizin iS 拿到的关键段）
SECTIONS = [
    (".text",        0x2ec5a8, 0x3b2714),
    (".rodata",      0x69ecbc + 0x571d0, 0),  # 稍后修正
]

# 直接扫全文件的 4 字节对齐常量
def find_const(val, lo, hi):
    pat = struct.pack('<I', val)
    res = []
    i = lo & ~3
    while True:
        i = d.find(pat, i, hi)
        if i < 0: break
        if i % 4 == 0:
            res.append(i)
        i += 1
    return res

for v in (817,818,819,820,837,838,181,182,178):
    hits = find_const(v, TEXT_OFF, TEXT_END)
    print("0x%04x (%d) 在 .text 中 4 字节对齐出现 %d 次: %s" % (v, v, len(hits), [hex(x) for x in hits[:12]]))
