#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从抓包生成 cmd -> 响应 payload 模板表"""
import os, json, struct, glob

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(HERE, "capture")
OUT = os.path.join(HERE, "templates.json")


def parse(data):
    pkts = []
    pos = 0
    while pos + 8 <= len(data):
        total, cmd, seq = struct.unpack_from(">HHI", data, pos)
        if total < 6 or pos + 2 + total > len(data):
            break
        pkts.append((cmd, seq, data[pos + 8:pos + 2 + total]))
        pos += 2 + total
    return pkts


def main():
    tpl = {}
    meta = []
    for f in sorted(glob.glob(os.path.join(CAP, "*game*.bin"))):
        for cmd, seq, payload in parse(open(f, "rb").read()):
            if seq == 0 and len(payload) > 0:
                # seq=0 是服务端推送，也可作为模板候选
                key = "push_%d" % cmd
            else:
                key = str(cmd)
            # 只保留最长的版本（最完整的样本）
            if key not in tpl or len(payload) > len(tpl[key]):
                tpl[key] = payload.hex()
            meta.append((cmd, seq, len(payload)))

    json.dump(tpl, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("生成 %d 条模板 -> %s" % (len(tpl), OUT))
    print()
    print("%-8s %-8s %s" % ("cmd", "bytes", "hex(前32)"))
    print("-" * 76)
    for k in sorted(tpl, key=lambda x: (x.startswith("push"), x)):
        b = bytes.fromhex(tpl[k])
        print("%-8s %-8d %s" % (k, len(b), b[:32].hex()))


if __name__ == "__main__":
    main()
