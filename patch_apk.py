#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd163 改包器：把自己的服务器地址/证书写进原版 APK，重签名成可独立安装的包。

做四件事：
  1. 把 dex 里 7 处 http(s)://124.223.56.189 换成目标 host（原地改写，长度只许变短，多余字节补 0）
  2. 把 dex 里内嵌的自签证书 PEM（信任根）换成自己的证书（同样只许变短）
  3. 重算 dex header 的 adler32 / sha1
  4. 重打包 + v1 (JAR) 签名，删掉原签名文件

用法：
  python patch_apk.py --src kd163.apk --out kd163_own.apk --host 192.168.0.111 \
                      --cert patch/srv.pem --key patch/srv.key
"""
import os
import io
import sys
import shutil
import struct
import hashlib
import zlib
import base64
import zipfile
import argparse
import subprocess
import tempfile

OLD_HOST_B = b"124.223.56.189"


def uleb_read(buf, o):
    r = 0
    s = 0
    while True:
        b = buf[o]
        o += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            break
        s += 7
    return r, o


def uleb_write(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def pad_host(src, old, new_host, pad=b"/"):
    """把 src 里的 old 换掉；新 host 更短时用 pad 补到原长度。

    为什么必须补：dex 的 string_data_item 在 data 段里是**顺序排布**的，ART 校验
    会按 map 顺序逐个往下走。原地缩短后槽尾剩下的字节会被当成下一条
    string_data_item，校验直接失败（真机报 `String longer than indicated size 0`，
    ClassNotFoundException → 闪退）。所以只能等长替换。

    补的位置在 `host[:port]` 之后、path 之前：
        http://192.168.0.111//assets.txt
        https://192.168.0.111:9000//auth
    服务端 local_http.py / local_server3.py 会把连续斜杠折叠成一条。
    """
    i = src.find(old)
    if i < 0:
        return None
    out = src[:i] + new_host.encode() + src[i + len(old):]
    if len(out) >= len(src):
        return out
    short = len(src) - len(out)
    # 找 host:port 结束位置
    h = out.find(b"://")
    p = out.find(new_host.encode(), h + 3) if h >= 0 else -1
    if p >= 0:
        p += len(new_host)
        if out[p:p + 1] == b":":
            q = p + 1
            while q < len(out) and 48 <= out[q] <= 57:
                q += 1
            p = q
    else:
        p = len(out)
    return out[:p] + pad * short + out[p:]


def patch_dex(dex, new_host, new_pem, allow_shrink=False, pad_slash=False):
    """原地替换字符串。

    默认**等长**替换（dex 的字符串区必须连续，长度一变就得整体重排）。
    但换一个更短的 IP 是常事（124.223.56.189 → 192.168.0.111），
    这时传 allow_shrink=True：改写 string_data_item 的 uleb128 长度前缀，
    新串写进**原槽位**、补 0x00 结尾，槽尾多余字节留着不引用（ART 只按
    长度前缀读，槽尾字节不可达）。string_ids 里的偏移是绝对的，不用动任何表。
    """
    d = bytearray(dex)
    u32 = lambda buf, o: struct.unpack_from("<I", buf, o)[0]
    str_size = u32(d, 0x38)
    str_ids_off = u32(d, 0x3c)

    changed = 0
    shrunk = 0
    for i in range(str_size):
        off = u32(d, str_ids_off + i * 4)
        ln, body = uleb_read(d, off)
        pre_len = body - off
        cur = bytes(d[body:body + ln])

        new = None
        if OLD_HOST_B in cur:
            cand = pad_host(cur, OLD_HOST_B, new_host) if pad_slash else None
            if cand is None:
                cand = cur.replace(OLD_HOST_B, new_host.encode())
            if cand != cur:
                new = cand
        elif b"BEGIN CERTIFICATE" in cur:
            new = new_pem

        if new is None:
            continue
        tag = "证书" if b"BEGIN CERTIFICATE" in cur else "地址"
        if len(new) == ln:
            d[body:body + ln] = new
        elif allow_shrink and len(new) < ln:
            newpre = uleb_write(len(new))
            if len(newpre) != pre_len:
                raise SystemExit(
                    f"[!] idx={i} 长度前缀字节数会从 {pre_len} 变成 {len(newpre)}，"
                    "这种要整体重排，放弃。")
            d[off:body] = newpre                     # 新长度前缀
            d[body:body + len(new)] = new            # 新串
            d[body + len(new)] = 0                   # 结尾 NUL
            # 槽尾 (ln - len(new)) 字节不再被引用，保持原样
            shrunk += 1
        else:
            raise SystemExit(
                f"[!] idx={i} 新长度 {len(new)} > 旧长度 {ln}，等长/变短都做不到。\n"
                f"    换更短的目标 host，或换一张更短的证书。")
        changed += 1
        print(f"  [{tag}] idx={i:<6} {ln:>5} → {len(new):>5} 字节"
              f"{'（变短·槽尾留空）' if len(new) < ln else '（等长）'}"
              f"  {cur[:44].decode('utf-8','replace')}")

    d[12:32] = hashlib.sha1(bytes(d[32:])).digest()
    struct.pack_into("<I", d, 8, zlib.adler32(bytes(d[12:])) & 0xFFFFFFFF)
    print(f"  共替换 {changed} 个字符串（其中变短 {shrunk} 个；"
          f"dex 长度不变：{len(d):,} 字节）")
    return bytes(d)


def patch_trust(dex):
    """把唯一实现 checkServerTrusted 的方法体改成 return-void（长度不变）"""
    d = bytearray(dex)
    u32 = lambda b, o: struct.unpack_from("<I", b, o)[0]
    u16 = lambda b, o: struct.unpack_from("<H", b, o)[0]
    S, So = u32(d, 0x38), u32(d, 0x3c)
    T, To = u32(d, 0x40), u32(d, 0x44)
    M, Mo = u32(d, 0x58), u32(d, 0x5c)
    C, Co = u32(d, 0x60), u32(d, 0x64)

    def s_at(i):
        off = u32(d, So + i * 4)
        ln, o = uleb_read(d, off)
        return bytes(d[o:o + ln]).decode("utf-8", "replace")

    target_name = None
    for i in range(S):
        if s_at(i) == "checkServerTrusted":
            target_name = i
            break
    mids = set()
    for m in range(M):
        if u32(d, Mo + m * 8 + 4) == target_name:
            mids.add(m)

    patched = 0
    for k in range(C):
        base = Co + k * 32
        cdata = u32(d, base + 24)
        if cdata == 0:
            continue
        o = cdata
        sf, o = uleb_read(d, o); inf, o = uleb_read(d, o)
        dm, o = uleb_read(d, o); vm, o = uleb_read(d, o)
        for _ in range(sf + inf):
            _, o = uleb_read(d, o); _, o = uleb_read(d, o)
        for cnt in (dm, vm):
            prev = 0
            for _ in range(cnt):
                diff, o = uleb_read(d, o); acc, o = uleb_read(d, o); code, o = uleb_read(d, o)
                prev += diff
                if prev in mids and code:
                    isz = u32(d, code + 12)
                    if isz >= 1:
                        struct.pack_into("<H", d, code + 16, 0x000E)      # return-void
                        for j in range(1, isz):
                            struct.pack_into("<H", d, code + 16 + j * 2, 0x0000)  # nop
                        patched += 1
                        print(f"  方法体已补丁：code_off=0x{code:x} insns_size={isz}")
    if not patched:
        print("  !! 没找到可补丁的方法")
    d[12:32] = hashlib.sha1(bytes(d[32:])).digest()
    struct.pack_into("<I", d, 8, zlib.adler32(bytes(d[12:])) & 0xFFFFFFFF)
    return bytes(d)


def build_manifest(entries):
    """entries: [(name, bytes)] -> (MANIFEST.MF bytes, {name: section bytes})"""
    out = io.BytesIO()
    out.write(b"Manifest-Version: 1.0\r\nCreated-By: 1.0 (Android)\r\n\r\n")
    sections = {}
    for name, data in entries:
        sec = (b"Name: " + name.encode() +
               b"\r\nSHA-256-Digest: " +
               base64.b64encode(hashlib.sha256(data).digest()) + b"\r\n\r\n")
        sections[name] = sec
        out.write(sec)
    return out.getvalue(), sections


def build_sf(manifest, sections):
    """.SF：整体摘要 + 每个条目的 section 摘要（Android 校验 AndroidManifest.xml 必须有这一段）"""
    out = io.BytesIO()
    out.write(b"Signature-Version: 1.0\r\n")
    out.write(b"Created-By: 1.0 (Android)\r\n")
    out.write(b"SHA-256-Digest-Manifest: " +
              base64.b64encode(hashlib.sha256(manifest).digest()) + b"\r\n\r\n")
    for name, sec in sections.items():
        out.write(b"Name: " + name.encode() + b"\r\n")
        out.write(b"SHA-256-Digest: " +
                  base64.b64encode(hashlib.sha256(sec).digest()) + b"\r\n\r\n")
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--cert", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--name", default="ONLINE-OWN")
    ap.add_argument("--patch-trust", action="store_true",
                    help="把 checkServerTrusted 补成直接 return-void")
    ap.add_argument("--no-strings", action="store_true",
                    help="不动字符串（只做其他补丁）")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="允许把字符串换成更短的（改写 uleb128 长度前缀，槽尾留空）。"
                         "★ Android 侧会整包验证失败（String longer than indicated size），别用")
    ap.add_argument("--pad-slash", action="store_true",
                    help="新 host 更短时用 '/' 补足等长（推荐）："
                         "http://192.168.0.111//assets.txt，服务端折叠斜杠")
    a = ap.parse_args()

    pem = open(a.cert, "rb").read()
    if not pem.endswith(b"\n"):
        pem += b"\n"

    zin = zipfile.ZipFile(a.src)
    print(f"[1/4] 读取 {a.src}")
    dex = zin.read("classes.dex")
    newdex = dex
    if not a.no_strings:
        print(f"[2/4] 打补丁 dex（host={a.host}）")
        newdex = patch_dex(dex, a.host, pem,
                           allow_shrink=a.allow_shrink, pad_slash=a.pad_slash)
    if a.patch_trust:
        print("[2/4] 补丁 checkServerTrusted -> return-void")
        newdex = patch_trust(newdex)

    # 收集所有条目（保持原顺序/压缩方式），换掉 dex 和 META-INF 签名
    items = []
    for info in zin.infolist():
        n = info.filename
        if n.startswith("META-INF/") and (n.endswith(".SF") or n.endswith(".RSA") or n.endswith(".DSA")
                                          or n.endswith("MANIFEST.MF")):
            continue
        data = newdex if n == "classes.dex" else zin.read(n)
        items.append((n, data, info.compress_type))

    payload = [(n, dt) for n, dt, _ in items]
    manifest, sections = build_manifest(payload)
    sf = build_sf(manifest, sections)

    print("[3/4] 重打包")
    tmpdir = tempfile.mkdtemp(prefix="apksign_")
    sf_path = os.path.join(tmpdir, "CERT.SF")
    rsa_path = os.path.join(tmpdir, "CERT.RSA")
    open(sf_path, "wb").write(sf)
    subprocess.check_call([
        "openssl", "smime", "-sign", "-binary", "-noattr",
        "-in", sf_path, "-signer", a.cert, "-inkey", a.key,
        "-outform", "DER", "-out", rsa_path,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(a.out):
        os.remove(a.out)
    with zipfile.ZipFile(a.out, "w") as zout:
        for n, dt, ct in items:
            zout.writestr(n, dt, compress_type=ct)
        zout.writestr("META-INF/MANIFEST.MF", manifest)
        zout.writestr("META-INF/%s.SF" % a.name, sf)
        zout.writestr("META-INF/%s.RSA" % a.name, open(rsa_path, "rb").read())

    shutil.rmtree(tmpdir, ignore_errors=True)
    size = os.path.getsize(a.out)
    print(f"[4/4] 输出 {a.out}  {size:,} 字节")
    print("      签名: v1 (JAR)，META-INF/%s.SF" % a.name)


if __name__ == "__main__":
    main()
