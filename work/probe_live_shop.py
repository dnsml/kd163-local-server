# -*- coding: utf-8 -*-
"""对本地服跑一遍「商城购买 / 合成 / 强化」的真实 socket 流程。

不依赖客户端，直接发协议帧，验证服务端 wiring：
  371 商城   ：买 20735 百年妖丹（area1 x1）、买 20141 初级灵石（area2 x5）
  831 合成   ：20141 x4 -> 20142，应扣 Formula.xml 的 cost=8000
  822 强化   ：槽1 用 20141 强化一次，看 818/29 是否跟着变

跑之前请先备份 player_state.json；跑完建议还原并重启服务端。
"""
import os
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import grant  # noqa: E402

HOST, PORT = "127.0.0.1", 9000
SEQ = [500]


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def recv_all(sock, wait=1.0):
    sock.settimeout(wait)
    buf = b""
    out = []
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
            need = total + 2
            if len(buf) < need:
                break
            cmd, seq = struct.unpack(">HI", buf[2:8])
            out.append((cmd, seq, buf[8:need]))
            buf = buf[need:]
    return out


def dec_tokens(p):
    """解 venc 令牌序列（0xCC/0xCD/0xCE 前缀）。"""
    out = []
    i = 0
    n = len(p)
    while i < n:
        c = p[i]
        if c == 0xCC:
            out.append(p[i + 1] if i + 1 < n else 0)
            i += 2
        elif c == 0xCD:
            out.append(int.from_bytes(p[i + 1:i + 3], "big"))
            i += 3
        elif c == 0xCE:
            out.append(int.from_bytes(p[i + 1:i + 5], "big"))
            i += 5
        else:
            out.append(c)
            i += 1
    return out


def money(pkts):
    for cmd, _seq, p in pkts:
        if cmd == 28:
            return grant.currencies(p)
    return {}


def send(sock, cmd, payload=b"", wait=1.0, tag=""):
    SEQ[0] += 1
    seq = SEQ[0]
    sock.sendall(pack(cmd, seq, payload))
    pk = recv_all(sock, wait)
    cmds = [c for c, _s, _p in pk]
    print("  %-22s -> %s" % (tag or ("cmd=%d" % cmd), cmds))
    return pk


def show_29(pkts, tag):
    for cmd, _seq, p in pkts:
        if cmd == 29:
            t = dec_tokens(p)
            print("     %s 29 字段 %d 个: %s" % (tag, len(t), t))
            return t
    print("     %s 没有 29 包" % tag)
    return None


def main():
    s = socket.create_connection((HOST, PORT), timeout=5)
    print("连接 %s:%d" % (HOST, PORT))

    # 先拿一次基线（随便发个 29 请求？本地无 handler，改用商城返回的 28）
    print("\n[1] 371 买 20735 百年妖丹 area=1 x1（Mall price=1，应扣元宝 1）")
    pk = send(s, 371, b"\xce" + (20735).to_bytes(4, "big") + b"\x01\x01", 1.2, "371 20735/1/1")
    m = money(pk)
    if m:
        print("     货币: 元宝%(元宝)d 绑元%(绑定元宝)d 铜钱%(铜钱)d 绑铜%(绑定铜钱)d" % m)
    show_29(pk, "购买后")

    print("\n[2] 371 买 20141 初级灵石 area=2 x5（Mall price=3，应扣元宝 15）")
    pk = send(s, 371, b"\xce" + (20141).to_bytes(4, "big") + b"\x02\x05", 1.2, "371 20141/2/5")
    m = money(pk)
    if m:
        print("     货币: 元宝%(元宝)d 绑元%(绑定元宝)d 铜钱%(铜钱)d 绑铜%(绑定铜钱)d" % m)

    print("\n[3] 831 合成 20141 x4 -> 20142（Formula cost=8000）")
    pk = send(s, 831, bytes([0x01]) + b"\xcd\x4e\xad" + bytes([0x01, 0x04]), 1.5, "831 20141x4")
    m = money(pk)
    if m:
        print("     货币: 元宝%(元宝)d 绑元%(绑定元宝)d 铜钱%(铜钱)d 绑铜%(绑定铜钱)d" % m)

    print("\n[4] 822 强化 槽1（部位码 00）用 20141 一次")
    pk = send(s, 822, bytes([0x00, 0x0A, 0x01]) + b"\xcd\x4e\xad", 1.5, "822 槽1")
    for cmd, _seq, p in pk:
        if cmd == 822:
            print("     822 结果码 %s（21成功/22失败/25铜钱不够/26材料不够）"
                  % (p[0] if p else "空"))
        if cmd == 818:
            print("     818 单槽 %s" % dec_tokens(p))
    show_29(pk, "强化后")
    m = money(pk)
    if m:
        print("     货币: 元宝%(元宝)d 绑元%(绑定元宝)d 铜钱%(铜钱)d 绑铜%(绑定铜钱)d" % m)

    s.close()
    print("\n完成")


if __name__ == "__main__":
    main()
