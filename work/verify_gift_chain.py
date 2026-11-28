"""用真服 178 快照回放「升级送礼」五连开包，验证本地分配规则与掉落表。

真服样本：2026-11-28 17:25:37 基线 178，之后 5 次 182（格5、5、52、52、53），
每次跟一条 178。脚本按「先扣格位 → 按描述顺序发掉落（可叠先叠，否则最小空格）」
推演，逐格和真服比。
"""
import re
import sys

sys.path.insert(0, r"D:/66666/kd163")
import bag_proto
import item_rules

LOG = r"D:/66666/kd163/logs/relay.log"

snaps = []
with open(LOG, "r", encoding="utf-8", errors="replace") as f:
    for ln in f:
        if "2026-11-28 17:2" in ln and "cmd=178" in ln:
            m = re.search(r"\[(2026-11-28 [\d:]+)\]", ln)
            mh = re.search(r"hex=([0-9a-fA-F]*)\s*$", ln.strip())
            if m and mh:
                snaps.append((m.group(1), bytes.fromhex(mh.group(1))))

# 基线 = 17:25:37，之后按时间顺序取 5 条
snaps = [s for s in snaps if s[0] >= "2026-11-28 17:25:37"]


def to_map(payload):
    out = {}
    for e in bag_proto.parse_bag(payload):
        out[e["pos"]] = [e["id"], e["num"]]
    return out


def stack_or_new(bag, iid, n, cap=60):
    for p in sorted(bag):
        v = bag[p]
        if v[0] == iid and v[1] + n <= 99:
            v[1] += n
            return p
    for p in range(cap):
        if p not in bag:
            bag[p] = [iid, n]
            return p
    raise RuntimeError("背包满")


def open_gift(bag, pos):
    iid, num = bag[pos]
    r = item_rules.rule(iid)
    if r["kind"] != "gift":
        raise RuntimeError("格%d 的 %s 不是礼包（kind=%s）" % (pos, r["name"], r["kind"]))
    del bag[pos]                                  # 真服：先扣格位
    landed = []
    for gid, gn in r["items"]:
        if isinstance(gid, str):
            landed.append((gid, gn, -1))
            continue
        landed.append((r["name"], gid, gn))
        stack_or_new(bag, gid, gn)                # 再按描述顺序入包
    return iid, r, landed


def diff(a, b):
    out = []
    for p in sorted(set(a) | set(b)):
        x, y = a.get(p), b.get(p)
        if x != y:
            out.append((p, x, y))
    return out


base_ts, base = snaps[0]
bag = to_map(base)
print("基线 %s 件数=%d" % (base_ts, len(bag)))

ok = True
for (ts, payload), pos in zip(snaps[1:], (5, 5, 52, 52, 53)):
    real = to_map(payload)
    iid, r, landed = open_gift(bag, pos)
    d = diff(bag, real)
    status = "✅" if not d else "❌"
    if d:
        ok = False
    print("%s %s 格%-2d %s -> %s" % (status, ts, pos, r["name"],
                                    "、".join("%s x%d" % (bag_proto.item(g)["name"] if not isinstance(g, str) else g, n)
                                             for _, g, n in [(a, b, c) for a, b, c in landed])))
    for p, x, y in d:
        fx = "%s x%d" % (bag_proto.item(x[0])["name"], x[1]) if x else "空"
        fy = "%s x%d" % (bag_proto.item(y[0])["name"], y[1]) if y else "空"
        print("    格%-2d 推演=%s  真服=%s" % (p, fx, fy))

print("\n总判定：", "全部一致" if ok else "存在差异")
