#!/usr/bin/env python
"""道具效果 / 商城价格 推断器（从 Item.xml 的中文描述里解析，不手写表）。

背景
    自建服之前只认死写的 USE_EFFECTS 几个表，包里常见的
    「10级元宝礼包」「中级见闻录」「神界新丁」一律回
    「该物品暂时无法使用」—— 玩家就是在这里觉得"道具用不了"。

    Item.xml 的描述格式非常统一：
        使用后可获得10000绑定铜币
        使用后可获得10绑定元宝
        增加300阅历,藏宝图掉落
        使用后获得10元宝卡*2，中级见闻录*5，初级强化灵石*8,15级妖灵礼包
        元宝商城特价为每个1元宝，每个角色每日限购10个
    所以这里直接按描述解析出「效果类型 / 数量 / 掉落列表 / 单价」，
    覆盖面比手写表大得多，也不会漏掉新道具。

接口
    rule(item_id) -> dict
        {"kind": "currency"|"yueli"|"gift"|"pet_spirit"|"title"|"equip"|"ui"|"unknown",
         "cur":  "bind_coin"|"bind_yuanbao"|"tongqian"|"yuanbao",
         "amount": int,            # 单个道具的增量
         "items":  [(id, num)],    # 礼包内容
         "missing": [名称...],      # 描述里有、但 Item.xml 找不到同名道具
         "price":  int, "price_cur": "yuanbao"}
    price(item_id) -> (单价, 货币键)
"""
from __future__ import annotations

import re

import bag_proto

# 描述里的名字和 Item.xml 里的正式名对不上时的别名
ALIAS = {
    "九朵鲜花": "9朵鲜花",
    "潜力符": "宠物潜力符",
    "坐骑进阶": "坐骑进阶灵丹",
    "坐骑进阶丹": "坐骑进阶灵丹",
    "坐骑洗髓丹": "坐骑洗髓灵丹",
    "妖灵觉醒": "妖灵觉醒丹",
}

# 「获得Nxxx」——顺序重要：先匹配带"绑定"的长词，否则会被短词吃掉
CUR_PATTERNS = [
    (re.compile(r"获得\s*(\d+)\s*绑定铜"), "bind_coin"),
    (re.compile(r"获得\s*(\d+)\s*绑定元宝"), "bind_yuanbao"),
    (re.compile(r"获得\s*(\d+)\s*铜"), "tongqian"),
    (re.compile(r"获得\s*(\d+)\s*元宝"), "yuanbao"),
]
YUELI_PAT = re.compile(r"增加\s*(\d+)\s*阅历")
PRICE_PATTERNS = [
    re.compile(r"特价[为是]\s*每[本个颗件]\s*(\d+)\s*元宝"),
    re.compile(r"商城\s*每[本个颗件]\s*(\d+)\s*元宝"),
    re.compile(r"每个?\s*(\d+)\s*元宝"),
]
# 掉落列表：名字 + *x× + 数量
DROP_ITEM_PAT = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9·]+?)\s*[*x×]\s*(\d+)")

# 没写"使用后获得"但确实是礼包的（如「妖灵觉醒丹盒：妖灵觉醒丹×30,商城获得」）
GIFT_BODY_PATS = [r"使用后获得(.+)$", r"打开(.+)$"]

# 商城价格兜底表（界面实测：商城 → 绑定专区 / 促销特价）
PRICE_TABLE = {
    20735: (1, "yuanbao"),      # 百年妖丹 1元宝
    20739: (1, "yuanbao"),      # 妖灵觉醒丹 1元宝
    20054: (1, "yuanbao"),      # 高级见闻录 1元宝
    20458: (1, "yuanbao"),      # 经验丹 1元宝
}
DEFAULT_PRICE = 1

_NAME2ID: dict[str, int] | None = None


def names() -> dict[str, int]:
    global _NAME2ID
    if _NAME2ID is None:
        _NAME2ID = {it["name"]: it["id"] for it in bag_proto.items().values()}
    return _NAME2ID


def _resolve(nm: str) -> int | None:
    nm = nm.strip()
    n2i = names()
    if nm in n2i:
        return n2i[nm]
    if nm in ALIAS and ALIAS[nm] in n2i:
        return n2i[ALIAS[nm]]
    # 去掉量词/后缀再试一次，例如「10元宝卡」->「元宝卡」
    for suf in ("卡", "丹", "符", "礼包", "盒"):
        if nm.endswith(suf) and nm[:-len(suf)] in n2i:
            return n2i[nm[:-len(suf)]]
    return None


def _drops(desc: str) -> tuple[list[tuple[int, int]], list[str]]:
    """从礼包描述里解出 [(item_id, num)]，解不出的名字单独返回。

    描述里最后一项经常不带「*数量」，例如
        「十年妖丹*2，妖灵觉醒*5，经验丹*5,20级鲜花礼包」
    所以按逗号切开逐段判断：带 *N 的取 N，不带的按 1 个。
    """
    body = desc
    for pat in GIFT_BODY_PATS:
        m = re.search(pat, desc)
        if m:
            body = m.group(1)
            break
    got, missing = [], []
    for seg in re.split(r"[，,。；;、]", body):
        seg = seg.strip()
        if not seg:
            continue
        m = DROP_ITEM_PAT.search(seg)
        nm, num = (m.group(1), int(m.group(2))) if m else (seg, 1)
        iid = _resolve(nm)
        if iid is None:
            # 「10元宝卡*2」这种找不到实体的，按元宝折算
            mm = re.match(r"(\d+)元宝卡$", nm)
            if mm:
                got.append(("yuanbao", int(mm.group(1)) * num))
                continue
            # 不带数量的段落多半是「商城获得」这类说明文字，只有当它能
            # 匹配到道具名时才记入 missing，避免刷一堆噪音
            if m or num > 1:
                missing.append(nm)
            continue
        got.append((iid, num))
    return got, missing


def rule(iid: int) -> dict:
    it = bag_proto.item(iid)
    k2, desc = it["kind2"], it["desc"]
    out = {"id": iid, "name": it["name"], "kind2": k2, "kind": "unknown",
           "cur": None, "amount": 0, "items": [], "missing": [],
           "price": DEFAULT_PRICE, "price_cur": "yuanbao"}

    if it["kind1"] == bag_proto.EQUIP_KIND1:
        out["kind"] = "equip"
        return out

    # 1) 直接加货币 / 阅历（kind2=56 的卡类、见闻录）
    for pat, key in CUR_PATTERNS:
        m = pat.search(desc)
        if m:
            out.update(kind="currency", cur=key, amount=int(m.group(1)))
            break
    if out["kind"] == "unknown":
        m = YUELI_PAT.search(desc)
        if m:
            out.update(kind="yueli", amount=int(m.group(1)))

    # 2) 礼包（kind2=68）：优先按描述解析掉落
    if k2 == 68:
        got, missing = _drops(desc)
        if got:
            # 礼包以掉落表为准，别把「获得10元宝卡*2」误当成整包加 10 元宝
            out.update(kind="gift", items=got, missing=missing, cur=None, amount=0)
    elif "使用后获得" in desc and out["kind"] == "unknown":
        got, missing = _drops(desc)
        if got:
            out.update(kind="gift", items=got, missing=missing)

    # 3) 按 kind2 归类剩余玩法（数值暂不入账，但要消耗掉并回成功）
    if out["kind"] == "unknown":
        out["kind"] = {
            36: "pet_spirit",     # 妖丹：妖灵灵力
            83: "title",          # 称号卡
            75: "mount_exp",      # 仙草：坐骑经验
            33: "pet_growth",     # 成长丹
            15: "wing_unlock",    # 翅膀形象
        }.get(k2, "ui" if k2 in bag_proto.USE_RULES else "unknown")

    # 4) 单价
    if iid in PRICE_TABLE:
        out["price"], out["price_cur"] = PRICE_TABLE[iid]
    else:
        for pat in PRICE_PATTERNS:
            m = pat.search(desc)
            if m:
                out["price"] = int(m.group(1))
                break
    return out


def price(iid: int) -> tuple[int, str]:
    r = rule(iid)
    return r["price"], r["price_cur"]


def main():
    import sys
    if len(sys.argv) > 1:
        for a in sys.argv[1:]:
            r = rule(int(a))
            print(r["id"], r["name"], "kind=%s" % r["kind"],
                  "cur=%s x%d" % (r["cur"], r["amount"]) if r["cur"] else "",
                  "items=%s" % (r["items"] or ""),
                  "missing=%s" % (r["missing"] or ""),
                  "price=%d%s" % (r["price"], r["price_cur"]))
        return
    # 全量体检：统计各类别覆盖率
    from collections import Counter
    c = Counter()
    bad = []
    for it in bag_proto.items().values():
        r = rule(it["id"])
        c[r["kind"]] += 1
        if r["kind"] == "unknown":
            bad.append((it["id"], it["name"], it["kind2"]))
    print("类别分布:", dict(c))
    print("unknown 数量:", len(bad))
    for b in bad[:20]:
        print("   ", b)


if __name__ == "__main__":
    main()
