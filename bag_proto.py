#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KD163 背包协议（真服实证版）

从 relay.log（真服 124.223.56.189:9000 透传抓包）反推出来的三条指令：

    181  C->S 空包        = 打开／刷新背包
         S->C [0x40][n] + n*条目        = 背包全量
    178  S->C [0x40][n] + n*条目        = 背包全量推送（物品变动后主动推，结构同 181）
    182  C->S [格子][数量]              = 使用／穿戴／打开 该格物品
         S->C 空包                      = 只有 ACK，状态走 28/29/30/178 推送

条目（7 字节，字段全是变长整数）：
    [pos 1B][item_id 变长][enh 1B][bind 1B][num 1B]

    pos  格子下标，0 起，**绝对槽位**：用掉某格后其它格不会前移，
         新物品填进最小的空格（实测 15:38:17 背包只剩 0/2/3，1 是空的）
    enh  装备强化/附加等级（非装备恒 0，天枢剑[二阶]=8）
    bind 恒 1，疑似绑定标志（待确认）
    num  堆叠数量；182 里数量写 0 表示"全部使用"

178 与 181 的包体一模一样，都是**全量**：客户端按包整体替换列表。
不要当成增量包去 upsert —— 用光一格后它就不在包里了，靠"缺席"表示删除。

数字编码（全协议通用）：
    <0xCD        单字节
    0xCD + u16BE
    0xCE + u32BE

用法：
    python bag_proto.py verify     # 用 relay.log 全文回归，统计解析成功率 + 复现使用链
    python bag_proto.py dump <hex> # 解一条 178/181
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ITEM_XML = HERE / "mirror" / "assets" / "configure" / "Item.xml"
RELAY_LOG = HERE / "logs" / "relay.log"

TAG_BAG = 0x40
CMD_BAG_OPEN = 181      # C->S 空包 / S->C 全量
CMD_BAG_PUSH = 178      # S->C 全量推送
CMD_BAG_USE = 182       # C->S [pos][num]

# 货币本体（Item.xml 里 item_kind1=14，can_use_in_bag=0，不占背包格子）
# 这些 id 只用于配置/发奖标识，不会以「物品条目」形式出现在 178/181 里。
CURRENCY = {
    19997: "铜钱",
    19998: "绑定铜钱",
    19999: "元宝",          # 28 包偏移 47（u16）
    20000: "绑定元宝",      # 28 包偏移 43（u16）
}

# 背包里能点「使用」变成货币的卡（item_kind1=3, item_kind2=56）
CURRENCY_CARDS = {
    20434: ("绑定元宝", 1),
    20432: ("绑定元宝", 10),
    22059: ("绑定元宝", 500),
    22066: ("元宝", 500),
    22065: ("元宝", 800),
    22084: ("元宝", 1000),
    22074: ("元宝", 1200),
    22085: ("元宝", 1500),
}

# ---------------------------------------------------------------- 变长整数

def dec_val(b: bytes, i: int) -> tuple[int, int]:
    x = b[i]
    if x < 0xCD:
        return x, i + 1
    if x == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if x == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    raise ValueError("非法变长整数前导 0x%02x @%d" % (x, i))


def enc_val(v: int) -> bytes:
    if v < 0xCD:
        return bytes([v])
    if v < 0x10000:
        return b"\xcd" + v.to_bytes(2, "big")
    return b"\xce" + v.to_bytes(4, "big")


# ---------------------------------------------------------------- 物品表

_ITEMS: dict[int, dict] | None = None


def _int(s, default: int = 0) -> int:
    """配置里有脏值（丹药的 cooldown="2,8"），容错取第一个数。"""
    try:
        return int(str(s).split(",")[0])
    except (TypeError, ValueError):
        return default


def items() -> dict[int, dict]:
    global _ITEMS
    if _ITEMS is None:
        _ITEMS = {}
        for it in ET.parse(ITEM_XML).getroot():
            a = it.attrib
            iid = _int(a.get("id"), -1)
            if iid < 0:
                continue
            _ITEMS[iid] = {
                "id": iid,
                "name": a.get("name", ""),
                "kind1": _int(a.get("item_kind1")),
                "kind2": _int(a.get("item_kind2")),
                "level": _int(a.get("level_limit")),
                "class_limit": _int(a.get("class_limit")),
                "can_use_in_bag": a.get("can_use_in_bag") == "1",
                "cooldown": _int(a.get("cooldown")),
                "desc": a.get("description", ""),
            }
    return _ITEMS


def raw_int(a: dict, key: str) -> int:
    return _int(a.get(key))


def item(iid: int) -> dict:
    return items().get(iid, {"id": iid, "name": "<未知%d>" % iid, "kind1": 0, "kind2": 0,
                             "level": 0, "class_limit": 0, "can_use_in_bag": False,
                             "cooldown": 0, "desc": ""})


# ---------------------------------------------------------------- 编解码

def parse_bag(payload: bytes) -> list[dict]:
    """解析 178 / 181 的包体；不是背包包体时返回 None。"""
    if len(payload) < 2 or payload[0] != TAG_BAG:
        return None
    n = payload[1]
    i = 2
    out = []
    for _ in range(n):
        pos = payload[i]; i += 1
        iid, i = dec_val(payload, i)
        enh, bind, num = payload[i], payload[i + 1], payload[i + 2]
        i += 3
        it = item(iid)
        out.append({"pos": pos, "id": iid, "enh": enh, "bind": bind, "num": num,
                    "name": it["name"], "kind1": it["kind1"], "kind2": it["kind2"]})
    if i != len(payload):
        raise ValueError("背包包体长度不符：解析到 %d，实际 %d" % (i, len(payload)))
    return out


def build_bag(entries: list[dict]) -> bytes:
    """entries: [{'pos','id','enh','bind','num'}] -> 178/181 包体。"""
    out = bytearray([TAG_BAG, len(entries)])
    for e in entries:
        out.append(e["pos"] & 0xFF)
        out += enc_val(e["id"])
        out += bytes([e.get("enh", 0), e.get("bind", 1), e.get("num", 1)])
    return bytes(out)


def build_181(bag: dict[int, dict]) -> bytes:
    """bag: {pos: entry}，按 pos 升序输出全量。"""
    return build_bag([bag[p] for p in sorted(bag)])


def build_178(bag: dict[int, dict]) -> bytes:
    """全量推送，和 build_181 字节一致（178/181 只是命令号不同）。"""
    return build_181(bag)


def build_182(pos: int, num: int = 1) -> bytes:
    """num=0 表示全部使用。"""
    return bytes([pos & 0xFF, num & 0xFF])


def parse_182(payload: bytes) -> tuple[int, int]:
    return payload[0], payload[1] if len(payload) > 1 else 1


# ---------------------------------------------------------------- 使用规则
# 真服实证 + Item.xml 描述归纳。返回 (kind, 参数) —— 服务端据此改状态，
# 其余"只开界面"的类型不用 182（客户端本地打开面板）。

USE_RULES = {
    56: ("instant_card", "直接生效：按 description 加绑定铜币/元宝/阅历"),
    68: ("gift_box", "礼包：按掉落表发物品"),
    75: ("mount_exp", "仙草：坐骑经验 +300"),
    83: ("title_card", "称号卡：解锁称号"),
    33: ("pet_growth", "成长丹：仙宠成长 +1"),
    36: ("pet_spirit", "妖丹：妖灵灵力 +100"),
    15: ("wing_unlock", "翅膀形象：解锁对应翅膀"),
    70: ("attr_pill", "气血丹：永久 +10 气血，上限 50 颗"),
    19: ("ui_only", "洗炼石：客户端自己开洗炼界面"),
    20: ("ui_only", "强化仙玉：客户端自己开强化界面"),
    21: ("ui_only", "强化灵石：客户端自己开强化界面"),
    48: ("ui_only", "宝石：客户端自己开镶嵌界面"),
    57: ("ui_only", "材料：客户端自己开打造界面"),
}

EQUIP_KIND1 = 1     # kind1==1 就是装备，"使用" = 穿戴到 kind2 对应的部位


def use_kind(iid: int) -> str:
    it = item(iid)
    if it["kind1"] == EQUIP_KIND1:
        return "equip"
    return USE_RULES.get(it["kind2"], ("unknown", "未知用法"))[0]


# ---------------------------------------------------------------- 回归验证

PAT = re.compile(r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")
# relay.log 的 hex 被截到 160 字符（80B），长包会缺尾，验证时按 len 过滤
HEX_CAP = 160


def _records():
    for line in open(RELAY_LOG, encoding="utf-8", errors="replace"):
        m = PAT.search(line)
        if not m:
            continue
        t, d, c, s, ln, h = m.groups()
        yield t, d, int(c), int(ln), h


def verify() -> int:
    ok = trunc = fail = 0
    bag: dict[int, dict] = {}
    events = []
    stale = False
    for t, d, c, ln, h in _records():
        if d == "S->C" and c in (CMD_BAG_OPEN, CMD_BAG_PUSH):
            raw = bytes.fromhex(h)
            if ln > len(raw):                    # 日志截断，跳过
                trunc += 1
                bag = {}                          # 状态不可信，置空
                stale = True
                continue
            try:
                entries = parse_bag(raw)
                if build_bag(entries) != raw:        # 编码回写必须逐字节一致
                    raise ValueError("重编码不一致")
            except Exception as e:
                fail += 1
                print("  ✗ %s cmd=%d %s" % (t, c, e))
                continue
            if entries is None:
                continue
            ok += 1
            bag = {e["pos"]: e for e in entries}      # 178/181 都是全量，整体替换
            stale = False
        elif d == "C->S" and c == CMD_BAG_USE:
            pos, num = parse_182(bytes.fromhex(h))
            cur = bag.get(pos)
            unknown = "<状态未知(日志截断)>" if stale else "<空>"
            events.append((t, pos, num, cur["name"] if cur else unknown,
                           cur["num"] if cur else 0, use_kind(cur["id"]) if cur else "-"))
            if cur:
                left = 0 if num == 0 else max(0, cur["num"] - num)
                if left:
                    bag[pos] = dict(cur, num=left)
                else:
                    bag.pop(pos, None)

    print("命中背包包体：%d 条（日志截断跳过 %d 条，解析失败 %d 条）" % (ok, trunc, fail))
    print("\n使用链（C->S 182）：")
    print("  时间      格子  数量  使用前            剩  ->  用法")
    for t, pos, num, nm, q, kind in events:
        print("  %s  0x%02x  %4s  %-16s x%-3d -> %s" % (t, pos, "全部" if num == 0 else num, nm, q, kind))
    print("\n最终背包：")
    for p in sorted(bag):
        e = bag[p]
        print("  %02x  %6d x%-3d enh=%d  %s" % (p, e["id"], e["num"], e["enh"], e["name"]))
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) > 1 and argv[1] == "verify":
        return verify()
    if len(argv) > 2 and argv[1] == "dump":
        for e in parse_bag(bytes.fromhex(argv[2])):
            print("%02x  id=%-6d x%-3d enh=%d bind=%d  %s" %
                  (e["pos"], e["id"], e["num"], e["enh"], e["bind"], e["name"]))
        return 0
    if len(argv) > 1 and argv[1] == "find":
        kw = argv[2] if len(argv) > 2 else ""
        it = items()
        hit = [v for v in it.values() if kw in v["name"] or (v.get("desc") and kw in v["desc"])]
        print("== 货币本体（kind1=14，不占背包）")
        for k, nm in CURRENCY.items():
            if not kw or kw in nm:
                print("  %6d  %s" % (k, nm))
        print("== 元宝/铜钱卡（kind1=3 kind2=56，点使用生效）")
        for k, (nm, n) in CURRENCY_CARDS.items():
            if not kw or kw in nm:
                print("  %6d  %s  +%d" % (k, nm, n))
        print("== Item.xml 命中 %d 条" % len(hit))
        for v in sorted(hit, key=lambda x: x["id"])[:80]:
            print("  %6d  k1=%-2d k2=%-3d %-14s %s" %
                  (v["id"], v["kind1"], v["kind2"], v["name"], (v.get("desc") or "")[:40]))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
