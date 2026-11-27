# -*- coding: utf-8 -*-
"""生成与 patch/exact.pem 等长（PEM 字节数完全相同）的新证书，
CN/SAN 换成新的服务器 IP，便于在 DEX 里做等长字符串替换。"""
import base64
import os
import subprocess
import sys
import tempfile

TARGET_DER = 808          # 原证书 DER 长度
KEY = "patch/newcert/pub.key"
CSR = "patch/newcert/pub.csr"
SUBJ = "/CN=129.204.52.72"
EXT = """basicConstraints=CA:TRUE
keyUsage=digitalSignature,keyEncipherment,keyCertSign
subjectAltName=IP:129.204.52.72
subjectKeyIdentifier=hash
1.2.3.4=DER:00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd
"""


def der_len(pem_path):
    d = open(pem_path, "rb").read()
    b = "".join(l for l in d.decode().splitlines() if "CERT" not in l)
    return len(base64.b64decode(b)), len(d)


def gen(serial_hex, out_pem, extfile):
    cmd = ["openssl", "x509", "-req", "-in", CSR, "-signkey", KEY,
           "-out", out_pem, "-days", "3650",
           "-set_serial", serial_hex, "-extfile", extfile]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        print(r.stderr.decode()[:400])
        raise SystemExit(1)


if __name__ == "__main__":
    subprocess.run(["openssl", "req", "-new", "-key", KEY, "-out", CSR,
                    "-subj", SUBJ], check=True, capture_output=True)
    extfile = tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False)
    extfile.write(EXT)
    extfile.close()
    found = None
    for nbytes in range(8, 40):
        serial = "0x01" + os.urandom(nbytes - 1).hex()
        gen(serial, "patch/newcert/try.pem", extfile.name)
        dl, pl = der_len("patch/newcert/try.pem")
        print("serial bytes=%d der=%d pem=%d" % (nbytes, dl, pl))
        if dl == TARGET_DER:
            found = (serial, dl, pl)
            break
    if not found:
        print("未找到等长组合")
        sys.exit(1)
    os.replace("patch/newcert/try.pem", "patch/newcert/exact_new.pem")
    print("OK serial=%s der=%d pem=%d" % found)
