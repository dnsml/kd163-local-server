# -*- coding: utf-8 -*-
"""宠物归属回归：真实 handle_game 收发路径，存档/备份全部隔离到临时目录。

运行：python -X utf8 work/test_pet_ownership.py
覆盖建角、登录初始化、未解锁操作、后台推送、旧档迁移及 400702 奖励/重登。
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.object(sys, "argv", [sys.argv[0]]):
    import local_server3 as sv
import player
import pet_proto


class Connection:
    """仅替换 socket 传输；帧解析和所有业务分支仍执行服务端原代码。"""
    def __init__(self, requests=()):
        self.incoming = b"".join(sv.pack(*r) for r in requests)
        self.packets = []

    def settimeout(self, timeout):
        pass

    def recv(self, size):
        data, self.incoming = self.incoming[:size], self.incoming[size:]
        return data

    def sendall(self, data):
        while data:
            total, cmd, seq = struct.unpack_from(">HHI", data)
            end = total + 2
            if end > len(data):
                raise AssertionError("服务端发送了不完整的协议帧")
            self.packets.append((cmd, seq, data[8:end]))
            data = data[end:]

    def close(self):
        pass


class PetOwnershipTest(unittest.TestCase):
    ACCT = "99002601"
    TOKEN = b"t" * 43

    @classmethod
    def setUpClass(cls):
        # 这套 fixture 是老 JSON 存档（patch 了 state_path 指向临时文件），
        # 所以显式跑 JSON 后端；SQLite 后端的完整读档/落盘由 test_state_store.py 覆盖。
        os.environ["KD_STATE_BACKEND"] = "json"
        pet_proto.load()
        sv._load_equip_str()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("KD_STATE_BACKEND", None)

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="kd_pet_")))
        self.logs = []
        for key, value in {
            "state_path": lambda pl=None: str(self.path((pl or player.cur()).acct)),
            "BACKUP_DIR": str(self.tmp / "backups"),
            "log": self.logs.append,
            "write_status": lambda: None,
            "FRESH_ACCT": "",
            "AUTO_NEWBIE": False,
        }.items():
            self.stack.enter_context(patch.object(sv, key, value))
        player.new()

    def path(self, acct):
        return self.tmp / ("player_state_%s.json" % acct)

    def seed(self, acct=None, **fields):
        acct = acct or self.ACCT
        d = {"name": "宠物验收", "has_char": True, "level": 1,
             "class": 6, "need": 200, "ver": 2, "pets": []}
        d.update(fields)
        self.path(acct).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def login(self, acct=None):
        body = b"\x01\xcc\x97\x02" + sv.sfield(acct or self.ACCT)
        return 17, 2, body + b"\xda\x00\x2b" + self.TOKEN

    def run_session(self, *requests):
        conn = Connection(requests)
        sv.handle_game(conn, 990026)
        self.assertFalse([s for s in self.logs if "异常" in s], self.logs)
        return conn.packets

    def assert_no_pet(self, packets):
        forbidden = [(c, q, len(b)) for c, q, b in packets if c in (947, 957, 964)]
        self.assertEqual(forbidden, [], "未获得宠物却下发了详情/出战数据")
        for c, q, b in packets:
            if c == 962:
                self.assertEqual(b.hex(), "00000403")

    def test_create_and_login_initialization_have_no_pet(self):
        body = (b"\x01\x01\x02" + sv.sfield(self.ACCT)
                + b"\xda\x00\x2b" + self.TOKEN + sv.sfield("宠物验收") + b"\x01")
        packets = self.run_session(
            self.login(), (20, 7, body), (956, 9, b""),
            (1409, 15, b""), (1265, 40, b""), (961, 41, b""), (946, 42, b"\x01"))
        self.assertTrue(any(c == 20 for c, _, _ in packets))
        self.assert_no_pet(packets)
        self.assertEqual(json.loads(self.path(self.ACCT).read_text(encoding="utf-8"))["pets"], [])
        self.assert_no_pet(self.run_session(self.login(), (956, 9, b""), (946, 42, b"\x01")))

    def test_locked_selection_cannot_replay_captured_panda(self):
        self.seed()
        for cmd, payload in [(963, b"\x01"), (965, b"\xce\x00\x0c\x6c\x15")]:
            with self.subTest(cmd=cmd):
                self.assert_no_pet(self.run_session(self.login(), (cmd, 80, payload)))
                self.assertEqual(player.cur().pets, [])

    def test_background_detail_push_respects_target_player(self):
        donor = player.cur()
        donor.pets = [10000]
        target = player.Player()
        conn = Connection()
        with patch.dict(sv.CONN_PLAYERS, {990026: target}):
            sv.send_push(conn, 990026, 947)
        self.assert_no_pet(conn.packets)
        self.assertIs(player.cur(), donor)
        # 已拥有宠物的角色仍能收到原来的详情。
        sv.send_push(conn, -1, 947)
        self.assertTrue(any(c == 947 for c, _, _ in conn.packets))

    def test_legacy_save_does_not_seed_captured_main_account_pets(self):
        for acct in [sv.PRIMARY_ACCOUNT, self.ACCT]:
            for completed in [False, True]:
                with self.subTest(acct=acct, completed=completed):
                    self.seed(acct, pets=None, quest_done=[400702] if completed else [])
                    self.run_session(self.login(acct))
                    self.assertEqual(player.cur().pets, [10000] if completed else [])

    def test_missing_pet_field_has_same_quest_based_migration(self):
        self.seed(sv.PRIMARY_ACCOUNT)
        path = self.path(sv.PRIMARY_ACCOUNT)
        d = json.loads(path.read_text(encoding="utf-8"))
        del d["pets"]
        path.write_text(json.dumps(d), encoding="utf-8")
        self.run_session(self.login(sv.PRIMARY_ACCOUNT))
        self.assertEqual(player.cur().pets, [])

    def test_replay_batch_cannot_inject_pet_packets(self):
        self.seed()
        detail = pet_proto.pack_947({})
        replay = [(17, 2, sv.TPL17_LIVE), (947, 0, detail), (957, 0, detail),
                  (964, 0, pet_proto.pack_964()), (962, 0, pet_proto.pack_962()),
                  (982, 3, sv.TPL[982])]
        with patch.object(sv, "SEQ", replay):
            packets = self.run_session(self.login(), (981, 3, b""))
        self.assertTrue(any(c == 982 for c, _, _ in packets))
        self.assert_no_pet(packets)

    def test_spirit_actions_without_pet_do_not_inject_details(self):
        self.seed(spirit_bag={"1101": {"idx": 0, "slot": 0}})
        packets = self.run_session(self.login(), (1411, 80, b"\x01"),
                                   (1413, 81, b"\x00"), (1414, 82, b"\x01"))
        self.assert_no_pet(packets)
        self.assertEqual(player.cur().spirit_slot, {})

    def test_premature_pet_quest_submission_does_not_award(self):
        quest = b"\xce" + (400702).to_bytes(4, "big")
        for active, ready in [(0, False), (400101, True), (400702, False)]:
            with self.subTest(active=active, ready=ready):
                self.seed(quest_active=active, quest_ready=ready)
                packets = self.run_session(self.login(), (214, 80, quest))
                self.assert_no_pet(packets)
                self.assertEqual(player.cur().pets, [])
                self.assertNotIn(400702, player.cur().done_quests)
                self.assertEqual(player.cur().quest_active, active)
                self.assertEqual(player.cur().level, 1)
                self.assertEqual(player.cur().coin_bound[0], 0)

    def test_explicit_pet_inventory_is_preserved(self):
        for pets in [[], [10000], pet_proto.PET_UIDS]:
            with self.subTest(pets=pets):
                self.seed(pets=pets, quest_done=[400702])
                self.run_session(self.login())
                self.assertEqual(player.cur().pets, pets)

    def test_quest_award_once_and_relogin_keeps_pet(self):
        self.seed(level=7, quest_active=400702, quest_ready=False, quest_done=[400701])
        quest = b"\xce" + (400702).to_bytes(4, "big")
        gather = b"\xce" + (102004).to_bytes(4, "big")
        packets = self.run_session(self.login(), (211, 78, quest), (124, 79, gather),
                                   (214, 80, quest), (214, 81, quest))
        awards = [b for c, _, b in packets if c == 957]
        self.assertEqual(len(awards), 1)
        self.assertIn("功夫熊猫".encode(), awards[0])
        self.assertEqual(player.cur().pets, [10000])
        saved = json.loads(self.path(self.ACCT).read_text(encoding="utf-8"))
        self.assertEqual(saved["pets"], [10000])
        self.assertIn(400702, saved["quest_done"])
        packets = self.run_session(self.login(), (956, 9, b""), (946, 80, b"\x01"))
        self.assertTrue(any(c == 957 for c, _, _ in packets))
        self.assertTrue(any(c == 947 for c, _, _ in packets))


if __name__ == "__main__":
    unittest.main(verbosity=2)
