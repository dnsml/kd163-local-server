import struct
d = open("work/libgame.so",'rb').read()
print("file size", hex(len(d)))
for val in (0x333,0x334,0x335,0x331,0x332):
    u32 = struct.pack('<I', val)
    u16 = struct.pack('<H', val)
    a = [i for i in range(len(d)) if d.startswith(u32, i)]
    b = [i for i in range(len(d)) if d.startswith(u16, i)]
    print("0x%x u32=%d u16=%d" % (val, len(a), len(b)))
    print("   u32:", [hex(x) for x in a[:15]])
    print("   u16:", [hex(x) for x in b[:15]])
