# -*- coding: utf-8 -*-
"""解析真服 cmd=150 结算包结构（含掉落列表），用于本地服补掉落下发。
真服样本：
  len=25 cd0dac 000000 cd1b58 00 02 cd4eae 01 01 cd4f91 01 02 00000000
  len=30 cd157c 000000 cd2af8 00 03 cd4efb 01 01 ...
"""
import re, sys, collections

LOG = sys.argv[1] if len(sys.argv) > 1 else r"D:\66666\kd163\logs\relay.log"
pat = re.compile(r"\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\] S->C cmd=150\s+seq=\d+\s+len=(\d+)\s+hex=([0-9a-f]*)")


def rd_var(b, i):
    """解本项目变长整数：0xCC+1B / 0xCD+2B / 0xCE+4B，普通字节原值。"""
    c = b[i]
    if c == 0xCC:
        return b[i + 1], i + 2
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    return c, i + 1


rows = []
for ln in open(LOG, encoding="utf-8", errors="ignore"):
    m = pat.search(ln)
    if not m:
        continue
    ts, n, hx = m.group(1), int(m.group(2)), m.group(3)
    if len(hx) != n * 2:
        continue
    rows.append((ts, bytes.fromhex(hx)))

print("cmd=150 样本 %d 个\n" % len(rows))
by_len = collections.defaultdict(list)
for ts, b in rows:
    by_len[len(b)].append((ts, b))

for n in sorted(by_len):
    print("=== len=%d（%d 个）===" % (n, len(by_len[n])))
    for ts, b in by_len[n][:4]:
        # 逐字节转义成可读串
        i = 0
        parts = []
        while i < len(b):
            v, j = rd_var(b, i)
            parts.append("%d[%s]" % (v, b[i:j].hex()))
            i = j
        print("  %s  %s" % (ts, " ".join(parts)))
    print()
