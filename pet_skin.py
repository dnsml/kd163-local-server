# -*- coding: utf-8 -*-
"""形象卡 → 形象 id 映射（宠物 / 坐骑）。

为什么单独一个模块
------------------
Item.xml 里的形象卡**没有外键**指向 Pet.xml / Horse.xml：
    20962 美人鱼形象卡  item_kind1=11 item_kind2=34  icon=p_meirenyu_1.png
    10017 美人鱼        Pet.xml  head=p_meirenyu.png
两边只能靠「名字」或「图标文件名」对。线上服 2026-11-27 抓包确认过一条：

    182 31 01（用掉背包 49 格的美人鱼形象卡）
    → 947 的 off48 由 cd2710(10000) 变成 cd2721(10017)

所以这里按「去掉 形象卡/形象/宠物形象 后缀的名字」+ 图标基名匹配，
再用抓包实证结果覆盖/兜底。41 张宠物形象卡里 39 张能自动对上，
剩下 30046 赤炎冥龙 / 30047 紫炎冥龙 在 Pet.xml 里没有条目（不是宠物形象）。

同名多份怎么选
--------------
Pet.xml 里 功夫熊猫/哈士奇/雪银狐/七巧虎/猪欢欢 各有三份（10000~10004、
10005~10009、10010 只重复了功夫熊猫）。形象卡走的是「第二块」10005~10009，
证据是老卡 20418~20422 正好是这五只连号，且 10005~10009 的动画与 10000~10004
逐字节相同（换形象只换 id、外观不变）。所以同名时优先取 id ≥ 10005 的最小值。

坐骑同理：31624 年年有鱼形象（kind1=4 kind2=61）→ Horse.xml 10054，
抓包实证 30 包尾部坐骑块由 cd2733(10035 筋斗云) 变成 cd2746(10054)。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = HERE / "mirror" / "assets" / "configure"
ITEM_XML = CFG / "Item.xml"
PET_XML = CFG / "Pet.xml"
HORSE_XML = CFG / "Horse.xml"

# 抓包实证覆盖（优先级最高）
FORCE_PET = {20962: 10017}      # 美人鱼形象卡 -> 美人鱼（2026-11-27 16:05 线上服）
FORCE_MOUNT = {31624: 10054}    # 年年有鱼形象 -> 年年有鱼（2026-11-27 16:14 线上服）

_PET_MAP = None
_MOUNT_MAP = None
_PET_NAME = None


def _targets(path):
    """读 Pet.xml / Horse.xml：id -> (名字, head 图标)。

    ★ 不能用 ET.parse：Pet.xml 第 31 行有个裸 `&`（"speak" 文本里），
      标准解析器直接 ParseError，整张表读成空 —— 表现就是「一张形象卡都认不出」。
      这两张表结构极简单（一行一个 <item>），用正则更稳。
    """
    out = []
    try:
        with open(str(path), "rb") as f:
            s = f.read().decode("utf-8", "replace")
    except OSError:
        return out
    for m in re.finditer(r'<item\s+[^>]*>', s):
        tag = m.group(0)
        mid = re.search(r'\bid="(\d+)"', tag)
        if not mid:
            continue
        mnm = re.search(r'\bname="([^"]*)"', tag)
        mhd = re.search(r'\bhead="([^"]*)"', tag)
        out.append((int(mid.group(1)),
                    mnm.group(1) if mnm else "",
                    mhd.group(1) if mhd else ""))
    return out


def _pick(cands):
    """同名多份时优先取 10005 起的那一块（形象区），否则取最小的。"""
    if not cands:
        return None
    hi = [c for c in cands if c >= 10005]
    return min(hi) if hi else min(cands)


def _build(kind2, tgt_xml, force, kind1=None):
    """kind1/kind2 选中「形象卡」这一类道具，再按名字/图标对到形象 id。

    ★ 坐骑的 kind2=61 同时包含「坐骑本体」（10035~10065，kind1=1，不能直接点使用）
      和「坐骑形象卡」（31617~31633，kind1=4）。不按 kind1 过滤会把坐骑本体
      也塞进映射表，玩家点坐骑本体时被当成「换形象」。
    """
    tgt = _targets(tgt_xml)
    by_name, by_head = {}, {}
    for tid, nm, hd in tgt:
        by_name.setdefault(nm, []).append(tid)
        if hd:
            by_head[hd] = tid
    out = {}
    try:
        root = ET.parse(str(ITEM_XML)).getroot()
    except (OSError, ET.ParseError):
        return dict(force)
    for el in root:
        a = el.attrib
        if a.get("item_kind2") != str(kind2):
            continue
        if kind1 is not None and a.get("item_kind1") != str(kind1):
            continue
        try:
            iid = int(a["id"])
        except (KeyError, ValueError):
            continue
        name = a.get("name", "")
        icon = a.get("icon", "")
        base = re.sub(r"(宠物)?形象卡?$", "", name)
        skin = _pick(by_name.get(base, []))
        if skin is None and icon.startswith("p_"):
            skin = by_head.get(icon.replace("_1.png", ".png"))
        if skin is None and base:
            cands = [tid for tid, nm, _ in tgt if nm and (base in nm or nm in base)]
            if len(cands) == 1:
                skin = cands[0]
        if skin is not None:
            out[iid] = skin
    out.update(force)
    return out


def pet_cards():
    """宠物形象卡 item_id -> Pet.xml 形象 id。"""
    global _PET_MAP
    if _PET_MAP is None:
        _PET_MAP = _build(34, PET_XML, FORCE_PET, kind1=11)
    return _PET_MAP


def mount_cards():
    """坐骑形象卡 item_id -> Horse.xml 坐骑 id。"""
    global _MOUNT_MAP
    if _MOUNT_MAP is None:
        _MOUNT_MAP = _build(61, HORSE_XML, FORCE_MOUNT, kind1=4)
    return _MOUNT_MAP


def pet_skin_of(item_id):
    return pet_cards().get(int(item_id))


def mount_skin_of(item_id):
    return mount_cards().get(int(item_id))


def pet_skin_name(skin_id):
    global _PET_NAME
    if _PET_NAME is None:
        _PET_NAME = {tid: nm for tid, nm, _ in _targets(PET_XML)}
    return _PET_NAME.get(int(skin_id), "?%s" % skin_id)


def mount_name(mount_id):
    return dict((tid, nm) for tid, nm, _ in _targets(HORSE_XML)).get(int(mount_id), "?%s" % mount_id)


if __name__ == "__main__":
    pc, mc = pet_cards(), mount_cards()
    print("宠物形象卡 %d 张，坐骑形象卡 %d 张" % (len(pc), len(mc)))
    for k in sorted(pc):
        print("  %6d -> %5d %s" % (k, pc[k], pet_skin_name(pc[k])))
    for k in sorted(mc):
        print("  %6d -> %5d %s" % (k, mc[k], mount_name(mc[k])))
