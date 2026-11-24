"""强化系统回归测试（概率 / 掉级保底 / 战力）。

跑法：
    python work/test_forge.py
全部打印 PASS / FAIL，最后一行是汇总。
"""
import importlib.util
import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())          # local_server3 依赖同目录的 battle/bag_proto
spec = importlib.util.spec_from_file_location("ls3", "local_server3.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m._load_equip_str()

fails = []


def ck(name, cond, extra=""):
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        fails.append(name)


# ---- 1. 掉级保底：3/6/9 掉不下去 ----
orig_rand = m.random.randint
m.random.randint = lambda a, b: 999          # 永远「失败」
for lv, want in [(0, 0), (1, 0), (2, 1), (3, 3), (4, 3), (5, 4),
                 (6, 6), (7, 6), (8, 7), (9, 9), (10, 9), (11, 10)]:
    ok, rate, nl = m.forge_decide(lv, 5)
    ck("失败 +%d -> +%d（期望 +%d）" % (lv, nl, want),
       (not ok) and nl == want, "rate=%d" % rate)
m.random.randint = orig_rand

# ---- 2. 成功率：石头加成生效、封顶 100、等级越高越低 ----
for lv, luck, want in [(0, 5, 100), (1, 5, 100), (2, 5, 95),
                       (6, 15, 85), (11, 15, 60)]:
    _, rate, _ = m.forge_decide(lv, luck)
    ck("成功率 +%d 石+%d%% = %d%%（期望 %d%%）" % (lv, luck, rate, want),
       rate == want)
ck("成功率随等级递减",
   m.forge_decide(2, 0)[1] > m.forge_decide(8, 0)[1] > m.forge_decide(11, 0)[1])

# ---- 3. 必成开关 ----
m.FORGE_LUCK[0] = False
m.random.randint = lambda a, b: 999
ck("必成开关下 999 也算成功", m.forge_decide(5, 5)[0] is True)
m.random.randint = orig_rand
m.FORGE_LUCK[0] = True

# ---- 4. 生命/攻击：11108 每级加成与真服 29 包逐条对齐 ----
ck("11108 strengthen_1 的 53 = 35", m.EQUIP_STR[(11108, 1)].get(53) == 35)
ck("11108 strengthen_2 的 53 = 53", m.EQUIP_STR[(11108, 2)].get(53) == 53)
ck("11108 strengthen_3 的 53 = 70", m.EQUIP_STR[(11108, 3)].get(53) == 70)
ck("武器 11114 强化不加 53（真服也只强化武器时生命不动）",
   m.EQUIP_STR[(11114, 3)].get(53) is None)

# ---- 5. build_29：26 字段 / 首字段=生命上限 / 强化生效 ----
m.EQUIP.clear()
m.ENHANCE.clear()
for s in range(1, 8):
    m.EQUIP[s] = 11107 + s
base = m.power_now()
p0 = m.build_29()
n, i = 0, 0
while i < len(p0):
    _, i = m.vread(p0, i)
    n += 1
ck("29 包 26 字段（真服口径）", n == 26, "fields=%d len=%dB" % (n, len(p0)))
ck("29 包长度 38B", len(p0) == 38, "len=%d" % len(p0))
ck("field0 = 生命上限 = Levelup+装备",
   m.power_now() == m.panel_attr("hp_max") and m.power_now() > 0,
   "hp=%d" % m.power_now())
m.ENHANCE[1] = 1
ck("槽1 +1 生命 +35", m.power_now() - base == 35,
   "%d -> %d" % (base, m.power_now()))
m.ENHANCE[1] = 2
ck("槽1 +2 生命 +53", m.power_now() - base == 53, "now %d" % m.power_now())
p2 = m.build_29()
ck("29 包随强化变长/变值", p2 != p0)

# ---- 6. 面板随等级增长（Levelup.xml 驱动）----
lv_now = m.PLAYER_LEVEL
low = m.panel_attr("hp_max")
m.PLAYER_LEVEL = lv_now + 10
high = m.panel_attr("hp_max")
m.PLAYER_LEVEL = lv_now
ck("等级 +10 生命上限上升", high > low, "%d -> %d" % (low, high))

print()
print("汇总：%d 项通过，%d 项失败" % (0 if fails else 1, len(fails)) if fails else "全部通过")
sys.exit(1 if fails else 0)
