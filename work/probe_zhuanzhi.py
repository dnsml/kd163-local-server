# -*- coding: utf-8 -*-
"""模拟客户端跑一遍「转职任务」协议流程，验证服务端回包是否完整。

帧格式：[u16 total_len][u16 cmd][u32 seq][payload]，total_len = 6 + len(payload)
"""
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 9000


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def recv_all(sock, wait=0.8):
    """连续收包，直到超时。返回 [(cmd, seq, payload)]。"""
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
            need = total + 2          # total_len 不含自身 2 字节
            if len(buf) < need:
                break
            cmd, seq = struct.unpack(">HI", buf[2:8])
            payload = buf[8:need]
            out.append((cmd, seq, payload))
            buf = buf[need:]
    return out


def show(tag, pkts):
    print("  -- %s --" % tag)
    if not pkts:
        print("     （无响应）")
        return
    for cmd, seq, p in pkts:
        print("     cmd=%-6d seq=%-4d len=%-4d %s"
              % (cmd, seq, len(p), p[:16].hex()))


def main():
    qid = int(sys.argv[1]) if len(sys.argv) > 1 else 400403
    qb = b"\xce" + qid.to_bytes(4, "big")
    s = socket.create_connection((HOST, PORT), timeout=5)
    print("连接 %s:%d  任务 %d" % (HOST, PORT, qid))

    # 1) 接任务 211
    s.sendall(pack(211, 101, qb))
    show("211 接任务", recv_all(s))
    time.sleep(0.3)

    # 2) 转职 35（payload=职业ID 1御剑）
    s.sendall(pack(35, 102, b"\x01"))
    show("35 转职", recv_all(s, 1.2))
    time.sleep(0.3)

    # 3) 交任务 214
    s.sendall(pack(214, 103, qb))
    show("214 交任务", recv_all(s, 1.2))
    time.sleep(0.3)

    # 4) 209 拉任务状态
    s.sendall(pack(209, 104, b""))
    show("209 拉状态", recv_all(s))

    s.close()
    print("完成")


if __name__ == "__main__":
    main()
