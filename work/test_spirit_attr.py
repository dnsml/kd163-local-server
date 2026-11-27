# -*- coding: utf-8 -*-
"""妖灵穿/脱对属性的影响自检（不动存档、不起服）。

用法：
    python work/test_spirit_attr.py

背景（2026-11-27 用户反馈）：
    「我的宠物妖灵穿和脱好像不加减宠物属性啊」——确实不加：
    pack_947 以前把模板头部数值段原样照抄，只动态拼了 8 个妖灵格，
    所以装上/卸下妖灵后，宠物面板里的气血、攻击、命中、战力一动不动。

真服证据（logs/relay.log，work/probe_947_attrs.py 解出来）：
    21:03:54 装 血·共工(53,550)  -> 947 idx6 2062→2612、战力 693→704
    21:03:58 装 攻·白光虎(5,90)  -> idx4 256→346、战力 704→740
    21:03:59 装 命·魍魉(11,25)   -> idx10 102→127、战力 740→752
    23:35:27 装 防·图腾王(7,120) -> idx8 186→306、战力 752→764
    23:35:29 装 闪·精卫鸟(9,25)  -> idx11 51→76、战力 764→777
    23:39:20 装 坚·醒狮(19,25)   -> idx9 51→76、战力 777→789
    23:39:22 装 暴·火烈鸟(13,25) -> idx5 51→76、战力 789→802
    23:39:24 装 绝·九尾(25,35)   -> idx12 0→35、战力 802→854
    21:56:48 卸 血·共工         -> idx6 2612→2062、战力 752→741
同时 29[25] / 28[31] 跟着同步（1246→1305 / 693→854），面板生命攻击防御不变。

断言：
1. 8 只逐个上阵，947 里对应字段与战力逐一命中真服数字
2. 卸下后按原值回退
3. pet_proto.spirit_power 与 947 战力增量一致
4. panel_attr("power") = 空身战力 + 妖灵战力（29 与 28/17 同源）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["KD_STATE_BACKEND"] = "json"

import local_server3 as sv          # noqa: E402
import pet_proto                    # noqa: E402
from player import ctx, new as new_player   # noqa: E402

# 槽位 -> 妖灵（槽位 = 面板格子，见 pet_proto.TYPE_SLOT）
SLOTS = [
    (1, 1101, "血·共工", "hp", 2612, 704),
    (2, 2101, "攻·白光虎", "atk", 346, 740),
    (4, 4101, "命·魍魉", "hit", 127, 752),     # 真服顺序：命第 3 个装
    (3, 3101, "防·图腾王", "def", 306, 764),
    (5, 5101, "闪·精卫鸟", "dodge", 76, 777),
    (7, 7101, "坚·醒狮", "ten", 76, 789),
    (6, 6101, "暴·火烈鸟", "crit", 76, 802),
    (8, 8101, "绝·九尾", "jue", 35, 854),
]
# 947 头部数值下标（0=等级）
IDX = {"atk": 4, "crit": 5, "hp": 6, "def": 8, "ten": 9,
       "hit": 10, "dodge": 11, "jue": 12, "power": 13}


def nums_of(b):
    n = b[1] - 0xA0
    j = b.find(b"\xcd\x27\x10")
    out, ok = pet_proto._head_nums(b[:j], 2 + n)
    if not ok:
        raise SystemExit("947 头部解析失败")
    return out


def main():
    pet_proto.load()
    fails = []

    def chk(cond, msg):
        if not cond:
            fails.append(msg)

    # ---- 1/2. 逐个上阵 + 卸下回退
    strap = {}
    for slot, iid, name, field, want, want_pw in SLOTS:
        strap[slot] = iid
        b = pet_proto.pack_947(strap)
        nums = nums_of(b)
        got, got_pw = nums[IDX[field]], nums[IDX["power"]]
        if got != want or got_pw != want_pw:
            fails.append("%s 上阵后 %s=%d 战力=%d（真服 %d / %d）"
                         % (name, field, got, got_pw, want, want_pw))
        # spirit_power 必须等于 947 的战力增量
        base_pw = 693
        if pet_proto.spirit_power(strap) != want_pw - base_pw:
            fails.append("%s spirit_power=%d（应为 %d）"
                         % (name, pet_proto.spirit_power(strap), want_pw - base_pw))

    # 卸血·共工：真服 21:56:48 是「血+攻+命 三只」卸掉血 → 气血 2612→2062、战力 752→741
    strap3 = {1: 1101, 2: 2101, 4: 4101}
    nums = nums_of(pet_proto.pack_947(strap3))
    chk(nums[IDX["power"]] == 752, "血+攻+命 战力=%d（真服 752）" % nums[IDX["power"]])
    strap3.pop(1)
    nums = nums_of(pet_proto.pack_947(strap3))
    chk(nums[IDX["hp"]] == 2062, "卸下共工后气血=%d（真服 2062）" % nums[IDX["hp"]])
    chk(nums[IDX["power"]] == 741, "卸下共工后战力=%d（真服 741）" % nums[IDX["power"]])
    # 八只全部卸下：回到模板基线
    nums = nums_of(pet_proto.pack_947({}))
    chk(nums[IDX["hp"]] == 2058, "空身上阵气血=%d（empty 模板 2058）" % nums[IDX["hp"]])
    chk(nums[IDX["power"]] == 691, "空身上阵战力=%d（empty 模板 691）" % nums[IDX["power"]])

    # ---- 3. 29[25] / 28[31] / 17 同源：panel_attr("power") 叠加妖灵战力
    pl = new_player()
    with ctx(pl):
        pl.level = 12
        pl.cls = 1
        pl.spirit_bag = {iid: {"idx": i} for i, (_, iid, *_1) in enumerate(SLOTS, 1)}
        pl.spirit_slot = {}
        base_power = sv.panel_attr("power")
        pl.spirit_slot = {slot: iid for slot, iid, *_1 in SLOTS}
        full_power = sv.panel_attr("power")
        sp = pet_proto.spirit_power(pl.spirit_slot)
        chk(sp == 854 - 693, "八只全装 spirit_power=%d（应 %d）" % (sp, 854 - 693))
        chk(full_power == base_power + sp,
            "29 战力 %d != 空身 %d + 妖灵 %d" % (full_power, base_power, sp))
        chk(sv.power_total() == full_power,
            "power_total()=%d 与 panel_attr('power')=%d 不同源"
            % (sv.power_total(), full_power))
        # 面板本体（生命/攻击/防御）必须**不**受妖灵影响（真服 29 实测没动）
        pl.spirit_slot = {}
        hp0, atk0 = sv.panel_attr("hp_max"), sv.panel_attr("attack_min")
        pl.spirit_slot = {slot: iid for slot, iid, *_1 in SLOTS}
        chk(sv.panel_attr("hp_max") == hp0,
            "妖灵不该改 29 生命（%d -> %d）" % (hp0, sv.panel_attr("hp_max")))
        chk(sv.panel_attr("attack_min") == atk0, "妖灵不该改 29 攻击下限")
        # 28 里的战力字段必须等于 29
        import grant
        tk = grant.tokenize(sv.build_28())
        chk(tk[31]["value"] == sv.power_total(),
            "28[31]=%d != 29[25]=%d" % (tk[31]["value"], sv.power_total()))

    if fails:
        print("FAIL %d 项：" % len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("OK 妖灵属性：8 系增量 / 卸下回退 / 29-28-17 同源 全部吻合真服抓包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
