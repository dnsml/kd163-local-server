# -*- coding: utf-8 -*-
"""从 ChatGPT 分享页 HTML 里抽出对话正文。正文以 JSON 转义字符串形式嵌在 page 数据里。"""
import json
import re
import sys

src = open(sys.argv[1], "r", encoding="utf-8", errors="replace").read()

out = []
seen = set()
# 形如 \"fallbackMarkdown\",\"正文\\n\\n...\"  —— 匹配所有长的 JSON 转义字符串
pat = re.compile(r'\\"((?:[^"\\]|\\.){200,}?)\\"(?=,|\\\])')
for m in pat.finditer(src):
    raw = m.group(1)
    # 还原 JSON 转义：\\n -> \n，\\\" -> "
    try:
        txt = raw.encode("utf-8").decode("unicode_escape")
    except Exception:
        txt = raw.replace("\\n", "\n").replace('\\"', '"')
    # unicode_escape 会破坏非 ASCII，改用 json 反解
    try:
        txt = json.loads('"' + raw + '"')
    except Exception:
        pass
    if len(txt) < 120:
        continue
    key = txt[:200]
    if key in seen:
        continue
    seen.add(key)
    out.append(txt)

open(sys.argv[2], "w", encoding="utf-8").write("\n\n\n".join(out))
print("### blocks =", len(out), file=sys.stderr)
print("### chars =", sum(len(x) for x in out), file=sys.stderr)
for i, t in enumerate(out):
    print("[%d] %d chars | %s" % (i, len(t), t[:80].replace("\n", " ")), file=sys.stderr)
