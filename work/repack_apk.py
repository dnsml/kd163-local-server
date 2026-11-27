# -*- coding: utf-8 -*-
"""重组 APK：只替换 classes.dex，其余 entry 原样保留（压缩方式/时间戳不变），
并对未压缩条目按 4 字节对齐（等价 zipalign）。"""
import struct
import sys
import zipfile


def align_extra(pos, name_len):
    """返回使数据区 4 字节对齐的 extra 字段字节。"""
    r = (pos + 30 + name_len) % 4
    need = (4 - r) % 4
    if need == 0:
        return b""
    total = need if need >= 4 else need + 4   # extra 最小长度 4（头）
    # 用保留 id 0x0000 作为 padding 块
    return b"\x00\x00" + struct.pack("<H", total - 4) + b"\x00" * (total - 4)


def build(src_apk, replace_map, out_apk):
    """replace_map: {entry 名: 新文件路径}"""
    zin = zipfile.ZipFile(src_apk)
    datas = {k: open(v, "rb").read() for k, v in replace_map.items()}
    zout = zipfile.ZipFile(out_apk, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
    replaced = 0
    for info in zin.infolist():
        name = info.filename
        if name.startswith("META-INF/") and name.upper().endswith((".SF", ".RSA", ".DSA", ".EC", ".MF")):
            continue  # 旧签名作废
        if name in datas:
            data = datas[name]
            replaced += 1
        else:
            data = zin.read(name)
        zi = zipfile.ZipInfo(name, date_time=info.date_time)
        zi.compress_type = info.compress_type
        zi.external_attr = info.external_attr
        zi.internal_attr = info.internal_attr
        zi.create_system = info.create_system
        if info.compress_type == zipfile.ZIP_STORED:
            zi.extra = align_extra(zout.fp.tell(), len(name.encode("utf-8")))
        zout.writestr(zi, data)
    zout.close()
    zin.close()
    return replaced


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    rmap = {}
    for kv in sys.argv[3:]:
        k, v = kv.split("=", 1)
        rmap[k] = v
    n = build(src, rmap, out)
    print("replaced entries:", n)
    print("wrote", out)
