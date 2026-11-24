#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd163 本地服务端 v2（L1/L2 验证）
核心变化：不再「按请求查模板」，而是**按真服原始发送顺序整段回放**
- 从 capture/c033_game.bin 读出真服 S->C 的完整序列（含 seq=0 的主动推送）
- 客户端每发一个请求（seq=S），就把回放指针推进到该 seq 的那条响应（含中间的推送）
- 序列里找不到的请求，才退回 templates.json / 空回包
这样登录后紧跟的 cmd=113(场景)、117、178 等推送不会被漏掉。

TLS 流量（/auth）继续透传真服，保证账号密码校验能过。
用法：python local_server2.py [port]
"""
import os
import sys
import json
import time
import socket
import struct
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(HERE, "replay_sc.json")
TEMPLATES = os.path.join(HERE, "templates.json")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
REAL_HOST, REAL_PORT = "124.223.56.189", 9000

lock = threading.Lock()


def log(msg):
    with lock:
        print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def load_seq():
    """真服 S->C 单向回放序列 [(cmd, seq, payload)]，来自 c033_game.log 的方向标记"""
    import json as _j
    if not os.path.exists(CAP):
        return []
    raw = _j.load(open(CAP, encoding="utf-8"))
    return [(c, sq, bytes.fromhex(h)) for c, sq, h in raw]


SEQ = load_seq()

TPL = {}
if os.path.exists(TEMPLATES):
    raw = json.load(open(TEMPLATES, encoding="utf-8"))
    TPL = {k: bytes.fromhex(v) for k, v in raw.items() if not k.startswith("push_")}


def relay_tls(client, cid):
    """首字节 0x16 = TLS，透传给真服 /auth"""
    try:
        client.settimeout(120)
        first = client.recv(1, socket.MSG_PEEK)
        if not first or first[0] != 0x16:
            return False
        remote = socket.create_connection((REAL_HOST, REAL_PORT), timeout=15)
        remote.settimeout(120)
        log("c%03d TLS -> 透传真服 /auth" % cid)

        def pipe(a, b):
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

        t = threading.Thread(target=pipe, args=(client, remote), daemon=True)
        t.start()
        pipe(remote, client)
        t.join(timeout=2)
        client.close(); remote.close()
        log("c%03d TLS 结束" % cid)
        return True
    except Exception as e:
        log("c%03d TLS 失败 %s" % (cid, e))
        try:
            client.close()
        except Exception:
            pass
        return True


def handle(conn, addr, cid):
    log("c%03d 连接 %s:%d" % (cid, addr[0], addr[1]))
    if relay_tls(conn, cid):
        return

    ptr = 0              # 回放指针
    buf = b""
    conn.settimeout(180)
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                log("c%03d 对端关闭（回放指针 %d/%d）" % (cid, ptr, len(SEQ)))
                break
            buf += data
            while len(buf) >= 8:
                total, cmd, seq = struct.unpack_from(">HHI", buf[:8])
                if total < 6 or total > 60000:
                    log("c%03d 非法长度 %d" % (cid, total))
                    return
                need = 2 + total
                if len(buf) < need:
                    break
                payload = buf[8:need]
                buf = buf[need:]
                log("c%03d <- cmd=%-6d seq=%-4d len=%d" % (cid, cmd, seq, len(payload)))

                # 1) 在真服序列里向前找「该 seq 的响应」，把中间包一并发出（含推送）
                j = None
                for k in range(ptr, len(SEQ)):
                    if SEQ[k][1] == seq:
                        j = k
                        break
                if j is not None:
                    for c2, s2, p2 in SEQ[ptr:j + 1]:
                        conn.sendall(pack(c2, s2, p2))
                        log("c%03d -> cmd=%-6d seq=%-4d len=%d%s"
                            % (cid, c2, s2, len(p2), "  [推送]" if s2 == 0 else ""))
                    ptr = j + 1
                    continue

                # 2) 序列里没有 → 退回模板
                resp = None
                for k in (str(cmd + 1), str(cmd)):
                    if k in TPL:
                        resp = pack(int(k), seq, TPL[k])
                        log("c%03d -> cmd=%-6d seq=%-4d (模板 %s)" % (cid, int(k), seq, k))
                        break
                if resp is None and cmd & 1:
                    resp = pack(cmd + 1, seq, b"")
                    log("c%03d -> cmd=%-6d seq=%-4d 空回包" % (cid, cmd + 1, seq))
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
    log("真服回放序列：%d 个包（其中推送 %d 个）"
        % (len(SEQ), sum(1 for _, s, _ in SEQ if s == 0)))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(16)
    log("本地服务端 v2 监听 :%d" % PORT)
    cid = 0
    while True:
        c, a = srv.accept()
        cid += 1
        threading.Thread(target=handle, args=(c, a, cid), daemon=True).start()


if __name__ == "__main__":
    main()
