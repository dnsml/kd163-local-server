# -*- coding: utf-8 -*-
"""背包 60 格上限自检（不动存档、不起服）。

用法：
    python work/test_bag_cap.py

背景（2026-11-26）：
    真服 178 只报 pos 0~59 = 60 格。旧 bag_add 没有上限，会一路写到 60/61…，
    客户端只画 60 格 → 物品「进了包但看不见」，玩家只能找 GM 删格。
    现在 bag_add 满包返回 None 并记日志；商城购买满包时先拦下、不扣钱。

断言：
1. 连发 60 件不同堆叠物 → 全部落在 0~59，第 61 件返回 None，件数仍是 60
2. 满包时同 id 还能叠的不算失败（走叠堆，不占新格）
3. bag_free_pos() 满包返回 None
4. load_state 会把 pos>=60 的越界物品搬回 0~59
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 本用例的 fixture 是临时 JSON 文件（patch 了 state_path），固定跑 JSON 后端；
# SQLite 后端的读档/落盘由 work/test_state_store.py 覆盖。
os.environ["KD_STATE_BACKEND"] = "json"

import local_server3 as sv                     # noqa: E402
from player import ctx, new as new_player      # noqa: E402

IID = 10305        # 飞仙戒指，配置表里真实存在
STACK_MAX = 1      # 每件占一格，方便把 60 格填满


def main():
    fails = []
    pl = new_player()
    with ctx(pl):
        pl.bag.clear()
        for i in range(sv.BAG_CAPACITY):
            p = sv.bag_add(IID, 1, stack_max=STACK_MAX)
            if p != i:
                fails.append("第%d件落在槽%r（应为 %d）" % (i + 1, p, i))
                break
        if len(pl.bag) != sv.BAG_CAPACITY:
            fails.append("填满后件数 %d（应 %d）" % (len(pl.bag), sv.BAG_CAPACITY))
        over = max(pl.bag) if pl.bag else -1
        if over >= sv.BAG_CAPACITY:
            fails.append("出现越界槽 %d" % over)

        p61 = sv.bag_add(IID, 1, stack_max=STACK_MAX)
        if p61 is not None:
            fails.append("第 61 件没被拦下（返回槽 %r）" % p61)
        if len(pl.bag) != sv.BAG_CAPACITY:
            fails.append("拦下后件数变成 %d" % len(pl.bag))
        if sv.bag_free_pos() is not None:
            fails.append("满包时 bag_free_pos 仍返回 %r" % sv.bag_free_pos())

        # 满包叠堆：同 id 且叠得上 → 允许
        pl.bag[0] = [IID, 1]
        p_stack = sv.bag_add(IID, 5, stack_max=99)
        if p_stack != 0 or pl.bag[0][1] != 6:
            fails.append("满包叠堆失败：返回 %r，槽0=%r" % (p_stack, pl.bag[0]))
        if not sv.bag_can_stack(IID, 1, stack_max=99):
            fails.append("bag_can_stack 对可叠物品返回 False")

        # 越界迁移：模拟老存档
        pl.bag.clear()
        pl.bag[0] = [IID, 1]
        pl.bag[73] = [IID, 2]
        saved = {"name": pl.name, "bag": {"0": [IID, 1], "73": [IID, 2]}}
        import json
        import tempfile
        import player as player_mod
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(saved, f)
        old_path = sv.state_path
        sv.state_path = lambda _pl=None: tmp
        try:
            sv.load_state(pl)
        finally:
            sv.state_path = old_path
            os.unlink(tmp)
        if 73 in pl.bag:
            fails.append("越界槽 73 读档后还在")
        if len(pl.bag) != 2:
            fails.append("迁移后件数 %d（应 2，格0 + 搬到格1）" % len(pl.bag))

    print("干净 60 格 / 满包拦截 / 叠堆 / 越界迁移：%s" % ("全部通过" if not fails else "FAIL"))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
