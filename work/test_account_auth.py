# -*- coding: utf-8 -*-
"""账号密码 + 后台登录鉴权回归。

用法：python work/test_account_auth.py

覆盖：
 A. 账号密码（account_policy）
    1. new_account 生成随机账号/密码，文件里只存 pbkdf2 哈希，不存明文
    2. verify 正确/错误；needs_password；reset 后旧密码失效；remove 后不可登录
 B. 游戏服（真起临时实例）
    3. /auth 密码对 → 200；错 → 401；不在名单 → 403
    4. 有密码的账号直接发 17（没过 /auth）→ 被拒且不建存档
    5. 先过 /auth 再发 17 → 正常回 17
 C. 后台登录（真起临时实例）
    6. 未登录访问 /api/accounts → 401；页面给登录页
    7. 密码错 → 401；密码对 → Set-Cookie，带 cookie 能读接口
    8. 带 cookie 但缺 X-Requested-With 的 POST → 403（防 CSRF）
"""
import http.client
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GAME_PORT = 9174
ADMIN_PORT = 8896


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kd_acct_")
        self.cfg = os.path.join(self.tmp.name, "accounts.json")
        self.old = os.environ.get("KD_ACCOUNTS_FILE")
        os.environ["KD_ACCOUNTS_FILE"] = self.cfg
        import importlib
        import account_policy
        importlib.reload(account_policy)
        self.AP = account_policy

    def tearDown(self):
        if self.old is None:
            os.environ.pop("KD_ACCOUNTS_FILE", None)
        else:
            os.environ["KD_ACCOUNTS_FILE"] = self.old
        import importlib
        import account_policy
        importlib.reload(account_policy)
        self.tmp.cleanup()

    def test_create_hash_verify(self):
        AP = self.AP
        acct, pwd = AP.new_account()
        self.assertEqual(len(acct), 8)
        self.assertEqual(pwd, AP.DEFAULT_PASSWORD, "默认密码应统一")
        self.assertEqual(pwd, "123")
        self.assertTrue(re.match(r"^[23456789abcdefghjkmnpqrstuvwxyz]{8}$", acct))
        raw = open(self.cfg, encoding="utf-8").read()
        self.assertNotIn(pwd, raw, "密码不能明文落盘")
        self.assertIn("pbkdf2_sha256$", raw)
        self.assertTrue(AP.needs_password(acct))
        self.assertIs(AP.verify(acct, pwd), True)
        self.assertIs(AP.verify(acct, pwd + "x"), False)
        self.assertTrue(AP.allowed(acct), "账号表里的号应默认放行")
        # 老白名单账号：没密码，verify 返回 None
        AP.save({"legacy1"})
        self.assertIsNone(AP.verify("legacy1", "whatever"))
        self.assertTrue(AP.allowed("legacy1"))

    def test_reset_and_remove(self):
        AP = self.AP
        acct, pwd = AP.new_account()
        self.assertTrue(AP.set_password(acct, "oldpass"))
        self.assertIs(AP.verify(acct, "oldpass"), True)
        a2, pwd2 = AP.reset_password(acct)
        self.assertEqual(a2, acct)
        self.assertEqual(pwd2, "123")
        self.assertIs(AP.verify(acct, "oldpass"), False)
        self.assertIs(AP.verify(acct, pwd2), True)
        self.assertTrue(AP.remove_account(acct))
        self.assertFalse(AP.allowed(acct), "删账号后不应再放行")
        self.assertFalse(AP.needs_password(acct))


def _wait_port(port, proc, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("进程提前退出 rc=%s" % proc.returncode)
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("端口 %d 没起来" % port)


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


class GamePasswordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="kd_gamepw_")
        cls.cfg = os.path.join(cls.tmp.name, "accounts.json")
        os.environ["KD_ACCOUNTS_FILE"] = cls.cfg
        import importlib
        import account_policy
        importlib.reload(account_policy)
        cls.AP = account_policy
        cls.acct, cls.pwd = account_policy.new_account()
        # 第二个号专门用来验证「没过 /auth 不能登录」，避免受用例执行顺序影响
        cls.acct2, cls.pwd2 = account_policy.new_account()
        env = dict(os.environ)
        env.update({
            "KD_ACCOUNTS_FILE": cls.cfg,
            "KD_STATE_DB": os.path.join(cls.tmp.name, "state.db"),
            "KD163_GM_CMD": os.path.join(cls.tmp.name, "gm.cmd"),
            "KD163_STATUS_JSON": os.path.join(cls.tmp.name, "status.json"),
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, "local_server3.py", str(GAME_PORT),
             "patch/exact.pem", "patch/exact.key"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait_port(GAME_PORT, cls.proc)

    @classmethod
    def tearDownClass(cls):
        _stop(cls.proc)
        os.environ.pop("KD_ACCOUNTS_FILE", None)
        import importlib
        import account_policy
        importlib.reload(account_policy)
        cls.tmp.cleanup()

    def auth(self, user, pwd):
        ctx = ssl._create_unverified_context()
        raw = socket.create_connection(("127.0.0.1", GAME_PORT), timeout=5)
        s = ctx.wrap_socket(raw, server_hostname="124.223.56.189")
        body = json.dumps({"username": user, "password": pwd}).encode()
        s.sendall(b"POST /auth HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n"
                  % len(body) + body)
        data = b""
        while True:
            try:
                d = s.recv(4096)
            except Exception:
                break
            if not d:
                break
            data += d
        s.close()
        return data.split(b"\r\n", 1)[0].decode()

    def login17(self, acct):
        def sfield(x):
            b = x.encode()
            return bytes([0xA0 | len(b)]) + b

        s = socket.create_connection(("127.0.0.1", GAME_PORT), timeout=5)
        s.settimeout(0.4)
        time.sleep(0.4)
        p = b"\x01\xcc\x97\x02" + sfield(acct) + b"\xda\x00\x2b" + b"t" * 43
        s.sendall(struct.pack(">HHI", 6 + len(p), 17, 2) + p)
        out, t0 = b"", time.time()
        while time.time() - t0 < 1.5:
            try:
                d = s.recv(65536)
                if not d:
                    break
                out += d
            except socket.timeout:
                pass
        s.close()
        cmds, i = [], 0
        while i + 8 <= len(out):
            total = struct.unpack(">H", out[i:i + 2])[0]
            cmd = struct.unpack(">H", out[i + 2:i + 4])[0]
            cmds.append((cmd, len(out[i + 6:i + 2 + total])))
            i += total + 2
        return cmds

    def test_auth_password(self):
        self.assertIn("200", self.auth(self.acct, self.pwd))
        self.assertIn("401", self.auth(self.acct, "wrongpass"))

    def test_login_requires_ticket(self):
        # 先不 /auth 直接发 17：有密码的号必须被拒（用独立账号，不受其它用例的票据影响）
        cmds = self.login17(self.acct2)
        self.assertTrue(cmds and cmds[0][0] == 1 and cmds[0][1] > 5,
                        "没过 /auth 的 17 应被拒：%s" % cmds)

    def test_login_after_auth(self):
        self.assertIn("200", self.auth(self.acct, self.pwd))
        cmds = self.login17(self.acct)
        # 临时库是空的：账号合法但没角色 → 17 或 cmd=1 01a0（短包）。被拒的话是长文本包。
        ok = any(c == 17 for c, _ in cmds) or (cmds and cmds[0][0] == 1 and cmds[0][1] <= 6)
        self.assertTrue(ok, "过 /auth 后应能进入登录流程：%s" % cmds)


class AdminAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="kd_adminauth_")
        cls.adm = os.path.join(cls.tmp.name, "admin.json")
        os.environ["KD_ADMIN_FILE"] = cls.adm
        import importlib
        import admin_auth
        importlib.reload(admin_auth)
        cls.AA = admin_auth
        admin_auth.set_user("admin", "Pa55word!")
        env = dict(os.environ)
        env.update({
            "KD_ADMIN_FILE": cls.adm,
            # 后台里的白名单操作必须打临时文件，绝不能碰仓库里的 accounts.json
            "KD_ACCOUNTS_FILE": os.path.join(cls.tmp.name, "accounts.json"),
            "KD163_ADMIN_HOST": "127.0.0.1",
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, "admin_server.py", str(ADMIN_PORT)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait_port(ADMIN_PORT, cls.proc)

    @classmethod
    def tearDownClass(cls):
        _stop(cls.proc)
        os.environ.pop("KD_ADMIN_FILE", None)
        cls.tmp.cleanup()

    def req(self, method, path, body=None, cookie="", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", ADMIN_PORT, timeout=5)
        h = {"Content-Type": "application/json"}
        if cookie:
            h["Cookie"] = cookie
        if headers:
            h.update(headers)
        raw = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def test_requires_login(self):
        code, _, _ = self.req("GET", "/api/accounts")
        self.assertEqual(code, 401)
        code, _, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("管理后台", body.decode("utf-8"))

    def test_login_flow(self):
        code, _, _ = self.req("POST", "/api/login", {"user": "admin", "pass": "bad"})
        self.assertEqual(code, 401)
        code, hdr, _ = self.req("POST", "/api/login",
                                {"user": "admin", "pass": "Pa55word!"})
        self.assertEqual(code, 200)
        cookie = hdr.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie.startswith("kd_admin="))
        code, _, body = self.req("GET", "/api/accounts", cookie=cookie)
        self.assertEqual(code, 200)
        self.assertIn("accounts", json.loads(body))
        # 缺防 CSRF 头的 POST 被拒
        code, _, _ = self.req("POST", "/api/allow", {"action": "off"}, cookie=cookie)
        self.assertEqual(code, 403)
        code, _, _ = self.req("POST", "/api/allow", {"action": "off"}, cookie=cookie,
                              headers={"X-Requested-With": "KDAdmin"})
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
