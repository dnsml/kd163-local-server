# -*- coding: utf-8 -*-
"""从 relay.log 抽出宠物相关真服原包，生成 pet_tpl.json。

只抽 2026-11-23 21:03 那次「真服开宠物界面 + 上阵 3 个妖灵」的样本：
  962 一份（40B，两组 uid + 04 + 选中位）
  947 四份（0/1/2/3 个妖灵上阵）
原包逐字节保留，本地服直接回放，避免猜结构把客户端打崩。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "logs", "relay.log")
OUT = os.path.join(HERE, "res_cfg", "pet_tpl.json")

WANT = {
    99: "0",     # 无妖灵
    101: "1101",
    103: "1101+2101",
    105: "1101+2101+4101",
}


def main():
    t962 = None
    t947 = {}
    for ln in open(LOG, encoding="utf-8", errors="ignore"):
        m = re.search(r'\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s+S->C cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=(\S*)', ln)
        if not m:
            continue
        t, cmd, seq, ln_, hx = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
        if t < "21:03:30" or t > "21:04:10":
            continue
        if cmd == 962 and t962 is None and len(hx) // 2 == 40:
            t962 = hx
        # 99B（无妖灵）那份是真服回 946 的应答（seq 非 0），其余是变更后的推送
        if cmd == 947 and ln_ in WANT and WANT[ln_] not in t947:
            t947[WANT[ln_]] = hx
    data = {"962": t962, "947": t947}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("962 len=%s" % (len(t962) // 2 if t962 else None))
    for k, v in t947.items():
        print("947[%s] len=%d" % (k, len(v) // 2))


if __name__ == "__main__":
    main()
