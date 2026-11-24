# -*- coding: utf-8 -*-
"""解码真服 relay.log 里所有 cmd=1 提示消息，并标注触发它的请求。"""
import re
import collections

PAT = re.compile(r'\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)')

cnt = collections.Counter()
first = {}
pending_c = {}   # seq -> 最近一条 C->S 描述
rows = []

for line in open('logs/relay.log', encoding='utf-8', errors='ignore'):
    m = PAT.search(line)
    if not m:
        continue
    t, side, cmd, seq, ln, h = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6)
    if side == 'C->S':
        pending_c[seq] = 'cmd=%d len=%d hex=%s' % (cmd, ln, h[:20])
        continue
    if cmd != 1:
        continue
    b = bytes.fromhex(h)
    txt = b[1:].decode('utf-8', 'ignore') if b else ''
    typ = b[0] if b else -1
    cnt[(typ, txt)] += 1
    if txt not in first:
        first[txt] = (t, seq, pending_c.get(seq, '?'))
        rows.append((t, seq, typ, txt, pending_c.get(seq, '?')))

print('=== 提示消息汇总 ===')
for (typ, txt), n in cnt.most_common():
    print('%3d次  type=%d  %s' % (n, typ, txt))

print()
print('=== 首次出现（时间 / seq / 触发请求）===')
for t, seq, typ, txt, req in rows:
    print('%s seq=%-4s %s   <= %s' % (t, seq, txt[:46], req))
