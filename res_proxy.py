# -*- coding: utf-8 -*-
"""资源缓存代理（低调模式）。

放在真服模式：DNAT 80 -> 宿主:81，本进程把客户端对资源服务器的请求
先查磁盘缓存（res_cache/），没有才去真服拉一次并落盘。
同一资源只会从他的服下载一次，之后全部本地直出——他的服不再被反复拉
129MB 的资源包。

用法：python res_proxy.py 81 124.223.56.189 80
"""
import http.client
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'res_cache')
os.makedirs(CACHE, exist_ok=True)

ORIGIN_HOST = '124.223.56.189'
ORIGIN_PORT = 80


def log(msg):
    import datetime
    print('[%s] %s' % (datetime.datetime.now().strftime('%H:%M:%S'), msg), flush=True)


def cache_path(urlpath):
    rel = urlpath.lstrip('/').replace('/', os.sep)
    p = os.path.normpath(os.path.join(CACHE, rel))
    if not p.startswith(CACHE):        # 防目录穿越
        return None
    return p


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _origin_request(self, method):
        """带 Range 等头的直通请求，返回 (status, headers, body或流长度)。"""
        headers = {}
        for k in ('Range', 'If-Modified-Since', 'If-None-Match', 'User-Agent'):
            v = self.headers.get(k)
            if v:
                headers[k] = v
        conn = http.client.HTTPConnection(ORIGIN_HOST, ORIGIN_PORT, timeout=30)
        try:
            conn.request(method, self.path, headers=headers)
            r = conn.getresponse()
            body = r.read()
            return r.status, dict(r.getheaders()), body
        finally:
            conn.close()

    def _serve(self, method):
        cp = cache_path(self.path.split('?')[0])
        rng = self.headers.get('Range')

        # 1) 无 Range 且缓存命中 → 本地直出
        if method == 'GET' and not rng and cp and os.path.isfile(cp):
            data = open(cp, 'rb').read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Cache', 'hit')
            self.end_headers()
            if method != 'HEAD':
                self.wfile.write(data)
            return

        # 2) 回源
        try:
            status, headers, body = self._origin_request(method)
        except Exception as e:
            log('!! 回源失败 %s %s' % (self.path, e))
            self.send_response(502)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return

        # 3) 200 且无 Range → 落盘缓存
        if method == 'GET' and status == 200 and not rng and cp and body:
            try:
                os.makedirs(os.path.dirname(cp), exist_ok=True)
                tmp = cp + '.part'
                open(tmp, 'wb').write(body)
                os.replace(tmp, cp)
                log('缓存 %s (%d B)' % (self.path, len(body)))
            except OSError as e:
                log('!! 缓存写失败 %s: %s' % (self.path, e))

        self.send_response(status)
        for k in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges',
                  'Content-Disposition', 'Last-Modified', 'ETag'):
            if k in headers:
                self.send_header(k, headers[k])
        if 'Content-Length' not in headers:
            self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Cache', 'miss')
        self.end_headers()
        if method != 'HEAD' and body:
            self.wfile.write(body)

    def do_GET(self):
        self._serve('GET')

    def do_HEAD(self):
        self._serve('HEAD')


def main():
    global ORIGIN_HOST, ORIGIN_PORT
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 81
    if len(sys.argv) > 2:
        ORIGIN_HOST = sys.argv[2]
    if len(sys.argv) > 3:
        ORIGIN_PORT = int(sys.argv[3])
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    log('资源缓存代理 :%d -> %s:%d  缓存目录 %s' % (port, ORIGIN_HOST, ORIGIN_PORT, CACHE))
    srv.serve_forever()


if __name__ == '__main__':
    main()
