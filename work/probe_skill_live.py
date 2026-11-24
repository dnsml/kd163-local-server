# -*- coding: utf-8 -*-
"""验证 148 是否回显 146 里的技能号（新学技能点了却放第一个技能的 bug）。

真服对照：
  146 0107cdeac5 -> 148 000002010107cdeac5...
  146 0107cd27d9 -> 148 010002010107cd27d9...
  146 040000     -> 148 000002010400000...
"""
import socket
import struct

HOST, PORT = "127.0.0.1", 9000
SEQ = [1200]


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def recv_all(sock, wait=1.0):
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


def go(s, cmd, payload, tag, wait=1.2):
    SEQ[0] += 1
    s.sendall(pack(cmd, SEQ[0], payload))
    pk = recv_all(s, wait)
    print("  %-18s -> %s" % (tag, [(c, len(p)) for c, _q, p in pk]))
    for c, _q, p in pk:
        if c == 148:
            print("     148[4:9]=%s  全文=%s" % (p[4:9].hex(), p.hex()))
    return pk


def main():
    s = socket.create_connection((HOST, PORT), timeout=5)
    print("连接 %s:%d" % (HOST, PORT))

    go(s, 145, bytes.fromhex("ce0001b1fd"), "145 开战 111101")

    for tag, hx in (("146 技能10201", "0107cd27d9"),
                    ("146 技能11001", "0107cd2af9"),
                    ("146 技能10101", "0107cd2775"),
                    ("146 防御", "040000")):
        go(s, 146, bytes.fromhex(hx), tag)
    s.close()
    print("完成")


if __name__ == "__main__":
    main()
