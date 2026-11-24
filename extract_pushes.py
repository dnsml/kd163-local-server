# -*- coding: utf-8 -*-
"""把 relay.log 中最新的 29/28/30/178 推送固化进 templates.json。"""
import re
import json

PAT = re.compile(r'\[(\d\d:\d\d:\d\d)\]\s+S->C\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)')

last = {}
for line in open('logs/relay.log', encoding='utf-8', errors='ignore'):
    m = PAT.search(line)
    if m:
        declared, hexlen = int(m.group(4)), len(m.group(5)) // 2
        # relay.log 的 hex 字段最多 160 字符（80 字节），长包会被截断。
        # 声明长度和 hex 长度不一致的样本是残包，直接下发会让客户端闪退
        # （2026-11-22：push_178 被截断版污染，进场景必崩）。
        if declared != hexlen:
            print('跳过截断包 cmd=%s len=%d 只有 %dB' % (m.group(2), declared, hexlen))
            continue
        last[int(m.group(2))] = (m.group(1), m.group(5))

out = {}
for c in (29, 28, 30, 178):
    if c in last:
        t, h = last[c]
        out['push_%d' % c] = h
        print(c, t, 'len', len(h) // 2, 'head', h[:60])

t = json.load(open('templates.json', encoding='utf-8'))
t.update(out)
json.dump(t, open('templates.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('已写入 templates.json:', list(out))
