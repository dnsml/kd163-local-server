# -*- coding: utf-8 -*-
"""空号宠物自检：登录新号 -> 961(962) / 946(947) / 890 检查，确认不推宠物。"""
import socket, struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ACCT = sys.argv[1] if len(sys.argv) > 1 else "900000101"
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"

def pack(c, s, p=b""): return struct.pack(">HHI", 6+len(p), c, s) + p
def sf(x):
    b = x.encode() if isinstance(x,str) else x
    return bytes([0xA0|len(b)]) + b

def recv_all(sock, wait=1.2):
    sock.settimeout(wait); buf=b""; out=[]
    while True:
        try:
            ch = sock.recv(65536)
            if not ch: break
            buf += ch
        except socket.timeout: break
        while len(buf)>=8:
            total=struct.unpack(">H",buf[:2])[0]; need=total+2
            if len(buf)<need: break
            c,q=struct.unpack(">HI",buf[2:8]); out.append((c,q,buf[8:need])); buf=buf[need:]
    return out

s = socket.create_connection(("127.0.0.1",9000), timeout=5)
s.sendall(pack(17,2,b"\x01\xcc\x97\x02"+sf(ACCT)+b"\xda\x00\x2b"+TOKEN))
pk = recv_all(s,1.5)
print("登录回包:", [(c,len(p)) for c,_,p in pk])
s.sendall(pack(961,50)); pk = recv_all(s,1.0)
print("961 ->", [(c,len(p),p.hex()) for c,_,p in pk])
s.sendall(pack(946,51,b"\x01")); pk = recv_all(s,1.0)
print("946 ->", [(c,len(p)) for c,_,p in pk])
s.close()
