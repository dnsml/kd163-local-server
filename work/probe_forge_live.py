# -*- coding: utf-8 -*-
"""强化成功路径的 socket 验证：看 818 等级、29 战力/属性字段是否跟着变。

用特级石（cap +12）避免被档位上限挡掉：
  槽1 11108 防具 -> 20457 特级灵石
  槽7 11114 武器 -> 20456 特级仙玉
"""
import os
import socket
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import grant  # noqa: E402

HOST, PORT = "127.0.0.1", 9000
SEQ = [900]


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def recv_all(sock, wait=1.2):
    sock.settimeout(wait)
    buf, out = b"", []
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
            cmd, seq = struct.unpack(">HI", buf[2:8])
            out.append((cmd, seq, buf[8:need]))
            buf = buf[need:]
    return out


def dec_tokens(p):
    out, i, n = [], 0, len(p)
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


def hit(pkts, cmd):
    for c, _s, p in pkts:
        if c == cmd:
            return p
    return None


def main():
    s = socket.create_connection((HOST, PORT), timeout=5)
    print("连接 %s:%d" % (HOST, PORT))

    for tag, slotcode, stone in (("槽1 11108 防具", 0x00, 20457),
                                 ("槽7 11114 武器", 0x0A, 20456)):
        SEQ[0] += 1
        seq = SEQ[0]
        payload = bytes([slotcode, 0x0A, 0x01]) + b"\xcd" + stone.to_bytes(2, "big")
        s.sendall(pack(822, seq, payload))
        pk = recv_all(s, 1.6)
        cmds = [c for c, _s, _p in pk]
        print("\n[%s] 822 payload=%s -> %s" % (tag, payload.hex(), cmds))

        p822 = hit(pk, 822)
        if p822 is not None:
            print("   822 结果 %s" % (("空包=成功" if not p822 else "码%d" % p822[0])))
        p818 = hit(pk, 818)
        if p818 is not None:
            print("   818 %s" % dec_tokens(p818))
        p29 = hit(pk, 29)
        if p29 is not None:
            t = dec_tokens(p29)
            print("   29 %d 字段 %s" % (len(t), t))
        p28 = hit(pk, 28)
        if p28 is not None:
            m = grant.currencies(p28)
            print("   28 元宝%(元宝)d 铜钱%(铜钱)d 绑铜%(绑定铜钱)d" % m)

    s.close()
    print("\n完成")


if __name__ == "__main__":
    main()
