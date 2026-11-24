#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 capture/*.bin 提取 HTTP 响应体，按 URL 路径还原成文件"""
import os, re, glob

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(HERE, "capture")
OUT = os.path.join(HERE, "mirror")

HDR = re.compile(rb"^([A-Z]{3,7}) (\S+) HTTP/1\.[01]\r?$", re.M)
RESP = re.compile(rb"^HTTP/1\.[01] (\d{3})", re.M)


def split_stream(d):
    """把一次连接里的多个 HTTP 事务拆开"""
    txns = []
    pos = 0
    while True:
        m = HDR.search(d, pos)
        if not m:
            break
        req_start = m.start()
        path = m.group(2).decode("utf-8", "replace")
        # 找响应
        r = RESP.search(d, m.end())
        if not r:
            break
        hdr_end = d.find(b"\r\n\r\n", r.start())
        nl = 2
        if hdr_end < 0:
            hdr_end = d.find(b"\n\n", r.start())
            nl = 1
        if hdr_end < 0:
            break
        head = d[r.start():hdr_end].decode("utf-8", "replace")
        body_start = hdr_end + (4 if nl == 2 else 2)
        cl = re.search(r"Content-Length:\s*(\d+)", head, re.I)
        if cl:
            body = d[body_start:body_start + int(cl.group(1))]
            next_pos = body_start + int(cl.group(1))
        else:
            body = d[body_start:]
            next_pos = len(d)
        txns.append((path, int(r.group(1)), head, body, d[req_start:r.start()]))
        pos = next_pos if next_pos > pos else len(d)
    return txns


def safe_path(url):
    p = url.split("?")[0].lstrip("/")
    p = p.replace("..", "_")
    return p or "index"


def main():
    os.makedirs(OUT, exist_ok=True)
    files = sorted(glob.glob(os.path.join(CAP, "*.bin")))
    saved = 0
    manifest = []
    for f in files:
        d = open(f, "rb").read()
        for path, status, head, body, reqblob in split_stream(d):
            if status != 200 or not body:
                continue
            rel = safe_path(path)
            dst = os.path.join(OUT, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst) and os.path.getsize(dst) >= len(body):
                continue
            with open(dst, "wb") as fh:
                fh.write(body)
            saved += 1
            manifest.append((path, len(body), rel))
    print("保存 %d 个文件到 %s" % (saved, OUT))
    print()
    print("%-52s %10s" % ("URL", "bytes"))
    print("-" * 64)
    for path, n, rel in sorted(manifest):
        print("%-52s %10d" % (path, n))
    # 记录清单
    with open(os.path.join(HERE, "mirror_manifest.txt"), "w", encoding="utf-8") as fh:
        for path, n, rel in sorted(manifest):
            fh.write("%s\t%d\t%s\n" % (path, n, rel))


if __name__ == "__main__":
    main()
