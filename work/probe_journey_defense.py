# -*- coding: utf-8 -*-
"""验证「开始旅程 182」与「防御回合」两条修复。

用法：python work/probe_journey_defense.py [账号]

流程：
  1) 登录拿 17
  2) 发 182 00 01（正服「开始旅程」原包）-> 期望 182 空 + 29/28/178，且不报错
  3) 145 开战（功夫熊猫 111202）-> 145 ACK + 151
  4) 连续 3 次 146 04 00 00（防御）-> 每回合都要有 148，
     且服务端日志里怪剩血 / 玩家剩血都要变化（以前整回合静止）
  5) 149 收尾 -> 150
"""
import socket
import struct
import sys
import time

ACCT = sys.argv[1] if len(sys.argv) > 1 else "900000102"
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"
HOST, PORT = "127.0.0.1", 9000


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sfield(x):
    b = x.encode() if isinstance(x, str) else x
    return bytes([0xA0 | len(b)]) + b


def recv_all(sk, wait=1.5):
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


def show(tag, pk):
    print("  %-14s %s" % (tag, [(c, len(p)) for c, _, p in pk]))


s = socket.create_connection((HOST, PORT), timeout=5)
s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sfield(ACCT) + b"\xda\x00\x2b" + TOKEN))
pk = recv_all(s, 2.0)
show("登录", pk)
if not any(c == 17 for c, _, _ in pk):
    print("[!] 没拿到 17，确认账号有角色")
    s.close()
    raise SystemExit(1)

# ---------- 1) 开始旅程 ----------
print("\n[1] 182 00 01 = 开始旅程")
s.sendall(pack(182, 10, b"\x00\x01"))
pk = recv_all(s, 1.5)
show("回包", pk)
cmds = [c for c, _, _ in pk]
ok1 = 182 in cmds and 1 not in cmds and 29 in cmds
print("    -> %s（期望 182 空 ACK + 29，且无 cmd=1 报错）"
      % ("[OK]" if ok1 else "[!!]"))

# ---------- 2) 开战 ----------
# 血厚的怪才能撑出多个「非结束」回合：lv30 日曜使者 hp 1939
MOB = int(sys.argv[2]) if len(sys.argv) > 2 else 2090101
print("\n[2] 145 开战 mob=%d" % MOB)
s.sendall(pack(145, 20, b"\xce" + MOB.to_bytes(4, "big")))
pk = recv_all(s, 1.5)
show("回包", pk)
o151 = [p for c, _, p in pk if c == 151]
if o151:
    print("    151 len=%d hex=%s" % (len(o151[0]), o151[0].hex()))

# ---------- 3) 连打 3 次防御 ----------
print("\n[3] 146 04 00 00 防御 x3")
for i in range(6):
    s.sendall(pack(146, 30 + i, bytes.fromhex("040000")))
    pk = recv_all(s, 1.2)
    show("回合%d" % (i + 1), pk)
    r148 = [p for c, _, p in pk if c == 148]
    if r148:
        print("      148 len=%d hex=%s" % (len(r148[0]), r148[0].hex()))
    else:
        print("      [!!] 没有 148 —— 防御回合没回包")

# ---------- 4) 结算 ----------
print("\n[4] 149 结算")
s.sendall(pack(149, 40, b""))
pk = recv_all(s, 1.5)
show("回包", pk)
for c, _, p in pk:
    if c == 150:
        print("    150 len=%d hex=%s" % (len(p), p.hex()))

s.close()
print("\n完成。服务端日志里应能看到 3 条 cmd=148 [回合 怪剩血… 玩家剩血…] 且数值逐回合变化。")
