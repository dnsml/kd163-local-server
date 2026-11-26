#!/usr/bin/env python
"""KD163 本机线上服 · 网页管理后台

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
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bag_proto          # 道具名查询
import item_rules         # 道具效果/价格（后台展示用）
import account_policy     # 账号白名单（accounts.json）

# 账号/昵称格式：和 account_from_login / create_role 的限制保持一致。
# 账号带空格或换行会被当 GM 指令拆开，所以这里必须卡死。
ACCT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,20}$")
NAME_RE = re.compile(r"^[^\s]{1,10}$")

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


def db_state(acct=None):
    """从 SQLite 读一份存档。后台数值源顺序：status.json → 库 → 老 JSON。

    ★ 2026-11-26：存档已迁到 server_state.db（state_store.py），
      player_state.json 只剩「迁移前的老文件 + 写库失败时的兜底」，
      再拿它当主数值源会显示上次迁移前的旧数字。
    """
    try:
        import state_store
        d, _ = state_store.load(acct or "1784366")
        return d or {}
    except Exception:
        return {}


def state_payload():
    """优先用 logs/status.json（游戏服每次变动都写，且是「当前在线角色」）。

    ★ 2026-11-25：以前拿 player_state.json 当数值源，多账号时那是主号/初始化文件，
      面板上的等级、元宝、背包全是别人的（实际角色 Lv43 面板显示 Lv45）。
      现在 status.json 有 level+bag 就直接用它，否则依次回退 SQLite / 老 JSON。
    """
    st = read_json(STATUS, {})
    ps = db_state() or read_json(STATE, {})
    if st.get("level") and st.get("bag"):
        ps = st
    bag = ps.get("bag") or {}
    names = {}
    for v in bag.values():
        try:
            names[str(v[0])] = bag_proto.item(int(v[0]))["name"]
        except Exception:
            pass
    return {
        "online": st.get("online", 0),
        "time": st.get("time", ""),
        "level": ps.get("level", 0),
        "exp": ps.get("exp", 0),
        "need": ps.get("need", 1),
        "yuanbao": ps.get("yuanbao", 0),
        "bind_yuanbao": ps.get("bind_yuanbao", 0),
        "tongqian": ps.get("tongqian", 0),
        "bind_coin": ps.get("bind_coin", ps.get("coin", 0)),
        "yueli": ps.get("yueli", 0),
        "bag": bag,
        "names": names,
    }


def run_gm(lines):
    """写 gm.cmd，等游戏服消费完，再从 server.log 捞回每条指令的执行结果。

    注意：游戏服是「先删 gm.cmd、后写 [gm] 日志」的（甚至反过来），
    只等「文件消失」就立刻读日志会抢跑，出现「明明执行成功却回没确认」。
    所以这里改成按行逐个轮询，最多等 6 秒，拿到全部结果就提前退出。
    """
    before = set(tail(SERVER_LOG, 4000).splitlines())
    with open(GM_CMD, "a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")
    deadline = time.time() + 6
    results = [None] * len(lines)
    while time.time() < deadline:
        fresh = [x for x in tail(SERVER_LOG, 4000).splitlines() if x not in before]
        for i, ln in enumerate(lines):
            if results[i] is not None:
                continue
            for row in reversed(fresh):
                if "[gm]" in row and (" " + ln + " ->") in row:
                    ok = "[gm] OK" in row
                    msg = row.split("->", 1)[1].strip() if "->" in row else row
                    results[i] = [ok, msg]
                    break
        if all(r is not None for r in results):
            break
        time.sleep(0.15)
    return [r or [False, "已写入，但没等到游戏服确认（服务端在跑吗？）"] for r in results]


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
        if u.path == "/api/accounts":
            try:
                import state_store
                allow, src = account_policy.load(force=True)
                accs = state_store.accounts()
                for a in accs:
                    a["allowed"] = (allow is None or str(a["acct"]) in allow)
                    a["online"] = False
                for o in state_store.online_list():
                    for a in accs:
                        if a["acct"] == o["acct"]:
                            a["online"] = True
                return self._send(200, {
                    "backend": state_store.backend(),
                    "db": os.path.basename(state_store.DB_FILE),
                    "accounts": accs,
                    "allow": sorted(allow) if allow is not None else None,
                    "policy": account_policy.describe(),
                    "src": src,
                    "file": os.path.basename(account_policy.FILE),
                })
            except Exception as e:
                return self._send(500, {"error": "读库失败: %s" % e})
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
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": "bad json: %s" % e})

        if u.path == "/api/gm":
            lines = body.get("lines")
            if not lines and body.get("line"):
                lines = [body["line"]]
            lines = [str(x).strip() for x in (lines or []) if str(x).strip()]
            if not lines:
                return self._send(400, {"error": "缺少指令"})
            return self._send(200, {"results": run_gm(lines)})

        # 账号管理：建角 / 删角色 / 踢下线（最终都走 GM，保证只有游戏服写存档）
        if u.path == "/api/char":
            action = str(body.get("action") or "").strip()
            acct = str(body.get("acct") or "").strip()
            if not ACCT_RE.match(acct):
                return self._send(400, {"error": "账号格式不对（1~20 位字母/数字/_ - .）"})
            if action == "create":
                name = str(body.get("name") or "").strip()
                if not NAME_RE.match(name):
                    return self._send(400, {"error": "昵称 1~10 个字符且不能有空格"})
                sex = "女" if str(body.get("sex") or "") in ("2", "女", "female", "f") else "男"
                line = "@%s 建角 %s %s" % (acct, name, sex)
            elif action == "delete":
                line = "@%s 删档 确认" % acct
            elif action == "kick":
                line = "@%s 踢下线" % acct
            else:
                return self._send(400, {"error": "action 只支持 create/delete/kick"})
            res = run_gm([line])[0]
            return self._send(200, {"line": line, "ok": bool(res[0]), "msg": res[1]})

        # 白名单：add / remove / sync / off
        if u.path == "/api/allow":
            return self._allow(body)

        return self._send(404, {"error": "not found"})

    def _allow(self, body):
        action = str(body.get("action") or "").strip().lower()
        if action == "off":
            try:
                if os.path.exists(account_policy.FILE):
                    os.remove(account_policy.FILE)
            except OSError as e:
                # Windows 上偶尔会出现「文件其实删掉了，但 remove 仍抛 0x2」
                # （杀毒/文件过滤驱动）。以「现在还在不在」为准，不要误报失败。
                if os.path.exists(account_policy.FILE):
                    return self._send(500, {"error": "关闭失败: %s" % e})
            account_policy.load(force=True)
            return self._send(200, {"ok": True, "policy": account_policy.describe(),
                                    "allow": None,
                                    "note": "白名单已关闭：所有账号都能登录"
                                            + ("（环境变量 KD_ALLOW_ACCOUNTS 仍生效）"
                                               if os.environ.get("KD_ALLOW_ACCOUNTS") else "")})
        if action == "sync":
            try:
                import state_store
                allow = {str(a["acct"]) for a in state_store.accounts()}
            except Exception as e:
                return self._send(500, {"error": "读库失败: %s" % e})
            if not allow:
                return self._send(400, {"error": "库里还没有角色，拒绝生成空名单"})
            account_policy.save(allow)
            return self._send(200, {"ok": True, "policy": account_policy.describe(),
                                    "allow": sorted(allow),
                                    "note": "已用库里现有 %d 个角色生成名单" % len(allow)})
        allow, _src = account_policy.load(force=True)
        was_off = allow is None
        allow = set(allow or [])
        accts = body.get("accounts")
        if accts is None:
            accts = [body.get("acct")]
        accts = [str(x).strip() for x in (accts or []) if str(x or "").strip()]
        if action == "add":
            bad = [x for x in accts if not ACCT_RE.match(x)]
            if bad:
                return self._send(400, {"error": "账号格式不对：%s" % "、".join(bad)})
            allow |= set(accts)
        elif action == "remove":
            allow -= set(accts)
        else:
            return self._send(400, {"error": "action 只支持 add/remove/sync/off"})
        account_policy.save(allow)
        note = ""
        if was_off and action == "add":
            note = "白名单已开启：名单外账号将无法登录（要全开请点「关闭白名单」）"
        return self._send(200, {"ok": True, "policy": account_policy.describe(),
                                "allow": sorted(allow), "note": note})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    shown = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    # flush：cmd 窗口重定向时 python 会缓冲，没这行要等很久才看到「已启动」
    print("后台已启动: http://%s:%d  (Ctrl+C 退出)" % (shown, PORT), flush=True)
    if HOST in ("0.0.0.0", "::"):
        print("已对局域网开放，同网段设备用本机内网 IP 访问，如 http://192.168.x.x:%d" % PORT,
              flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
