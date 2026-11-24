# -*- coding: utf-8 -*-
"""回归：148 必须原样回显 146 的技能号（新学技能点了放不出来）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import battle  # noqa: E402

CASES = [
    # (146 载荷, 期望 148[4:9], 说明)
    ("0107cd27d9", "0107cd27d9", "御剑 10201 九天剑阵 原样回显"),
    ("0107cd2af9", "0107cd2af9", "御剑 11001 原样回显"),
    ("0107cd283d", "0107cd283d", "御剑 10301 原样回显"),
    ("0107cdeac5", "0107cdeac5", "未转职 60101 原样回显"),
    ("040000", "0400000000", "防御不带技能号，不能补成技能"),
    ("010700", "0107cd2775", "无技能号的老包按职业兜底 10101"),
]


def main():
    bad = 0
    for hx, want, note in CASES:
        got = battle.build_round(bytes.fromhex(hx), cls=1)[4:9].hex()
        ok = got == want
        bad += 0 if ok else 1
        print("%s  %-14s -> %-14s %s" % ("PASS" if ok else "FAIL", hx, got, note))
    if bad:
        print("%d 项失败" % bad)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
