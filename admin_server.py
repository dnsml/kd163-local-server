#!/usr/bin/env python
"""KD163 本机私服 · 网页管理后台

只用标准库（http.server + json），不装任何依赖。

为什么走文件而不是直接连游戏服：
    游戏服（local_server3.py）已经在跑，给它再加一个管理端口就要改监听逻辑；
    直接复用它的 GM 文件通道最省事 —— 后台把指令写进 logs/gm.cmd，
    游戏服每秒扫一次、执行、删文件，改完立刻把 28/178 推给在线客户端，
    所以数值是"当场变"，不用重登。

用法
    python admin_server.py [端口]          # 默认 8899，只监听 127.0.0.1
    浏览器打开 http://127.0.0.1:8899
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bag_proto          # 道具名查询
import item_rules         # 道具效果/价格（后台展示用）

STATE = os.path.join(HERE, "player_state.json")
STATUS = os.path.join(HERE, "logs", "status.json")
GM_CMD = os.path.join(HERE, "logs", "gm.cmd")
SERVER_LOG = os.path.join(HERE, "logs", "server.log")
HTML = os.path.join(HERE, "admin.html")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
# 默认只给本机开。要让局域网里别人也能看，设环境变量 KD163_ADMIN_HOST=0.0.0.0
HOST = os.environ.get("KD163_ADMIN_HOST", "127.0.0.1") or "127.0.0.1"


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def tail(path, n=60):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = min(size, 96 * 1024)
            f.seek(size - block)
            data = f.read().decode("utf-8", "replace")
        return "\n".join(data.splitlines()[-n:])
    except Exception:
        return ""


def state_payload():
    """数值以 player_state.json 为准（游戏服每次改动都会落盘），
    status.json 只用来拿「在线人数 / 更新时间」—— 它只在连接进出时刷新，
    直接拿它当数值来源会看到旧值（GM 改完元宝还显示老的）。"""
    st = read_json(STATUS, {})
    ps = read_json(STATE, {})
    bag = ps.get("bag") or st.get("bag", {})
    names = {}
    for v in bag.values():
        try:
            names[str(v[0])] = bag_proto.item(int(v[0]))["name"]
        except Exception:
            pass
    return {
        "online": st.get("online", 0),
        "time": st.get("time", ""),
        "level": ps.get("level", st.get("level", 0)),
        "exp": ps.get("exp", st.get("exp", 0)),
        "need": ps.get("need", st.get("need", 1)),
        "yuanbao": ps.get("yuanbao", st.get("yuanbao", 0)),
        "bind_yuanbao": ps.get("bind_yuanbao", st.get("bind_yuanbao", 0)),
        "tongqian": ps.get("tongqian", st.get("tongqian", 0)),
        "bind_coin": ps.get("coin", st.get("bind_coin", 0)),
        "yueli": ps.get("yueli", st.get("yueli", 0)),
        "bag": bag,
        "names": names,
    }


def run_gm(lines):
    """写 gm.cmd，等游戏服消费完，再从 server.log 捞回每条指令的执行结果。"""
    before = tail(SERVER_LOG, 4000)
    with open(GM_CMD, "a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")
    deadline = time.time() + 4
    while time.time() < deadline and os.path.exists(GM_CMD):
        time.sleep(0.06)
    after = tail(SERVER_LOG, 4000)
    fresh = [x for x in after.splitlines() if x not in set(before.splitlines())]
    results = []
    for ln in lines:
        hit = None
        for row in reversed(fresh):
            if "[gm]" in row and (" " + ln + " ->") in row:
                hit = row
                break
        if hit:
            ok = "[gm] OK" in hit
            msg = hit.split("->", 1)[1].strip() if "->" in hit else hit
            results.append([ok, msg])
        else:
            results.append([False, "已写入，但没等到游戏服确认（服务端在跑吗？）"])
    return results


class Handler(BaseHTTPRequestHandler):
    server_version = "KD163Admin/1.0"

    def log_message(self, *a):        # 静音，避免刷屏
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except Exception:
            pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            try:
                with open(HTML, encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, "admin.html 打不开: %s" % e, "text/plain; charset=utf-8")
        if u.path == "/api/state":
            return self._send(200, state_payload())
        if u.path == "/api/items":
            kw = (q.get("q", [""])[0] or "").strip().lower()
            if not kw:
                return self._send(200, [])
            pool = []
            for it in bag_proto.items().values():
                nm = it["name"]
                low = nm.lower()
                if kw not in low:
                    continue
                rank = 0 if low.startswith(kw) else 1
                pool.append((rank, len(nm), it["id"], nm))
            pool.sort()
            return self._send(200, [{"id": i, "name": n} for _, _, i, n in pool[:30]])
        if u.path == "/api/log":
            n = int(q.get("n", ["60"])[0] or 60)
            return self._send(200, {"text": tail(SERVER_LOG, max(1, min(n, 400)))})
        if u.path == "/api/ping":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/gm":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": "bad json: %s" % e})
        lines = body.get("lines")
        if not lines and body.get("line"):
            lines = [body["line"]]
        lines = [str(x).strip() for x in (lines or []) if str(x).strip()]
        if not lines:
            return self._send(400, {"error": "缺少指令"})
        return self._send(200, {"results": run_gm(lines)})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    shown = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    print("后台已启动: http://%s:%d  (Ctrl+C 退出)" % (shown, PORT))
    if HOST in ("0.0.0.0", "::"):
        print("已对局域网开放，同网段设备用本机内网 IP 访问，如 http://192.168.x.x:%d" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
