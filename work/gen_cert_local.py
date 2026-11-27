# -*- coding: utf-8 -*-
"""生成与原版客户端内嵌证书同构、等长（DER=808 / PEM 无尾换行=1150）的本地证书。

原版证书结构（work/local/pinned_orig.pem）：
  C=3, serial 20B, sha256WithRSA, Issuer=Subject=CN=124.223.56.189,
  RSA2048, ext: SKI, AKI, basicConstraints(critical,CA:TRUE), SAN=IP:124.223.56.189
本脚本用全新私钥复刻同样的 subject/SAN/扩展/serial 长度，保证 DER 长度一致，
从而可以在 classes.dex 里做等长字符串替换（delta=0）。
"""
import base64
import os
import subprocess
import sys
import tempfile

IP = os.environ.get("KD_LOCAL_IP") or "124.223.56.189"
OUT_DIR = "patch/local"
KEY = os.path.join(OUT_DIR, "exact.key")
CSR = os.path.join(OUT_DIR, "exact.csr")
TARGET_DER = 808
TARGET_PEM = 1150          # 不含尾部换行

EXT = """basicConstraints=critical,CA:TRUE
subjectAltName=IP:%s
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always
""" % IP


def der_pem_len(pem_path):
    d = open(pem_path, "rb").read()
    b = "".join(l for l in d.decode().splitlines() if "CERT" not in l)
    return len(base64.b64decode(b)), len(d), len(d.rstrip(b"\n"))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(KEY):
        subprocess.run(["openssl", "genrsa", "-out", KEY, "2048"],
                       check=True, capture_output=True)
    subprocess.run(["openssl", "req", "-new", "-key", KEY, "-out", CSR,
                    "-subj", "/CN=%s" % IP], check=True, capture_output=True)

    extfile = tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False)
    extfile.write(EXT)
    extfile.close()

    tmp = os.path.join(OUT_DIR, "try.pem")
    ok = None
    # serial 20 字节且首字节 < 0x80，避免 DER 的 INTEGER 前导 0x00 多一字节
    for nbytes in (20, 19, 21, 18, 22, 17, 23):
        for _ in range(40):
            first = os.urandom(1)[0] & 0x7F
            if first == 0:
                first = 0x11
            serial = "0x%02x" % first + os.urandom(nbytes - 1).hex()
            r = subprocess.run(
                ["openssl", "x509", "-req", "-in", CSR, "-signkey", KEY,
                 "-out", tmp, "-days", "3650", "-set_serial", serial,
                 "-extfile", extfile.name, "-sha256"],
                capture_output=True)
            if r.returncode:
                continue
            dl, pl, ps = der_pem_len(tmp)
            if dl == TARGET_DER and ps == TARGET_PEM:
                ok = (serial, dl, pl, ps)
                break
        if ok:
            break
        print("serial bytes=%d 未命中，继续试" % nbytes)

    os.unlink(extfile.name)
    if not ok:
        print("FAILED: 未找到 DER=%d / PEM=%d 的组合" % (TARGET_DER, TARGET_PEM))
        sys.exit(1)

    final = os.path.join(OUT_DIR, "exact.pem")
    os.replace(tmp, final)
    print("OK serial=%s der=%d pem=%d pem_strip=%d" % ok)
    print("pem ->", final)
    print("key ->", KEY)


if __name__ == "__main__":
    main()
