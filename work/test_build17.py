# -*- coding: utf-8 -*-
"""回归：17 包动态昵称/账号 + 建角写档。

跑法：
    python work/test_build17.py

盯三件事：
1. 主号（7 位账号 + 「萌新」6 字节）shift 必须是 0，包长仍是 197，与历史逐字节一致；
2. 昵称变短/变长时，包长按 shift 变化，职业/等级字节跟着平移，不能错位；
3. create_character() 能落出 Lv1 存档，且 save_state 的三重闸门不会拦它。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
# 本用例靠「建角后 player_state_<账号>.json 是否生成」来判断落档，
# 属于老 JSON 后端的格式测试；SQLite 后端由 test_state_store.py 覆盖。
os.environ["KD_STATE_BACKEND"] = "json"

import player as player_mod
import local_server3 as srv

OK = [0, 0]


def check(name, cond, extra=""):
    if cond:
        OK[0] += 1
        print("  [OK] %s %s" % (name, extra))
    else:
        OK[1] += 1
        print("  [NG] %s %s" % (name, extra))


def with_player(name, acct, p=None):
    p = p or player_mod.Player()
    p.acct = acct
    p.name = name
    return p


print("== 1. 主号 17 包 ==")
pl = with_player("萌新", "1784366")
with player_mod.ctx(pl):
    b = srv.build_17()
check("包长 197", len(b) == 197, "len=%d" % len(b))
check("账号字段", b[1:9] == b"\xa7" + b"1784366", b[1:9].hex())
check("昵称字段", b[9:16] == b"\xa6" + "萌新".encode("utf-8"), b[9:16].hex())
check("性别 off16", b[16] == (1 if pl.sex == 1 else 0), "sex=%d b[16]=%d" % (pl.sex, b[16]))
check("职业 off17", b[17] == pl.cls, "cls=%d b[17]=%d" % (pl.cls, b[17]))
check("等级 off18", b[18] == pl.level, "lv=%d b[18]=%d" % (pl.level, b[18]))
check("尾部结束符", b[-1] == 0xA0, hex(b[-1]))

print("== 2. 短昵称（ASCII 2 字节）==")
pl2 = with_player("ab", "1784366")
with player_mod.ctx(pl2):
    b2 = srv.build_17()
shift = len(b2) - 197
check("长度按 shift 收缩", shift == -4, "len=%d shift=%d" % (len(b2), shift))
check("账号字段不动", b2[1:9] == b"\xa7" + b"1784366", b2[1:9].hex())
check("昵称内容", b2[9:12] == b"\xa2" + b"ab", b2[9:12].hex())
check("职业平移到 17+shift", b2[17 + shift] == pl2.cls,
      "b[%d]=%d" % (17 + shift, b2[17 + shift]))
check("等级平移到 18+shift", b2[18 + shift] == pl2.level,
      "b[%d]=%d" % (18 + shift, b2[18 + shift]))
check("结束符仍在末尾", b2[-1] == 0xA0, hex(b2[-1]))

print("== 3. 长昵称（10 字 = 30 字节）==")
long_name = "一二三四五六七八九十"
pl3 = with_player(long_name, "1784366")
with player_mod.ctx(pl3):
    b3 = srv.build_17()
nb = len(long_name.encode("utf-8"))
check("长度按 shift 增长", len(b3) == 197 + (nb - 6), "len=%d" % len(b3))
check("昵称字段头 0x%02x" % (0xA0 | nb), b3[9] == (0xA0 | nb), hex(b3[9]))
check("昵称内容", b3[10:10 + nb] == long_name.encode("utf-8"),
      b3[10:10 + nb].decode("utf-8", "replace"))
check("结束符仍在末尾", b3[-1] == 0xA0, hex(b3[-1]))

print("== 4. 151 开战包动态昵称 ==")
nb2 = pl2.name.encode("utf-8")
with player_mod.ctx(pl3 if False else pl2):
    ob = srv.battle_open_body()
check("头两字节", bytes(ob[:2]) == b"\x01\x0c", bytes(ob[:2]).hex())
check("职业字节", ob[2] == pl2.cls, hex(ob[2]))
check("昵称长度标记", ob[3] == (0xA0 | len(nb2)), hex(ob[3]))
check("昵称内容", bytes(ob[4:4 + len(nb2)]) == nb2,
      bytes(ob[4:4 + len(nb2)]).decode("utf-8", "replace"))
check("整包 = 头 + 尾巴 25B",
      len(ob) == 4 + len(nb2) + len(bytes.fromhex(srv.BATTLE_OPEN_TAIL)),
      "len=%d" % len(ob))
# 最强的一条：昵称回到「萌新」时必须与真服原包逐字节一致
ORIG151 = bytes.fromhex(
    "010c06a6e8908ce696b001004c4c2f2f00a00000000001"
    "ce0001b1fd50500000020107")
pl4 = with_player("萌新", "1784366")
with player_mod.ctx(pl4):
    ob_ref = bytes(srv.battle_open_body())
check("昵称=萌新 时与真服 151 原包逐字节一致", ob_ref == ORIG151,
      "%d vs %d 字节" % (len(ob_ref), len(ORIG151)))

print("== 5. 建角落档 ==")
TEST_ACCT = "__unit_test_char__"
path = os.path.join(HERE, "player_state_%s.json" % TEST_ACCT)
if os.path.exists(path):
    os.remove(path)
tp = player_mod.Player()
tp.acct = TEST_ACCT
tp.authed = True
with player_mod.ctx(tp):
    ok, msg = srv.create_character("测试角色", 2)
check("建角返回成功", ok, msg)
check("存档文件已生成", os.path.exists(path))
with player_mod.ctx(tp):
    srv.load_state(tp)
check("等级 1", tp.level == 1, "lv=%d" % tp.level)
check("未转职 cls=6", tp.cls == 6, "cls=%d" % tp.cls)
check("昵称回读", tp.name == "测试角色", tp.name)
check("性别=2", tp.sex == 2, "sex=%d" % tp.sex)
check("has_char", tp.has_char is True)
check("背包是新人三件套", sorted(v[0] for v in tp.bag.values()) == [20190, 20432, 21001],
      "%d 格 %s" % (len(tp.bag), [v[0] for v in tp.bag.values()]))
check("钱 0", tp.bind_yuanbao[0] == 0 and tp.tongqian[0] == 0,
      "绑元=%d 铜钱=%d" % (tp.bind_yuanbao[0], tp.tongqian[0]))

# 收尾：别把测试档留在目录里
for p in (path, path + ".tmp"):
    try:
        os.remove(p)
    except OSError:
        pass

print("\n通过 %d 项 / 失败 %d 项" % (OK[0], OK[1]))
sys.exit(1 if OK[1] else 0)
