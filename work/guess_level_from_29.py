# -*- coding: utf-8 -*-
"""用本地模板 29 反推「抓包时等级」和属性 id -> 面板字段的映射。

思路：模板 29 的每个字段 = Levelup[等级] 基础值 + 该字段对应的装备属性 id 合计。
已知 53=生命(f0)、51/52=攻击(f2/f3)、7=防御(f4)、57=f12..16，
把其它字段也按「装备合计」减掉，看剩下的像不像某一级的 Levelup 基础值。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import equip_attr  # noqa: E402

TPL29 = [1716, 159, 176, 219, 270, 33, 39, 30, 0, 28, 50, 4,
         200, 300, 200, 200, 200, 0, 0, 0, 0, 0, 0, 0, 240, 1050]


def load_levelup():
    path = os.path.join(ROOT, "res_cfg", "Levelup.xml")
    txt = open(path, encoding="utf-8", errors="replace").read()
    rows = {}
    for m in re.finditer(r"<item\b([^>]*)/>", txt):
        tag = m.group(1)
        cls = re.search(r'class="(\d+)"', tag)
        lv = re.search(r'level="(\d+)"', tag)
        if not cls or not lv:
            continue
        d = {}
        for k, v in re.findall(r'(\w+)="([^"]*)"', tag):
            try:
                d[k] = float(v)
            except ValueError:
                pass
        rows[(int(cls.group(1)), int(lv.group(1)))] = d
    return rows


def main():
    equip_attr.init()
    eq = {"1": 11108, "2": 11109, "3": 11110, "4": 11111,
          "5": 11112, "6": 11113, "7": 11114}
    enh = {"1": 6, "6": 5, "5": 6, "4": 6, "2": 6, "3": 6, "7": 3}
    agg = equip_attr.sum_strengthen(eq, enh)
    print("装备强化合计(按属性 id):", dict(sorted(agg.items())))

    lv = load_levelup()
    # 用 hp 定位等级：模板 f0 - 装备53 = Levelup.hp
    target_hp = TPL29[0] - agg.get(53, 0)
    target_atkmin = TPL29[2] - agg.get(51, 0)
    target_atkmax = TPL29[3] - agg.get(52, 0)
    target_def = TPL29[4] - agg.get(7, 0)
    print("减去装备后：hp=%.0f atk=%.0f-%.0f def=%.0f" %
          (target_hp, target_atkmin, target_atkmax, target_def))

    for (cls, level), d in sorted(lv.items()):
        if cls != 1:
            continue
        if (abs(d.get("hp_max", -1) - target_hp) <= 1
                or abs(d.get("attack_min", -1) - target_atkmin) <= 1):
            print("  匹配 lv%-3d hp=%.0f mp=%.0f atk=%.0f-%.0f def=%.0f hit=%.0f "
                  "dodge=%.0f crit=%.0f spirit=%.0f agile=%.0f"
                  % (level, d.get("hp_max", 0), d.get("mp_max", 0),
                     d.get("attack_min", 0), d.get("attack_max", 0),
                     d.get("defense", 0), d.get("hit", 0), d.get("dodge", 0),
                     d.get("crit", 0), d.get("spirit", 0), d.get("agile", 0)))


if __name__ == "__main__":
    main()
