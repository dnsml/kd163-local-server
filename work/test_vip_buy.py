"""本地服 VIP 卡链路自测：371 购买 + 1313 角色页签，核对回包。"""
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


def run(label, cmd, seq, payload):
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.sendall(pack(cmd, seq, payload))
    res = parse(recv_all(s))
    s.close()
    print("== %s：发 cmd=%d payload=%s，收到 %d 包" % (label, cmd, payload.hex() or "-", len(res)))
    for c, sq, pl in res:
        extra = ""
        if c == 28:
            tk = grant.tokenize(pl)
            extra = ("  <- 元宝=%d VIP=%s/%s" % (tk[21]["value"], tk[22]["value"], tk[23]["value"]))
        elif c == 178:
            extra = "  <- 背包 %d 件" % (pl[1] if len(pl) > 1 else -1)
        print("   S->C cmd=%-5d seq=%-4d len=%-3d %s%s" % (c, sq, len(pl), pl.hex()[:48], extra))
    print()


if __name__ == "__main__":
    run("371 购买 VIP半年卡(area1)", 371, 141, bytes.fromhex("cd4ea20101"))
    run("1313 角色页签02", 1313, 107, bytes([0x02]))
    run("1313 角色页签0a", 1313, 109, bytes([0x0A]))
