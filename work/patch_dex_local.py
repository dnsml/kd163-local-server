# -*- coding: utf-8 -*-
"""把原版 classes.dex 内嵌的证书等长替换为本地证书（CN 仍是 124.223.56.189）。

两边 PEM 都去掉尾部换行，保证 1150 字节等长 -> delta=0，不影响任何 section 偏移。
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import dexpatch  # noqa: E402

SRC_DEX = "work/local/classes_orig.dex"
DST_DEX = "work/local/classes_local.dex"
OLD_PEM = "work/local/pinned_orig.pem"
NEW_PEM = "patch/local/exact.pem"


def main():
    old = open(OLD_PEM, "rb").read().rstrip(b"\r\n")
    new = open(NEW_PEM, "rb").read().rstrip(b"\r\n")
    print("old pem %d, new pem %d" % (len(old), len(new)))
    if len(old) != len(new):
        raise SystemExit("PEM 长度不等，无法等长替换")

    blob = open(SRC_DEX, "rb").read()
    if blob.count(old) != 1:
        raise SystemExit("旧 PEM 在 dex 中命中 %d 次，应为 1" % blob.count(old))
    if new in blob:
        raise SystemExit("dex 里已经是新 PEM，无需处理")

    n, delta = dexpatch.patch(SRC_DEX, DST_DEX, {old: new})
    out = open(DST_DEX, "rb").read()
    print("changed strings:", n, "delta:", delta, "size:", len(out))
    print("new pem in dex:", out.count(new), " old pem left:", out.count(old))
    print("sha256:", hashlib.sha256(out).hexdigest()[:16])
    if delta != 0 or out.count(new) != 1 or len(out) != len(blob):
        raise SystemExit("补丁结果异常")


if __name__ == "__main__":
    main()
