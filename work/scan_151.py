# -*- coding: utf-8 -*-
"""打印真服 cmd=151（进入战斗）里的中文名，判断在打什么怪。
注意：不能把整段当 UTF-8 解，包里中文前有单字节长度前缀（0xAC 之类），
      前缀本身落在 0x80~0xBF，直接整段 decode 会把串弄坏 → 必须按 3 字节组扫。
"""
import re, sys

LOG = sys.argv[1] if len(sys.argv) > 1 else r"D:\66666\kd163\logs\relay.log"
CMD = int(sys.argv[2]) if len(sys.argv) > 2 else 151
pat = re.compile(r"S->C cmd=%d\s+seq=\d+\s+len=(\d+)\s+hex=([0-9a-f]*)" % CMD)


def cjk_runs(b):
    out, i = [], 0
    while i + 2 < len(b):
        if 0xE4 <= b[i] <= 0xE9 and 0x80 <= b[i + 1] <= 0xBF and 0x80 <= b[i + 2] <= 0xBF:
            j = i
            while j + 2 < len(b) and 0xE4 <= b[j] <= 0xE9 and \
                    0x80 <= b[j + 1] <= 0xBF and 0x80 <= b[j + 2] <= 0xBF:
                j += 3
            try:
                s = b[i:j].decode("utf-8")
                if len(s) >= 2:
                    out.append(s)
            except Exception:
                pass
            i = j
        else:
            i += 1
    return out


for ln in open(LOG, encoding="utf-8", errors="ignore"):
    m = pat.search(ln)
    if not m:
        continue
    ts = ln[1:9]
    n, hx = int(m.group(1)), m.group(2)
    if len(hx) != n * 2:
        continue
    b = bytes.fromhex(hx)
    print("%s len=%-4d %s" % (ts, n, " / ".join(cjk_runs(b))))
