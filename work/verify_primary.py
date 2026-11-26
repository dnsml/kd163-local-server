# -*- coding: utf-8 -*-
"""验证主号当前角色：登录后武器槽、背包全量是否正常。

用法：python work/verify_primary.py [acct]
不发任何写操作（不推 115、不穿脱），只登录 + 开背包 + 读包。
"""
import os
import socket
import struct
import sys

HOST, PORT = "127.0.0.1", 9000
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"
ACCT = sys.argv[1] if len(sys.argv) > 1 else "1784366"


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sfield(s):
    b = s.encode("utf-8")
    return bytes([0xA0 | len(b)]) + b


def recv_all(sock, wait=1.2):
    sock.settimeout(wait)
    buf, out = b"", []
    while True:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            break
        while len(buf) >= 8:
            total = struct.unpack(">H", buf[:2])[0]
            if len(buf) < total:
                break
            cmd, seq = struct.unpack(">HI", buf[2:8])
            out.append((cmd, seq, buf[8:total]))
            buf = buf[total:]
    return out


s = socket.create_connection((HOST, PORT), timeout=3)
s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sfield(ACCT) + b"\xda\x00\x2b" + TOKEN))
pk = recv_all(s, 2.0)
print("登录回包：", [(c, len(p)) for c, _q, p in pk])
s.sendall(pack(177, 30))
pk = recv_all(s, 1.5)
print("开背包回包：", [(c, len(p)) for c, _q, p in pk])
# 1265 = 登录初始化最后一步，服务端在这一步补推 29/30（不推这一步看不到 30）
s.sendall(pack(1265, 31))
pk2 = recv_all(s, 2.0)
print("1265 之后推送：", [(c, len(p)) for c, _q, p in pk2])
pk = pk + pk2
for cmd, _q, p in pk:
    if cmd == 178:
        n = p[1]
        print("178 全量 %d 件" % n)
        i = 2
        for _ in range(n):
            pos = p[i]
            i += 1
            enc = p[i]
            if enc == 0xcd:
                iid = struct.unpack(">H", p[i + 1:i + 3])[0]
                i += 3
            elif enc == 0xce:
                iid = struct.unpack(">I", p[i + 1:i + 5])[0]
                i += 5
            elif enc == 0xcc:
                iid = struct.unpack(">H", p[i + 1:i + 3])[0]
                i += 3
            else:
                iid = enc
                i += 1
            i += 3
            print("  槽%02d  id=%d" % (pos, iid))
s.close()
