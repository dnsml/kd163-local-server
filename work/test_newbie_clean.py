# -*- coding: utf-8 -*-
"""新号「不该有的东西」回归：称号 88、宠物 957/962、存档 pets 字段。

真服基准（logs/relay.log 2026-11-24 12:39:34，账号 954259122 刚建完角）：
  S->C cmd=88  len=19  d08800070000000000000000000000000000
  C->S cmd=956  -> 服务端**不回** 957（该号一只宠物都没有）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import player as P
import pet_proto

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK] %s" % name)
    else:
        fail += 1
        print("  [!!] %s %s" % (name, extra))


print("[1] build_88 按等级分档")
import local_server3 as L

p = P.cur()

# 直接改 threading.local 里的当前角色
p.acct = "900000009"
p.name = "新号"
p.level = 1
b1 = L.build_88()
check("Lv1 发 19B", len(b1) == 19, "实际 %d" % len(b1))
check("Lv1 与真服新号逐字节一致",
      b1.hex() == "d0880007000000000000000000000000000000", b1.hex())

p.level = 4
check("Lv4 仍是 19B", len(L.build_88()) == 19)

# ★ 2026-11-26 21:4x：88 现在只认存档里的 title（不再按等级自动挂称号）。
p.level = 5
check("Lv5 未佩戴称号仍是 19B 空表", len(L.build_88()) == 19, "实际 %d" % len(L.build_88()))
p.title = 1019
b5 = L.build_88()
check("佩戴称号后 21B", len(b5) == 21, "实际 %d" % len(b5))
check("尾巴是 cd+称号id", b5[-3:].hex() == "cd03fb", b5[-3:].hex())
p.title = 0
p.level = 45
check("Lv45 未佩戴称号仍是 19B", len(L.build_88()) == 19, "实际 %d" % len(L.build_88()))

print("\n[2] 962 仙府探秘面板（2026-11-28 新语义）")
# 962 = [n1 秘籍库×(id,exp)][n2 技能栏×(id,exp)][04 容量][剩余免费]，
# 8xxxxx 是 Skill.xml class=9 的仙宠技能 id，不是宠物形象 uid。
check("空档 -> 4B 00 00 04 03",
      pet_proto.pack_962([], [], 3) == b"\x00\x00\x04\x03",
      pet_proto.pack_962([], [], 3).hex())
b3 = pet_proto.pack_962([], pet_proto.DEFAULT_SKILLS, 3)
check("库0/栏3 -> 22B（真服原包）", len(b3) == 22, "实际 %d (%s)" % (len(b3), b3.hex()))
check("库0/栏3 逐字节 == 真服",
      b3.hex() == "0003ce000c456900ce000c5c7500ce000c6445000403", b3.hex())

print("\n[3] Player 默认与新号都不带宠物")
p2 = P.Player()
check("Player() 默认 pets 为空", p2.pets == [], str(p2.pets))
p3 = P.Player()
P.reset_newbie(p3)
check("reset_newbie 后 pets 为空", p3.pets == [], str(p3.pets))

print("\n[4] state_dict 带 pets 字段")
p = P.cur()
p.pets = [814101]
d = L.state_dict()
check("state_dict 里有 pets", "pets" in d)
check("pets 值正确", d.get("pets") == [814101], str(d.get("pets")))

print("\n通过 %d 项 / 失败 %d 项" % (ok, fail))
sys.exit(1 if fail else 0)
