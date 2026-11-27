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


def _rv(b, i):
    """读 varint：<0x80 单字节 / cc+1B / cd+2B / ce+4B。返回 (值, 新下标)。"""
    c = b[i]
    if c < 0x80:
        return c, i + 1
    if c == 0xCC:
        return b[i + 1], i + 2
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    raise ValueError("bad varint 0x%02x at %d" % (c, i))


def _dec962(b):
    """[n1 库×(id,exp)][n2 栏×(id,exp)][04][free] -> (n1, n2, free)"""
    i = 0
    n1 = b[i]
    i += 1
    for _ in range(n1):
        _, i = _rv(b, i)
        i += 1
    n2 = b[i]
    i += 1
    for _ in range(n2):
        _, i = _rv(b, i)
        i += 1
    assert b[i] == 0x04, "962 容量字节必须是 04，实际 %02x" % b[i]
    i += 1
    free = b[i]
    assert i + 1 == len(b), "962 还有 %d 字节剩余" % (len(b) - i - 1)
    return n1, n2, free


def _dec964(b):
    """964 无 04 容量字节 -> (new_ids, ups, n1, n2, free)"""
    i = 0
    n_new = b[i]
    i += 1
    new_ids = []
    for _ in range(n_new):
        v, i = _rv(b, i)
        new_ids.append(v)
    n_up = b[i]
    i += 1
    ups = []
    for _ in range(n_up):
        v, i = _rv(b, i)
        ups.append([v, b[i]])
        i += 1
    n1 = b[i]
    i += 1
    for _ in range(n1):
        _, i = _rv(b, i)
        i += 1
    n2 = b[i]
    i += 1
    for _ in range(n2):
        _, i = _rv(b, i)
        i += 1
    free = b[i]
    assert i + 1 == len(b), "964 还有 %d 字节剩余" % (len(b) - i - 1)
    return new_ids, ups, n1, n2, free


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

        # ★ 2026-11-27：格子号＝面板位置（1血 2攻 3防 4命 5闪 6坚 7绝 8暴），
        #   GM 三只现在放 1(血1101)/2(攻2101)/4(命4101)。所以先卸「攻」=槽2。
        #   1410 每条 `[序号-1][id][部位][01][01]`，真服 3 只在包时 = 24B（见下）。
        m.spirit_unequip(2)
        ck("卸槽2 1410 = 10B(只剩攻·白光虎)",
           m.spirit_1410().hex() == "cc800100cd0835030101",
           m.spirit_1410().hex())
        ck("卸槽2 后 947 = 103B(还剩2只)",
           len(p.pack_947(m.spirit_strap())) == 103,
           "len=%d" % len(p.pack_947(m.spirit_strap())))

        m.spirit_unequip(1)
        ck("再卸槽1 1410 = 17B(1101+2101)",
           m.spirit_1410().hex() == "cc800200cd044d01010101cd0835030101",
           m.spirit_1410().hex())

        # ★ 2026-11-27：妖灵归属已改成按角色（Player.spirit_slot），旧的全局
        #   SPIRIT_SLOT 只剩 MAX_SPIRIT_SLOT 常量，这里改清当前角色。
        m.cur().spirit_slot.clear()
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
        ck("槽2 选到攻·白光虎", m.spirit_pick_for_slot(2) == 2101,
           str(m.spirit_pick_for_slot(2)))
        ck("1412 = [槽][id][00][00]",
           p.pack_1412(2, 4101).hex() == "02cd10050000",
           p.pack_1412(2, 4101).hex())

        # 上阵/顶替
        # ★ 2026-11-27：妖灵归属已改成按角色（Player.spirit_slot），旧的全局
        #   SPIRIT_SLOT 只剩 MAX_SPIRIT_SLOT 常量，这里改清当前角色。
        m.cur().spirit_slot.clear()
        old, put = m.spirit_equip(1, 1101)
        ck("上阵 1101 到槽1", put == 1101 and old is None, str((old, put)))
        old, put = m.spirit_equip(1, 4101)
        ck("命系 4101 不能进槽1", put is None, str((old, put)))
        m.spirit_equip(4, 4101)
        old, put = m.spirit_equip(4, 4101)
        ck("同只重复上阵不报错", put == 4101, str((old, put)))

        # 名字检索
        ck("名找 命·魍魉", p.find("命·魍魉")[:1] == [4101], str(p.find("命·魍魉")[:2]))
        ck("名找 血·共工", p.find("血·共工")[:1] == [1101], str(p.find("血·共工")[:2]))
        ck("名找 白光虎", p.find("攻·白光虎")[:1] == [2101], str(p.find("攻·白光虎")[:2]))

        # ---- 962 仙府探秘面板（2026-11-28 定案语义）----
        # [n1 秘籍库×(id,经验)][n2 技能栏×(id,经验)][04 容量][剩余免费]
        # ★ 8xxxxx 是 Skill.xml class=9 的仙宠技能 id，不是宠物形象 uid。
        ck("962 空档 == 真服 4B 00000403",
           p.pack_962([], [], 3).hex() == "00000403",
           p.pack_962([], [], 3).hex())
        # 20:52:32 / 21:02:23 两份同字节：库 0 条、栏 3 条、容量 4、免费 3
        ck("962 库0/栏3 == 真服 22B 原包",
           p.pack_962([], [[804201, 0], [810101, 0], [812101, 0]], 3).hex()
           == "0003ce000c456900ce000c5c7500ce000c6445000403",
           p.pack_962([], [[804201, 0], [810101, 0], [812101, 0]], 3).hex())
        # 01:06:44 抽到第一张后：库 4 条、栏 4 条、免费 2 → 4×6×2+4 = 52B
        _bag4 = [[804201, 0], [814201, 2], [808201, 0], [806201, 0]]
        _slot4 = [[810101, 0], [812101, 1], [803201, 2], [815201, 0]]
        _b52 = p.pack_962(_bag4, _slot4, 2)
        ck("962 库4/栏4/免费2 == 52B", len(_b52) == 52, "len=%d" % len(_b52))
        ck("962 52B 尾 2 字节 = 04 02", _b52[-2:].hex() == "0402",
           _b52[-2:].hex())
        ck("962 解回来 库4/栏4/免费2", _dec962(_b52) == (4, 4, 2),
           str(_dec962(_b52)))
        # 库3/栏3/免费3 → 3×6×2+4 = 40B（今天线上服刚抽完那档）
        _b40 = p.pack_962(_slot4[:3], _slot4[:3], 3)
        ck("962 库3/栏3 == 40B", len(_b40) == 40, "len=%d" % len(_b40))

        # ---- 964 抽奖结果：用真服 01:06:44 len=58 单抽现场做往返校验 ----
        # 01 ce000c4d39 | 00 | 04[804201:0][814201:2][808201:0][806201:0]
        #                | 04[810101:0][812101:1][803201:2][815201:0] | 02
        _b58 = p.pack_964([806201], [], _bag4, _slot4, 2)
        ck("964 单抽真服现场 = 58B", len(_b58) == 58, "len=%d" % len(_b58))
        _d = _dec964(_b58)
        ck("964 new=[806201]", _d[0] == [806201], str(_d[0]))
        ck("964 up=[] （新技能不升级）", _d[1] == [], str(_d[1]))
        ck("964 库4/栏4/免费2", (_d[2], _d[3], _d[4]) == (4, 4, 2), str(_d[2:]))
        # 01:06:46 len=64：抽到 814201 → 升级为 814202 经验 4
        _b64 = p.pack_964([814201], [[814202, 4]],
                          [[804201, 0], [814202, 4], [808201, 0], [806201, 0]],
                          _slot4, 1)
        ck("964 升级现场 = 64B", len(_b64) == 64, "len=%d" % len(_b64))
        _d = _dec964(_b64)
        ck("964 up=[814202:4]", _d[1] == [[814202, 4]], str(_d[1]))
        ck("964 升级后免费 1", _d[4] == 1, str(_d[4]))
        ck("964 无 04 容量字节（尾部只剩免费次数）",
           _b64[-1:] == b"\x01" and _b64[-2:-1] != b"\x04", _b64[-2:].hex())

        print("\n结果：%d 通过 / %d 失败" % (ok, bad))
        return 0 if bad == 0 else 1
    finally:
        if os.path.exists(BAK):
            shutil.move(BAK, STATE)


if __name__ == "__main__":
    sys.exit(main())
