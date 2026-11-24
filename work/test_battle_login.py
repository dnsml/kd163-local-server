"""登录后打任务小怪，验证 145/151/146/148/149/150 整条链在按连接状态下的表现。

用法： python work/test_battle_login.py [怪物ID] [账号]
默认打 111101 仙界守卫（新手任务那只），账号 1784366。
"""
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bag_proto
import grant

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


def dec150(pl):
    """150: [exp][3B][coin][1B][n]{[id][num][flag]}[4B]"""
    i, vals = 0, {}
    def rv():
        nonlocal i
        b = pl[i]
        if b == 0xCE:
            v = int.from_bytes(pl[i + 1:i + 5], "big"); i += 5; return v
        if b == 0xCD:
            v = int.from_bytes(pl[i + 1:i + 3], "big"); i += 3; return v
        if b == 0xCC:
            v = pl[i + 1]; i += 2; return v
        i += 1
        return b
    vals["exp"] = rv()
    i += 3
    vals["coin"] = rv()
    i += 1
    n = pl[i]; i += 1
    drops = []
    for _ in range(n):
        iid = rv()
        num = pl[i]; i += 1
        i += 1
        drops.append((iid, num))
    vals["drops"] = drops
    return vals


def main():
    mob = int(sys.argv[1]) if len(sys.argv) > 1 else 111101
    acct = sys.argv[2] if len(sys.argv) > 2 else "1784366"

    s = socket.create_connection((HOST, PORT), timeout=5)
    body = b"\x01\xcc\x97\x02\xa7" + acct.encode() + b"\xda\x00\x2b" + b"\x00" * 43
    s.sendall(pack(17, 2, body))
    res = parse(recv_all(s, 1.5))
    lv = cls = None
    for cmd, seq, pl in res:
        if cmd == 17 and len(pl) > 18:
            lv, cls = pl[18], pl[16]
    print("登录账号 %s -> Lv%s cls%s（回包 %d 个）" % (acct, lv, cls, len(res)))

    m = b"\xce" + mob.to_bytes(4, "big")
    s.sendall(pack(145, 90, m))
    res = parse(recv_all(s, 1.0))
    for cmd, seq, pl in res:
        if cmd == 151:
            print("151 开战包 len=%d hex=%s" % (len(pl), pl.hex()))
        else:
            print("收到 cmd=%d seq=%d len=%d" % (cmd, seq, len(pl)))

    for r in range(1, 7):
        s.sendall(pack(146, 90 + r, bytes.fromhex("0107cdeac5")))
        res = parse(recv_all(s, 0.8))
        end = False
        for cmd, seq, pl in res:
            if cmd == 148:
                end = "结束" in ""
                print("148 回合%d len=%d %s" % (r, len(pl), pl.hex()[:40]))
            elif cmd == 1:
                print("提示: %s" % pl[4:].decode("utf-8", "ignore"))
        if not res:
            print("回合%d 无回包" % r)
            break

    s.sendall(pack(149, 200, b""))
    res = parse(recv_all(s, 1.5))
    for cmd, seq, pl in res:
        if cmd == 150:
            d = dec150(pl)
            names = []
            for iid, num in d["drops"]:
                try:
                    names.append("%s x%d" % (bag_proto.item(iid)["name"], num))
                except Exception:
                    names.append("%d x%d" % (iid, num))
            print("150 结算 len=%d 经验=%d 铜钱=%d 掉落=%s"
                  % (len(pl), d["exp"], d["coin"], "、".join(names) or "无"))
        else:
            print("收到 cmd=%d len=%d" % (cmd, len(pl)))
    s.close()


if __name__ == "__main__":
    main()
