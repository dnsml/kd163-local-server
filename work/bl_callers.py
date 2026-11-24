# -*- coding: utf-8 -*-
"""列出调用某个地址的所有 bl/blx 点。"""
import struct, sys
raw=open('so/libgame.so','rb').read()
TEXT_LO,TEXT_HI=0x2ec4a8,0x69e312

def bl_target(off):
    h1,h2=struct.unpack_from('<HH',raw,off)
    if (h1&0xF800)!=0xF000: return None
    if (h2&0xD000)!=0xD000: return None
    s=(h1>>10)&1; imm10=h1&0x3FF
    j1=(h2>>13)&1; j2=(h2>>11)&1; imm11=h2&0x7FF
    i1=1-(j1^s); i2=1-(j2^s)
    imm=(s<<24)|(i1<<23)|(i2<<22)|(imm10<<12)|(imm11<<1)
    if imm&0x8000000: imm-=0x10000000
    return (off+4+imm)&0xFFFFFFFF

def callers(target):
    out=[]
    for off in range(TEXT_LO,TEXT_HI-4,2):
        t=bl_target(off)
        if t==target: out.append(off)
    return out

if __name__=='__main__':
    for a in sys.argv[1:]:
        t=int(a,16)
        cs=callers(t)
        print(hex(t),'callers:',[hex(c) for c in cs])
