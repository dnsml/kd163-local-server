# -*- coding: utf-8 -*-
"""直连本地服 :9000 走一遍宠物协议（明文），确认 962/947/1412 真的发出来了。

不依赖客户端：连上后等回放结束，再按真服顺序发 961/946/1411/1413。
"""
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 9000


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def main():
    s = socket.create_connection((HOST, PORT), timeout=8)
    s.settimeout(1.0)
    buf = b""
    # 先收 2.5 秒回放
    t0 = time.time()
    while time.time() - t0 < 2.5:
        try:
            d = s.recv(65536)
            if not d:
                break
            buf += d
        except socket.timeout:
            pass

    got = []
    for seq, cmd, pl in ((5001, 961, b""), (5002, 946, b"\x01"),
                         (5003, 963, b"\x01"), (5004, 963, b"\x01"),
                         (5005, 965, b"\xce" + (804201).to_bytes(4, "big")),
                         (5006, 1411, b"\x01"), (5007, 1413, b"\x00")):
        s.sendall(pack(cmd, seq, pl))
        time.sleep(0.4)
        try:
            d = s.recv(65536)
            buf += d
        except socket.timeout:
            pass

    # 拆帧：帧 = [u16 长度(=2+4+payload)][u16 cmd][u32 seq][payload]
    i = 0
    while i + 8 <= len(buf):
        total, cmd, seq = struct.unpack(">HHI", buf[i:i + 8])
        end = i + 2 + total
        if total < 6 or end > len(buf):
            break
        got.append((cmd, seq, buf[i + 8:end]))
        i = end

    # cmd -> 期望长度（None = 只验到达，不卡长度）
    want = {962: 22, 964: None, 965: 0, 947: None, 1412: 6, 1413: 0,
            1410: None, 178: None}
    fail = 0
    for cmd, ln in want.items():
        hit = [g for g in got if g[0] == cmd]
        if not hit:
            print("  FAIL cmd=%d 没收到" % cmd)
            fail += 1
            continue
        real = len(hit[-1][2])
        bad = (ln is not None and real != ln)
        print("  %s cmd=%-5d len=%-4d hex=%s"
              % ("OK  " if not bad else "FAIL", cmd, real,
                 hit[-1][2][:48].hex()))
        if bad:
            print("       期望 len=%d" % ln)
            fail += 1
    extra = sorted(set(g[0] for g in got) - set(want))
    print("  收到其它 cmd: %s" % extra)
    s.close()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
