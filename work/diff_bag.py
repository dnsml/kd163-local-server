"""对比时间窗内所有 cmd=178 全量背包，找出增/减的道具。

用法：python work/diff_bag.py 19:22:00 19:24:00
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bag_proto

BS = chr(92)
PAT = re.compile(r"\[(" + BS + "d" + BS + "d:" + BS + "d" + BS + "d:" + BS + "d" + BS + "d" + r")\]"
                 r"\s+(C->S|S->C)\s+cmd=(" + BS + "d+)\s+seq=(" + BS + "d+)"
                 r"\s+len=(" + BS + "d+)\s+hex=([0-9a-fA-F]*)")

t0 = sys.argv[1] if len(sys.argv) > 1 else "00:00:00"
t1 = sys.argv[2] if len(sys.argv) > 2 else "23:59:59"

snaps = []
for ln in open("logs/relay.log", encoding="utf-8", errors="ignore"):
    m = PAT.search(ln)
    if not m:
        continue
    t, d, cmd, seq, ln_, h = m.groups()
    if not (t0 <= t <= t1) or int(cmd) != 178 or d != "S->C":
        continue
    try:
        items = bag_proto.parse_bag(bytes.fromhex(h))
    except Exception as e:
        print(t, "解析失败", e)
        continue
    if items is None:
        continue
    snaps.append((t, {i["pos"]: (i["id"], i["num"]) for i in items}))

for k in range(1, len(snaps)):
    tp, prev = snaps[k - 1]
    t, cur = snaps[k]
    added = {p: v for p, v in cur.items() if p not in prev}
    removed = {p: v for p, v in prev.items() if p not in cur}
    changed = {p: (prev[p], cur[p]) for p in cur if p in prev and prev[p] != cur[p]}
    print("=" * 70)
    print("%s -> %s  (%d 格 -> %d 格)" % (tp, t, len(prev), len(cur)))
    for p, (iid, num) in added.items():
        print("  + 格%02x %s x%d" % (p, bag_proto.item(iid)["name"], num))
    for p, (iid, num) in removed.items():
        print("  - 格%02x %s x%d" % (p, bag_proto.item(iid)["name"], num))
    for p, (a, b) in changed.items():
        print("  ~ 格%02x %s %d -> %d" % (p, bag_proto.item(a[0])["name"],
                                          a[1], b[1]))
