#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_apk.pad_host 回归：内置地址换短 IP 时必须**等长**。

背景（2026-11-27 真机踩坑）：dex 的 string_data_item 在 data 段里顺序排布，
ART 按 map 顺序逐条校验。原地缩短会让槽尾残留字节被当成下一条 string 的
长度前缀，真机报 `String longer than indicated size 0` → ClassNotFoundException。
所以补位只能写在 URL 里（host 之后补 '/'），由服务端折叠。

跑法：python work/test_patch_dex.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from patch_apk import pad_host  # noqa: E402

OLD = b"124.223.56.189"
NEW = "192.168.0.111"

# 真包里那 7 条（长度按 dex 实测）
CASES = [
    b"http://124.223.56.189",
    b"http://124.223.56.189/assets.txt",
    b"http://124.223.56.189/legacy/equipment.php",
    b"http://124.223.56.189/legacy/receive.php",
    b"http://124.223.56.189/server-list.xml",
    b"http://124.223.56.189/version.txt",
    b"https://124.223.56.189:9000/auth",
]

EXPECT = [
    b"http://192.168.0.111/",
    b"http://192.168.0.111//assets.txt",
    b"http://192.168.0.111//legacy/equipment.php",
    b"http://192.168.0.111//legacy/receive.php",
    b"http://192.168.0.111//server-list.xml",
    b"http://192.168.0.111//version.txt",
    b"https://192.168.0.111:9000//auth",
]


def main():
    bad = 0
    for src, want in zip(CASES, EXPECT):
        got = pad_host(src, OLD, NEW)
        if got is None:
            print("FAIL 未命中：%s" % src.decode())
            bad += 1
            continue
        ok_len = len(got) == len(src)
        ok_val = got == want
        print("%s %-52s len %d→%d %s"
              % ("OK  " if ok_len and ok_val else "FAIL",
                 got.decode(), len(src), len(got),
                 "" if ok_len and ok_val else "期望 %s" % want.decode()))
        if not (ok_len and ok_val):
            bad += 1

    # 补位必须落在 host:port 之后、path 之前
    g = pad_host(b"https://124.223.56.189:9000/auth", OLD, NEW)
    if not g.startswith(b"https://192.168.0.111:9000/"):
        print("FAIL 端口后位置不对：%s" % g.decode())
        bad += 1
    else:
        print("OK   端口 :9000 保留在 host 之后")

    # 新 host 更长时直接返回（不补位），由 patch_dex 报错
    g = pad_host(b"http://1.2.3.4", b"1.2.3.4", NEW)
    if g != b"http://192.168.0.111":
        print("FAIL 变长情形应原样替换：%r" % g)
        bad += 1
    else:
        print("OK   变长情形原样替换（交给 patch_dex 报错）")

    print("\n结果：%s" % ("全部通过" if not bad else "%d 项失败" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
