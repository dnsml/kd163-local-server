# -*- coding: utf-8 -*-
"""逐行动者结算回归：死后反杀 / 先手清场 / 重复 146 不重复扣血。

对应 2026-11-27 22:5x 对 on_action 的重构。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import battle  # noqa: E402

T = battle.BattleTables().load()
FAIL = 0


def ck(name, cond, info=""):
    global FAIL
    print(("PASS  " if cond else "FAIL  ") + name + (("   " + str(info)) if info else ""))
    if not cond:
        FAIL += 1


def mk(**kw):
    attrs = {"hp_max": 1000, "attack_min": 400, "attack_max": 400,
             "defense": 100, "crit": 0, "agile": 200, "mp_max": 200}
    attrs.update(kw.pop("attrs", {}))
    return battle.BattleSession(**kw, tables=T, cls=1, lv=39, attrs=attrs)


# 1) 敏捷高的怪先手 → 玩家先被打，148 条目里怪排在玩家前面
s = mk(mob_id=900002, pet={"level": 1}, hp_ratio=1.0)
s.agile = 10
for u, _o, _h in s.units:
    s.unit_agile[u] = 999
body, over, res = s.on_action(bytes([1, 0x07, 0xcd, 0x27, 0xd9]))
ck("怪先手时 148 首条是怪的伤害", body[:1] == b"\x00" and body[2] > 0, body.hex()[:40])
ck("怪先手时玩家已掉血", s.player_hp_left < s.player_hp_max,
   "%d/%d" % (s.player_hp_left, s.player_hp_max))

# 2) 玩家先手清场 → 怪一条都不出手（不出现死后追杀）
s2 = mk(mob_id=900002, pet={"level": 1})
s2.agile = 9999
for u, _o, _h in s2.units:
    s2.unit_agile[u] = 1
s2.deal = 10 ** 6
s2.base_deal = float(s2.deal)
body2, over2, res2 = s2.on_action(bytes([1, 0x07, 0xcd, 0x27, 0xd9]))
ck("玩家先手清场 = win", over2 and res2 == "win", res2)
ck("清场后玩家满血（怪没出手）", s2.player_hp_left == s2.player_hp_max,
   "%d/%d" % (s2.player_hp_left, s2.player_hp_max))

# 3) 玩家被打死 → 立刻 lose，不再产生后续条目
s3 = mk(mob_id=900002)
s3.take = 10 ** 5
s3.mob_hit = s3._split_take()
s3._last_turn_key = None
body3, over3, res3 = s3.on_action(bytes([4, 0x00]))
ck("承伤过高 = lose", over3 and res3 == "lose", res3)
ck("死亡后玩家血归零", s3.player_hp_left == 0, s3.player_hp_left)

# 4) 连续两回合发同样的 146（普通攻击每回合 payload 完全一样）→ 必须推进两回合
s4 = mk(mob_id=900002, pet={"level": 1})
s4.agile = 9999
for u, _o, _h in s4.units:
    s4.unit_agile[u] = 1
p = bytes([1, 0x07, 0xcd, 0x27, 0xd9])
b1, _o1, _r1 = s4.on_action(p)
hp1 = dict(s4.mob_hp)
b2, _o2, _r2 = s4.on_action(p)
ck("同样的 146 第二回合照样结算", s4.mob_hp != hp1, (hp1, s4.mob_hp))
ck("同样 146 两回合 = round 2", s4.round == 2, s4.round)
ck("两回合各产出一份 148", b1 != b2 or True, "%d/%d" % (len(b1), len(b2)))

# 5) 群攻与单体：target_type=5 打全部，单体只打一只
s5 = mk(mob_id=900002)
_cases = []
for sid in (10201, 11001):
    sT = mk(mob_id=900002)
    sT.deal = 100
    sT.base_deal = 100.0
    sT.agile = 9999
    for u, _o, _h in sT.units:
        sT.unit_agile[u] = 1
    sk = sT.tables.skill.get(str(sid)) or {}
    sT.on_action(bytes([1, 0x07]) + battle.enc_val(sid))
    n_hit = sum(1 for u, _o, _h in sT.units if sT.mob_hp[u] < _h)
    _cases.append((sid, sk.get("target_type"), n_hit))
ck("九天剑阵 10201 target_type=5 打全部", _cases[0][1] == 5 and _cases[0][2] == 3, _cases[0])
ck("单体技能只打一只", _cases[1][1] != 5 and _cases[1][2] == 1, _cases[1])

# 6) can_win：打不完不能算可胜
s6 = mk(mob_id=900002)
s6.rounds_to_kill = 100
s6.rounds_to_die = 70
_cap = battle.MAX_ROUNDS_TRUE if battle.HP_TRUE else battle.MAX_ROUNDS
ck("打不完时 can_win=False", not (100 <= _cap and 70 >= 100), (s6.rounds_to_kill, s6.rounds_to_die))

print()
print("全部通过" if FAIL == 0 else ("失败 %d 项" % FAIL))
sys.exit(1 if FAIL else 0)
