# -*- coding: utf-8 -*-
"""28 / 29 交叉验证探针：同一时刻的 29 属性包与 28 快照逐字段对照。

目的：确认 28 的 hp/攻防等字段与 29 的 panel_attr 同源，
把 28 里还没定语义的 idx6/7/9/10/11 钉死。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.argv = [sys.argv[0]]
import grant  # noqa: E402

REC = re.compile(
    r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]+)")


def load():
    out = {"28": [], "29": []}
    for line in (ROOT / "logs" / "relay.log").open(encoding="utf-8", errors="replace"):
        m = REC.search(line)
        if not m:
            continue
        t, d, c, _seq, ln, hx = m.groups()
        if d != "S->C" or c not in ("28", "29"):
            continue
        if int(ln) != len(hx) // 2:
            continue
        out[c].append((t, bytes.fromhex(hx)))
    return out


def show(tag, p, n=45):
    tk = grant.tokenize(p)
    vals = []
    for i, x in enumerate(tk[:n]):
        v = x["value"]
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        vals.append("%d=%s" % (i, v))
    print("%-8s len=%-3d %s" % (tag, len(p), " ".join(vals)))


def main():
    d = load()
    print("28=%d 29=%d" % (len(d["28"]), len(d["29"])))
    for t, p in d["28"][-3:]:
        show("28@" + t, p)
    for t, p in d["29"][-3:]:
        show("29@" + t, p)
    # 找时间最接近的一对
    if d["28"] and d["29"]:
        t28 = d["28"][-1][0]
        near = min(d["29"], key=lambda x: abs(
            int(x[0][:2]) * 3600 + int(x[0][3:5]) * 60 + int(x[0][6:])
            - (int(t28[:2]) * 3600 + int(t28[3:5]) * 60 + int(t28[6:]))))
        print("\n最近一对：28@%s  vs  29@%s（跨零点/跨天时这个配对不准）" % (t28, near[0]))
        show("28", d["28"][-1][1], 20)
        show("29", near[1], 45)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
