# -*- coding: utf-8 -*-
"""验证 400702「前世仙宠」交任务会推 957（80+2B 功夫熊猫）并写进 pets。

用法：python work/probe_400702.py [账号]
前提：该账号 quest_done 里没有 400702，否则走「重复交」分支不发奖励。
"""
import socket
import struct
import sys

ACCT = sys.argv[1] if len(sys.argv) > 1 else "900000102"
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sfield(x):
    b = x.encode() if isinstance(x, str) else x
    return bytes([0xA0 | len(b)]) + b


def recv_all(sk, wait=2.0):
    sk.settimeout(wait)
    buf = b""
    out = []
    while True:
        try:
            ch = sk.recv(65536)
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
            cmd, seq = struct.unpack(">HI", buf[2:8])
            out.append((cmd, seq, buf[8:need]))
            buf = buf[need:]
    return out


s = socket.create_connection(("127.0.0.1", 9000), timeout=5)
s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sfield(ACCT) + b"\xda\x00\x2b" + TOKEN))
pk = recv_all(s, 2.0)
print("登录回包:", [(c, len(p)) for c, _, p in pk])
if not any(c == 17 for c, _, _ in pk):
    print("[!] 没拿到 17，先确认账号有角色")
    s.close()
    raise SystemExit(1)

s.sendall(pack(214, 50, bytes.fromhex("ce00061d3e")))   # 400702
pk = recv_all(s, 2.0)
print("交任务回包:", [(c, len(p)) for c, _, p in pk])

hit = [p for c, _, p in pk if c == 957]
if hit:
    print("[OK] 收到 957，len=%d" % len(hit[0]))
    print("     " + hit[0].hex())
else:
    print("[!!] 没有 957")

for c, _, p in pk:
    if c in (29, 28, 210):
        print("  cmd=%-4d len=%-3d %s" % (c, len(p), p.hex()[:60]))
s.close()
