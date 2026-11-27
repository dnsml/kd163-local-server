# -*- coding: utf-8 -*-
"""共存包生成器：改 AndroidManifest 的 package + label，给图标打角标，重签名。

用法：
    python work/apk_coexist.py <src.apk> <out.apk> --pkg cn.kd163.online \
           --label "口袋修仙·公网版" [--badge 公网] [--no-badge]

要点：
  * 字符串池按**整串相等**替换（不是子串），否则 com.kdxx.online 会把
    com.kdxx.online.recharge.RechargeActivity 这类真实类名一起改掉 → 找不到类。
  * 池内条目数量与顺序不变，所有 attribute 的 string index 继续有效。
  * 组件名原本就是绝对类名（com.aiyou.mhsj.*），改 package 不影响类解析。
"""
import os
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "work"))

import axmlpatch  # noqa: E402
import axml_dump  # noqa: E402
import repack_apk  # noqa: E402

RES_XML_START_ELEMENT = 0x0102


def attr_of(m, strings, pos, hsz, want):
    """取某个起始元素里名为 want 的属性值（字符串）。"""
    ns_i, name_i, a_start, a_size, a_count = struct.unpack_from("<IIHHH", m, pos + hsz)
    ap = pos + hsz + a_start
    out = []
    for k in range(a_count):
        a_ns, a_name, a_raw, v_size, v_res0, v_type, v_data = \
            struct.unpack_from("<IIIHBBI", m, ap + k * a_size)
        if strings[a_name] == want:
            out.append(strings[v_data] if v_type == 0x03 else "")
    return strings[name_i], out


def scan_manifest(m):
    """返回 (package, Labels集合)。"""
    base, psz, cnt, _sty, flags, _ss, _sst, strings = axmlpatch.read_pool(m)
    pkg = None
    labels = []
    pos = base + psz
    while pos + 8 <= len(m):
        t, hsz, sz = struct.unpack_from("<HHI", m, pos)
        if sz <= 0:
            break
        if t == RES_XML_START_ELEMENT:
            name, vals = attr_of(m, strings, pos, hsz, "package")
            if name == "manifest" and vals:
                pkg = vals[0]
            name, vals = attr_of(m, strings, pos, hsz, "label")
            labels.extend(v for v in vals if v)
        pos += sz
    return pkg, sorted(set(labels))


def patch_manifest(src, dst, old_pkg, new_pkg, old_label, new_label):
    m = open(src, "rb").read()
    ftype, fhsz, fsize = struct.unpack_from("<HHI", m, 0)
    base, psz, cnt, _sty, flags, _ss, _sst, strings = axmlpatch.read_pool(m)
    repl = {old_pkg: new_pkg}
    if old_label and new_label:
        repl[old_label] = new_label
    new, hit = [], 0
    for s in strings:
        ns = repl.get(s, s)          # 整串相等才换
        if ns != s:
            hit += 1
        new.append(ns)
    rest = m[base + psz:]
    pool = axmlpatch.build_pool(new, utf8=bool(flags & axmlpatch.UTF8_FLAG))
    out = bytearray(struct.pack("<HHI", ftype, fhsz, fhsz + len(pool) + len(rest)))
    out += pool + rest
    open(dst, "wb").write(bytes(out))
    return hit, len(out) - len(m), new, strings


def badge_icon(src_png, dst_png, text):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(src_png).convert("RGBA")
    w, h = im.size
    d = ImageDraw.Draw(im)
    band = int(h * 0.30)
    d.rectangle([0, h - band, w, h], fill=(230, 126, 34, 235))
    fs = int(band * 0.62)
    font = None
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, fs)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()
        text = text[:1]
    bb = d.textbbox((0, 0), text, font=font)
    d.text(((w - (bb[2] - bb[0])) / 2 - bb[0],
            h - band + (band - (bb[3] - bb[1])) / 2 - bb[1]),
           text, font=font, fill=(255, 255, 255, 255))
    im.save(dst_png, "PNG")


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit(__doc__)
    src, out = args[0], args[1]
    new_pkg, new_label, badge = None, None, None
    no_badge = "--no-badge" in args
    i = 2
    while i < len(args):
        if args[i] == "--pkg":
            new_pkg = args[i + 1]; i += 2
        elif args[i] == "--label":
            new_label = args[i + 1]; i += 2
        elif args[i] == "--badge":
            badge = args[i + 1]; i += 2
        else:
            i += 1
    if not new_pkg:
        raise SystemExit("必须给 --pkg")

    zin = zipfile.ZipFile(src)
    man = zin.read("AndroidManifest.xml")
    old_pkg, labels = scan_manifest(man)
    old_label = labels[0] if labels else None
    print("源包: %s" % src)
    print("旧 package = %s" % old_pkg)
    print("旧 label   = %s" % (labels or "(无)"))

    tmp = os.path.join(HERE, "work", "coex")
    os.makedirs(tmp, exist_ok=True)
    man_out = os.path.join(tmp, "AndroidManifest_coex.xml")
    open(man_out + ".in", "wb").write(man)   # patch_manifest 从文件读
    hit, delta, new_strings, old_strings = patch_manifest(
        man_out + ".in", man_out, old_pkg, new_pkg, old_label, new_label)
    print("manifest 替换 %d 条字符串，池长度变化 %+d 字节" % (hit, delta))

    left = [s for s in new_strings if s.startswith(old_pkg + ".")]
    if left:
        print("注意：以下类名仍用旧包名（正常，DEX 里就是这些类）：")
        for s in left:
            print("   %s" % s)

    replace = {"AndroidManifest.xml": man_out}

    if not no_badge and badge:
        for name in zin.namelist():
            if name.startswith("res/drawable") and name.endswith("/icon.png"):
                src_p = os.path.join(tmp, "icon_in.png")
                dst_p = os.path.join(tmp, name.replace("/", "_"))
                open(src_p, "wb").write(zin.read(name))
                badge_icon(src_p, dst_p, badge)
                replace[name] = dst_p
                print("图标角标: %s (%d B)" % (name, os.path.getsize(dst_p)))

    unsigned = os.path.join(tmp, "coex_unsigned.apk")
    repack_apk.build(src, replace, unsigned)
    print("重组完成: %s (%.1f MB)" % (unsigned, os.path.getsize(unsigned) / 1048576.0))
    print("下一步签名：")
    print('  "/c/Program Files/Java/jdk1.8.0_162/bin/jarsigner" -keystore patch/kd163.keystore '
          '-storepass kd163key -keypass kd163key -sigalg SHA1withRSA -digestalg SHA1 '
          '%s %s' % (unsigned, out))


if __name__ == "__main__":
    main()
