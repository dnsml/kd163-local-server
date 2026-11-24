# -*- coding: utf-8 -*-
"""17 / 28 包头三连（性别 / 职业 / 等级）回归。

真服实证：
  主号 1784366「萌新」 Lv10 御剑（男）
    17: 0c a7"1784366" a6"萌新" 01 01 0a ...
  新号 954259122「夏侯健远」 Lv1 新手（男）
    28: 0c a9"954259122" ac"夏侯健远" 01 06 01 30 1f ...
=> rel0 = 性别(1男/0女)  rel1 = 职业  rel2 = 等级

历史 bug：把职业写进 rel0，rel1 保留模板值 → 新号交完任务收到 28 变「御剑」。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import player as P
import local_server3 as L

ok = 0
bad = 0


def check(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  [OK] %s" % name)
    else:
        bad += 1
        print("  [FAIL] %s %s" % (name, extra))


def rel_head(body):
    """按 sfield 结构算出头长，返回 (性别, 职业, 等级)。"""
    assert body[0] == 0x0C
    n1 = body[1] & 0x1F
    i = 2 + n1
    n2 = body[i] & 0x1F
    i += 1 + n2
    return body[i], body[i + 1], body[i + 2]


def setp(acct, name, sex, cls, level, exp=0):
    p = P.cur()
    p.acct = acct
    p.name = name
    p.sex = sex
    p.cls = cls
    p.level = level
    p.exp = exp
    p.exp_need = 200
    p.has_char = True
    return p


print("[1] 新号：男 / 新手6 / Lv1")
setp("954259122", "夏侯健远", 1, 6, 1, 0)
h17 = rel_head(L.build_17())
h28 = rel_head(L.build_28())
check("17 头 = (1,6,1)", h17 == (1, 6, 1), str(h17))
check("28 头 = (1,6,1)", h28 == (1, 6, 1), str(h28))

print("[2] 新号升到 Lv4 仍是新手、性别不变")
setp("17843661", "百里海熠", 1, 6, 4, 86)
h17 = rel_head(L.build_17())
h28 = rel_head(L.build_28())
check("17 头 = (1,6,4)", h17 == (1, 6, 4), str(h17))
check("28 头 = (1,6,4)", h28 == (1, 6, 4), str(h28))

print("[3] 女号：rel0 必须是 0")
setp("900000002", "小师妹", 2, 6, 1, 0)
h17 = rel_head(L.build_17())
h28 = rel_head(L.build_28())
check("17 头 = (0,6,1)", h17 == (0, 6, 1), str(h17))
check("28 头 = (0,6,1)", h28 == (0, 6, 1), str(h28))

print("[4] 主号回归：男 / 御剑1 / Lv45")
setp("1784366", "萌新", 1, 1, 45, 116313)
b17 = L.build_17()
b28 = L.build_28()
h17 = rel_head(b17)
h28 = rel_head(b28)
check("17 头 = (1,1,45)", h17 == (1, 1, 45), str(h17))
check("28 头 = (1,1,45)", h28 == (1, 1, 45), str(h28))
check("17 长度仍是模板长", len(b17) == len(L.TPL17_LIVE), "%d vs %d" % (len(b17), len(L.TPL17_LIVE)))
check("28 主号长度与模板差 ≤4（货币宽度会变）",
      abs(len(b28) - len(L.PLAYER_28_TPL)) <= 4,
      "%d vs %d" % (len(b28), len(L.PLAYER_28_TPL)))

print("[5] 转职后：御剑 Lv45 与职业 4 法尊都要写进 rel1")
setp("1784366", "萌新", 1, 4, 45, 116313)
h28 = rel_head(L.build_28())
check("28 头 = (1,4,45)", h28 == (1, 4, 45), str(h28))

print("\n通过 %d 项 / 失败 %d 项" % (ok, bad))
sys.exit(1 if bad else 0)
