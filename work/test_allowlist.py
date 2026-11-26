# -*- coding: utf-8 -*-
"""账号白名单回归（account_policy + /auth + 登录拦截）。

用法：python work/test_allowlist.py

覆盖：
 1. 未配置 → 所有账号放行（开发默认）
 2. accounts.json 生效 → 名单内放行、名单外拒绝、空账号拒绝
 3. 环境变量 KD_ALLOW_ACCOUNTS 覆盖文件；`*` = 全开
 4. 名单为空 / JSON 坏掉 → 全部拒绝（fail closed，不能静默放开）
 5. 真起一个临时服（随机端口 + 临时库）：
    名单内 /auth 200、名单外 /auth 403；
    名单外直接发 17 也被拒（cmd=1），且不会在库里建出这个账号。
"""
import json
import os
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

import account_policy as AP      # noqa: E402

PORT = 9137


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kd_allow_")
        self.cfg = os.path.join(self.tmp.name, "accounts.json")
        self.old = {k: os.environ.get(k) for k in ("KD_ACCOUNTS_FILE", "KD_ALLOW_ACCOUNTS")}
        os.environ["KD_ACCOUNTS_FILE"] = self.cfg
        os.environ.pop("KD_ALLOW_ACCOUNTS", None)
        AP.FILE = self.cfg
        AP.load(force=True)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        AP.FILE = self.old.get("KD_ACCOUNTS_FILE") or os.path.join(ROOT, "accounts.json")
        AP.load(force=True)
        self.tmp.cleanup()

    def write(self, obj):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        # 有些文件系统 mtime 精度只有 1s，手动往后拨一点，保证「热加载」用例可靠
        t = time.time() + 2
        os.utime(self.cfg, (t, t))
        AP.load(force=True)

    def test_off_allows_all(self):
        self.assertIsNone(AP.load(force=True)[0])
        self.assertTrue(AP.allowed("anyone"))
        self.assertFalse(AP.allowed(""))

    def test_file_list(self):
        self.write({"allow": ["a1", "b2"]})
        self.assertTrue(AP.allowed("a1"))
        self.assertTrue(AP.allowed("b2"))
        self.assertFalse(AP.allowed("c3"))
        self.assertFalse(AP.allowed(""))

    def test_env_overrides(self):
        self.write({"allow": ["a1"]})
        os.environ["KD_ALLOW_ACCOUNTS"] = "x9, y8"
        self.assertTrue(AP.allowed("x9"))
        self.assertFalse(AP.allowed("a1"))
        os.environ["KD_ALLOW_ACCOUNTS"] = "*"
        self.assertTrue(AP.allowed("whatever"))

    def test_empty_and_broken_fail_closed(self):
        self.write({"allow": []})
        self.assertFalse(AP.allowed("a1"), "空名单必须全部拒绝")
        self.write("{ not json")
        self.assertFalse(AP.allowed("a1"), "配置坏掉必须全部拒绝")

    def test_hot_reload(self):
        self.write({"allow": ["a1"]})
        self.assertFalse(AP.allowed("b2"))
        time.sleep(0.01)
        self.write({"allow": ["a1", "b2"]})
        self.assertTrue(AP.allowed("b2"), "改文件后应立即生效，不用重启")


class LiveTest(unittest.TestCase):
    """真起一个临时服，验证 /auth 与 17 两层拦截。"""

    ALLOWED = "allow_me"
    DENIED = "deny_me"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="kd_allow_live_")
        cfg = os.path.join(cls.tmp.name, "accounts.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"allow": [cls.ALLOWED]}, f)
        env = dict(os.environ)
        env.update({
            "KD_ACCOUNTS_FILE": cfg,
            "KD_STATE_DB": os.path.join(cls.tmp.name, "state.db"),
            # 临时实例不能用正式的 gm.cmd / status.json，否则会抢指令、覆盖后台数值
            "KD163_GM_CMD": os.path.join(cls.tmp.name, "gm.cmd"),
            "KD163_STATUS_JSON": os.path.join(cls.tmp.name, "status.json"),
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, "local_server3.py", str(PORT),
             "patch/exact.pem", "patch/exact.key"],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", PORT), timeout=0.3).close()
                return
            except OSError:
                time.sleep(0.2)
        cls.proc.kill()
        raise RuntimeError("临时服没起来（端口 %d）" % PORT)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        cls.tmp.cleanup()

    def auth(self, user):
        ctx = ssl._create_unverified_context()
        raw = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        s = ctx.wrap_socket(raw, server_hostname="124.223.56.189")
        body = json.dumps({"username": user}).encode()
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

    def login_cmds(self, acct):
        def sfield(x):
            b = x.encode()
            return bytes([0xA0 | len(b)]) + b

        def pack(cmd, seq, p=b""):
            return struct.pack(">HHI", 6 + len(p), cmd, seq) + p

        s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        s.settimeout(0.4)
        time.sleep(0.4)
        s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sfield(acct)
                       + b"\xda\x00\x2b" + b"t" * 43))
        out = b""
        t0 = time.time()
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

    def test_auth(self):
        self.assertIn("200", self.auth(self.ALLOWED))
        self.assertIn("403", self.auth(self.DENIED))

    def test_login_blocked_without_row(self):
        cmds = self.login_cmds(self.DENIED)
        # 拒绝用的是 cmd=1 + 「该账号未开通」文本（>10B），和「无角色」的 01a0(2B) 区分开
        self.assertEqual(cmds[:1], [(1, 22)], "名单外账号应只收到拒绝提示：%s" % cmds)
        import sqlite3
        con = sqlite3.connect(os.path.join(self.tmp.name, "state.db"))
        try:
            rows = [r[0] for r in con.execute("SELECT acct FROM player")]
        finally:
            con.close()
        self.assertNotIn(self.DENIED, rows, "被拒账号不该在库里建行")

    def test_login_allowed(self):
        cmds = self.login_cmds(self.ALLOWED)
        self.assertNotIn((1, 22), cmds, "名单内账号不该被白名单拦：%s" % cmds)
        # 临时库是空的：账号合法但没角色 → 17 或 cmd=1 01a0（短包），都算通过
        self.assertTrue(any(c == 17 for c, _ in cmds)
                        or (cmds and cmds[0][0] == 1 and cmds[0][1] <= 6),
                        "名单内账号登录应答异常：%s" % cmds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
