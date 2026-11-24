# -*- coding: utf-8 -*-
"""打印一套神装的 basic_effect / strengthen_N，并统计各属性 id 的合计。"""
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import equip_attr  # noqa: E402

EQ = os.path.join(ROOT, "mirror", "assets", "configure", "Equip.xml")
ITEM = os.path.join(ROOT, "mirror", "assets", "configure", "Item.xml")


def tag_of(iid):
    txt = open(EQ, encoding="utf-8", errors="replace").read()
    m = re.search(r'<item\b[^>]*id="%d"[^>]*>' % iid, txt)
    return m.group(0) if m else ""


def main():
    set_ids = [11108, 11109, 11110, 11111, 11112, 11113, 11114]
    tot = defaultdict(float)
    for iid in set_ids:
        tag = tag_of(iid)
        be = re.search(r'basic_effect="([^"]*)"', tag)
        print("\n%d basic_effect=%s" % (iid, be.group(1) if be else ""))
        if be and be.group(1):
            for pair in be.group(1).split(";"):
                k, v = pair.split(",")
                tot[int(k)] += float(v)
    print("\n基础属性合计:", dict(sorted(tot.items())))

    # 强化合计（+6 一套，武器+3）
    eq = {"1": 11108, "2": 11109, "3": 11110, "4": 11111,
          "5": 11112, "6": 11113, "7": 11114}
    enh = {"1": 6, "6": 5, "5": 6, "4": 6, "2": 6, "3": 6, "7": 3}
    agg = equip_attr.sum_strengthen(eq, enh)
    print("强化合计:", dict(sorted(agg.items())))
    allt = defaultdict(float)
    for d in (tot, agg):
        for k, v in d.items():
            allt[k] += v
    print("基础+强化:", dict(sorted(allt.items())))


if __name__ == "__main__":
    main()
