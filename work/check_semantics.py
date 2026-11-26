# -*- coding: utf-8 -*-
"""17 / 28 / 30 字段语义表自检（对应 docs/reference/字段语义表_17_28_30_20261126.md）。

只做纯逻辑断言，不起服、不连客户端、不写真实存档。

用法：
    python work/check_semantics.py

覆盖：
  A. 17 模板：长度 197、token 136、货币定宽 5/3/1/3、装备 off=110 步进 6、
     武器 off=162、翅膀 off=172
  B. 17 动态写：等级/经验/场景/坐标/货币/称号/战力 落到正确偏移，长度不随值变
  C. 28：41 token、货币 idx18~21、等级 idx5、战力 idx31 = panel 战力
  D. 30：长度定律 L = 68 + 2N + 6*hasMount（0/7/11 件 + 坐骑）
  E. 30 尾部：翅膀恒 20B；无坐骑时尾部恰好 24B
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import local_server3 as sv                     # noqa: E402
import grant                                   # noqa: E402
from player import Player, ctx, new as new_player  # noqa: E402


class MountedPlayer(Player):
    """测试专用：Player 目前还没有 mount 字段（坐骑系统未做），
    30 的坐骑块只能靠子类补一个 slot 来验长度定律。"""
    __slots__ = ("mount",)


fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def tk(payload):
    return grant.tokenize(payload)


def main():
    # ---------------- A：17 模板结构 ----------------
    t17 = sv.TPL17_LIVE
    k17 = tk(t17)
    check(len(t17) == 197, "A 17 模板长度 %d（应 197）" % len(t17))
    check(len(k17) == 136, "A 17 token 数 %d（应 136）" % len(k17))

    widths = {18: 5, 19: 3, 20: 1, 21: 3, 24: 3}
    for idx, w in widths.items():
        got = k17[idx]["end"] - k17[idx]["start"]
        check(got == w, "A 17 idx%d 宽度 %d（应 %d，货币定宽写的前提）"
              % (idx, got, w))

    for i, slot in enumerate((1, 2, 3, 4, 5, 6)):
        t = k17[67 + i * 4]
        check(t["start"] == 110 + i * 6, "A 17 装备槽%d off=%d（应 %d）"
              % (slot, t["start"], 110 + i * 6))
    check(k17[107]["start"] == 162, "A 17 武器块 off=%d（应 162）" % k17[91]["start"])
    check(k17[115]["start"] == 172, "A 17 翅膀块 off=%d（应 172）" % k17[107]["start"])
    check(t17[165] == 0x07, "A 17 武器槽字节 %02x（应 07）" % t17[165])

    # ---------------- B：17 动态写 ----------------
    pl = new_player()
    pl.acct = "1784366"
    pl.name = "萌新"
    pl.sex, pl.cls, pl.level = 1, 2, 37
    pl.exp = 1234
    pl.scene, pl.pos_x, pl.pos_y = 21202, 35, 5
    pl.coin_bound[0] = 298120
    pl.bind_yuanbao[0] = 4050
    pl.tongqian[0] = 0
    pl.yuanbao[0] = 1749
    pl.vip_level[0] = 3
    pl.title = 1019
    pl.wing[0] = 41002
    pl.equip = {1: 11108, 2: 11109, 3: 11110, 4: 11111,
                5: 11112, 6: 11113, 8: 11114}
    with ctx(pl):
        b17 = sv.build_17()
        k = tk(b17)
    check(len(b17) == 197, "B 17 动态写后长度 %d（必须仍 197）" % len(b17))
    check(len(k) == 136, "B 17 动态写后 token %d（应 136，定宽不许变）" % len(k))
    check((b17[16], b17[17], b17[18]) == (1, 2, 37),
          "B 17 性别/职业/等级 = %r（应 1/2/37）" % ((b17[16], b17[17], b17[18]),))
    check(int.from_bytes(b17[23:25], "big") == 1234, "B 17 经验写错")
    check(int.from_bytes(b17[26:28], "big") == 21202, "B 17 场景写错")
    check((b17[28], b17[29]) == (35, 5), "B 17 坐标写错")
    check(int.from_bytes(b17[39:43], "big") == 298120, "B 17 绑铜写错")
    check(int.from_bytes(b17[44:46], "big") == 4050, "B 17 绑元写错")
    check(b17[46] == 0, "B 17 铜钱写错")
    check(int.from_bytes(b17[48:50], "big") == 1749, "B 17 元宝写错")
    check(b17[50] == 3, "B 17 VIP 写错")
    check(int.from_bytes(b17[53:55], "big") == 1019, "B 17 称号写错")
    check(int.from_bytes(b17[162 + 1:162 + 3], "big") == 11114, "B 17 武器 id 写错")

    # 长账号/昵称：长度只随 shift 变，结构不变
    pl2 = new_player()
    pl2.acct = "{{ACCOUNT_abcdefgh}}"
    pl2.name = "很长的名字测试"
    pl2.level = 1
    with ctx(pl2):
        b17b = sv.build_17()
    shift = (len(pl2.acct) + 1 - 8) + (len(pl2.name.encode("utf-8")) + 1 - 7)
    check(len(b17b) == 197 + shift,
          "B 17 shift 后长度 %d（应 %d）" % (len(b17b), 197 + shift))

    # ---------------- C：28 ----------------
    k28 = tk(sv.PLAYER_28_TPL)
    check(len(sv.PLAYER_28_TPL) == 73, "C 28 模板长度 %d（应 73）" % len(sv.PLAYER_28_TPL))
    check(len(k28) == 41, "C 28 token %d（应 41）" % len(k28))

    with ctx(pl):
        b28 = sv.build_28()
        power = sv.equip_attr.power_of(sv.panel_attr("hp_max"))
    k = tk(b28)
    check(len(k) == 41, "C 28 动态写后 token %d（应 41）" % len(k))
    check(k[5]["value"] == 37, "C 28 idx5 等级 = %r（应 37）" % (k[5]["value"],))
    want = {18: 298120, 19: 4050, 20: 0, 21: 1749, 22: 3}
    for idx, v in want.items():
        check(k[idx]["value"] == v,
              "C 28 idx%d = %r（应 %d）" % (idx, k[idx]["value"], v))
    check(k[31]["value"] == power,
          "C 28 idx31 战力 %r（应 %d，与 29[25] 同源）"
          % (k[31]["value"], power))

    # ---------------- D/E：30 长度定律 ----------------
    def len30(p, mount=0):
        p.mount = [mount]
        with ctx(p):
            return sv.build_30()

    empty = MountedPlayer()
    b = len30(empty, 0)
    check(len(b) == 68, "D 30 全脱无坐骑 %dB（应 68 = 44+4+20）" % len(b))
    check(b[-24:] == b"\x00" * 24, "E 30 全脱尾部应 24B 全 0")

    story = MountedPlayer()
    story.cls = 1
    with ctx(story):
        sv.equip_story_set(story)
    b = len30(story, 0)
    check(len(b) == 82, "D 30 剧情神装 7 件无坐骑 %dB（应 82）" % len(b))

    full = MountedPlayer()
    full.equip = {1: 11322, 2: 11323, 3: 11324, 4: 11325,
                  5: 11326, 6: 11327, 8: 11328,
                  9: 10305, 10: 10305, 11: 10306, 12: 10306}
    b = len30(full, 10035)
    check(len(b) == 96, "D 30 11 件 + 坐骑 %dB（应 96）" % len(b))
    check(b[-6:] == b"\xcd\x27\x33\x0d\x01\x01",
          "E 30 坐骑块 = %s（应 cd2733 0d 01 01）" % b[-6:].hex())

    # 翅膀块恒 20B：全脱时 66..86
    b = len30(empty, 0)
    check(b[44:48] == b"\x00\x00\x00\x00", "E 30 第 12 块不是 4B 0")
    check(len(b[48:68]) == 20, "E 30 翅膀块长度 %d（应 20）" % len(b[48:68]))

    # 无坐骑时整包不含坐骑块：尾部到翅膀结束就没了
    check(len(b) == 68, "E 30 无坐骑不应再补 6B（当前 %dB）" % len(b))

    if fails:
        print("失败 %d 项：" % len(fails))
        for f in fails:
            print("  FAIL ", f)
        return 1
    print("17 结构 / 17 动态写 / 28 字段 / 30 长度定律：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
