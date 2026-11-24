#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 capture/*.bin 中提取 HTTP 请求与响应概要"""
import os, re, sys, glob

CAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture")
HDR = re.compile(rb"^([A-Z]{3,7}) (\S+) HTTP/1\.[01]\r?$", re.M)
RESP = re.compile(rb"^HTTP/1\.[01] (\d{3}) ([^\r\n]*)\r?$", re.M)


def analyze(path):
    d = open(path, "rb").read()
    out = {"file": os.path.basename(path), "size": len(d),
           "req": None, "method": None, "status": None, "ctype": None, "clen": None, "body_off": None}
    m = HDR.search(d)
    if m:
        out["method"] = m.group(1).decode()
        out["req"] = m.group(2).decode("utf-8", "replace")
    r = RESP.search(d, 0)
    if r:
        out["status"] = int(r.group(1))
        hdr_end = d.find(b"\r\n\r\n", r.start())
        if hdr_end < 0:
            hdr_end = d.find(b"\n\n", r.start())
            body_start = hdr_end + 2
        else:
            body_start = hdr_end + 4
        head = d[r.start():body_start].decode("utf-8", "replace")
        cm = re.search(r"Content-Type:\s*([^\r\n]+)", head, re.I)
        cl = re.search(r"Content-Length:\s*(\d+)", head, re.I)
        out["ctype"] = cm.group(1).strip() if cm else None
        out["clen"] = int(cl.group(1)) if cl else None
        out["body_off"] = body_start
    return out


def main():
    files = sorted(glob.glob(os.path.join(CAP, "*.bin")))
    rows = []
    for f in files:
        try:
            rows.append(analyze(f))
        except Exception as e:
            print("ERR", f, e)
    print("%-18s %9s %-6s %-38s %-6s %-22s" % ("file", "bytes", "method", "path", "status", "content-type"))
    print("-" * 105)
    for r in rows:
        print("%-18s %9d %-6s %-38s %-6s %-22s" % (
            r["file"], r["size"], r["method"] or "-", (r["req"] or "-")[:38],
            r["status"] or "-", (r["ctype"] or "-")[:22]))

    print()
    print("=== 去重后的请求路径 ===")
    seen = {}
    for r in rows:
        if r["req"]:
            key = "%s %s" % (r["method"], r["req"].split("?")[0])
            seen.setdefault(key, []).append(r)
    for k in sorted(seen):
        rs = seen[k]
        ok = [x for x in rs if x["status"] == 200]
        print("  %-8s %-48s 请求%d次 200:%d 大小:%s" % (
            k.split(" ")[0], k.split(" ")[1], len(rs), len(ok),
            ",".join(str(x["clen"]) for x in ok[:2]) or "-"))


if __name__ == "__main__":
    main()
