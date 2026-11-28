#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd163 本地 HTTP 服务端（端口 80）
模拟真服的静态资源端点，供「零改客户端 + DNAT」路线使用。

端点：
  GET /server-list.xml   服务器列表（把 ip 改写成本机）
  GET /version.txt       强制更新开关 → 永不强制
  GET /assets.txt        资源清单（md5 用本地包的真实 md5，url 指向本机）
  GET /meta.xml          资源 md5 清单（原样镜像）
  GET /mhsj_res.zip      完整资源包（114.6MB，流式发送）
  GET /assets/*          单文件资源（mirror/assets 下已镜像的部分）
  其它                   404，但会记录（方便发现漏了哪个端点）

用法：python local_http.py [port] [host_ip]
"""
import os
import sys
import time
import hashlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
MIRROR = os.path.join(HERE, "mirror")
# 资源必须与「抓包时的服务端数据」同版本，否则协议里引用的新资源在包里不存在，
# nativeGetFileOffsetAndSize 查不到偏移直接 abort。
# APK 内置包(8c6ee66e)比服务端更新包(5efc42ed)旧，用它会在进主城时崩。
RESPKG = os.path.join(HERE, "res_update", "mhsj_res.zip")
RESPKG_MD5 = "5efc42ed36978e860f135a96bd93c85a"   # 真服当前版本，与抓包数据配套
# meta.xml 两份内容一致（md5 2369522e…），仓库里带的是 mirror/meta.xml；
# srvres/ 是解包产物、不入库。所以这里按顺序找，clone 下来也能起。
META_XML_CANDIDATES = [
    os.path.join(HERE, "srvres", "meta.xml"),
    os.path.join(MIRROR, "meta.xml"),
]
META_XML = next((p for p in META_XML_CANDIDATES if os.path.exists(p)),
                META_XML_CANDIDATES[0])


def norm_path(p):
    """折叠连续斜杠。

    直连包的内置地址要把 13 字符 IP 塞进 14 字符的老 host 槽位，dex 字符串又
    只能等长替换（ART 会顺序校验 string_data_item，缩短必定失败），所以补的那
    个字符写成 `http://192.168.0.111//assets.txt` 这种形式，由服务端还原。
    """
    while "//" in p:
        p = p.replace("//", "/")
    return p

def req_path(raw):
    """把请求行里的目标还原成真正的路径。

    ★ 2026-11-28 15:25：开发版客户端请求行是 `GET //assets.txt HTTP/1.1`。
      `urlparse("//assets.txt")` 会按「协议相对 URL」解析成 netloc=assets.txt、path=""，
      于是 assets.txt / version.txt / server-list.xml 全部变 404，客户端拉不到资源就崩。
      这里只有带 scheme 的绝对 URL 才交给 urlparse，其余直接折叠 `//`。
    """
    if raw.startswith(("http://", "https://")):
        return norm_path(unquote(urlparse(raw).path or "/"))
    return norm_path(unquote(raw.split("?", 1)[0])) or "/"


PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 80
HOST_IP = sys.argv[2] if len(sys.argv) > 2 else "192.168.0.111"
REAL_IP = "124.223.56.189"
# 公网独立部署时置 1：把 assets.txt / meta.xml 里的真服 IP 换成 HOST_IP。
# 本地开发（客户端靠 DNAT 连 124.223.56.189）必须保持 0，否则客户端会认为
# 「资源地址的 host 和我连的服务器不一致」，卡在 0/6251 已更新 0KB。
REWRITE_ASSETS = os.environ.get("KD_ASSETS_REWRITE") == "1"
# 80 端口复用：客户端把 server-list 的 port 写成 80 后，游戏连接（TLS，首字节
# 0x16）从 80 进来，这里按首字节分流到本地游戏服。留空=关闭。
MUX_GAME = os.environ.get("KD_MUX_GAME") or ""

lock = threading.Lock()


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with lock:
        print(line, flush=True)


def read(name, default=b""):
    p = os.path.join(MIRROR, name)
    if os.path.exists(p):
        return open(p, "rb").read()
    return default


def rewrite_host(b):
    """把镜像里的真服 IP 换成本机 IP（DNAT 下其实无所谓，但便于直连调试）"""
    return b.replace(REAL_IP.encode(), HOST_IP.encode())


# 服务器列表里下发的游戏端口。公网复用 80 端口时设 KD_GAME_PORT=80。
GAME_PORT = os.environ.get("KD_GAME_PORT") or ""


def rewrite_server_list(b):
    b = rewrite_host(b)
    if GAME_PORT:
        b = b.replace(b'port="9000"', ('port="%s"' % GAME_PORT).encode())
    return b


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "nginx/1.18.0"

    def log_message(self, fmt, *args):
        pass  # 用统一 log

    def _send(self, code, body, ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_HEAD(self):
        """客户端更新前可能先探测大小；不实现会回 501 导致它放弃下载。"""
        path = req_path(self.path)
        log("HEAD %s" % path)
        if path in ("/mhsj_res.zip", "/mhsj_res_2.zip") and os.path.exists(RESPKG):
            size = os.path.getsize(RESPKG)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        # ★ 2026-11-28 15:17：客户端打进来但 path 解析成空串（日志 `GET   UA=…`），
        #   看不出是「绝对 URL 形式」还是「请求行本身缺目标」。先把原始请求行打出来，
        #   再兜底：绝对形式取 path，空的按 "/" 处理，至少别再落到 404 空分支。
        _raw = (getattr(self, "requestline", "") or "")[:160]
        # ★ 2026-11-28 15:25 抓到真凶：开发版补位后请求行是 `GET //assets.txt HTTP/1.1`。
        #   `urlparse("//assets.txt")` 会把它按「协议相对 URL」解析 —— netloc=assets.txt、
        #   path=""，于是所有双斜杠端点（assets.txt/version.txt/server-list.xml）全变 404。
        #   统一走 req_path()：只有真绝对 URL 才解析，其余按原路径折叠斜杠。
        path = req_path(self.path)
        log("GET %s  UA=%s" % (path, (self.headers.get("User-Agent") or "")[:40]))
        _host = self.headers.get("Host") or ""
        if _host:
            log("  .. Host=%s" % _host)
        # 路径与原始请求行不一致时留证据（双斜杠/绝对形式都在这里现形）
        if self.path not in (path, path + "/") and not self.path.startswith(("http://", "https://")):
            log("  .. 原始请求行 %r  (self.path=%r)" % (_raw, self.path))

        if path in ("/server-list.xml", "/server_list.xml"):
            body = rewrite_server_list(read("server-list.xml"))
            return self._send(200, body, "text/xml; charset=utf-8")

        if path == "/version.txt":
            body = b"version=0&url=-&force=0"
            return self._send(200, body, "text/plain")

        if path == "/assets.txt":
            # 必须用原版镜像的格式、原版 host（124.223.56.189）。
            # 客户端会校验 URL 里的 host 与它连接的服务器是否一致；
            # 换成 192.168.x.x 会导致它认为资源地址无效，卡在「0/6251 已更新 0KB」。
            body = read("assets.txt")
            if body:
                import re as _re
                body = _re.sub(rb"assets_md5=[0-9a-f]+", b"assets_md5=" + RESPKG_MD5.encode(), body)
                body = _re.sub(rb"assets_md5_2=[0-9a-f]+", b"assets_md5_2=" + RESPKG_MD5.encode(), body)
                if REWRITE_ASSETS:
                    body = rewrite_host(body)
            else:
                url = "http://%s:80/mhsj_res.zip" % REAL_IP
                body = ("assets_md5=%s&assets_md5_2=%s&assets_url=%s&assets_url_2=%s"
                        "&meta=http://%s:80/meta.xml&meta_2=http://%s:80/meta.xml"
                        % (RESPKG_MD5, RESPKG_MD5, url, url, REAL_IP, REAL_IP)).encode()
            return self._send(200, body, "text/plain")

        if path == "/meta.xml":
            if os.path.exists(META_XML):
                body = open(META_XML, "rb").read()
                if REWRITE_ASSETS:
                    body = rewrite_host(body)
                return self._send(200, body, "text/xml; charset=utf-8")
            return self._send(200, rewrite_host(read("meta.xml")), "text/xml; charset=utf-8")

        if path in ("/mhsj_res.zip", "/mhsj_res_2.zip"):
            if not os.path.exists(RESPKG):
                log("  !! 资源包不存在：%s" % RESPKG)
                return self._send(404, b"not found")
            size = os.path.getsize(RESPKG)
            # 支持 Range：114MB 大包，客户端常用分块续传，回 200 全量会让它一直等
            rng = self.headers.get("Range")
            start, end, partial = 0, size - 1, False
            if rng and rng.startswith("bytes="):
                spec = rng[6:].split(",")[0].strip()
                a, _, b = spec.partition("-")
                try:
                    if a:
                        start = int(a)
                        if b:
                            end = int(b)
                    elif b:
                        start = max(0, size - int(b))
                    partial = True
                except ValueError:
                    partial = False
                if start >= size or end < start:
                    return self._send(416, b"range not satisfiable")
                end = min(end, size - 1)
            length = (end - start + 1) if partial else size
            log("  资源包下发 start=%d end=%d（%d 字节，%s）"
                % (start, end, length, "Range" if partial else "全量"))
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            sent = 0
            t0 = time.time()
            try:
                with open(RESPKG, "rb") as f:
                    f.seek(start)
                    while sent < length:
                        chunk = f.read(min(1 << 20, length - sent))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        sent += len(chunk)
            except Exception as e:
                log("  资源包传输中断 %s（已发 %d/%d）" % (e, sent, length))
                return
            log("  资源包发送完成 %d 字节，%.1fs" % (sent, time.time() - t0))
            return

        if path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            p = os.path.join(MIRROR, "assets", rel.replace("/", os.sep))
            if os.path.isfile(p):
                return self._send(200, open(p, "rb").read())
            log("  !! 资源缺失 /assets/%s" % rel)
            return self._send(404, b"not found")

        if path in ("/", "/index.html"):
            # ★ 2026-11-28 15:17：开发版客户端启动时会**先要裸主机地址**
            #   （请求路径为空，日志 `GET   UA=Apache-HttpClient/UNAVAILABLE`），
            #   连打 8 次。以前这里回 404，它拿不到列表就直接崩（用户看到
            #   「口袋修仙·开发版屡次停止运行」）。裸地址按服务器列表回。
            if not path.strip("/"):
                log("  .. 裸地址按 server-list.xml 回（启动探测）")
                body = rewrite_server_list(read("server-list.xml"))
                return self._send(200, body, "text/xml; charset=utf-8")
            return self._send(200, b"kd163 local http ok\n", "text/plain")

        log("  !! 未实现端点 %s" % path)
        return self._send(404, b"not found")


def _pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


class MuxServer(ThreadingHTTPServer):
    """同一端口既服务 HTTP 资源，又把 TLS 连接（0x16 开头）转给游戏服。"""

    def process_request(self, request, client_address):
        if MUX_GAME:
            try:
                request.settimeout(1.0)
                head = request.recv(1, socket.MSG_PEEK)
            except Exception:
                head = b""
            request.settimeout(None)
            if head == b"\x16":
                try:
                    host, port = MUX_GAME.rsplit(":", 1)
                    up = socket.create_connection((host, int(port)), 5)
                except Exception as e:
                    log("mux 转发失败 %s: %s" % (MUX_GAME, e))
                    try:
                        request.close()
                    except Exception:
                        pass
                    return
                threading.Thread(target=_pump, args=(up, request), daemon=True).start()
                threading.Thread(target=_pump, args=(request, up), daemon=True).start()
                return
        return super().process_request(request, client_address)


def main():
    if not os.path.exists(RESPKG):
        log("警告：没有 %s" % RESPKG)
        log("      → 客户端 /assets.txt 会退回镜像版本，/mhsj_res.zip 会 404。")
        log("      → 表现：能登录，但进主城/切图时 native abort 或一直卡加载。")
        log("      → 需要与抓包同版本的资源包（md5 %s），见 README「必须自备」。" % RESPKG_MD5)
    if not os.path.exists(META_XML):
        log("警告：找不到 meta.xml（srvres/ 与 mirror/ 都没有）")
    else:
        log("meta.xml：%s" % META_XML)
    srv = MuxServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    log("本地 HTTP 监听 :%d   host_ip=%s" % (PORT, HOST_IP))
    if MUX_GAME:
        log("80 端口复用已开：TLS 连接转发到 %s" % MUX_GAME)
    srv.serve_forever()


if __name__ == "__main__":
    main()
