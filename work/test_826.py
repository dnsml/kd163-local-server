"""本地服 826（锻造台放装备）自测：连上 9000 明文通道，发 826 看回包序列。"""
import socket
import struct
import sys

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
    """帧头 8B = u16 total / u16 cmd / u32 seq，total = 6 + len(payload)，
    所以线上整帧长度 = total + 2。"""
    out = []
    i = 0
    while i + 8 <= len(buf):
        total, cmd, seq = struct.unpack(">HHI", buf[i:i + 8])
        end = i + total + 2
        if total < 6 or end > len(buf):
            break
        payload = buf[i + 8:end]
        out.append((cmd, seq, len(payload), payload.hex()))
        i = end
    return out


def main():
    payload826 = bytes.fromhex(sys.argv[1] if len(sys.argv) > 1 else "0a0a")
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.sendall(pack(826, 95, payload826))
    res = parse(recv_all(s))
    s.close()
    print("发 826 %s，收到 %d 个包：" % (payload826.hex(), len(res)))
    for cmd, seq, n, hx in res:
        print("  S->C cmd=%-5d seq=%-4d len=%-3d %s"
              % (cmd, seq, n, hx if n <= 80 else hx[:76] + "..."))


if __name__ == "__main__":
    main()
