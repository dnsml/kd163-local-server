# -*- coding: utf-8 -*-
"""宠物 / 妖灵回归：GM 发妖灵 -> 1410 与真服逐字节一致，947 走真服原包。

直接 import local_server3（不会启动监听，main 有 __main__ 保护）。
用临时备份还原存档，不污染玩家数据。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

STATE = os.path.join(HERE, "player_state.json")
BAK = STATE + ".pettest"

ok = 0
bad = 0


def ck(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  OK  %s %s" % (name, extra))
    else:
        bad += 1
        print("  FAIL %s %s" % (name, extra))


def main():
    if os.path.exists(STATE):
        shutil.copy2(STATE, BAK)
    try:
        import local_server3 as m
        p = m.pet_proto

        # 2026-11-26：妖灵归属改成按连接的角色（Player.spirit_bag/spirit_slot）
        m.cur().spirit_bag.clear()
        m.cur().spirit_slot.clear()

        r, msg = m.apply_gm("妖灵 三只")
        ck("GM 妖灵 三只", r, msg)
        ck("三只全上阵 1410 = cc8000", m.spirit_1410().hex() == "cc8000",
           m.spirit_1410().hex())
        ck("947 = 105B 真服原包", len(p.pack_947(m.spirit_strap())) == 105,
           "len=%d" % len(p.pack_947(m.spirit_strap())))

        m.spirit_unequip(3)
        ck("卸槽3 1410 = 10B(只剩2101)",
           m.spirit_1410().hex() == "cc800102cd0835030101",
           m.spirit_1410().hex())
        ck("卸槽3 后 947 无原包 -> 回 98B 空妖灵态",
           len(p.pack_947(m.spirit_strap())) == 98,
           "len=%d" % len(p.pack_947(m.spirit_strap())))

        m.spirit_unequip(1)
        ck("再卸槽1 1410 = 17B(1101+2101)",
           m.spirit_1410().hex() == "cc800200cd044d01010102cd0835030101",
           m.spirit_1410().hex())

        m.SPIRIT_SLOT.clear()
        ck("全卸 1410 = 真服 24B 原包",
           m.spirit_1410().hex() == "cc800300cd044d01010101cd100502010102cd0835030101",
           m.spirit_1410().hex())
        ck("全卸 947 = 98B 空妖灵态", len(p.pack_947(m.spirit_strap())) == 98)

        # 部位 / 选将
        ck("部位 1101=1", p.nature_of(1101) == 1, str(p.nature_of(1101)))
        ck("部位 4101=2", p.nature_of(4101) == 2, str(p.nature_of(4101)))
        ck("部位 2101=3", p.nature_of(2101) == 3, str(p.nature_of(2101)))
        ck("槽1 选到血·共工", m.spirit_pick_for_slot(1) == 1101,
           str(m.spirit_pick_for_slot(1)))
        ck("槽3 选到攻·白光虎", m.spirit_pick_for_slot(3) == 2101,
           str(m.spirit_pick_for_slot(3)))
        ck("1412 = [槽][id][00][00]",
           p.pack_1412(2, 4101).hex() == "02cd10050000",
           p.pack_1412(2, 4101).hex())

        # 上阵/顶替
        m.SPIRIT_SLOT.clear()
        old, put = m.spirit_equip(1, 1101)
        ck("上阵 1101 到槽1", put == 1101 and old is None, str((old, put)))
        old, put = m.spirit_equip(1, 4101)
        ck("命系 4101 不能进槽1", put is None, str((old, put)))
        m.spirit_equip(2, 4101)
        old, put = m.spirit_equip(2, 4101)
        ck("同只重复上阵不报错", put == 4101, str((old, put)))

        # 名字检索
        ck("名找 命·魍魉", p.find("命·魍魉")[:1] == [4101], str(p.find("命·魍魉")[:2]))
        ck("名找 血·共工", p.find("血·共工")[:1] == [1101], str(p.find("血·共工")[:2]))
        ck("名找 白光虎", p.find("攻·白光虎")[:1] == [2101], str(p.find("攻·白光虎")[:2]))

        # ---- 962 宠物栏：必须逐字节等于真服 20:52:31 那份 22B 原包 ----
        # relay.log 20:52:32 / 21:02:23 两次同字节，均为「宠物栏正常」现场：
        #   00 03 [4569][5c75][6445] 04 03   (n1=0 本次未领取, n2=3 已拥有)
        # 40B 的 n1=3 是 963 领取过程中的临时帧，做默认会让客户端认为"已领完"→界面全空。
        b962 = p.pack_962()
        ck("962 默认 22B", len(b962) == 22, "len=%d" % len(b962))
        ck("962 == 真服22B原包",
           b962.hex() == "0003ce000c456900ce000c5c7500ce000c6445000403",
           b962.hex())
        ck("962 空栏 == 真服4B原包", p.pack_962([], []).hex() == "00000403",
           p.pack_962([], []).hex())
        # 参数是 (第二组 pets, 第一组 out)；真服 10B 是「1 只已出战」= out 有 1 只
        ck("962 一只出战 == 真服10B原包",
           p.pack_962([], [804201]).hex() == "01ce000c456900000402",
           p.pack_962([], [804201]).hex())

        # ---- 964 出战确认：六档真服原包全对齐 ----
        G2 = [0x0c4569, 0x0c5c75, 0x0c6445]
        for pets, sel, extra, want_hex in (
            ([0x0c4569], 0x0c4569, [], "01ce000c45690001ce000c4569000002"),
            ([0x0c4569, 0x0c5c75], 0x0c5c75, [],
             "01ce000c5c750002ce000c456900ce000c5c75000001"),
            (G2, 0x0c6445, [],
             "01ce000c64450003ce000c456900ce000c5c7500ce000c6445000000"),
            ([0x0c6c15], 0x0c6c15, G2,
             "01ce000c6c150001ce000c6c150003ce000c456900ce000c5c7500ce000c64450002"),
            ([0x0c6c15, 0x0c7061], 0x0c7061, G2,
             "01ce000c70610002ce000c6c1500ce000c706100"
             "03ce000c456900ce000c5c7500ce000c64450001"),
            ([0x0c6c15, 0x0c7061, 0x0c4181], 0x0c4181, G2,
             "01ce000c41810003ce000c6c1500ce000c706100ce000c418100"
             "03ce000c456900ce000c5c7500ce000c64450000"),
        ):
            got = p.pack_964(pets, sel, extra).hex()
            ck("964 %d只 %dB 原包" % (len(pets), len(got) // 2), got == want_hex, got)

        print("\n结果：%d 通过 / %d 失败" % (ok, bad))
        return 0 if bad == 0 else 1
    finally:
        if os.path.exists(BAK):
            shutil.move(BAK, STATE)


if __name__ == "__main__":
    sys.exit(main())
