"""831 合成 / 306 技能解锁 / 371 字段顺序 回归测试。

跑法：python work/test_synth.py
"""
import importlib.util
import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())
spec = importlib.util.spec_from_file_location("ls3", "local_server3.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []


def ck(name, cond, extra=""):
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        fails.append(name)


# ---- 1. 技能解锁（真服 19:20~19:21 实证）----
ck("未转职 Lv1 = 60101/60201", m.skills_for(6, 1) == [60101, 60201],
   str(m.skills_for(6, 1)))
ck("御剑 Lv10 = 10101", m.skills_for(1, 10) == [10101], str(m.skills_for(1, 10)))
ck("御剑 Lv12 = 10101/10201", m.skills_for(1, 12) == [10101, 10201],
   str(m.skills_for(1, 12)))
ck("御剑 Lv30 多出 10301（主动）",
   set([10301]).issubset(set(m.skills_for(1, 30))),
   str(m.skills_for(1, 30)))
# tape=5 是被动（永久加属性），不该进快捷栏；tape=4 破天一剑要技能点单独学。
ck("被动 11001/11101 不进 306",
   not set([11001, 11101]).intersection(set(m.skills_for(1, 50))),
   str(m.skills_for(1, 50)))
ck("破天一剑 10401 不自动下发",
   10401 not in m.skills_for(1, 60), str(m.skills_for(1, 60)))

# ---- 2. 306 包长（真服：1 条 10B，2 条 18B）----
b1 = m.build_306.__wrapped__ if hasattr(m.build_306, "__wrapped__") else None
m.PLAYER_CLASS = 1
m.PLAYER_LEVEL = 10
ck("306 单技能 10B", len(m.build_306()) == 10, "%dB" % len(m.build_306()))
ck("306 单技能 = 01 cd2775 ...",
   m.build_306().hex() == "01cd2775000000000000", m.build_306().hex())
m.PLAYER_LEVEL = 12
ck("306 双技能 18B", len(m.build_306()) == 18, "%dB" % len(m.build_306()))
ck("306 双技能 = 02 10101 10201",
   m.build_306().hex() == "02cd27750000000000cd27d9000000000000", m.build_306().hex())
m.PLAYER_CLASS = 6
m.PLAYER_LEVEL = 1
ck("306 未转职 = 02 60101 60201",
   m.build_306().hex() == "02cdeac50000000000cdeb29000000000000", m.build_306().hex())

# ---- 3. 831 合成（4 合 1 + Formula.xml 铜钱）----
# 真服 19:23:02 实证：初级灵石x4 -> 中级灵石，绑定铜钱 2252500 -> 2244500（-8000）
m.TONGQIAN[0] = 0
m.COIN_BOUND_local[0] = 100000
m.BAG.clear()
m.BAG[1] = [20141, 6]
out, msg = m.synth_apply(20141, 1, 4)
ck("初级灵石 4 合 1 出中级", out == 20142, "%s %s" % (out, msg))
ck("扣完剩 2 个", m.BAG.get(1, [0, 0])[1] == 2, str(m.BAG))
ck("中级灵石到手 x1",
   sum(v[1] for v in m.BAG.values() if v[0] == 20142) == 1, str(m.BAG))
ck("合成扣铜钱 8000（铜钱0 时扣绑铜）",
   m.COIN_BOUND_local[0] == 92000, "余绑铜 %d" % m.COIN_BOUND_local[0])

out2, msg2 = m.synth_apply(20141, 1, 4)
ck("只剩 2 个时拒绝", out2 is None, msg2)

m.BAG.clear()
m.BAG[1] = [20138, 8]
out3, _ = m.synth_apply(20138, 2, 4)
ck("仙玉 8 个可合 2 次 -> 中级仙玉 x2",
   out3 == 20139 and sum(v[1] for v in m.BAG.values() if v[0] == 20139) == 2,
   str(m.BAG))
ck("2 次共扣 16000", m.COIN_BOUND_local[0] == 76000, "余 %d" % m.COIN_BOUND_local[0])

# 铜钱优先于绑铜
m.TONGQIAN[0] = 5000
m.COIN_BOUND_local[0] = 100000
m.BAG.clear()
m.BAG[1] = [20141, 4]
m.synth_apply(20141, 1, 4)
ck("铜钱优先扣（5000 铜 + 3000 绑铜）",
   m.TONGQIAN[0] == 0 and m.COIN_BOUND_local[0] == 97000,
   "铜%d 绑%d" % (m.TONGQIAN[0], m.COIN_BOUND_local[0]))

m.TONGQIAN[0] = 0
m.COIN_BOUND_local[0] = 100

ck("特级石头不能继续合成", m.synth_apply(20456, 1, 4)[0] is None)
ck("普通道具不能合成", m.synth_apply(1001, 1, 4)[0] is None)

# ---- 4. 371 = [商品ID][商城分区][数量]（分区不是货币码）----
iid, i = m.bag_proto.dec_val(bytes.fromhex("cd4ead0205"), 0)
area, cnt = bytes.fromhex("cd4ead0205")[i], bytes.fromhex("cd4ead0205")[i + 1]
ck("371 cd4ead 02 05 -> 20141 area2 x5",
   iid == 20141 and area == 2 and cnt == 5, "id=%d area=%d cnt=%d" % (iid, area, cnt))
ck("area2 单价 3 元宝（Mall.xml）", m.equip_attr.mall_price(2, 20141) == (3, True),
   str(m.equip_attr.mall_price(2, 20141)))
ck("area1 百年妖丹 20735 单价 1 元宝",
   m.equip_attr.mall_price(1, 20735) == (1, True),
   str(m.equip_attr.mall_price(1, 20735)))
ck("area2 货币=元宝", m.equip_attr.AREA_CUR.get(2) == "yuanbao")
ck("area7 不是元宝（真服 880 元宝仍提示不足）",
   m.equip_attr.AREA_CUR.get(7) != "yuanbao", m.equip_attr.AREA_CUR.get(7))

print()
print("全部通过" if not fails else "失败 %d 项：%s" % (len(fails), fails))
sys.exit(1 if fails else 0)
