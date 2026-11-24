# -*- coding: utf-8 -*-
"""统计 relay.log 中 C->S / S->C 命令分布，用于定位世界BOSS等新玩法的命令号。"""
import re, sys, collections

LOG = sys.argv[1] if len(sys.argv) > 1 else r"D:\66666\kd163\logs\relay.log"
cp = re.compile(r"\[(\d\d:\d\d:\d\d)\] C->S cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")
sp = re.compile(r"\[(\d\d:\d\d:\d\d)\] S->C cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")

cs = collections.Counter(); cs_len = collections.defaultdict(set)
sc = collections.Counter(); sc_len = collections.defaultdict(set)
first = {}; last = {}
for ln in open(LOG, encoding="utf-8", errors="ignore"):
    m = cp.search(ln)
    if m:
        ts, cmd, n = m.group(1), int(m.group(2)), int(m.group(4))
        cs[cmd] += 1; cs_len[cmd].add(n)
        first.setdefault(cmd, ts); last[cmd] = ts
        continue
    m = sp.search(ln)
    if m:
        cmd, n = int(m.group(2)), int(m.group(4))
        sc[cmd] += 1; sc_len[cmd].add(n)

print("== C->S 共 %d 个命令号 ==" % len(cs))
for cmd in sorted(cs):
    print("  %5d x%-4d len=%-22s %s ~ %s" % (cmd, cs[cmd], str(sorted(cs_len[cmd])[:5]), first[cmd], last[cmd]))
print("\n== S->C 共 %d 个命令号 ==" % len(sc))
for cmd in sorted(sc):
    print("  %5d x%-4d len=%-22s" % (cmd, sc[cmd], str(sorted(sc_len[cmd])[:5])))
