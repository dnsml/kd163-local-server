# -*- coding: utf-8 -*-
"""形象卡（宠物 / 坐骑）自检：不动存档、不起服。

用法：
    python work/test_pet_skin.py

背景（2026-11-27 线上服抓包）：
    宠物形象卡 20962「美人鱼形象卡」→ 182 用掉后，947 的 off48 由
    cd2710(10000 功夫熊猫) 变 cd2721(10017 美人鱼)，宠物气血 2617→3619。
    坐骑形象卡 31624「年年有鱼形象」→ 30 包尾部坐骑块 cd2733(10035 筋斗云)
    变 cd2746(10054 年年有鱼)。
    两边的映射 Item.xml 里没有外键，靠名字/图标 + 抓包实证覆盖，见 pet_skin.py。

断言：
1. 映射表：20962→10017、31624→10054；坐骑本体（10035）不会混进「形象卡」
2. Player 默认 pet_skin=[10000]、mount=[0]
3. pack_947 不带形象参数 = 模板原样（cd2710、气血不变）
4. pack_947(skin=10017) 出现 cd2721，且气血 +1000
5. 存档 state_dict 带 pet_skin / mount_skin，load_state 能读回
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["KD_STATE_BACKEND"] = "json"

import pet_proto                                   # noqa: E402
import pet_skin                                    # noqa: E402
from player import Player, ctx, new as new_player  # noqa: E402


def main():
    fails = []

    def chk(name, cond, extra=""):
        print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + extra) if extra else ""))
        if not cond:
            fails.append(name)

    print("1. 映射表")
    chk("20962 美人鱼形象卡 -> 10017", pet_skin.pet_skin_of(20962) == 10017,
        "实际 %s" % pet_skin.pet_skin_of(20962))
    chk("31624 年年有鱼 -> 10054", pet_skin.mount_skin_of(31624) == 10054,
        "实际 %s" % pet_skin.mount_skin_of(31624))
    chk("坐骑本体 10035 不算形象卡", pet_skin.mount_skin_of(10035) is None)
    chk("未知道具 99999 返回 None", pet_skin.pet_skin_of(99999) is None)
    chk("宠物形象卡 >= 30 张", len(pet_skin.pet_cards()) >= 30,
        "实际 %d" % len(pet_skin.pet_cards()))
    chk("10017 名字是美人鱼", pet_skin.pet_skin_name(10017) == "美人鱼",
        pet_skin.pet_skin_name(10017))

    print("2. Player 默认值")
    pl = Player()
    chk("pet_skin 默认 10000", pl.pet_skin == [10000])
    chk("mount 默认 0", pl.mount == [0])

    print("3/4. pack_947 形象与气血")
    pet_proto.load()
    base0 = pet_proto.TPL["947"].get("empty")
    if not base0:
        print("  [SKIP] templates.json 里没有 947 模板，跳过 3/4")
    else:
        hp0 = pet_proto._hp_span(base0)[2]
        a = pet_proto.pack_947({}, 10000)
        b = pet_proto.pack_947({}, 10017)
        chk("默认形象写 cd2710", b"\xcd\x27\x10" in a)
        chk("美人鱼写 cd2721", b"\xcd\x27\x21" in b)
        hpb = pet_proto._hp_span(b)
        chk("气血 +1000", hpb and hpb[2] == hp0 + 1000,
            "原 %d -> %s" % (hp0, hpb[2] if hpb else "解析失败"))
        chk("长度不变（形象 id 同宽）", len(a) == len(b), "%d vs %d" % (len(a), len(b)))

    print("5. 存档字段")
    pl = new_player()
    with ctx(pl):
        import local_server3 as sv
        pl.has_char = True
        pl.name = "形象测试"
        pl.pet_skin[0] = 10017
        pl.mount_skin[0] = 10054
        d = sv.state_dict()
        chk("state_dict 带 pet_skin", d.get("pet_skin") == 10017, str(d.get("pet_skin")))
        chk("state_dict 带 mount_skin", d.get("mount_skin") == 10054, str(d.get("mount_skin")))
        chk("pet_skin_id() 读 10017", sv.pet_skin_id() == 10017)
        pl.pet_skin[0] = 10000
        chk("pet_skin_id() 回 10000", sv.pet_skin_id() == 10000)

    print("6. 182 使用链路（离线驱动 builtin_resp）")
    import local_server3 as sv

    pl = new_player()
    pl.has_char = True
    pl.name = "形象卡测试"
    pl.pets = [10000]
    pl.bag = {49: [20962, 1]}
    with ctx(pl):
        r = sv.builtin_resp(182, bytes([49, 1]))
        chk("ACK 是 182 空包", r[0] == 182 and r[1] == b"", "cmd=%s len=%d" % (r[0], len(r[1] or b"")))
        chk("推送序列 29/28/30/947/178/306", r[2] == [29, 28, 30, 947, 178, 306], str(r[2]))
        chk("道具已消耗", 49 not in pl.bag, str(pl.bag))
        chk("pet_skin 变 10017", pl.pet_skin == [10017], str(pl.pet_skin))

    pl2 = new_player()
    pl2.has_char = True
    pl2.name = "无宠测试"
    pl2.pets = []
    pl2.bag = {49: [20962, 1]}
    with ctx(pl2):
        r = sv.builtin_resp(182, bytes([49, 1]))
        chk("没有宠物时不消耗", 49 in pl2.bag and r[0] == 1, str(pl2.bag))

    pl3 = new_player()
    pl3.has_char = True
    pl3.name = "坐骑卡测试"
    pl3.bag = {10: [31624, 1]}
    with ctx(pl3):
        r = sv.builtin_resp(182, bytes([10, 1]))
        chk("坐骑形象卡保留（坐骑系统未实现）", 10 in pl3.bag and r[0] == 1, str(pl3.bag))

    print()
    if fails:
        print("失败 %d 项：%s" % (len(fails), "、".join(fails)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
