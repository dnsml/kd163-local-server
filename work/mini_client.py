# -*- coding: utf-8 -*-
"""最小协议客户端：登录一个账号，然后发一条指定 cmd，打印服务端回应。

用途：验证「服务端是不是真正的权威」——比如 371 买一个余额根本不够的东西，
      看它是回 `cmd=1 对应货币不足`（拒绝），还是回 `371 01` + 178（发货）。

用法：
  # 本地服，免密码账号，买 20019 初级攻击药（area9=铜钱，单价 28000，余额 0）
  python work/mini_client.py --acct {{ACCOUNT_9p2qk7w1}} --cmd 371 --hex cd4e330901

  # 线上服（需要密码，会先走 TLS /auth 拿票据再发明文帧）
  python work/mini_client.py --host 124.223.56.189 --acct 1784366 --pass 你的密码 \
      --cmd 371 --hex cd4e330901

  # 只登录看回包
  python work/mini_client.py --acct {{ACCOUNT_9p2qk7w1}}
"""
import argparse
import os
import socket
import ssl
import struct
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def pack(cmd, seq, payload=b""):
    """帧 = >HHI(len 含 6B 头) + cmd + seq + payload"""
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def str_field(s):
    """短串编码：0xA0|len + utf-8（len<32）"""
    b = s.encode("utf-8")
    assert len(b) < 32, "账号串过长"
    return bytes([0xA0 | len(b)]) + b


def login_payload(acct):
    """17 登录包：01 cc9702 <账号串> da002b <43B token>"""
    return b"\x01\xcc\x97\x02" + str_field(acct) + b"\xda\x00\x2b" + os.urandom(43)


def recv_frame(sock):
    """头是 8 字节（len:2 + cmd:2 + seq:4），len 含 cmd+seq 的 6 字节。"""
    head = b""
    while len(head) < 8:
        b = sock.recv(8 - len(head))
        if not b:
            return None
        head += b
    ln, cmd, seq = struct.unpack(">HHI", head)
    body = b""
    while len(body) < ln - 6:
        b = sock.recv(ln - 6 - len(body))
        if not b:
            break
        body += b
    return cmd, seq, body


def auth(host, port, acct, password, timeout=6):
    """走 TLS 打 /auth，返回 (状态行, 响应体)。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, port), timeout)
    s = ctx.wrap_socket(raw)
    body = ('{"username":"%s","password":"%s"}' % (acct, password)).encode()
    req = (b"POST /auth HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\n"
           b"Content-Length: %d\r\nConnection: close\r\n\r\n" % (host.encode(), len(body))
           ) + body
    s.sendall(req)
    data = b""
    while True:
        try:
            b = s.recv(4096)
        except Exception:
            break
        if not b:
            break
        data += b
    s.close()
    head = data.split(b"\r\n\r\n", 1)
    return head[0].split(b"\r\n")[0].decode("latin1"), head[1] if len(head) > 1 else b""


def err_text(payload):
    """cmd=1 的报错文本：`04` + 短串(0xA0|len) 或 `04 da u16 len` + utf-8。"""
    b = payload[1:] if payload[:1] == b"\x04" else payload
    if b[:1] and 0xDA == b[0] and len(b) >= 3:
        n = struct.unpack(">H", b[1:3])[0]
        s = b[3:3 + n]
    elif b[:1] and 0xA0 <= b[0] <= 0xBF:
        n = b[0] & 0x1F
        s = b[1:1 + n]
    else:
        s = b
    try:
        return s.decode("utf-8")
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--acct", required=True)
    ap.add_argument("--pass", dest="password", default="")
    ap.add_argument("--cmd", type=int, default=0)
    ap.add_argument("--hex", default="")
    ap.add_argument("--wait", type=float, default=2.5, help="登录后等回包的秒数")
    a = ap.parse_args()

    if a.password:
        st, body = auth(a.host, a.port, a.acct, a.password)
        print("/auth -> %s %s" % (st, body[:80].decode("utf-8", "ignore")))

    s = socket.create_connection((a.host, a.port), 8)
    s.settimeout(6)
    s.sendall(pack(17, 1, login_payload(a.acct)))

    got = []
    t0 = time.time()
    while time.time() - t0 < a.wait:
        try:
            f = recv_frame(s)
        except socket.timeout:
            continue
        if not f:
            print("连接被服务端关闭")
            break
        got.append(f)
        if f[0] == 1:
            print("  cmd=1 len=%d %r" % (len(f[2]), err_text(f[2])))
    print("登录回包 %d 条：%s" % (len(got), ", ".join("cmd=%d/%dB" % (c, len(p)) for c, _, p in got)))
    if not got:
        print("没有回包，登录可能被拒（账号未放行 / 需要 /auth 票据）")
        return

    if a.cmd:
        payload = bytes.fromhex(a.hex) if a.hex else b""
        print("发送 cmd=%d payload=%s" % (a.cmd, payload.hex()))
        s.sendall(pack(a.cmd, 100, payload))
        t0 = time.time()
        while time.time() - t0 < 4:
            try:
                f = recv_frame(s)
            except socket.timeout:
                continue
            if not f:
                print("连接被关闭")
                break
            cmd, seq, body = f
            extra = ""
            if cmd == 1:
                extra = " 文本=%r" % err_text(body)
            print("  <- cmd=%d seq=%d len=%d hex=%s%s"
                  % (cmd, seq, len(body), body[:40].hex(), extra))
    s.close()


if __name__ == "__main__":
    main()
