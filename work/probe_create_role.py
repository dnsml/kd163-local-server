# -*- coding: utf-8 -*-
"""建角闭环直连探针（无角色 -> cmd=1 01a0 -> cmd=20 -> 160B 应答 -> 重登进游戏）

真服原包（2026-11-24 12:38 实测，logs/relay.log 19703~19706）：
  C->S 17 len=60   S->C cmd=1  len=2  hex=01a0
  C->S 20 len=73   S->C cmd=20 len=160 + 113(34) + 117(1)

用法：
  python work/probe_create_role.py [账号] [昵称] [1|2]
跑完会把该账号的存档内容打出来，便于核对是否 Lv1 / 空背包。
"""
import json
import os
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

HOST, PORT = "127.0.0.1", 9000
ACCT = sys.argv[1] if len(sys.argv) > 1 else "90000001"
NAME = sys.argv[2] if len(sys.argv) > 2 else "{{PERSON_9t4vbu6y}}"
SEX = int(sys.argv[3]) if len(sys.argv) > 3 else 1

TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"
ok_all = True


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sfield(s):
    b = s.encode("utf-8") if isinstance(s, str) else s
    assert len(b) <= 0x1F, "短字符串最长 31 字节"
    return bytes([0xA0 | len(b)]) + b


def recv_all(sock, wait=1.2):
    sock.settimeout(wait)
    buf, out = b"", []
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
            need = total + 2
            if len(buf) < need:
                break
            cmd, seq = struct.unpack(">HI", buf[2:8])
            out.append((cmd, seq, buf[8:need]))
            buf = buf[need:]
    return out


def check(tag, cond, extra=""):
    global ok_all
    print("  [%s] %s %s" % ("OK" if cond else "FAIL", tag, extra))
    if not cond:
        ok_all = False


def login(sock):
    """真服形状：01 cc9702 + sfield(账号) + da00 2b + 43B token"""
    pl = b"\x01\xcc\x97\x02" + sfield(ACCT) + b"\xda\x00\x2b" + TOKEN
    sock.sendall(pack(17, 2, pl))
    return recv_all(sock, 1.5)


def main():
    print("账号=%s 昵称=%r 性别=%d" % (ACCT, NAME, SEX))

    print("\n[1] 首次登录（该账号应该还没有角色）")
    s = socket.create_connection((HOST, PORT), timeout=5)
    pk = login(s)
    print("     收到: %s" % [(c, len(p), p.hex()) for c, _q, p in pk])
    nochar = [(c, p) for c, _q, p in pk if c == 1 and p == b"\x01\xa0"]
    check("无角色应答 cmd=1 01a0", bool(nochar))
    check("不应回 17（有角色才会回）", not any(c == 17 for c, _q, _p in pk))
    s.close()

    print("\n[2] 发 cmd=20 建角（真服同构 payload）")
    s = socket.create_connection((HOST, PORT), timeout=5)
    pk = login(s)
    if not any(c == 1 and p == b"\x01\xa0" for c, _q, p in pk):
        print("     注意：这次登录没有回 01a0，可能已经建过角，继续发 20 观察")
    body = (b"\x01\x01\x02" + sfield(ACCT) + b"\xda\x00\x2b" + TOKEN
            + sfield(NAME) + bytes([SEX]))
    print("     C->S 20 len=%d hex=%s" % (len(body), body.hex()))
    s.sendall(pack(20, 7, body))
    pk = recv_all(s, 2.0)
    for c, q, p in pk:
        print("     <- cmd=%-4d seq=%-3d len=%-3d %s" % (c, q, len(p), p.hex()[:96]))
    ack = [p for c, _q, p in pk if c == 20]
    check("有 cmd=20 应答", bool(ack))
    if ack:
        a = ack[0]
        # 长度 = 0x14 + sfield(账号) + sfield(昵称) + 性别 + 职业 + 134B 尾
        want = 1 + (1 + len(ACCT)) + (1 + len(NAME.encode("utf-8"))) + 2 + 134
        check("应答长度按真服公式", len(a) == want, "实际 %d 期望 %d" % (len(a), want))
        check("0x14 头", a[0] == 0x14, "0x%02x" % a[0])
        n = a[1] & 0x1F
        check("账号字段", a[2:2 + n].decode("ascii", "replace") == ACCT,
              a[2:2 + n].decode("ascii", "replace"))
        n2 = a[2 + n] & 0x1F
        check("昵称字段", a[3 + n:3 + n + n2].decode("utf-8", "replace") == NAME)
    check("推送 113", any(c == 113 for c, _q, _p in pk))
    check("推送 117", any(c == 117 for c, _q, _p in pk))
    s.close()

    print("\n[3] 存档核对")
    p = os.path.join(ROOT, "player_state_%s.json" % ACCT)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        print("     %s" % p)
        for k in ("name", "sex", "has_char", "level", "class", "exp", "coin"):
            print("       %-9s = %r" % (k, d.get(k)))
        print("       bag       = %r" % (d.get("bag"),))
        check("已落盘", True)
        check("昵称正确", d.get("name") == NAME, repr(d.get("name")))
        check("性别正确", d.get("sex") == SEX, repr(d.get("sex")))
        check("等级 Lv1", d.get("level") == 1, repr(d.get("level")))
        check("未转职 cls6", d.get("class") == 6, repr(d.get("class")))
        check("背包为空", not d.get("bag"), repr(d.get("bag")))
        check("战力为 0/未继承主号", d.get("exp", 0) == 0, repr(d.get("exp")))
    else:
        check("存档文件存在", False, p)

    print("\n[4] 重新登录（这次应该回 17，不再是 01a0）")
    s = socket.create_connection((HOST, PORT), timeout=5)
    pk = login(s)
    cmds = [c for c, _q, _p in pk]
    print("     收到: %s" % [(c, len(p)) for c, _q, p in pk][:12])
    check("回了 17 登录应答", 17 in cmds)
    check("不再回 01a0", not any(c == 1 and p == b"\x01\xa0" for c, _q, p in pk))
    s.close()

    print("\n结论：%s" % ("全部通过" if ok_all else "有失败项，见上"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
