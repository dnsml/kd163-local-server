# -*- coding: utf-8 -*-
"""做共存版 APK：改包名（等长 UTF-16 替换）+ 改应用名，v1 重签。

用法：python patch_coexist.py
输入  patch/kd163_v2.apk  输出  patch/kd163_coexist.apk
"""
import io
import os
import re
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch_apk import build_manifest, build_sf, patch_dex, patch_trust, OLD_HOST_B  # noqa: E402

# 基于 orig.apk 做：换内嵌 PEM 为 exact.pem（本地服出示的证书）+ 放宽 TLS + 改包名。
# 路由靠 per-UID DNAT（switch.sh coexist）：只有本包 uid 的 9000/80 流量进本地服，
# orig.apk（真服）完全不受影响 —— 两包可同时安装、同时在线。
# 注意：patch_trust 单独用不行（kd163_trust.apk 实测登录 TLS 被拒），
#       必须换内嵌 PEM（1151 字节槽位，exact.pem 按此长度精确生成，v2 已验证可行）。
SRC = 'orig.apk'
OUT = 'patch/kd163_coexist.apk'
CERT = 'patch/exact.pem'
KEY = 'patch/exact.key'

OLD_PKG = 'com.kdxx.online'          # 15 字符
NEW_PKG = 'com.kdxx.local1'          # 15 字符，等长替换不破坏 AXML 偏移
OLD_LABEL = '口袋修仙·联网复活版'      # manifest 字符串池里的应用名（实测在 AXML，不在 arsc）
NEW_LABEL = '口袋修仙·本机服务版'      # 同 10 个字符（·也占 1 个），等长替换


def utf16_bytes(s):
    return s.encode('utf-16-le')


def main():
    zin = zipfile.ZipFile(SRC)
    items = []
    for info in zin.infolist():
        n = info.filename
        if n.startswith('META-INF/') and re.search(r'\.(SF|RSA|DSA)$|MANIFEST\.MF$', n):
            continue
        data = zin.read(n)
        if n == 'classes.dex':
            # host 保持不变（per-UID DNAT 负责分流），只换内嵌 PEM + 放宽校验
            data = patch_dex(data, OLD_HOST_B.decode(), open(CERT, 'rb').read())
            data = patch_trust(data)
            print('[1/3] classes.dex: 内嵌 PEM -> exact.pem，checkServerTrusted -> return-void')
        if n == 'AndroidManifest.xml':
            old = utf16_bytes(OLD_PKG)
            # 只替换「独立字符串」：前导 u16 长度必须是 15。
            # com.kdxx.online 同时是两个 Activity 类名的前缀
            #（com.kdxx.online.recharge.*），那些的前导长度是 45/44，绝不能动，
            # 否则类名和 DEX 对不上，启动即崩。
            pos = 0
            cnt = 0
            while True:
                i = data.find(old, pos)
                if i < 0:
                    break
                if i >= 2 and int.from_bytes(data[i - 2:i], 'little') == len(OLD_PKG):
                    data = data[:i] + utf16_bytes(NEW_PKG) + data[i + len(old):]
                    cnt += 1
                    pos = i + len(utf16_bytes(NEW_PKG))
                else:
                    pos = i + 1
            assert cnt == 1, '独立包名出现 %d 次' % cnt
            print('[1/3] AndroidManifest.xml: %s -> %s' % (OLD_PKG, NEW_PKG))
        # 应用名：manifest 字符串池 + arsc 都试（UTF-8 / UTF-16 各一次，等长）
        for target in ('AndroidManifest.xml', 'resources.arsc'):
            if n != target:
                continue
            o8 = data.count(OLD_LABEL.encode('utf-8'))
            o16 = data.count(utf16_bytes(OLD_LABEL))
            if o8 or o16:
                data = data.replace(OLD_LABEL.encode('utf-8'), NEW_LABEL.encode('utf-8'))
                data = data.replace(utf16_bytes(OLD_LABEL), utf16_bytes(NEW_LABEL))
                print('[1/3] %s 应用名: %s -> %s (utf8 x%d, utf16 x%d)'
                      % (target, OLD_LABEL, NEW_LABEL, o8, o16))
        items.append((n, data, info.compress_type))

    payload = [(n, d) for n, d, _ in items]
    manifest, sections = build_manifest(payload)
    sf = build_sf(manifest, sections)

    tmpdir = tempfile.mkdtemp(prefix='coexist_')
    sf_path = os.path.join(tmpdir, 'CERT.SF')
    rsa_path = os.path.join(tmpdir, 'CERT.RSA')
    open(sf_path, 'wb').write(sf)
    subprocess.check_call([
        'openssl', 'smime', '-sign', '-binary', '-noattr',
        '-in', sf_path, '-signer', CERT, '-inkey', KEY,
        '-outform', 'DER', '-out', rsa_path,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(OUT):
        os.remove(OUT)
    with zipfile.ZipFile(OUT, 'w') as zout:
        for n, d, ct in items:
            zout.writestr(n, d, compress_type=ct)
        zout.writestr('META-INF/MANIFEST.MF', manifest)
        zout.writestr('META-INF/COEXIST.SF', sf)
        zout.writestr('META-INF/COEXIST.RSA', open(rsa_path, 'rb').read())
    print('[3/3] 输出 %s  %s 字节' % (OUT, format(os.path.getsize(OUT), ',')))


if __name__ == '__main__':
    main()
