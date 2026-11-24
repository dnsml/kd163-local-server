# -*- coding: utf-8 -*-
"""统计 Equip.xml 里出现的属性 id 与量级，用来推断 id 语义。"""
import os
import re
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
EQ = os.path.join(os.path.dirname(HERE), "mirror", "assets", "configure", "Equip.xml")


def main():
    txt = open(EQ, encoding="utf-8", errors="replace").read()
    cnt = Counter()
    samples = defaultdict(list)
    for m in re.finditer(r'<item\b[^>]*>', txt):
        tag = m.group(0)
        for attr in ("basic_effect", "strengthen_1", "strengthen_6"):
            mm = re.search(attr + r'="([^"]*)"', tag)
            if not mm or not mm.group(1):
                continue
            for pair in mm.group(1).split(";"):
                if "," not in pair:
                    continue
                k, v = pair.split(",", 1)
                try:
                    k, v = int(k), float(v)
                except ValueError:
                    continue
                cnt[k] += 1
                if len(samples[k]) < 3 and v:
                    samples[k].append(round(v, 2))
    for k in sorted(cnt):
        print("%-4d 次数%-5d 样例 %s" % (k, cnt[k], samples[k]))


if __name__ == "__main__":
    main()
