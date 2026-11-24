# -*- coding: utf-8 -*-
"""扫 relay.log 里全部 S->C cmd=28，统计 idx22(VIP等级)/idx23(VIP剩余x0.1天) 出现过哪些值。
目的：确认周卡=黄金VIP、月卡=钻石VIP 是否对应 idx22 = 2 / 3（半年卡已实证 4）。
"""
import re, sys, collections
sys.path.insert(0, r"D:\66666\kd163")
import grant

LOG = r"D:\66666\kd163\logs\relay.log"
pat = re.compile(r"S->C cmd=28\s+seq=\d+\s+len=(\d+)\s+hex=([0-9a-f]*)")

rows = []
bad = 0
for ln in open(LOG, "r", encoding="utf-8", errors="ignore"):
    m = pat.search(ln)
    if not m:
        continue
    ln_len = int(m.group(1))
    hexs = m.group(2)
    if len(hexs) != ln_len * 2:
        bad += 1                      # 截断包（relay 最多 160 字符），丢掉
        continue
    pl = bytes.fromhex(hexs)
    try:
        tk = grant.tokenize(pl)
    except Exception as e:
        bad += 1
        continue
    ts = ln[1:9]
    rows.append((ts, len(pl), tk[22]["value"], tk[23]["value"]))

print("有效 28 包 %d 个，截断/异常 %d 个" % (len(rows), bad))
c22 = collections.Counter(r[2] for r in rows)
c23 = collections.Counter(r[3] for r in rows)
print("\nidx22（VIP等级）分布：", dict(sorted(c22.items())))
print("idx23（VIP时长x0.1天）非零值：", sorted(v for v in c23 if v))

print("\n按时间列出所有 idx22/23 非零样本：")
for ts, ln_len, a, b in rows:
    if a or b:
        print("  %s len=%d idx22=%s idx23=%s" % (ts, ln_len, a, b))

print("\n出现过的 (idx22,idx23) 组合：", dict(collections.Counter((r[2], r[3]) for r in rows)))
