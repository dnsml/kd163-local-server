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
import account_policy     # 账号白名单 / 账号密码（accounts.json）
import admin_auth         # 后台管理员账号与会话（admin.json）

# 账号/昵称格式：和 account_from_login / create_role 的限制保持一致。
# 账号带空格或换行会被当 GM 指令拆开，所以这里必须卡死。
ACCT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,20}$")
NAME_RE = re.compile(r"^[^\s]{1,10}$")

STATE = os.path.join(HERE, "player_state.json")
STATUS = os.path.join(HERE, "logs", "status.json")
GM_CMD = os.path.join(HERE, "logs", "gm.cmd")
SERVER_LOG = os.path.join(HERE, "logs", "server.log")
HTML = os.path.join(HERE, "admin.html")
LOGIN_HTML = os.path.join(HERE, "admin_login.html")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
# 默认只给本机开。要让局域网里别人也能看，设环境变量 KD163_ADMIN_HOST=0.0.0.0
HOST = os.environ.get("KD163_ADMIN_HOST", "127.0.0.1") or "127.0.0.1"
# 是否强制登录：对外绑定（非 127.0.0.1）必须登录；本机配了 admin.json 也登录。
AUTH_REQUIRED = [False]


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

    # ---------------- 登录态 ----------------
    def _cookie_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "kd_admin":
                return v
        return ""

    def _user(self):
        return admin_auth.check(self._cookie_token())

    def _need_login(self):
        """没登录就回 401（页面则给登录页）。返回 True 表示可以继续。"""
        if not AUTH_REQUIRED[0] or self._user():
            return True
        self._send(401, {"error": "未登录", "login": True})
        return False

    def _serve_login(self):
        try:
            with open(LOGIN_HTML, encoding="utf-8") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        except Exception as e:
            return self._send(500, "登录页打不开: %s" % e, "text/plain; charset=utf-8")

    def _set_cookie(self, code, body, cookie):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except Exception:
            pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html", "/login"):
            if AUTH_REQUIRED[0] and not self._user():
                return self._serve_login()
            try:
                with open(HTML, encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, "admin.html 打不开: %s" % e, "text/plain; charset=utf-8")
        if u.path == "/api/ping":
            return self._send(200, {"ok": True})
        if u.path == "/api/me":
            if not self._need_login():
                return
            return self._send(200, {"user": self._user() or "",
                                    "required": AUTH_REQUIRED[0]})
        if not self._need_login():
            return
        if u.path == "/api/state":
            return self._send(200, state_payload())
        if u.path == "/api/accounts":
            try:
                import state_store
                allow, src = account_policy.load(force=True)
                reg = account_policy.registry()
                accs = state_store.accounts()
                by = {str(a["acct"]): a for a in accs}
                # 后台刚生成、玩家还没进游戏建角的账号：库里没有行，也要列出来
                for acct, ent in reg.items():
                    if acct in by:
                        continue
                    row = {"id": 0, "acct": acct, "name": "", "level": 0, "cls": 0,
                           "gold": 0, "copper": 0, "bind_gold": 0, "bind_copper": 0,
                           "bag": 0, "rev": 0, "updated_at": ""}
                    accs.append(row)
                    by[acct] = row
                for a in accs:
                    acct = str(a["acct"])
                    a["allowed"] = account_policy.allowed(acct)
                    a["has_pass"] = account_policy.needs_password(acct)
                    ent = reg.get(acct) or {}
                    a["created_at"] = ent.get("created_at") or ""
                    a["note"] = ent.get("note") or ""
                    a["online"] = False
                for o in state_store.online_list():
                    if o["acct"] in by:
                        by[o["acct"]]["online"] = True
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
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": "bad json: %s" % e})

        # ---- 登录/登出（登录本身不需要已登录）----
        ip = (self.client_address or ("?", 0))[0]
        if u.path == "/api/login":
            wait = admin_auth.wait_seconds(ip)
            if wait:
                return self._send(429, {"error": "失败次数太多，%d 秒后再试" % wait})
            user = str(body.get("user") or "")
            pwd = str(body.get("pass") or "")
            if not admin_auth.verify(user, pwd):
                admin_auth.throttle(ip, False)
                return self._send(401, {"error": "管理员账号或密码不对"})
            admin_auth.throttle(ip, True)
            tok = admin_auth.issue(user)
            return self._set_cookie(
                200, {"ok": True, "user": user},
                "kd_admin=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d"
                % (tok, admin_auth.TOKEN_TTL))
        if u.path == "/api/logout":
            admin_auth.revoke(self._cookie_token())
            return self._set_cookie(200, {"ok": True},
                                    "kd_admin=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")

        if not self._need_login():
            return
        # 防 CSRF：跨站表单发不出自定义头；前端所有 POST 都会带这个头
        if self.headers.get("X-Requested-With") != "KDAdmin":
            return self._send(403, {"error": "缺少 X-Requested-With 头"})

        if u.path == "/api/gm":
            lines = body.get("lines")
            if not lines and body.get("line"):
                lines = [body["line"]]
            lines = [str(x).strip() for x in (lines or []) if str(x).strip()]
            if not lines:
                return self._send(400, {"error": "缺少指令"})
            return self._send(200, {"results": run_gm(lines)})

        # 游戏账号：生成（随机账号+随机密码）/ 重置密码 / 删除
        if u.path == "/api/account":
            action = str(body.get("action") or "").strip()
            if action == "create":
                # 第一次生成账号会「从无文件变成有文件」：白名单随之生效。
                # 这时必须把库里现有角色都写进 allow，否则老玩家会被一起锁在门外。
                if not os.path.exists(account_policy.FILE):
                    try:
                        import state_store
                        seed = {str(a["acct"]) for a in state_store.accounts()}
                        if seed:
                            account_policy.save(seed, is_open=False)
                    except Exception:
                        pass
                acct, pwd = account_policy.new_account()
                note = str(body.get("note") or "").strip()
                if note:
                    account_policy.set_note(acct, note[:40])
                return self._send(200, {
                    "ok": True, "acct": acct, "password": pwd,
                    "msg": "账号 %s 已生成，密码 %s（发给玩家即可）" % (acct, pwd)})
            acct = str(body.get("acct") or "").strip()
            if not ACCT_RE.match(acct):
                return self._send(400, {"error": "账号格式不对"})
            if action == "reset":
                _a, pwd = account_policy.reset_password(acct)
                if not pwd:
                    return self._send(404, {
                        "error": "账号 %s 没有密码记录（老白名单账号不校验密码）" % acct})
                return self._send(200, {"ok": True, "acct": acct, "password": pwd,
                                        "msg": "已把 %s 的密码重置为 %s" % (acct, pwd)})
            if action == "delete":
                had = account_policy.remove_account(acct)
                has_char = False
                try:
                    import state_store
                    has_char = state_store.exists(acct)
                except Exception:
                    pass
                msg = "已删除账号 %s（不能再登录）" % acct
                if has_char:
                    msg += "；角色存档还在，需要的话用「删角色」一并清掉"
                return self._send(200, {"ok": True, "msg": msg if had else
                                        "账号 %s 不在账号表里，已从白名单移除" % acct})
            return self._send(400, {"error": "action 只支持 create/reset/delete"})

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
            # 注意：这里**不删文件**。accounts.json 里还存着账号密码，
            # 删掉等于把玩家密码一起清了。改成写 open=true：任何账号都能进，
            # 但已有账号的密码依旧校验。
            try:
                desc = account_policy.set_open(True)
            except Exception as e:
                return self._send(500, {"error": "关闭失败: %s" % e})
            return self._send(200, {"ok": True, "policy": desc, "allow": None,
                                    "note": "白名单已放开：新账号可直接登录；"
                                            "已有账号的密码仍然校验"})
        if action == "sync":
            try:
                import state_store
                allow = {str(a["acct"]) for a in state_store.accounts()}
            except Exception as e:
                return self._send(500, {"error": "读库失败: %s" % e})
            if not allow:
                return self._send(400, {"error": "库里还没有角色，拒绝生成空名单"})
            account_policy.save(allow, is_open=False)
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
        account_policy.save(allow, is_open=False)
        note = ""
        if was_off and action == "add":
            note = "白名单已开启：名单外账号将无法登录（要全开请点「关闭白名单」）"
        return self._send(200, {"ok": True, "policy": account_policy.describe(),
                                "allow": sorted(allow), "note": note})


def _wrap_tls(srv):
    """可选 HTTPS：KD_ADMIN_TLS=1 时用 patch/exact.pem|key（或 KD_ADMIN_CERT/KEY）加密。"""
    if os.environ.get("KD_ADMIN_TLS") != "1":
        return False
    import ssl
    cert = os.environ.get("KD_ADMIN_CERT") or os.path.join(HERE, "patch", "exact.pem")
    key = os.environ.get("KD_ADMIN_KEY") or os.path.join(HERE, "patch", "exact.key")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return True


def main():
    loopback = HOST in ("127.0.0.1", "localhost", "::1")
    require_env = os.environ.get("KD_ADMIN_REQUIRE") == "1"
    gen = admin_auth.ensure(HOST, force=require_env)
    AUTH_REQUIRED[0] = admin_auth.configured() or (not loopback) or require_env

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    https = _wrap_tls(srv)
    shown = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    # flush：cmd 窗口重定向时 python 会缓冲，没这行要等很久才看到「已启动」
    print("后台已启动: %s://%s:%d  (Ctrl+C 退出)"
          % ("https" if https else "http", shown, PORT), flush=True)
    if HOST in ("0.0.0.0", "::"):
        print("已对公网/局域网开放，用 http://<本机IP>:%d 访问" % PORT, flush=True)
    if gen:
        print("=" * 64, flush=True)
        print(" 已生成后台管理员账号（只显示这一次，请立刻保存）", flush=True)
        print("   用户名：%s" % gen[0], flush=True)
        print("   密码  ：%s" % gen[1], flush=True)
        print("   文件  ：%s" % admin_auth.FILE, flush=True)
        print(" 改密码：python work/admin_password.py set admin 新密码", flush=True)
        print("=" * 64, flush=True)
    elif AUTH_REQUIRED[0]:
        print("后台登录已开启：用户名 %s（密码在 %s）"
              % (admin_auth.load().get("user", "admin"), admin_auth.FILE), flush=True)
    else:
        print("后台登录未开启（仅本机绑定）。对外用：KD163_ADMIN_HOST=0.0.0.0 启动，"
              "会自动生成管理员密码；走 Nginx 反代时加 KD_ADMIN_REQUIRE=1", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
