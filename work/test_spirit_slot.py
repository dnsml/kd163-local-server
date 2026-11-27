# -*- coding: utf-8 -*-
"""妖灵「属性码 / 格子位置」双表回归（不起服、不动存档）。

两层概念，别混：
  · nature_of(iid) = **属性码**（1410 每条第 3 字节 / 947 每条第 3 字节）
      真服 17:39 抓包实证：1101→01 4101→02 2101→03 3101→04
                            5101→05 6101→07 7101→08 8101→09（06 空档）
  · slot_of(iid)   = **格子位置**（面板从左到右、从上到下）
      1血 2攻 3防 4命 5闪 6暴 7坚 8绝（19:3x 用户实测纠偏）

★ 2026-11-27 18:2x 真机实测：客户端按**格子位置**上色，属性码只决定
  「+气血 / +防御」那行小字。所以老代码直接把属性码当格子用 → 魍魉(命) 进了
  「攻」格、白光虎(攻) 进了「防」格、图腾王(防) 进了「命」格；而 暴(码7)/坚(码8)
  被塞进 7/8 号格（面板上是 绝/暴），点自己的格子发 1414 时位置对不上 →
  「装上去了脱不下来」。用户 001 号八系全装的现场反馈就是这三条。

用法：python work/test_spirit_slot.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pet_proto  # noqa: E402

# 抓包实证的属性码（1410 每条第 3 字节）
TYPE = {
    1101: 1,   # 血·共工
    4101: 2,   # 命·魍魉
    2101: 3,   # 攻·白光虎
    3101: 4,   # 防·图腾王（1411 04 -> 1412 cd0c1d）
    5101: 5,   # 闪·精卫鸟
    6101: 7,   # 暴·火烈鸟
    7101: 8,   # 坚·醒狮
    8101: 9,   # 绝·九尾
}
# 真机实测的格子位置（面板顺序）
CELL = {
    1101: 1,   # 血
    2101: 2,   # 攻
    3101: 3,   # 防
    4101: 4,   # 命
    5101: 5,   # 闪
    6101: 6,   # 暴（19:3x 用户实测：底排第一格是暴）
    7101: 7,   # 坚
    8101: 8,   # 绝
}


def main():
    bad = 0
    for iid in sorted(TYPE):
        t = pet_proto.nature_of(iid)
        c = pet_proto.slot_of(iid)
        ok = (t == TYPE[iid] and c == CELL[iid])
        bad += 0 if ok else 1
        print("[%s] %s %-8s 属性码=%d(要%d) 格子=%d%s(要%d)"
              % ("OK" if ok else "FAIL", iid, pet_proto.name_of(iid),
                 t, TYPE[iid], c, pet_proto.slot_name(c), CELL[iid]))

    # 同千位下的其它等级妖灵必须落到同一格
    for iid, cell in ((1401, 1), (2401, 2), (3401, 3), (4401, 4),
                      (5401, 5), (6401, 6), (7401, 7), (8401, 8)):
        got = pet_proto.slot_of(iid)
        ok = got == cell
        bad += 0 if ok else 1
        print("[%s] 同系 %s 格子 got=%d want=%d"
              % ("OK" if ok else "FAIL", iid, got, cell))

    # 八格各占一次，不能有重复（否则两只妖灵抢同一格）
    cells = [pet_proto.slot_of(i) for i in TYPE]
    ok = sorted(cells) == [1, 2, 3, 4, 5, 6, 7, 8]
    bad += 0 if ok else 1
    print("[%s] 八系占满 1~8 且不重复（实际 %s）"
          % ("OK" if ok else "FAIL", sorted(cells)))

    # 未知千位必须返回 0（调用方据此留在背包，不再猜槽位）
    for iid in (9801, 123456):
        got = (pet_proto.nature_of(iid), pet_proto.slot_of(iid))
        ok = got == (0, 0)
        bad += 0 if ok else 1
        print("[%s] 未知 %d 属性码/格子 got=%s want=(0, 0)"
              % ("OK" if ok else "FAIL", iid, got))

    print("失败 %d 项" % bad if bad else "全部通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
