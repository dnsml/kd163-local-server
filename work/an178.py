import re, sys

sys.path.insert(0, r"D:/66666/kd163")
import bag_proto

lines = {}
with open(r"D:/66666/kd163/logs/relay.log", "r", encoding="utf-8", errors="replace") as f:
    for idx, ln in enumerate(f, 1):
        if "cmd=178" in ln and "2026-11-28 17:2" in ln:
            m = re.search(r"hex=([0-9a-fA-F]*)\s*$", ln.strip())
            t = re.search(r"\[(2026-11-28 [\d:]+)\]", ln)
            if m:
                lines[idx] = (t.group(1) if t else "?", m.group(1))

prev = None
for idx in sorted(lines):
    ts, hx = lines[idx]
    try:
        bag = bag_proto.parse_bag(bytes.fromhex(hx))
    except Exception as e:
        print(idx, ts, "解析失败", e)
        continue
    d = {e["pos"]: e for e in bag}
    print("=== line %d %s  件数=%d len=%d" % (idx, ts, len(bag), len(hx) // 2))
    if prev is not None:
        for p in sorted(set(prev) | set(d)):
            a, b = prev.get(p), d.get(p)
            ka = "%s x%d" % (a["name"], a["num"]) if a else "-"
            kb = "%s x%d" % (b["name"], b["num"]) if b else "-"
            if ka != kb:
                print("   pos%-3d %-24s -> %-24s (id %s -> %s)" % (
                    p, ka, kb, a["id"] if a else "-", b["id"] if b else "-"))
    prev = d
