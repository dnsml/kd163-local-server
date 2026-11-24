#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装备属性 / 强化加成 / 29 属性包 / 合成配方 / 商城分区 —— 全部由客户端配置驱动。

数据来源（都是原样提取，不是猜的）：
  mirror/assets/configure/Equip.xml   strengthen_1..12（累计值）+ strengthen_N_gift + suite_id
  res_cfg/StrengthenAddOn.xml          强化大师/强化共鸣（id -> effect）
  res_cfg/Suite.xml                    套装件数效果 effect_2 / effect_3 / effect_4 / effect_6
  res_cfg/Formula.xml                  合成配方 + 铜钱费用 cost
  mirror/assets/configure/Mall.xml     商城分区（area）与价格 price

实测锚点（真服 logs/relay.log 18:20:20~18:30:58 连续 16 次强化，误差 0）：
  29.field0  战力  = 基数 + Σstrengthen_当前等级[53]
  29.field2  攻击下限 = 基数 + Σ[51]
  29.field3  攻击上限 = 基数 + Σ[52]
  29.field4  防御     = 基数 + Σ[7]
  29.field12/14/15/16 强化汇总 = 基数 + Σ[57]（field13 = field12 + 100）
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
EQUIP_XML = os.path.join(HERE, "mirror", "assets", "configure", "Equip.xml")
MALL_XML = os.path.join(HERE, "mirror", "assets", "configure", "Mall.xml")
ITEM_XML = os.path.join(HERE, "mirror", "assets", "configure", "Item.xml")
ADDON_XML = os.path.join(HERE, "res_cfg", "StrengthenAddOn.xml")
SUITE_XML = os.path.join(HERE, "res_cfg", "Suite.xml")
FORMULA_XML = os.path.join(HERE, "res_cfg", "Formula.xml")
LEVELUP_XML = os.path.join(HERE, "res_cfg", "Levelup.xml")

# 属性 id 语义（从 Equip.xml / StrengthenAddOn.xml 的用法反推）：
#   5 生命  6 暴击率  7 防御  8 抗暴率  9 命中  11 闪避  13 暴击  19 生命%
#   23 攻击%  25 伤害  37/39/41/45/47/49 五行抗性  51 攻击下限  52 攻击上限
#   53 攻击/战力  54 暴击率  55 命中/穿透  57 强化评分  58 伤害加成
#
# ★ 2026-11-23 20:30 实证修正：53 = 生命上限，51/52 = 攻击上下限，7 = 防御，
#   57 = 29 包 12~16 那一组，11 = 命中，23 = 敏捷。
#   依据：本地 29 模板 [1716,159,176,219,270,...] 与「Levelup 9 级基础 + 整套
#   神装 basic_effect + strengthen」逐字段相等（1716=272+1444、176=39+137、
#   219=45+174、270=0+270、39=3+36、50=40+10）。
HP_ATTR = 53
POWER_ATTR = 53          # 兼容旧引用（语义同 HP_ATTR）
ATK_MIN, ATK_MAX = 51, 52
DEF_ATTR = 7
SUM_ATTR = 57            # 29 包里 12/14/15/16 那一组的来源
HIT_ATTR = 11            # 命中
AGILE_ATTR = 23          # 敏捷
MP_ATTR = 0              # 未确认（先用 0 = 不加）
DODGE_ATTR = 0           # 未确认
CRIT_ATTR = 0            # 未确认
SPIRIT_ATTR = 0          # 未确认

_MAX_LV = 12


def _parse_effect(raw):
    """'53,35;7,7;57,26' -> {53: 35.0, 7: 7.0, 57: 26.0}"""
    out = {}
    for part in (raw or "").split(";"):
        if "," not in part:
            continue
        k, v = part.split(",", 1)
        try:
            out[int(k)] = float(v)
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------- Equip.xml
EQUIP_STR = {}      # (iid, lv) -> {attr: 累计值}
EQUIP_GIFT = {}     # (iid, lv) -> {attr: 该档 gift}
EQUIP_SUITE = {}    # iid -> suite_id
EQUIP_LVTEXT = {}   # iid -> level_text
EQUIP_BASE = {}     # iid -> {attr: basic_effect 基础属性}


def load_equip(path=EQUIP_XML):
    if not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    for it in root:
        try:
            iid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        EQUIP_SUITE[iid] = int(it.get("suite_id") or 0)
        EQUIP_LVTEXT[iid] = int(it.get("level_text") or 0)
        # basic_effect 是装备自带基础属性（不算强化）。本地 29 模板的
        # 1716 生命 = Levelup 9 级 272 + strengthen 565 + basic_effect 879，
        # 少了这一段，按等级重算出来的面板会全偏小。
        EQUIP_BASE[iid] = _parse_effect(it.get("basic_effect"))
        for lv in range(1, _MAX_LV + 1):
            raw = it.get("strengthen_%d" % lv)
            if raw:
                EQUIP_STR[(iid, lv)] = _parse_effect(raw)
            g = it.get("strengthen_%d_gift" % lv)
            if g:
                EQUIP_GIFT[(iid, lv)] = _parse_effect(g)
    return path


# ------------------------------------------------------- StrengthenAddOn.xml
ADDON = {}          # id -> {attr: value}


def load_addon(path=ADDON_XML):
    if not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    for it in root:
        try:
            ADDON[int(it.get("id"))] = _parse_effect(it.get("effect"))
        except (TypeError, ValueError):
            pass
    return path


# --------------------------------------------------------------- Suite.xml
SUITE = {}          # suite_id -> {2:{...}, 3:{...}, 4:{...}, 6:{...}}
SUITE_NAME = {}


def load_suite(path=SUITE_XML):
    if not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    for it in root:
        try:
            sid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        d = {}
        for n in (2, 3, 4, 6):
            e = _parse_effect(it.get("effect_%d" % n))
            if e:
                d[n] = e
        if d:
            SUITE[sid] = d
            SUITE_NAME[sid] = it.get("name") or ""
    return path


# ------------------------------------------------------------- Formula.xml
FORMULA = {}        # 产物 item_id -> {"mats": {mat: per}, "cost": int, "kind": int}


def load_formula(path=FORMULA_XML):
    if not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    for it in root:
        try:
            out = int(it.get("item_id"))
        except (TypeError, ValueError):
            continue
        mats = {}
        for part in (it.get("formula") or "").split(";"):
            if "," not in part:
                continue
            k, v = part.split(",", 1)
            try:
                mats[int(k)] = mats.get(int(k), 0) + int(v)
            except ValueError:
                pass
        if not mats:
            continue
        try:
            cost = int(it.get("cost") or 0)
        except ValueError:
            cost = 0
        FORMULA[out] = {"mats": mats, "cost": cost,
                        "kind": int(it.get("formula_kind") or 0)}
    return path


# ---------------------------------------------------------------- Mall.xml
MALL = {}           # (area, item_id) -> price


def load_mall(path=MALL_XML):
    if not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    for it in root:
        try:
            area = int(it.get("area"))
            iid = int(it.get("type"))
            price = int(it.get("price"))
        except (TypeError, ValueError):
            continue
        MALL[(area, iid)] = price
    return path


def init():
    load_equip()
    load_addon()
    load_suite()
    load_formula()
    load_mall()
    load_levelup()


# ------------------------------------------------------- Levelup.xml（等级基础属性）
# 2026-11-23 20:30 定位到「面板不随等级变化」的真因：
#   本地 29 包模板里的基础值是 **9 级** 的（hp272/mp159/atk39-45），
#   加上整套神装后正好等于 [1716,159,176,219,270,...]：
#     1716 = 272 + 1444（53 合计）   176 = 39 + 137（51 合计）
#     219  = 45 + 174（52 合计）     270 = 0 + 270（7 合计）
#   所以不管升到多少级，面板永远停在新手期数值。
LEVELUP = {}          # (cls, level) -> {字段: 值}


def load_levelup(path=LEVELUP_XML):
    if not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    for it in root:
        try:
            cls = int(it.get("class"))
            lv = int(it.get("level"))
        except (TypeError, ValueError):
            continue
        d = {}
        for k, v in it.attrib.items():
            if k in ("class", "level"):
                continue
            try:
                d[k] = float(v)
            except ValueError:
                pass
        LEVELUP[(cls, lv)] = d
    return LEVELUP


def level_stats(cls, level):
    """取该职业该等级的基础属性；等级超表取最高级，职业缺失回退 1。"""
    if not LEVELUP:
        return None
    cls = int(cls or 1)
    lv = int(level or 1)
    if (cls, lv) in LEVELUP:
        return LEVELUP[(cls, lv)]
    best = None
    for (c, l) in LEVELUP:
        if c == cls and (best is None or l > best):
            best = l
    d = LEVELUP.get((cls, best)) if best else None
    if d is None:
        d = level_stats(1, lv)
    return d



# ------------------------------------------------------------------ 汇总
def sum_strengthen(equip, enhance):
    """Σ 已穿装备的 strengthen_当前等级（累计值直接相加，不是增量）。"""
    agg = {}
    for slot, iid in equip.items():
        lv = int(enhance.get(slot, 0) or 0)
        if lv <= 0 or lv > _MAX_LV:
            continue
        for a, v in EQUIP_STR.get((int(iid), lv), {}).items():
            agg[a] = agg.get(a, 0.0) + v
    return agg


def sum_gift(equip, enhance):
    """Σ strengthen_N_gift（真服配置在 +5/+6/+7 与 +8~+12 两段）。"""
    agg = {}
    for slot, iid in equip.items():
        lv = int(enhance.get(slot, 0) or 0)
        if lv <= 0 or lv > _MAX_LV:
            continue
        for a, v in EQUIP_GIFT.get((int(iid), lv), {}).items():
            agg[a] = agg.get(a, 0.0) + v
    return agg


def min_level(equip, enhance):
    """全身已穿装备里最低的强化等级（强化大师档位判定用）。"""
    lvs = [int(enhance.get(s, 0) or 0) for s in equip if equip.get(s)]
    return min(lvs) if lvs else 0


def addon_effect(equip, enhance):
    """StrengthenAddOn：按「全身最低强化等级」取档。

    配置档位 7~12 与 40/50/60...；40 以上是另一套（装备阶数）体系，
    单件强化上限 +12，所以这里只取 <=12 的档，避免误触发 +4000 攻击。
    """
    mlv = min_level(equip, enhance)
    tier = 0
    for k in ADDON:
        if k <= 12 and k <= mlv and k > tier:
            tier = k
    if not tier:
        return {}, 0
    return ADDON.get(tier, {}), tier


def suite_effect(equip, enhance=None):
    """同套装件数效果：取件数最多的那套套装，按 2/3/4/6 件档给属性。"""
    cnt = {}
    for slot, iid in equip.items():
        sid = EQUIP_SUITE.get(int(iid), 0)
        if sid:
            cnt[sid] = cnt.get(sid, 0) + 1
    if not cnt:
        return {}, 0, 0
    sid = max(cnt, key=lambda k: (cnt[k], k))
    n = cnt[sid]
    tier = 0
    for th in (2, 3, 4, 6):
        if n >= th and th in SUITE.get(sid, {}):
            tier = th
    if not tier:
        return {}, sid, n
    return SUITE[sid][tier], sid, n


def sum_base(equip):
    """Σ 已穿装备的 basic_effect（装备自带白字属性，不含强化）。"""
    agg = {}
    for _slot, iid in equip.items():
        for a, v in EQUIP_BASE.get(int(iid), {}).items():
            agg[a] = agg.get(a, 0.0) + v
    return agg


def total_attrs(equip, enhance):
    """全身属性合计（basic_effect + strengthen + gift + 强化大师 + 套装件数）。"""
    agg = sum_base(equip)
    for src in (sum_strengthen(equip, enhance), sum_gift(equip, enhance),
                addon_effect(equip, enhance)[0], suite_effect(equip)[0]):
        for a, v in src.items():
            agg[a] = agg.get(a, 0.0) + v
    return agg


# --------------------------------------------------------------- 29 包
def power_of(hp_max):
    """战力（29.field25 / 17.off107 同一个数）。

    真服实证（同一账号、同一套神装，强化前 -> 强化后）：
      29 包 [1151,...,33,39,30,0,12,50,...,240,948]  -> 17 战力字段 948
      29 包 [1271,...,53,61,50,0,19,50,4,2,102,2,2,2,...,240,1020] -> 1020
      同号另一次 [1123,...,32,38,30,0,11,50,...,240,937] / [1067,...,32,38,30,0,8,50,...,240,225]
    => field25 与登录包 17 的战力字段恒等，所以两处必须用同一个函数算。
    跨账号基数不同（未转职号明显偏低），这里按同账号三组样本拟合
    hp*0.8237：1151->948 / 1123->925(真服937) / 1271->1047(真服1020)，
    误差 <3%，只求「随等级和强化单调上涨、量级正确」。
    """
    return int(round(float(hp_max) * 0.8237))


def compose29(tpl_tokens, level, agg, cls=1, min_enh=0, mp_add=0):
    """组装 cmd=29 属性面板（真服 26 字段）。

    ★ 2026-11-23 20:30 更正字段语义（之前把 [0] 当战力，其实 [0] 是生命）：
        [0] 生命上限   [1] 法力上限   [2] 攻下限   [3] 攻上限   [4] 防御
        [5] 闪避       [6] 命中       [7] 暴击     [8] 韧性     [9] 精神
        [10] 敏捷      [11] 强化档位  [12..16] 强化汇总组
        [17..23] 0     [24] 240       [25] 战力
    实证：
      - 真服 2 级新号 = [76,47,11,17,0,31,1,30,0,5,40,0,...,240,82]
        与 Levelup 2 级 hp76 mp47 atk11-17 hit1 dodge31 crit30 spirit5 agile40 完全一致
      - 真服 16 级 = [1798,327,220,265,230,55,63,50,0,28,50,4,...]
        mp327/dodge55/crit50 正好等于 Levelup 16 级，其余是装备加成
      - 本地旧模板 = 9 级基础 + 全套神装，所以升级后面板纹丝不动
    """
    t = list(tpl_tokens)
    if len(t) < 24:
        return None
    if len(t) == 26:
        out = list(t)
    else:
        head = t[:11]
        tail = t[11:]
        if len(tail) < 2:
            return None
        out = head + [0] * 7 + [tail[-2], tail[-1]]

    base = level_stats(cls, level)

    if base:
        out[0] = int(round(base.get("hp_max", 0) + agg.get(HP_ATTR, 0)))
        out[1] = int(round(base.get("mp_max", 0) + agg.get(MP_ATTR, 0)
                           + int(mp_add or 0)))
        out[2] = int(round(base.get("attack_min", 0) + agg.get(ATK_MIN, 0)))
        out[3] = int(round(base.get("attack_max", 0) + agg.get(ATK_MAX, 0)))
        out[4] = int(round(base.get("defense", 0) + agg.get(DEF_ATTR, 0)))
        out[5] = int(round(base.get("dodge", 0) + agg.get(DODGE_ATTR, 0)))
        out[6] = int(round(base.get("hit", 0) + agg.get(HIT_ATTR, 0)))
        out[7] = int(round(base.get("crit", 0) + agg.get(CRIT_ATTR, 0)))
        out[8] = int(round(base.get("tenacity", 0)))
        out[9] = int(round(base.get("spirit", 0) + agg.get(SPIRIT_ATTR, 0)))
        out[10] = int(round(base.get("agile", 0) + agg.get(AGILE_ATTR, 0)))
    else:
        # 没读到 Levelup.xml 时退回「模板 + 强化增量」的老算法
        out[0] = int(t[0]) + int(round(agg.get(HP_ATTR, 0)))
        out[2] = int(t[2]) + int(round(agg.get(ATK_MIN, 0)))
        out[3] = int(t[3]) + int(round(agg.get(ATK_MAX, 0)))
        out[4] = int(t[4]) + int(round(agg.get(DEF_ATTR, 0)))

    # [11] = 全身最低强化等级（强化大师按最低档生效）。真服 +4 全套 -> 4。
    # [12..16] 强化汇总组：真服 0 档全 0；4 档 = 164 / 264 / 164 / 164 / 164。
    # 中间档没有样本，按 4 档线性插值并封顶在 4 档，绝不超出真服观测范围。
    mn = max(0, int(min_enh or 0))
    if mn > 0:
        x = int(round(162.0 * min(mn, 4) / 4.0))
        out[11] = min(mn, 4)
        out[12] = 2 + x
        out[13] = 102 + x
        out[14] = out[15] = out[16] = 2 + x
    else:
        out[11] = 0
        out[12] = out[13] = out[14] = out[15] = out[16] = 0

    # [25] = 战力，与 17 包 off107 同源
    out[25] = power_of(out[0])
    return out


# ------------------------------------------------------------- 商城分区
# ★ 371 第二个字节是「商城分区(area)」，不是货币码。实证：
#   15:51:25 cd50ff 01 01 = 20735 百年妖丹（Mall area=1 price=1）→ 元宝 870->869
#   19:22:51 cd4ead 02 05 = 20141 初级灵石（Mall area=2 price=3）→ 元宝 1749->1734
#   15:32:55 cd4f61 07 01 = 20321 1级宝石盒（Mall area=7 price=30）→ 元宝 880 却回
#            「对应货币不足」⇒ area 7 不是元宝
AREA_CUR = {
    1: "yuanbao", 2: "yuanbao", 4: "yuanbao", 5: "yuanbao", 6: "yuanbao",
    8: "yuanbao", 10: "yuanbao", 11: "yuanbao", 13: "yuanbao",
    14: "yuanbao", 15: "yuanbao",
    7: "bind_yuanbao",     # 实证：不是元宝；与 area1 同商品但价更高，判为绑元专区
    9: "tongqian",         # 推测：单价 28000/30000 量级
    12: "tongqian",        # 推测：单价 1000~50000，sell_state=8
}


def mall_price(area, iid):
    """返回 (单价, 是否命中 Mall.xml)。"""
    p = MALL.get((int(area), int(iid)))
    if p is None:
        return None, False
    return p, True


if __name__ != "__main__":
    # 导入即加载：local_server3 / 测试脚本直接用，不用记得先 init()
    init()

if __name__ == "__main__":
    init()
    print("Equip 条数   :", len(EQUIP_STR), " gift 条数:", len(EQUIP_GIFT))
    print("AddOn 档位   :", sorted(ADDON))
    print("Suite 套数   :", len(SUITE), "示例:", list(SUITE.items())[:1])
    print("Formula 条数 :", len(FORMULA))
    print("Mall 条数    :", len(MALL))
    for k in (20139, 20140, 20142, 20143, 20456, 20457):
        f = FORMULA.get(k)
        print("  合成", k, "->", f)
    e = {7: 11114, 1: 11108}
    en = {7: 3, 1: 3}
    print("示例属性(武器+3/衣服+3):", total_attrs(e, en))
    print("强化大师档:", addon_effect(e, en))
