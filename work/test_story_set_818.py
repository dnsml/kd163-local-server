# -*- coding: utf-8 -*-
"""400501「物归原主」自动穿神装后必须补 818（面板才显示）。

背景（2026-11-26 21:50 用户反馈）：
  太虚星君那条任务线交完 400501 后，服务端直接把 7 件神装写进装备栏。
  模型（30 包）立刻穿上，但角色面板 11 个格子全空 —— 因为面板的装备列表是
  按 **818 全量**画的，服务端一件都没推。登录时那份 818 是「空装备」的旧列表，
  之后没人刷新，面板就一直是空的。

断言：
 1. 交 400501 的下行包里出现 818，且 818 在 29 之前；
 2. 818 = 197B / 7 条，槽 1~6 + 武器，id 是 11108~11114；
 3. 装备栏确实落了 7 件，武器在内部槽 8。
"""
import contextlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["KD_STATE_BACKEND"] = "json"      # fixture 是临时 JSON，固定跑旧后端

import local_server3 as sv                   # noqa: E402
import player                                # noqa: E402


class Conn:
    def __init__(self, requests=()):
        self.incoming = b"".join(sv.pack(*r) for r in requests)
        self.packets = []

    def settimeout(self, t):
        pass

    def recv(self, size):
        data, self.incoming = self.incoming[:size], self.incoming[size:]
        return data

    def sendall(self, data):
        while data:
            total, cmd, seq = struct.unpack_from(">HHI", data)
            end = total + 2
            if end > len(data):
                raise AssertionError("服务端发送了不完整的帧")
            self.packets.append((cmd, seq, data[8:end]))
            data = data[end:]

    def close(self):
        pass


class StorySet818(unittest.TestCase):
    ACCT = "99007701"
    QID = 400501

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="kd_818_")))
        self.logs = []
        for key, value in {
            "state_path": lambda pl=None: str(self.tmp / ("player_state_%s.json" % self.ACCT)),
            "BACKUP_DIR": str(self.tmp / "backups"),
            "log": self.logs.append,
            "write_status": lambda: None,
            "FRESH_ACCT": "",
            "AUTO_NEWBIE": False,
        }.items():
            self.stack.enter_context(patch.object(sv, key, value))
        player.new()
        self.seed()

    def seed(self):
        d = {"name": "神装验收", "has_char": True, "level": 11, "class": 1,
             "need": 3000, "exp": 0, "coin": 0, "ver": 2,
             "quest_active": self.QID, "quest_ready": True,
             "equip": {}, "bag": {}, "pets": []}
        (self.tmp / ("player_state_%s.json" % self.ACCT)).write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def run_session(self, *requests):
        conn = Conn(requests)
        sv.handle_game(conn, 990077)
        self.assertFalse([s for s in self.logs if "异常" in s], self.logs)
        return conn.packets

    def test_submit_400501_pushes_818(self):
        login = (17, 2, b"\x01\xcc\x97\x02" + sv.sfield(self.ACCT)
                 + b"\xda\x00\x2b" + b"t" * 43)
        submit = (214, 3, b"\xce" + self.QID.to_bytes(4, "big"))
        packets = self.run_session(login, submit)

        cmds = [c for c, _, _ in packets]
        self.assertIn(214, cmds)
        idx818 = [i for i, c in enumerate(cmds) if c == 818]
        idx29 = [i for i, c in enumerate(cmds) if c == 29]
        self.assertTrue(idx818, "交 400501 后没有下发 818，面板会是空的")
        self.assertLess(idx818[-1], idx29[-1], "818 必须在 29 之前，先把列表给客户端")

        body = [b for c, _, b in packets if c == 818][-1]
        self.assertEqual(len(body), 197, "818 应是 7 条 × 28B + 1：%dB" % len(body))
        self.assertEqual(body[0], 7)
        ids = [int.from_bytes(body[1 + i * 28 + 2:1 + i * 28 + 4], "big") for i in range(7)]
        self.assertEqual(ids, [11108, 11109, 11110, 11111, 11112, 11113, 11114])
        slots = [body[1 + i * 28] for i in range(7)]
        self.assertEqual(slots, [1, 2, 3, 4, 5, 6, 7], "武器协议槽由 817/查表决定")

        pl = player.cur()
        self.assertEqual(sorted(pl.equip), [1, 2, 3, 4, 5, 6, 8])
        self.assertEqual(pl.equip[8], 11114)

    def test_gm_equip_pushes_818(self):
        """GM 穿/脱装备也属于「服务端单方面改装备」，必须带 818，否则面板不刷新。"""
        seen = []
        with patch.object(sv, "push_all", lambda cmds: seen.append(list(cmds))):
            ok, msg = sv.apply_gm("装备 11108")
        self.assertTrue(ok, msg)
        self.assertIn(818, seen[-1], "GM 装备后没推 818：%s" % seen[-1])
        with patch.object(sv, "push_all", lambda cmds: seen.append(list(cmds))):
            ok, msg = sv.apply_gm("脱装备 1")
        self.assertTrue(ok, msg)
        self.assertIn(818, seen[-1], "GM 脱装备后没推 818：%s" % seen[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
