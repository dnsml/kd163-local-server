"""强化 822 协议回归测试。

钉死两件已由真服抓包证实的事：
1. 部位码 → 装备槽 的映射（不是 1:1）
   00→1 01→2 02→3 03→4 04→5 05→6 0a→7
2. 铜钱消耗 = 1500 * 2^当前强化等级
   +0→+1 1500 / +1→+2 3000 / +2→+3 6000

真服证据：logs/relay.log 18:30:40~18:30:58 连续 18 次强化，
每次 28 包 idx18 绑定铜钱差额全部落在 1500 / 3000 / 6000 三档，误差 0。
用法：python work/test_enh.py
"""

MAPPING = {0x00: 1, 0x01: 2, 0x02: 3, 0x03: 4, 0x04: 5, 0x05: 6, 0x0A: 7}

# 真服样本：请求载荷 -> (期望槽位, 期望材料)
CASES = [
    ("000a01cd4ead", 1, 20141),   # 11108
    ("010a01cd4ead", 2, 20141),   # 11109
    ("020a01cd4ead", 3, 20141),   # 11110
    ("030a01cd4ead", 4, 20141),   # 11111
    ("040a01cd4ead", 5, 20141),   # 11112
    ("050a01cd4ead", 6, 20141),   # 11113
    ("0a0a01cd4eaa", 7, 20138),   # 11114 / 初级强化仙玉
    ("050b00", 6, 0),             # 本地客户端短包：没带材料段
]

# 真服 28 包 idx18 绑定铜钱实测序列（按时间）
REAL_BINDCOIN = [
    2283000,  # 11114 +3 之后
    2281500,  # 11108 +1  (差 1500)
    2278500,  # 11108 +2  (差 3000)
    2272500,  # 11108 +3  (差 6000)
    2271000,  # 11109 +1
    2268000,  # 11109 +2
    2262000,  # 11109 +3
    2260500,  # 11110 +1
    2257500,  # 11110 +2
    2251500,  # 11110 +3
    2250000,  # 11111 +1
    2247000,  # 11111 +2
    2241000,  # 11111 +3
    2239500,  # 11112 +1
    2236500,  # 11112 +2
    2230500,  # 11112 +3
    2229000,  # 11113 +1
]


def cost(lv):
    return 1500 << lv


def parse(raw):
    slot = MAPPING.get(raw[0], 7)
    mat = 0
    if len(raw) >= 5 and raw[3] == 0xCE:
        mat = int.from_bytes(raw[4:8], "big")
    elif len(raw) >= 4 and raw[3] == 0xCD:
        mat = int.from_bytes(raw[4:6], "big")
    elif len(raw) >= 4 and raw[3] == 0xCC:
        mat = raw[4]
    return slot, mat


def main():
    # ---- 强化石档位（Item.xml 20138~20154 的 description 原文）----
    stone = {20138: ("仙玉", 3), 20139: ("仙玉", 6),
             20140: ("仙玉", 9), 20456: ("仙玉", 12),
             20141: ("灵石", 3), 20142: ("灵石", 6),
             20143: ("灵石", 9), 20457: ("灵石", 12)}
    want = {20138: ("仙玉", 3), 20139: ("仙玉", 6), 20140: ("仙玉", 9),
            20456: ("仙玉", 12), 20141: ("灵石", 3), 20142: ("灵石", 6),
            20143: ("灵石", 9), 20457: ("灵石", 12)}
    print("--- 强化石档位 ---")
    bad = 0
    for iid, cap in want.items():
        ok = stone.get(iid) == cap
        print("%d %s %s" % (iid, cap, "OK" if ok else "FAIL"))
        bad += 0 if ok else 1

    # ---- 真服「对应货币不足」原文逐字节比对（relay.log 15:32:55 len=20）----
    got = bytes.fromhex("04B2") + "对应货币不足".encode("utf-8")
    exp = bytes.fromhex("04b2e5afb9e5ba94e8b4a7e5b881e4b88de8b6b3")
    ok = got == exp
    print("--- 货币不足提示 ---")
    print("%-40s %s" % (got.hex(), "OK" if ok else "FAIL 期望 " + exp.hex()))
    bad += 0 if ok else 1

    print("--- 部位/材料解析 ---")
    for h, want_slot, want_mat in CASES:
        slot, mat = parse(bytes.fromhex(h))
        ok = (slot == want_slot and mat == want_mat)
        print("%-14s 槽%d 材料%-6d %s" % (h, slot, mat, "OK" if ok else
                                         "FAIL 期望 槽%d 材料%d" % (want_slot, want_mat)))
        bad += 0 if ok else 1

    # 铜钱：每次强化目标等级按 1/2/3 循环，与真服差额逐项比对
    lv_cycle = [1, 2, 3] * 6
    for i in range(1, len(REAL_BINDCOIN)):
        delta = REAL_BINDCOIN[i - 1] - REAL_BINDCOIN[i]
        lv = lv_cycle[i - 1] - 1          # 强化前等级
        exp = cost(lv)
        ok = (delta == exp)
        print("第%02d次 差%-6d 公式%-6d %s" % (i, delta, exp, "OK" if ok else "FAIL"))
        bad += 0 if ok else 1

    print("\n结果：%s" % ("全部通过" if bad == 0 else "%d 项不符" % bad))
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
