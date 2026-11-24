# -*- coding: utf-8 -*-
"""列出 server.log 里客户端发来的请求（C->S），重点标出服务端没实现的包。

用来定位宠物/妖灵这类新系统的协议号：让用户在客户端点一次界面，
然后跑这个脚本，看哪些 cmd 只出现一次、且被「无模板」丢弃。

跑法：
    python work/dump_new_cmds.py            # 最近 300 行
    python work/dump_new_cmds.py 2000       # 最近 2000 行
    python work/dump_new_cmds.py 2000 --all # 不看窗口，全量统计
"""
import re
import sys
from collections import Counter, OrderedDict

LOG = "logs/server.log"
lines = int(sys.argv[1]) if len(sys.argv) > 1 else 300
only_all = "--all" in sys.argv

rows = []
with open(LOG, encoding="utf-8", errors="ignore") as f:
    data = f.readlines()
if not only_all:
    data = data[-lines:]

pat = re.compile(r'\[(\d\d:\d\d:\d\d)\]\s+c(\d+)\s+<-\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)(?:\s+hex=([0-9a-f]*))?')
for ln in data:
    m = pat.search(ln)
    if not m:
        continue
    rows.append(m.groups())

if not rows:
    print("窗口内没有 C->S 包（客户端可能没连上，或只发了心跳）")
    sys.exit(0)

# 哪些 cmd 被服务端丢弃（同文件里的「无模板」行）
dropped = set()
for ln in data:
    m = re.search(r'无模板 cmd=(\d+)', ln)
    if m:
        dropped.add(int(m.group(1)))

cnt = Counter(int(r[2]) for r in rows)
print("窗口 %d 行 / C->S %d 包 / cmd 种类 %d" % (len(data), len(rows), len(cnt)))
print("%-8s %-5s %-9s %-8s %s" % ("cmd", "次数", "首个时间", "len", "首包hex"))
print("-" * 86)
for cmd in sorted(cnt, key=lambda c: (-int(c in dropped), cnt[c], c)):
    first = next(r for r in rows if int(r[2]) == cmd)
    tag = " ★未实现" if cmd in dropped else ""
    print("%-8d %-5d %-9s %-8s %s%s"
          % (cmd, cnt[cmd], first[0], first[4], (first[5] or "")[:70], tag))

print()
print("★ = 服务端当前没有模板/处理逻辑，正是要找的新系统协议。")
