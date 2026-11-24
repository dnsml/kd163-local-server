# -*- coding: utf-8 -*-
"""本地服 149 结算自测：打一只世界BOSS，验证 150 的掉落结构 + 178 刷新。

真服 150 结构（58 份样本实证）：
  [经验 变长][3B][铜钱 变长][1B][u8 件数]{[物品ID 变长][u8 数量][u8 标记]}[4B]
用法： python work/test_150.py [怪物ID]   默认 900001 九尾狐（世界BOSS lv25）
"""
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bag_proto

HOST, PORT = "127.0.0.1", 9000


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def recv_all(sock, wait=1.2):
    sock.settimeout(wait)
    buf = b""
    try:
        while True:
            d = sock.recv(65536)
            if not d:
                break
            buf += d
    except socket.timeout:
        pass
    return buf


def parse(buf):
    out, i = [], 0
    while i + 8 <= len(buf):
        total, cmd, seq = struct.unpack(">HHI", buf[i:i + 8])
        end = i + total + 2
        if total < 6 or end > len(buf):
            break
        out.append((cmd, seq, buf[i + 8:end]))
        i = end
    return out


def rd(b, i):
    c = b[i]
    if c == 0xCC:
        return b[i + 1], i + 2
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    return c, i + 1


def decode_150(pl):
    i = 0
    exp, i = rd(pl, i)
    gap = pl[i:i + 3]
    i += 3
    coin, i = rd(pl, i)
    d = pl[i]
    i += 1
    n = pl[i]
    i += 1
    drops = []
    for _ in range(n):
        iid, i = rd(pl, i)
        cnt, flag = pl[i], pl[i + 1]
        i += 2
        drops.append((iid, cnt, flag))
    tail = pl[i:]
    return exp, gap, coin, d, drops, tail


def main():
    mob = int(sys.argv[1]) if len(sys.argv) > 1 else 900001
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.sendall(pack(145, 100, b"\xce" + struct.pack(">I", mob)))
    res = parse(recv_all(s))
    print("开战 145 -> %s" % [(c, len(p)) for c, _, p in res])

    end_pk = None
    # 固定打 6 回合（普通怪 2~6 回合内必死；怪死后继续发 146 只会回结束包，无副作用）
    for r in range(1, 7):
        s.sendall(pack(146, 100 + r, bytes([0x01, 0x07, 0xCD, 0x27, 0x75])))
        pk = parse(recv_all(s, 0.6))
        got = [(c, len(p)) for c, _, p in pk]
        print("  回合%d -> %s" % (r, got))
        for c, _, p in pk:
            if c == 148:
                end_pk = p

    s.sendall(pack(149, 200))
    pk = parse(recv_all(s, 1.5))
    print("\n结算 149 收到 %d 个包：" % len(pk))
    for c, _, p in pk:
        print("  cmd=%-5d len=%-3d %s" % (c, len(p), p.hex()[:80]))
        if c == 150:
            exp, gap, coin, d, drops, tail = decode_150(p)
            print("    经验=%d 铜钱=%d 未知3B=%s 未知1B=%d 件数=%d 尾=%s"
                  % (exp, coin, gap.hex(), d, len(drops), tail.hex()))
            for iid, cnt, flag in drops:
                try:
                    nm = bag_proto.item(iid)["name"]
                except Exception:
                    nm = "??"
                print("      掉落 %d %s x%d 标记=%d" % (iid, nm, cnt, flag))
    s.close()


if __name__ == "__main__":
    main()
