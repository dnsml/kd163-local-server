# -*- coding: utf-8 -*-
"""
把 relay.py 抓到的透传日志，切成「请求-应答」样本库，并自动推导包结构。

输出：
  samples/capture_samples.json   cmd -> {cs:[...], sc:[...]}  含 base64 payload
  samples/structure_<cmd>.txt    同长度样本的逐字节差异（?? = 变化的字节）

用法：
  python capture_pack.py                    # 读 logs/relay.log
  python capture_pack.py other.log
  python capture_pack.py --only 145,146,148
"""
import re
import os
import sys
import json
import base64
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
LINE = re.compile(
    r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")
NEW = re.compile(r"\[(\d\d:\d\d:\d\d)\]\s+连接来自")
END = re.compile(r"\[(\d\d:\d\d:\d\d)\]\s+连接结束")


def parse(path):
    """→ [ {start, packets:[{dir,cmd,seq,data,t}] } ]"""
    sessions = []
    cur = None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if NEW.search(line):
                cur = {"start": NEW.search(line).group(1), "packets": []}
                sessions.append(cur)
                continue
            if END.search(line):
                cur = None
                continue
            m = LINE.search(line)
            if not m or cur is None:
                continue
            t, d, cmd, seq, ln, hx = m.groups()
            data = bytes.fromhex(hx) if hx else b""
            cur["packets"].append({
                "dir": "C" if d == "C->S" else "S",
                "cmd": int(cmd), "seq": int(seq),
                "data": data, "t": t,
                "len_ok": (len(data) == int(ln)),
            })
    return sessions


def pair(sessions):
    """一个 C->S 后面紧跟的 S->C（直到下一个 C->S）都算它的应答。"""
    pairs = []
    for s in sessions:
        cur = None
        for p in s["packets"]:
            if p["dir"] == "C":
                cur = {"session": s["start"], "req": p, "replies": []}
                pairs.append(cur)
            elif cur is not None:
                cur["replies"].append(p)
    return pairs


def diff_template(samples):
    """同长度样本 → 结构模板，变化的字节用 ?? 表示，并给出每列取值集合。"""
    if not samples:
        return "", {}
    n = min(len(x) for x in samples)
    cols = []
    varying = {}
    for i in range(n):
        vals = sorted({x[i] for x in samples})
        if len(vals) == 1:
            cols.append("%02x" % vals[0])
        else:
            cols.append("??")
            varying[i] = [("%02x" % v) for v in vals]
    return " ".join(cols), varying


def main():
    args = [a for a in sys.argv[1:]]
    only = None
    path = os.path.join(HERE, "logs", "relay.log")
    for a in args:
        if a.startswith("--only"):
            only = {int(x) for x in a.split("=", 1)[-1].replace(",", " ").split()}
        elif not a.startswith("-"):
            path = a
    if not os.path.exists(path):
        print("找不到日志:", path)
        return 1

    sessions = parse(path)
    pairs = pair(sessions)
    print("日志 %s：%d 个连接，%d 个 C->S 请求" % (os.path.basename(path), len(sessions), len(pairs)))

    cs_by = collections.defaultdict(list)
    sc_by = collections.defaultdict(list)
    for s in sessions:
        for p in s["packets"]:
            (cs_by if p["dir"] == "C" else sc_by)[p["cmd"]].append(p)

    if only:
        cs_by = {k: v for k, v in cs_by.items() if k in only}
        sc_by = {k: v for k, v in sc_by.items() if k in only}

    outdir = os.path.join(HERE, "samples")
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    def dump(lst):
        return [{
            "seq": p["seq"], "len": len(p["data"]),
            "hex": p["data"].hex(),
            "b64": base64.b64encode(p["data"]).decode(),
        } for p in lst]

    payload = {
        "source": os.path.basename(path),
        "pairs": [{
            "session": pr["session"],
            "req": {"cmd": pr["req"]["cmd"], "seq": pr["req"]["seq"], "hex": pr["req"]["data"].hex()},
            "replies": [{"cmd": r["cmd"], "seq": r["seq"], "hex": r["data"].hex()} for r in pr["replies"]],
        } for pr in pairs],
        "cs": {str(k): dump(v) for k, v in sorted(cs_by.items())},
        "sc": {str(k): dump(v) for k, v in sorted(sc_by.items())},
    }
    jpath = os.path.join(outdir, "capture_samples.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print("\n%-8s %-6s %-6s %s" % ("cmd", "C->S", "S->C", "C->S 长度分布"))
    print("-" * 52)
    allcmd = sorted(set(cs_by) | set(sc_by))
    for c in allcmd:
        lens = collections.Counter(len(p["data"]) for p in cs_by.get(c, []))
        dist = " ".join("%dB×%d" % (k, v) for k, v in sorted(lens.items()))
        print("%-8d %-6d %-6d %s" % (c, len(cs_by.get(c, [])), len(sc_by.get(c, [])), dist))

    # 同长度、且 payload 不同的 C->S 样本 → 自动结构
    report = []
    for c in allcmd:
        lst = cs_by.get(c, [])
        if len(lst) < 2:
            continue
        groups = collections.defaultdict(list)
        for p in lst:
            groups[len(p["data"])].append(p["data"])
        for ln, arr in sorted(groups.items()):
            uniq = list(dict.fromkeys(arr))
            if len(uniq) < 2 or ln < 2:
                continue
            tpl, var = diff_template(uniq)
            report.append("cmd=%d len=%d 样本=%d（唯一 %d）" % (c, ln, len(arr), len(uniq)))
            report.append("  模板: " + tpl)
            for i, vals in var.items():
                report.append("    off %-3d 变化: %s" % (i, " ".join(vals[:8])))
            report.append("")

    if report:
        rp = os.path.join(outdir, "structure_auto.txt")
        with open(rp, "w", encoding="utf-8") as f:
            f.write("\n".join(report))
        print("\n已推导 %d 组结构 → %s" % (len(report) // 3, rp))
    print("样本库 → %s" % jpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
