# -*- coding: utf-8 -*-
"""统计哪些请求被真服回 '本服尚未恢复此功能'。"""
import re
import collections

PAT = re.compile(r'\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)')

pending = {}
hits = collections.Counter()
samples = {}
notrestored = '本服尚未恢复此功能'

for line in open('logs/relay.log', encoding='utf-8', errors='ignore'):
    m = PAT.search(line)
    if not m:
        continue
    t, side, cmd, seq, ln, h = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6)
    if side == 'C->S':
        pending[seq] = (t, cmd, ln, h)
        continue
    b = bytes.fromhex(h)
    txt = b[1:].decode('utf-8', 'ignore') if b else ''
    if txt == notrestored and seq in pending:
        rt, rcmd, rln, rh = pending[seq]
        hits[rcmd] += 1
        samples.setdefault(rcmd, (rt, rh))
    # 处理完即清理，避免 seq 复用串线
    if seq in pending:
        del pending[seq]

print('被回「本服尚未恢复此功能」的命令：')
for cmd, n in hits.most_common():
    t, rh = samples[cmd]
    print('  cmd=%-6d %2d 次   样本 %s' % (cmd, n, rh[:30] if rh else '(空)'))
