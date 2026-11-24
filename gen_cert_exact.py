#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「PEM 长度精确等于目标值」的自签证书，用来替换 dex 里内嵌的那张。
原理：PEM 长度 = 28(BEGIN行) + 26(END行) + base64长度 + 行数。
目标 1151 → base64=1080 字符 / 17 行 → DER 必须正好 810 字节。
通过在证书里加一条自定义扩展（内容是 N 个填充字节）微调 DER 长度。

用法：python gen_cert_exact.py --cn 124.223.56.189 --want 1151 --out patch/srv
"""
import os
import subprocess
import argparse
import tempfile


def der_size(pem_path):
    out = subprocess.check_output(["openssl", "x509", "-in", pem_path, "-outform", "DER"])
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cn", required=True)
    ap.add_argument("--want", type=int, default=1151)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bits", type=int, default=2048)
    a = ap.parse_args()

    key = a.out + ".key"
    pem = a.out + ".pem"
    if not os.path.exists(key):
        subprocess.check_call(["openssl", "genrsa", "-out", key, str(a.bits)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for n in range(0, 200):
        pad = ":" + ":".join(["A5"] * n) if n else ""
        cfg = tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False, encoding="utf-8")
        cfg.write(f"""[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = {a.cn}
[v3]
basicConstraints = CA:TRUE
keyUsage = digitalSignature,keyEncipherment,keyCertSign
subjectAltName = IP:{a.cn}
1.2.3.4 = DER:{pad[1:] if pad else ""}
""")
        cfg.close()
        try:
            subprocess.check_call(
                ["openssl", "req", "-x509", "-new", "-key", key, "-out", pem,
                 "-days", "3650", "-config", cfg.name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            os.unlink(cfg.name)
            continue
        os.unlink(cfg.name)

        dsz = der_size(pem)
        plen = os.path.getsize(pem)
        if plen == a.want:
            print(f"[OK] 填充 {n} 字节 → DER {dsz} 字节，PEM {plen} 字节（目标 {a.want}）")
            print(f"     证书: {pem}\n     私钥: {key}")
            return
        if n % 20 == 0:
            print(f"  尝试 n={n}: DER={dsz} PEM={plen}")
    raise SystemExit("没能命中目标长度，扩大搜索范围")


if __name__ == "__main__":
    main()
