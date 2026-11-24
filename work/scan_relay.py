# -*- coding: utf-8 -*-
"""扫描 relay.log：按 cmd 统计、提取指定 cmd 的上下文样本。"""
import re
import sys

PAT = re.compile(
    r"\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-fA-F]*)"
)


def load(path="logs/relay.log"):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = PAT.search(line)
            if m:
                g = m.groups()
                rows.append((g[0], g[1], int(g[2]), int(g[3]), int(g[4]), g[5]))
    return rows


if __name__ == "__main__":
    rows = load()
    print("rows", len(rows))
    if len(sys.argv) > 1 and sys.argv[1] == "ctx":
        target = int(sys.argv[2])
        side = sys.argv[3] if len(sys.argv) > 3 else "C->S"
        lim = int(sys.argv[4]) if len(sys.argv) > 4 else 3
        idx = [i for i, r in enumerate(rows) if r[1] == side and r[2] == target]
        print("hits", len(idx))
        for i in idx[:lim]:
            print("==== ctx", rows[i][0], "idx", i)
            for r in rows[max(0, i - 6):i + 10]:
                print(r[0], r[1], "cmd=%d" % r[2], "seq=%d" % r[3],
                      "len=%d" % r[4], r[5][:70])
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "count":
        from collections import Counter
        c = Counter((r[1], r[2]) for r in rows)
        for (side, cmd), n in sorted(c.items(), key=lambda x: -x[1])[:40]:
            print(side, "cmd=%d" % cmd, n)
        sys.exit(0)
