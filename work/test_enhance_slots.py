# -*- coding: utf-8 -*-
"""强化记录的槽位口径自检（不动存档、不起服）。

用法：
    python work/test_enhance_slots.py

背景（2026-11-26）：
    槽 7 是**协议层**的武器字节（30 包 `cd2be3 08 01 01`），内部武器槽是 8。
    老存档是「武器挂 7」那个年代写的，enhance 里跟着留下了 enhance[7]。
    - sum_strengthen / sum_gift / addon_effect 都按「已穿装备」遍历再查 enhance[槽]，
      所以孤立记录不进属性，面板看不出问题；
    - 但 min_enh_lv() 原来直接遍历 enhance.values() 再拿条目数比装备件数，
      跟 equip_attr.min_level() 是两套算法，条目数一凑够就会按错的集合算
      「强化大师」档位（29.field11）。
    修复：读档时把 enhance[7] 搬到 8（武器等级不能丢），非法槽位清掉；
    min_enh_lv() 统一走 equip_attr.min_level()。

断言：
1. 老档 enhance{7:5, 8:6} → 读档后没有 7，8 仍是 6（8 权威，不叠加）
2. 老档只有 enhance{7:5}（武器没穿、等级留在协议槽）→ 搬到 8，等级不丢
3. 没穿但合法的槽（例如槽 6 的 +2）必须保留 —— 强化是按槽记的，卸下不该掉级
4. min_enh_lv() 与 equip_attr.min_level() 恒等（含首饰无强化 → 0 的情况）
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 本用例的 fixture 是临时 JSON 文件（patch 了 state_path），固定跑 JSON 后端；
# SQLite 后端的读档/落盘由 work/test_state_store.py 覆盖。
os.environ["KD_STATE_BACKEND"] = "json"

import local_server3 as sv                     # noqa: E402
import equip_attr                              # noqa: E402
from player import ctx, new as new_player      # noqa: E402

WPN = 12114        # 逍遥匕首（protocol slot 07）
ARM = 12113        # 护腕
RING = 10305       # 飞仙戒指

fails = []


def _load(save):
    """把 save 写成临时档，让 load_state 读它，返回载入后的 Player。"""
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(save, f)
    old = sv.state_path
    sv.state_path = lambda _pl=None: tmp
    pl = new_player()
    with ctx(pl):
        try:
            sv.load_state(pl)
        finally:
            sv.state_path = old
            os.unlink(tmp)
    return pl


def main():
    base = {"name": "槽位自检", "has_char": True, "level": 10, "ver": 2,
            "scene": 1101, "pos_x": 44, "pos_y": 9}

    # 1) 武器已在 8，enhance 里 7/8 都有 → 7 清掉，8 不动
    d = dict(base)
    d["equip"] = {"8": WPN}
    d["enhance"] = {"7": 5, "8": 6}
    pl = _load(d)
    if 7 in pl.enhance:
        fails.append("① enhance[7] 没被清掉：%r" % (pl.enhance,))
    if pl.enhance.get(8) != 6:
        fails.append("① enhance[8] 被改坏：%r" % (pl.enhance.get(8),))

    # 2) 武器没穿、等级只留在协议槽 7 → 搬到 8，不能丢
    d = dict(base)
    d["equip"] = {}
    d["enhance"] = {"7": 5}
    pl = _load(d)
    if 7 in pl.enhance:
        fails.append("② enhance[7] 还在：%r" % (pl.enhance,))
    if pl.enhance.get(8) != 5:
        fails.append("② 强化等级没搬到槽8：%r" % (pl.enhance,))

    # 3) 合法但当前空着的槽必须保留（强化按槽记，卸下不掉级）
    d = dict(base)
    d["equip"] = {"1": ARM}
    d["enhance"] = {"1": 3, "6": 2}
    pl = _load(d)
    if pl.enhance.get(6) != 2:
        fails.append("③ 空槽6 的 +2 被误删：%r" % (pl.enhance,))
    if pl.enhance.get(1) != 3:
        fails.append("③ 槽1 的 +3 丢了：%r" % (pl.enhance,))

    # 4) min_enh_lv 与 equip_attr.min_level 恒等（三个样本）
    samples = [
        ({"8": WPN, "1": ARM}, {"8": 6, "1": 4}),          # 都强化过
        ({"8": WPN, "9": RING}, {"8": 6}),                  # 首饰没强化 → 0
        ({"1": ARM}, {"1": 3, "6": 2}),                     # 空槽不参与
    ]
    for eq_ids, enh in samples:
        d = dict(base)
        d["equip"] = {str(k): v for k, v in eq_ids.items()}
        d["enhance"] = {str(k): v for k, v in enh.items()}
        pl = _load(d)
        with ctx(pl):
            a = sv.min_enh_lv()
            b = equip_attr.min_level(pl.equip, pl.enhance)
        if a != b:
            fails.append("④ 口径不一致 %r/%r：min_enh_lv=%r min_level=%r"
                         % (eq_ids, enh, a, b))

    print("槽7迁移 / 空槽保留 / min_enh_lv 口径：%s"
          % ("全部通过" if not fails else "FAIL"))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
