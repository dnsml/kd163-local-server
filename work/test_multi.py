"""双开验证：两个账号同时在线，状态互不污染。

做法：两个 socket 都登录，然后用 GM 通道改数值（GM 会 push_all 28/29/30/178），
各自收到的 28/178 里读到的必须是自己的数值。

  1. GM「元宝 +1000」默认作用最新连接 = B → 只有 B 的 28 变；
  2. GM「@主号 元宝 +500」→ 只有 A 的 28 变；
  3. GM「@主号 物品 30999 1」→ 只有 A 的 178 里有这件；
  4. 两个存档文件互不影响。
"""
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grant
import bag_proto

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST, PORT = "127.0.0.1", 9000
GM = os.path.join(HERE, "logs", "gm.cmd")
PRIMARY = "1784366"
SECOND = sys.argv[1] if len(sys.argv) > 1 else "9990001"
F_PRIMARY = os.path.join(HERE, "player_state.json")
F_SECOND = os.path.join(HERE, "player_state_%s.json" % SECOND)


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


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


def drain(s, wait=1.0):
    s.settimeout(wait)
    buf = b""
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            buf += d
    except socket.timeout:
        pass
    return parse(buf)


def login(acct, wait=2.0):
    s = socket.create_connection((HOST, PORT), timeout=5)
    body = b"\x01\xcc\x97\x02\xa7" + acct.encode() + b"\xda\x00\x2b" + b"\x00" * 43
    s.sendall(pack(17, 2, body))     # 回放里的登录请求就是 seq=2
    return s, drain(s, wait)


def yb_of(pkts):
    """最后一份 28 里的元宝。"""
    v = None
    for cmd, seq, pl in pkts:
        if cmd == 28:
            v = grant.tokenize(pl)[21]["value"]
    return v


def bag_count(pkts):
    """178 包里的件数（[TAG][n]...，所以取第 2 字节）。"""
    n = None
    for cmd, seq, pl in pkts:
        if cmd == 178 and len(pl) > 1:
            n = pl[1]
    return n


def bag_ids(pkts):
    """178 包里的物品 id 集合。"""
    ids = set()
    for cmd, seq, pl in pkts:
        if cmd == 178 and len(pl) > 1:
            try:
                ids |= {e["id"] for e in bag_proto.parse_bag(pl)}
            except Exception:
                pass
    return ids


def gm(line, wait=2.0):
    with open(GM, "w", encoding="utf-8") as f:
        f.write(line + "\n")
    time.sleep(wait)


def main():
    a, pa = login(PRIMARY)
    b, pb = login(SECOND)
    print("A 登录回包 %d 个，B 登录回包 %d 个" % (len(pa), len(pb)))
    drain(a, 0.2)
    drain(b, 0.2)

    ok = True

    gm("元宝 +1000")
    ya, yb = yb_of(drain(a)), yb_of(drain(b))
    print("[1] GM 元宝+1000（默认 B）：A 元宝=%s  B 元宝=%s" % (ya, yb))

    gm("@%s 元宝 +500" % PRIMARY)
    ya2, yb2 = yb_of(drain(a)), yb_of(drain(b))
    print("[2] GM @主号 元宝+500：A 元宝=%s  B 元宝=%s" % (ya2, yb2))
    if ya is not None and ya2 is not None:
        ok &= (ya2 == ya + 500)
    if yb is not None and yb2 is not None:
        ok &= (yb2 == yb)

    gm("@%s 物品 30999 1" % PRIMARY)
    ka, kb = drain(a), drain(b)
    ca, cb = bag_count(ka), bag_count(kb)
    ia, ib = bag_ids(ka), bag_ids(kb)
    print("[3] GM @主号 物品30999：A 背包件数=%s 含20369=%s | B 件数=%s 含20369=%s"
          % (ca, 30999 in ia, cb, 20369 in ib))
    ok &= (30999 in ia) and (30999 not in ib)

    print("[4] 存档：")
    for f in (F_PRIMARY, F_SECOND):
        try:
            print("    %-30s %d 字节" % (os.path.basename(f), os.path.getsize(f)))
        except OSError as e:
            print("    %s 读不到：%s" % (f, e))
            ok = False

    print("\n结论：%s" % ("通过（两个号状态互不影响）" if ok else "不通过，见上面的数值"))
    a.close()
    b.close()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
