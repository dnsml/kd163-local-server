#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翅膀（形象卡）协议 —— 1922 / 1931 / 1932

真服实证（logs/relay.log 23:35:49 → 23:59:51）：
  1) 23:35:49 背包 181 全量里 第 15 格（0x0f）= 41002 天使羽翼形象
  2) 23:59:50 客户端发 `182 0f 01`（用掉 1 张）
  3) 真服应答顺序：
        182  空 ACK
        29   属性刷新
        28   角色属性
        30   职业/技能
        1922 (48B)   ← 翅膀/装备栏 全量（登录时是 33B 空面板）
        1931 (35B)   ← 装备栏（带对象 ID）
        1932 (13B)   ← 单件外观条目
        178  背包全量（此时该格已消失）
  对比同一会话里用消耗品（00:00:36 `182 01 01`）：只有 182/29/28/30/178/306，
  没有 1922/1931/1932 —— 所以这三个包就是「穿戴结果」。

包体结构（按 varint 拆开后对齐出来的）：

  # 登录时的空面板（33B，每次登录都会推）
  01 00 00 00 00 00 01 00 ...      → 01 + 6 个 0 + 01 + 25 个 0

  # 装备了翅膀之后的 1922（48B）
  [01][00 00 00]                   ← 固定头
  + OBJ(19B)                       ← 翅膀对象
  + BLK_A(6B) + BLK_B(7B)          ← 属性块 1（= 1932 整个包体，13B）
  + BLK_A(6B) + BLK_C(6B)          ← 属性块 2（12B，也出现在 1931 开头）

  # 1931（35B）
  BLK_A(6B) + BLK_C(6B) + [01][00 00 00] + OBJ(19B)

  # 1932（13B）
  BLK_A(6B) + BLK_B(7B)

  # OBJ 19B 拆开（varint）
    ce00061a82  400002   角色/世界对象 ID（本地服回放的 cmd=30 里也是这个值，同源）
    08          对象等级
    02
    00
    02
    cd9c41      40001    翅膀外观 ID
    ff
    ce00061a82  400002
    ff

注意：400002 在真服的 cmd=30 / cmd=47（附近玩家）里反复出现，是同一套对象体系；
本地服回放用的 cmd=30 模板里本来就带 `ce00061a82`，所以原样回放不会对象对不上。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# 固定段
HEAD = "01000000"                       # 1922 头 / 1931 中段
OBJ = "ce00061a8208020002cd9c41ffce00061a82ff"     # 19B 翅膀对象
BLK_A = "0402030c0502"                  # 6B 属性块公共头
BLK_B = "68141fcc845f34"                # 7B 属性块 1 值
BLK_C = "080406180a04"                  # 6B 属性块 2 值

_1922_WING = HEAD + OBJ + BLK_A + BLK_B + BLK_A + BLK_C
_1931_WING = BLK_A + BLK_C + HEAD + OBJ
_1932_WING = BLK_A + BLK_B

# 登录时的空面板（真服每次登录都推这个 33B）
_1922_EMPTY = "01" + "00" * 6 + "01" + "00" * 25

# cmd=30（职业/技能）在「装上翅膀之后」会变长：82B → 86B。
# 真服 23:59:51 起所有 cmd=30 都是 86B，偏移 62 处多出 `ce00061a82`（=角色自身对象 ID），
# 相当于「这套外观归属于谁」。82B 版本该位置是 0。这是对比穿翅膀前后唯一发现的
# 非 1922/1931/1932 的差异，客户端不画翅膀大概率就卡在这里。
_CMD30_WING = (
    "cd2b64010101cd2b65020101cd2b66030101cd2b67040101cd2b68050101cd2b69060101"
    "00000000000000000000000000000000"
    "cd2b6a07010100000000ce00061a8200000000000000000000000000000000000000"
)
BODY_30_WING = bytes.fromhex(_CMD30_WING)

BODY_1922 = bytes.fromhex(_1922_WING)
BODY_1931 = bytes.fromhex(_1931_WING)
BODY_1932 = bytes.fromhex(_1932_WING)
BODY_1922_EMPTY = bytes.fromhex(_1922_EMPTY)

# 形象卡 item_id -> 翅膀外观 ID
# 40001 是从 41002 的样本里读出来的；其它形象卡还没采样，不猜。
APPEARANCE = {
    # 41002 天使羽翼形象卡 -> 17 包「外观槽」的模型 ID。
    # 真服 197B 登录样本这里是 ce00061a82 = 400002（不是 1922 里的对象 ID 40001）。
    41002: 400002,
}

# ---- 从 Wing.xml 自动补全外观 ID ----------------------------------------
# 真服样本只覆盖了 41002 一张形象卡，但客户端本地的 Wing.xml 把
# 「翅膀模型 ID -> 动画资源」全列出来了，不需要一个个采样：
#   <item id="400002" name="天使翅膀" animation="wings/w_baiseyuyi,6"/>
#   <item id="400012" name="孔明王翼" animation="wings/w_kongmingwangyi,6"/>
# 规律（两条实测都成立）：
#   41xxx 形象卡  ->  +359000  =  400xxx 翅膀模型
#     41002 天使羽翼形象 -> 400002 天使翅膀
#     41012 孔明王翼形象 -> 400012 孔明王翼
#   400xx 翅膀本体（二阶翅膀 40002 蜻蜓点水）  ->  id 本身就是模型
WING_XML = Path(__file__).resolve().parent / "mirror" / "assets" / "configure" / "Wing.xml"
CARD_DELTA = 359000


def _load_wing() -> dict:
    out = {}
    try:
        for it in ET.parse(WING_XML).getroot().iter("item"):
            try:
                out[int(it.get("id"))] = it.get("name") or ""
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return out


WING_IDS = _load_wing()


def model_of(item_id) -> int | None:
    """道具 id -> 17 包外观槽里的模型 ID；None = 认不出来。

    不要在这里猜：Wing.xml 里没有的模型，客户端加载资源时可能直接崩，
    所以只认「Wing.xml 里真实存在」的 id。
    """
    if not item_id:
        return None
    if item_id in APPEARANCE:
        return APPEARANCE[item_id]
    if 41000 <= item_id <= 41999:
        cand = item_id + CARD_DELTA
        if cand in WING_IDS:
            return cand
        return None
    if item_id in WING_IDS:
        return item_id
    return None


def wing_name(item_id) -> str:
    m = model_of(item_id)
    return WING_IDS.get(m, "") if m else ""

WING_CMDS = (1922, 1931, 1932)


def supported(item_id: int) -> bool:
    return model_of(item_id) is not None


def body(cmd: int, item_id: int | None) -> bytes | None:
    """返回 cmd 对应的包体；None = 这种情况不该发这个包。

    item_id=None 表示当前没装备翅膀。
    注意：1922/1931/1932 里那条 19B 对象恒为 cd9c41(40001)「翅膀对象」，
    真正的外观模型只走 17 包外观槽，所以这里不需要按模型重建。
    """
    if cmd == 1922:
        return BODY_1922 if supported(item_id) else BODY_1922_EMPTY
    if cmd == 1931:
        return BODY_1931 if supported(item_id) else None
    if cmd == 1932:
        return BODY_1932 if supported(item_id) else None
    raise KeyError(cmd)


def verify() -> None:
    """自检：三段包体的长度与互相嵌套关系必须和真服样本一致。"""
    assert len(BODY_30_WING) == 86, len(BODY_30_WING)
    assert BODY_30_WING[62:67] == bytes.fromhex("ce00061a82"), BODY_30_WING[62:67].hex()
    assert len(BODY_1922) == 48, len(BODY_1922)
    assert len(BODY_1931) == 35, len(BODY_1931)
    assert len(BODY_1932) == 13, len(BODY_1932)
    assert len(BODY_1922_EMPTY) == 33, len(BODY_1922_EMPTY)
    # 1922 = 头4 + OBJ19 + 1932体13 + 属性块2(12)
    assert BODY_1922[:4] == BODY_1931[12:16]
    assert BODY_1922[4:4 + 19] == BODY_1931[16:]
    assert BODY_1922[4 + 19:] == BODY_1932 + BODY_1931[:12]
    # 1932 = 属性块A + 属性块B
    assert BODY_1932[:6] == BODY_1931[:6]


if __name__ == "__main__":
    verify()
    print("翅膀包体自检通过")
    for c in WING_CMDS:
        b = body(c, 41002)
        e = body(c, None)
        print("  cmd=%-5d 有翅膀 len=%-3d %s" % (c, len(b), b.hex()))
        print("          无翅膀 %s" % ("len=%d %s" % (len(e), e.hex()) if e else "(不发)"))
