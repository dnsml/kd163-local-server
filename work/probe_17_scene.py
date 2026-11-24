# -*- coding: utf-8 -*-
"""登录探针：打印 17 里的场景字段（off25 cd+u16）与 113 的场景 id，确认两处同源。"""
import socket, struct, sys, os
ACCT = sys.argv[1] if len(sys.argv) > 1 else "900000102"
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"


def pack(c, s, p=b""):
    return struct.pack(">HHI", 6 + len(p), c, s) + p


def sf(x):
    b = x.encode() if isinstance(x, str) else x
    return bytes([0xA0 | len(b)]) + b


def recv_all(sock, wait=1.5):
    sock.settimeout(wait)
    buf = b""
    out = []
    while True:
        try:
            ch = sock.recv(65536)
            if not ch:
                break
            buf += ch
        except socket.timeout:
            break
        while len(buf) >= 8:
            total = struct.unpack(">H", buf[:2])[0]
            need = total + 2
            if len(buf) < need:
                break
            c, q = struct.unpack(">HI", buf[2:8])
            out.append((c, q, buf[8:need]))
            buf = buf[need:]
    return out


def dec_scene(b):
    if not b:
        return None
    if b[0] == 0xCD and len(b) >= 3:
        return int.from_bytes(b[1:3], "big")
    return b[0]


s = socket.create_connection(("127.0.0.1", 9000), timeout=5)
s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sf(ACCT) + b"\xda\x00\x2b" + TOKEN))
pk = recv_all(s, 2.0)
for c, q, p in pk:
    if c == 17:
        print("17 len=%d 场景=%s (bytes %s) 坐标=%s" %
              (len(p), dec_scene(p[25 + (len(p) - 197):28 + (len(p) - 197)]), p[25 + (len(p) - 197):28 + (len(p) - 197)].hex(), p[28 + (len(p) - 197):30 + (len(p) - 197)].hex()))
    elif c == 113:
        print("113 len=%d 场景=%s" % (len(p), dec_scene(p)))
    elif c == 117:
        print("117 len=%d %s" % (len(p), p.hex()))
    else:
        print("cmd=%-5d len=%d" % (c, len(p)))
s.close()
