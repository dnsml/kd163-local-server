# -*- coding: utf-8 -*-
"""把 Monster.xml / Skill.xml 的结构化摘要导出来。

用法：
  python work/dump_tables.py            控制台摘要
  python work/dump_tables.py --md       只打印写盘目录（半成品，未真正生成）
  真正的数值总表用 work/gen_tables_html.py → docs/reference/怪物与技能表.html
"""
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = os.path.join(ROOT, "res_cfg")

ITEM = re.compile(r'<item ([^>]*?)/>')


def attrs(block):
    return dict(re.findall(r'([A-Za-z_][\w]*)="([^"]*)"', block))


def load(name):
    p = os.path.join(CFG, name)
    with open(p, encoding="utf-8", errors="ignore") as f:
        s = f.read()
    out = []
    for m in ITEM.finditer(s):
        d = attrs(m.group(1))
        if d.get("id"):
            out.append(d)
    return out


mobs = load("Monster.xml")
skills = load("Skill.xml")

print("Monster.xml 条目", len(mobs))
print("字段", sorted({k for d in mobs for k in d}))
print()
print("monster_type 分布", Counter(d.get("monster_type") for d in mobs).most_common())
print("level 范围", min(int(d.get("level", 0) or 0) for d in mobs),
      max(int(d.get("level", 0) or 0) for d in mobs))
print()
print("Skill.xml 条目", len(skills))
print("字段", sorted({k for d in skills for k in d}))
print()
print("class 分布", sorted(Counter(d.get("class") for d in skills).items()))
print("target_type 分布", sorted(Counter(d.get("target_type") for d in skills).items()))
print()

# 例子
print("=== 前 3 条怪物 ===")
for d in mobs[:3]:
    print({k: d.get(k) for k in ("id", "name", "level", "hp_max", "monster_type", "round_talk")})
print()
print("=== class=1 前 6 条技能 ===")
n = 0
for d in skills:
    if d.get("class") == "1":
        print({k: d.get(k) for k in ("id", "name", "level", "target_type", "damage_percent",
                                     "consume_mp", "type", "category", "description")})
        n += 1
        if n >= 6:
            break

if "--md" in sys.argv:
    outdir = os.path.join(ROOT, "docs", "reference")
    os.makedirs(outdir, exist_ok=True)
    print("\n写 markdown ->", outdir)
