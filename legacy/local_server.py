#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd163 本地服务端原型（L1 验证）
- 监听 9000，接管客户端的游戏协议连接
- cmd 0x11(17) 返回抓包得到的真实登录响应
- 其他奇数 cmd 一律回空响应（cmd+1, seq 相同）
- 打印全部收发包，便于继续补实现
"""
import socket
import struct
import threading
import time
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(HERE, "capture")
TEMPLATES = os.path.join(HERE, "templates.json")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9000

TPL = {}
if os.path.exists(TEMPLATES):
    raw = json.load(open(TEMPLATES, encoding="utf-8"))
    TPL = {k: bytes.fromhex(v) for k, v in raw.items() if not k.startswith("push_")}

lock = threading.Lock()


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    with lock:
        print(line, flush=True)


def load_login_resp():
    """从抓包中提取 cmd=17 且 payload 大的那个包（服务端登录响应）"""
    for fn in ("c033_game.bin",):
        p = os.path.join(CAP, fn)
        if not os.path.exists(p):
            continue
        d = open(p, "rb").read()
        pos = 0
        while pos + 8 <= len(d):
            total, cmd, seq = struct.unpack_from(">HHI", d, pos)
            if total < 6 or pos + 2 + total > len(d):
                break
            payload = d[pos + 8:pos + 2 + total]
            if cmd == 17 and len(payload) > 100:
                return payload, seq
            pos += 2 + total
    return None, None


LOGIN_RESP, LOGIN_SEQ = load_login_resp()


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


REAL_HOST = "124.223.56.189"


def relay_tls(client, cid):
    """TLS 连接（/auth 登录接口）透传给真服务器，保证登录能过"""
    try:
        client.settimeout(120)
        first = client.recv(1, socket.MSG_PEEK)
        if not first or first[0] != 0x16:
            return False
        remote = socket.create_connection((REAL_HOST, PORT), timeout=15)
        remote.settimeout(120)
        log("c%03d TLS -> 透传给真服 /auth" % cid)

        def pipe(a, b, name):
            try:
                while True:
                    d = a.recv(65536)
                    if not d:
                        break
                    b.sendall(d)
            except Exception:
                pass
            finally:
                try:
                    b.shutdown(socket.SHUT_WR)
                except Exception:
                    pass

        t = threading.Thread(target=pipe, args=(client, remote, "up"), daemon=True)
        t.start()
        pipe(remote, client, "down")
        t.join(timeout=2)
        client.close(); remote.close()
        log("c%03d TLS 结束" % cid)
        return True
    except Exception as e:
        log("c%03d TLS 透传失败 %s" % (cid, e))
        try:
            client.close()
        except Exception:
            pass
        return True


def handle(conn, addr, cid):
    log("c%03d 连接 %s:%d" % (cid, addr[0], addr[1]))
    if relay_tls(conn, cid):
        return
    buf = b""
    conn.settimeout(180)
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                log("c%03d 对端关闭" % cid)
                break
            buf += data
            while len(buf) >= 8:
                total, cmd, seq = struct.unpack_from(">HHI", buf[:8])
                if total < 6 or total > 60000:
                    log("c%03d 非法长度 %d，断开" % (cid, total))
                    return
                need = 2 + total
                if len(buf) < need:
                    break
                payload = buf[8:need]
                buf = buf[need:]
                log("c%03d <- cmd=%-5d seq=%-4d payload=%d %s"
                    % (cid, cmd, seq, len(payload), payload[:40].hex()))

                resp = None
                if cmd == 17:
                    if LOGIN_RESP:
                        resp = pack(17, seq, LOGIN_RESP)
                        log("c%03d -> 登录响应 %d 字节" % (cid, len(LOGIN_RESP)))
                    else:
                        log("c%03d !! 没有登录响应模板" % cid)
                        resp = pack(18, seq, b"")
                else:
                    # 优先用请求+1 的模板（奇数请求 -> 偶数响应）
                    cand = None
                    for k in (str(cmd + 1), str(cmd)):
                        if k in TPL:
                            cand = TPL[k]
                            resp_cmd = int(k)
                            break
                    if cand is not None:
                        resp = pack(resp_cmd, seq, cand)
                        log("c%03d -> cmd=%-5d seq=%-4d len=%-4d (模板 %d)"
                            % (cid, resp_cmd, seq, len(cand), resp_cmd))
                    elif cmd & 1:
                        resp = pack(cmd + 1, seq, b"")
                        log("c%03d -> cmd=%-5d seq=%-4d 空回包" % (cid, cmd + 1, seq))
                    else:
                        log("c%03d -- cmd=%-5d 无模板，不回" % (cid, cmd))
                if resp:
                    conn.sendall(resp)
    except socket.timeout:
        log("c%03d 超时" % cid)
    except Exception as e:
        log("c%03d 异常 %s %s" % (cid, type(e).__name__, e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    if not LOGIN_RESP:
        log("警告：未找到登录响应模板")
    else:
        log("登录响应模板已加载：%d 字节（seq=%s）" % (len(LOGIN_RESP), LOGIN_SEQ))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(16)
    log("本地服务端监听 :%d" % PORT)
    cid = 0
    while True:
        c, a = srv.accept()
        cid += 1
        threading.Thread(target=handle, args=(c, a, cid), daemon=True).start()


if __name__ == "__main__":
    main()
