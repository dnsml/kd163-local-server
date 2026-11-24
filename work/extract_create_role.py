# -*- coding: utf-8 -*-
"""从 logs/watch.log 抽出真服「无角色登录 + 建角」原包，存成 res_cfg/create_role_tpl.json。

真服实测（2026-11-24 12:37-12:38，账号 954259122，MuMu）：
    C->S cmd=17  len=60   登录（账号存在但没角色）
    S->C cmd=1   len=2    01 a0  ← 这就是「无角色」信号，不是 17！
    C->S cmd=20  len=73   建角请求（昵称 + 性别）
    S->C cmd=20  len=160  建角应答，紧接着主动推 113 + 117

以前一直猜「截断 17」「17 里昵称置空」，全错。真服是**直接回 cmd=1 的 01a0**，
客户端收到就切到 CreateRoleScene。

用法：python work/extract_create_role.py
"""
import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = os.path.join(HERE, "logs", "watch.log")
OUT = os.path.join(HERE, "res_cfg", "create_role_tpl.json")


def main():
    txt = open(WATCH, encoding="utf-8").read().splitlines()
    got = {}
    cur = None
    for ln in txt:
        m = re.search(r"(C->S|S->C) cmd=(\d+) seq=(\d+) len=(\d+)", ln)
        if m:
            cur = (m.group(1), int(m.group(2)))
            continue
        m2 = re.search(r"hex=([0-9a-f]+)", ln)
        if m2 and cur:
            d, cmd = cur
            got["%s_%d" % (d, cmd)] = m2.group(1)
            cur = None

    need = ["C->S_17", "C->S_20", "S->C_20"]
    miss = [k for k in need if k not in got]
    if miss:
        raise SystemExit("watch.log 缺包：%s" % miss)

    # 建角应答拆成 头 + 尾：头 = 14 + 账号 + 昵称 + 性别 + 职业，尾固定 133B
    resp = bytes.fromhex(got["S->C_20"])
    tail = resp[26:]

    data = {
        "_note": "真服建角原包（2026-11-24 12:38 实测）",
        "req17": got["C->S_17"],
        "req20": got["C->S_20"],
        "resp20": got["S->C_20"],
        "resp20_tail": tail.hex(),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("已写 %s" % OUT)
    print("  req17  %d B" % (len(got["C->S_17"]) // 2))
    print("  req20  %d B" % (len(got["C->S_20"]) // 2))
    print("  resp20 %d B（头 26 + 尾 %d）" % (len(resp), len(tail)))
    print("  resp20 尾部 = %s" % tail.hex())


if __name__ == "__main__":
    main()
