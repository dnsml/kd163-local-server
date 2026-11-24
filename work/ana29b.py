"""用真服强化样本反推 cmd=29 各字段 = 哪条属性之和（验证线性关系）。

做法：按时间顺序取 818（7 件装备的强化等级）+ 紧随的 29 包，
算出 Σstrengthen[lv][attr]，和 29 的每个字段比对差值是否恒定。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import local_server3 as m

BS = chr(92)
PAT = re.compile(r"\[(?:20\d\d-\d\d-\d\d )?(" + BS + "d" + BS + "d:" + BS + "d" + BS + "d:" + BS + "d" + BS + "d" + r")\]"
                 r"\s+(C->S|S->C)\s+cmd=(" + BS + "d+)\s+seq=(" + BS + "d+)"
                 r"\s+len=(" + BS + "d+)\s+hex=([0-9a-fA-F]*)")

m._load_equip_str()


def toks(b):
    out = []
    i = 0
    while i < len(b):
        v, i = m.vread(b, i)
        out.append(v)
    return out


def parse818(b):
    """818 -> {槽: 等级}，以及 {槽: 物品id}"""
    n = b[0]
    i = 1
    lv = {}
    eq = {}
    for _ in range(n):
        slot = b[i]
        iid, j = m.vread(b, i + 1)
        lv[slot] = b[j + 2]
        eq[slot] = iid
        i = j + 22
    return lv, eq


rows = []
cur = None
for ln in open("logs/relay.log", encoding="utf-8", errors="ignore"):
    mm = PAT.search(ln)
    if not mm:
        continue
    t, d, cmd, seq, ln_, h = mm.groups()
    cmd = int(cmd)
    b = bytes.fromhex(h)
    if d == "S->C" and cmd == 818 and len(b) > 100:
        cur = parse818(b)
    elif d == "S->C" and cmd == 29 and cur and t >= "18:20":
        lv, eq = cur
        sums = {}
        for attr in (51, 52, 53, 7, 57):
            s = 0
            for slot, iid in eq.items():
                l = lv.get(slot, 0)
                if l > 0:
                    s += m.EQUIP_STR.get((iid, min(l, 12)), {}).get(attr, 0)
            sums[attr] = int(s)
        rows.append((t, toks(b), dict(lv), sums))
        cur = None

print("样本 %d 组" % len(rows))
print("时间       Σ51 Σ52 Σ53  Σ7 Σ57 | 29 字段")
for t, tk, lv, s in rows:
    print("%s %3d %3d %3d %3d %3d | %s"
          % (t, s[51], s[52], s[53], s[7], s[57], tk))

# 对每个字段求 「字段 - 各属性对应和」 的差值，看哪条属性能让差值恒定
print("\n字段与哪条属性的差值恒定（min==max 即线性吻合）：")
attr_of = {0: 53, 2: 51, 3: 52, 4: 7, 12: 57}
for idx, attr in sorted(attr_of.items()):
    d = [tk[idx] - s[attr] for t, tk, lv, s in rows]
    print("  field[%2d] vs Σ%-3d 差值 %s  恒定=%s"
          % (idx, attr, d, len(set(d)) == 1))
