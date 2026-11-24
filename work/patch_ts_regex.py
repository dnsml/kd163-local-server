# -*- coding: utf-8 -*-
"""把 work/ 里解析日志时间戳的正则改成「日期前缀可选」。

旧格式 [01:23:23]             （历史 relay.log / server.log）
新格式 [2026-11-25 01:23:23]  （2026-11-25 之后的日志）

改动都是加一个非捕获组，组号不变，旧日志照样能解析。
"""
import glob
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))

OLD = r"\[(\d\d:\d\d:\d\d)\]"
NEW = r"\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]"

total = 0
for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
    if os.path.abspath(path) == os.path.abspath(__file__):
        continue
    src = io.open(path, encoding="utf-8").read()
    n = src.count(OLD)
    if not n:
        continue
    io.open(path, "w", encoding="utf-8", newline="\n").write(src.replace(OLD, NEW))
    print("%-30s %d 处" % (os.path.basename(path), n))
    total += n
print("合计 %d 处" % total)
