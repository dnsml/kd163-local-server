"""本地服 VIP 半年卡自测：发 182 使用背包里的 VIP卡，核对回包序列与 28 包 idx22/23。"""
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grant

HOST, PORT = "127.0.0.1", 9000


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def recv_all(sock, wait=1.5):
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


def main():
    pos = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x1C
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.sendall(pack(182, 142, bytes([pos, 0x01])))
    res = parse(recv_all(s))
    s.close()
    print("发 182 格%02x 数量1，收到 %d 个包：" % (pos, len(res)))
    for cmd, seq, pl in res:
        extra = ""
        if cmd == 28:
            tk = grant.tokenize(pl)
            extra = ("  <- idx22(VIP等级)=%s idx23(VIP时长x0.1天)=%s"
                     % (tk[22]["value"], tk[23]["value"]))
        print("  S->C cmd=%-5d seq=%-4d len=%-3d %s%s"
              % (cmd, seq, len(pl), pl.hex()[:64], extra))


if __name__ == "__main__":
    main()
