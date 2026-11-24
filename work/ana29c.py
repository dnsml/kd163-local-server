#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把真服 818(装备栏)/29(属性) 按时间顺序配对，反推 29 每个字段的算法。

关键点（上一版 ana29b.py 错了）：
  不只要「7 件全量 818」，强化过程中真服会回【单槽 818】(len=29)，
  必须把单槽更新也吃进装备状态，才能得到每次强化后的真实全身属性。

用法: python work/ana29c.py [起 止]
"""
import re
import sys
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "logs", "relay.log")
EQUIP_XML = os.path.join(HERE, "mirror", "assets", "configure", "Equip.xml")

REC = re.compile(r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")


def vread(b, i):
    c = b[i]
    if c == 0xCC:
        return b[i + 1], i + 2
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    return c, i + 1


def tokens(b):
    out, i = [], 0
    while i < len(b):
        v, i = vread(b, i)
        out.append(v)
    return out


# ---------- Equip.xml ----------
def load_equip_xml():
    import xml.etree.ElementTree as ET
    tree = ET.parse(EQUIP_XML)
    str_tbl, gift_tbl, suite = {}, {}, {}
    for it in tree.getroot():
        iid = int(it.get("id"))
        for lv in range(1, 13):
            raw = it.get("strengthen_%d" % lv)
            if raw:
                d = {}
                for p in raw.split(";"):
                    if "," in p:
                        k, v = p.split(",", 1)
                        d[int(k)] = float(v)
                str_tbl[(iid, lv)] = d
            g = it.get("strengthen_%d_gift" % lv)
            if g:
                d = {}
                for p in g.split(";"):
                    if "," in p:
                        k, v = p.split(",", 1)
                        d[int(k)] = float(v)
                gift_tbl[(iid, lv)] = d
        suite[iid] = int(it.get("suite_id") or 0)
    return str_tbl, gift_tbl, suite


STR, GIFT, SUITE = load_equip_xml()


def main():
    t0 = sys.argv[1] if len(sys.argv) > 1 else "00:00:00"
    t1 = sys.argv[2] if len(sys.argv) > 2 else "99:99:99"
    eq = {}          # slot -> [iid, lv]
    rows = []
    for line in open(LOG, encoding="utf-8", errors="replace"):
        m = REC.search(line)
        if not m:
            continue
        t, d, c, _s, ln, hx = m.groups()
        if not (t0 <= t <= t1):
            continue
        if d != "S->C":
            continue
        c, ln = int(c), int(ln)
        if ln != len(hx) // 2:
            continue
        b = bytes.fromhex(hx)
        if c == 818 and b:
            n = b[0]
            if len(b) == 1 + 28 * n:
                for k in range(n):
                    rec = b[1 + 28 * k: 1 + 28 * (k + 1)]
                    slot = rec[0]
                    iid, _ = vread(rec, 1)
                    lv = rec[6]
                    eq[slot] = [iid, lv]
        elif c == 29:
            tk = tokens(b)
            attrs = {}
            for iid, lv in eq.values():
                if lv <= 0:
                    continue
                for a, v in STR.get((iid, min(lv, 12)), {}).items():
                    attrs[a] = attrs.get(a, 0) + v
                for a, v in GIFT.get((iid, min(lv, 12)), {}).items():
                    attrs["g%d" % a] = attrs.get("g%d" % a, 0) + v
            rows.append((t, list(tk), dict(attrs),
                         {s: tuple(v) for s, v in eq.items()}))
    print("样本 %d 组" % len(rows))
    if not rows:
        return
    keys = sorted({k for r in rows for k in r[2]})
    print("装备属性键:", keys)
    print("装备状态(最后):", rows[-1][3])
    print()
    for idx in range(len(rows[-1][1])):
        print("field[%2d]" % idx, [r[1][idx] if idx < len(r[1]) else None for r in rows])
    print()
    for idx in range(len(rows[-1][1])):
        for k in keys:
            vals = [r[1][idx] if idx < len(r[1]) else 0 for r in rows]
            ks = [r[2].get(k, 0) for r in rows]
            diff = [v - x for v, x in zip(vals, ks)]
            if max(diff) - min(diff) <= 1 and max(diff) != 0:
                print("field[%d] = %s + %g   (恒定, n=%d)" % (idx, k, diff[0], len(rows)))
    print()
    print("== 逐行 ==")
    for t, tk, a, e in rows:
        print(t, tk, "|", {k: round(v, 2) for k, v in a.items()})


if __name__ == "__main__":
    main()
