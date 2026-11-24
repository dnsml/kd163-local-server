"""扫 relay.log 指定时间窗，过滤心跳/位置噪音，列出有效帧并配对。

用法：
    python work/scan_ops.py 19:15:00 [19:30:00]
"""
import re
import sys
from collections import Counter

BS = chr(92)
PAT = re.compile(r"\[(" + BS + "d" + BS + "d:" + BS + "d" + BS + "d:" + BS + "d" + BS + "d" + r")\]"
                 r"\s+(C->S|S->C)\s+cmd=(" + BS + "d+)\s+seq=(" + BS + "d+)"
                 r"\s+len=(" + BS + "d+)\s+hex=([0-9a-fA-F]*)")

t0 = sys.argv[1] if len(sys.argv) > 1 else "00:00:00"
t1 = sys.argv[2] if len(sys.argv) > 2 else "23:59:59"
NOISE = {4, 115}          # 心跳 / 位置上报

rows = []
for ln in open("logs/relay.log", encoding="utf-8", errors="ignore"):
    m = PAT.search(ln)
    if not m:
        continue
    t, d, cmd, seq, ln_, h = m.groups()
    if not (t0 <= t <= t1):
        continue
    rows.append((t, d, int(cmd), int(seq), int(ln_), h))

print("时间窗 %s ~ %s 共 %d 帧（含噪音）" % (t0, t1, len(rows)))
cnt = Counter((r[1], r[2]) for r in rows if r[2] not in NOISE)
print("有效帧按 cmd 统计：")
for k, v in sorted(cnt.items(), key=lambda x: (-x[1], x[0])):
    print("   %-4s cmd=%-6d %d 次" % (k[0], k[1], v))

print("\n---- 有效帧明细（cmd 非心跳）----")
for t, d, cmd, seq, ln_, h in rows:
    if cmd in NOISE:
        continue
    print("%s %-4s cmd=%-6d seq=%-5d len=%-4d %s" % (t, d, cmd, seq, ln_, h))
