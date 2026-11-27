#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""github.com 专用 DNS 修正隧道（本地 CONNECT 代理）。

背景
----
2026-11-27 实测：`github.com:443` 域名直连 TCP 超时，但 GitHub 的真实 IP
（140.82.112/113/114.4、20.205.243.166、20.27.177.113）443 全部 OPEN，
且 `curl --resolve github.com:443:140.82.113.4` 访问 git-receive-pack 返回 200。
即故障在域名这一层（解析/SNI 前的连接），不在 TLS/鉴权。

用法
----
    python work/gh_tunnel.py [port]          # 默认 7899
    git -c http.proxy=http://127.0.0.1:7899 -c http.sslBackend=openssl push

它只做一件事：收到 `CONNECT github.com:443` 时，按 IP 池依次尝试
建立上游 TCP 连接；其它域名交给系统解析，行为等同普通代理。
TLS 由 git 自己握手，SNI 仍是 github.com，证书校验不受影响。
"""
import socket
import sys
import threading

DEFAULT_PORT = 7899
# 依次尝试，成功过的 IP 会被记住，后续优先使用
IP_POOL = [
    "140.82.113.4",
    "20.205.243.166",
    "140.82.112.4",
    "20.27.177.113",
    "140.82.114.4",
]
# 这些域名强制走 IP 池
OVERRIDE_HOSTS = ("github.com", "www.github.com", "uploads.github.com")

_good_ip = None
_lock = threading.Lock()


def _log(*a):
    print("[gh_tunnel]", *a, flush=True)


def _pick_ip(host, port):
    global _good_ip
    with _lock:
        cands = []
        if _good_ip:
            cands.append(_good_ip)
        cands += [ip for ip in IP_POOL if ip != _good_ip]
    if host not in OVERRIDE_HOSTS:
        # 非目标域名：交给系统解析
        try:
            return socket.create_connection((host, port), timeout=8)
        except OSError as e:
            _log("直连 %s:%s 失败：%s" % (host, port, e))
            return None
    for ip in cands:
        try:
            s = socket.create_connection((ip, port), timeout=6)
            with _lock:
                _good_ip = ip
            _log("上游 %s:%s -> %s" % (host, port, ip))
            return s
        except OSError:
            continue
    _log("IP 池全部不可用：%s:%s" % (host, port))
    return None


def _pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for x in (a, b):
            try:
                x.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle(conn):
    up = None
    try:
        conn.settimeout(10)
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = conn.recv(4096)
            if not d:
                return
            buf += d
            if len(buf) > 65536:
                return
        head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        line = head.split("\r\n", 1)[0]
        parts = line.split()
        if len(parts) < 2:
            return
        if parts[0].upper() != "CONNECT":
            conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        host, _, p = parts[1].rpartition(":")
        port = int(p or 443)
        up = _pick_ip(host, port)
        if not up:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        conn.settimeout(None)
        up.settimeout(None)
        t = threading.Thread(target=_pipe, args=(up, conn), daemon=True)
        t.start()
        _pipe(conn, up)
        t.join(timeout=30)
    except Exception as e:  # noqa: BLE001
        _log("连接异常：%r" % (e,))
    finally:
        for x in (conn, up):
            if x:
                try:
                    x.close()
                except OSError:
                    pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(50)
    _log("监听 127.0.0.1:%d，IP 池 %d 个" % (port, len(IP_POOL)))
    while True:
        try:
            c, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=_handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
