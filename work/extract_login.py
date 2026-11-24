# -*- coding: utf-8 -*-
"""从 relay.log 抽取指定时间窗口的登录原包，输出 JSON（cmd/seq/len/hex）。"""
import json
import re
import sys

RE = re.compile(r"^\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\] (C->S|S->C) cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)(?: hex=(\S*))?")


def main(path, stamp, out):
    rows = []
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    start = None
    for i, L in enumerate(lines):
        if L.startswith("[%s] C->S cmd=17 " % stamp):
            start = i
            break
    if start is None:
        print("找不到 %s 的登录" % stamp)
        return
    for L in lines[start:start + 200]:
        m = RE.match(L)
        if not m:
            continue
        t, d, c, s, ln, h = m.groups()
        if t != stamp and rows:
            # 登录段结束：出现别的秒级时间且已经有内容
            pass
        rows.append({"t": t, "dir": d, "cmd": int(c), "seq": int(s),
                     "len": int(ln), "hex": h or ""})
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写入 %s：%d 条" % (out, len(rows)))
    for r in rows:
        if r["dir"] == "S->C":
            print("  S->C %-5d seq=%-4d len=%-5d" % (r["cmd"], r["seq"], r["len"]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
