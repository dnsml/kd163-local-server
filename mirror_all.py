#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 meta.xml 清单全量下载服务器资源"""
import os, re, sys, time, threading, urllib.request, hashlib
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "mirror", "meta.xml")
OUT = r"D:\66666\kd163\mirror_full"
BASE = "http://124.223.56.189/"
WORKERS = 12

lock = threading.Lock()
stat = {"ok": 0, "skip": 0, "fail": 0, "bytes": 0}
fails = []


def items():
    data = open(META, "r", encoding="utf-8", errors="replace").read()
    for m in re.finditer(r"<item>(.*?)</item>", data, re.S):
        blk = m.group(1)
        p = re.search(r"<path>(.*?)</path>", blk)
        u = re.search(r"<uri>(.*?)</uri>", blk)
        md5 = re.search(r"<md5>(.*?)</md5>", blk)
        if p and u:
            yield p.group(1), u.group(1), (md5.group(1) if md5 else None)


def fetch(one):
    rel, url, md5 = one
    dst = os.path.join(OUT, rel.replace("/", os.sep))
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        with lock:
            stat["skip"] += 1
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "UnityPlayer/2019.4"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
            with lock:
                stat["ok"] += 1
                stat["bytes"] += len(data)
            return
        except Exception as e:
            if attempt == 2:
                with lock:
                    stat["fail"] += 1
                    fails.append((rel, type(e).__name__, str(e)[:80]))
            else:
                time.sleep(1 + attempt)


def main():
    os.makedirs(OUT, exist_ok=True)
    lst = list(items())
    total = len(lst)
    print("清单共 %d 个文件，开始下载 -> %s" % (total, OUT), flush=True)
    t0 = time.time()
    done = [0]

    def run(one):
        fetch(one)
        with lock:
            done[0] += 1
            if done[0] % 200 == 0:
                el = time.time() - t0
                print("进度 %d/%d (%.1f%%)  ok=%d skip=%d fail=%d  %.1f MB  %.0fs"
                      % (done[0], total, done[0] * 100.0 / total, stat["ok"], stat["skip"],
                         stat["fail"], stat["bytes"] / 1048576, el), flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(run, lst))

    print()
    print("完成: 下载 %d  跳过 %d  失败 %d  共 %.1f MB  用时 %.0fs"
          % (stat["ok"], stat["skip"], stat["fail"], stat["bytes"] / 1048576, time.time() - t0))
    if fails:
        print("失败样例:")
        for f in fails[:20]:
            print("  ", f)
        with open(os.path.join(HERE, "mirror_fails.txt"), "w", encoding="utf-8") as fh:
            for f in fails:
                fh.write("\t".join(f) + "\n")


if __name__ == "__main__":
    main()
