# -*- coding: utf-8 -*-
"""从 chatgpt 分享页 HTML 里抽出对话正文（流式 JSON 转义串）。"""
import re
import sys

src = sys.argv[1] if len(sys.argv) > 1 else "cg.html"
dst = sys.argv[2] if len(sys.argv) > 2 else "cgpt_text.md"

h = open(src, encoding="utf-8", errors="replace").read()
s = h
for a, b in (('\\"', '"'), ('\\n', '\n'), ('\\u003c', '<'), ('\\u003e', '>'),
             ('\\u0026', '&'), ('\\\\', '\\')):
    s = s.replace(a, b)

# 抓所有被引号包起来的较长片段
pat = re.compile(r'"([^"]{60,})"', re.S)
cjk = re.compile(r'[\u4e00-\u9fff]')

out, seen = [], set()
for t in pat.findall(s):
    if not cjk.search(t):
        continue
    k = t[:50]
    if k in seen:
        continue
    seen.add(k)
    out.append(t.strip())

open(dst, "w", encoding="utf-8").write("\n\n=====\n\n".join(out))
print("segments=%d chars=%d -> %s" % (len(out), sum(len(x) for x in out), dst))
