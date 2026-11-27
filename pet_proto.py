# -*- coding: utf-8 -*-
"""宠物 / 妖灵协议（真服 2026-11-23 21:03 实测样本）。

抓到的完整序列（真服号开宠物界面 + 依次上阵 3 个妖灵）：

  C->S 961 (len=0)          -> S->C 962 (40B)    宠物栏列表
  C->S 946 01               -> S->C 947 (99~105B) 选中宠物详情
  C->S 973 01 00 00         -> S->C 973 (1B=01)
  C->S 1411 <槽位>          -> S->C 1412 [槽位][妖灵id][00][00]
  C->S 1413 <0/1/2>         -> S->C 1413 (空)
  S->C 1410                 妖灵背包 / 上阵状态（每次变更都推）

962（40B）结构：
  [u8 3][3 × (u32 uid + u8 00)][u8 3][3 × (u32 uid + u8 00)][u8 04][u8 选中位]
  两组 uid 分别对应「宠物栏 / 已解锁」，04 是固定常量。

947（99~105B）结构（实测，不做二次解析，直接回放原包）：
  [01][AC][名字 UTF-8][u8 等级][00][u8 ?] + 属性段 + [u8 08] + 妖灵块 + [u8 03][3 × u32 uid]
  妖灵块每上阵一只 +2 字节（真服原包长度 99/101/103/105），
  所以四种组合直接存真服原包，逐字节回放，绝不猜结构。

1410（妖灵背包）结构：
  cc80 [u8 数量] + 每条 7B: [u8 槽位(0=未上阵)][varint 妖灵id][u8 序号][01][01]
  实测样本：
    cc80 00
    cc80 01 01 cd1005 02 01 01
    cc80 02 01 cd1005 02 01 01 02 cd0835 03 01 01
    cc80 03 00 cd044d 01 01 01 01 cd1005 02 01 01 02 cd0835 03 01 01
  排序按「序号」而不是 id —— 序号是账号里该妖灵的获得次序。

1412： [u8 槽位][varint 妖灵id][00][00]（卸下时 id 传 0）

妖灵属性来自 PetEquip.xml 的 basic（与 Item.xml kind2=76 同一批 id）：
  1101 血·共工    basic=53,550  -> 生命 +550
  2101 攻·白光虎  basic=5,90    -> 攻击 +90
  4101 命·魍魉    basic=11,25   -> 命中 +25
  1401 血·战神刑天 basic=53,1800
注意：真服上阵妖灵后 cmd=29 面板不变（生命 2132 没动），
妖灵属性只进宠物自己的面板（947），所以本地不要把 basic 加进玩家 29。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "res_cfg")
EQUIP_XML = os.path.join(CFG, "PetEquip.xml")
PET_XML = os.path.join(CFG, "Pet.xml")
TPL_JSON = os.path.join(CFG, "pet_tpl.json")

SPIRIT = {}          # 妖灵 id -> {"name","basic","level","color"}
PET = {}             # 宠物模板 id -> {"name","level","effect"}
TPL = {"962": b"", "947": {}}
MAX_SLOT = 3         # 真服样本里槽位只出现 01/02/03
# 本账号的宠物形象实例（真服 962 40B 稳定态的两组 uid）
#   第一组 = 列表里显示的那三只（814101/815201/803201）
#   第二组 = 947 末尾那三只（804201/810101/812101）
# uid 编码：800000 + 形象id*1000 + 序号
PET_UIDS = [814101, 815201, 803201]
PET_SKIN = [804201, 810101, 812101]
# 千位 -> 部位（**八系全部实证**，2026-11-27 17:39 线上装卸抓包）
NATURE = {1: 1, 4: 2, 2: 3, 3: 4, 5: 5, 6: 7, 7: 8, 8: 9}
# 部位编号 = 真服 1410 每条里的第 3 个字节（= 1411 的槽位字节）。
# 实证（1410 快照 + 1412 回包逐条对齐）：
#   1101 血→01   4101 命→02   2101 攻→03   3101 防→04（1411 04→1412 cd0c1d）
#   5101 闪→05   6101 暴→07   7101 坚→08   8101 绝→09
#   ★ 06 是空档：八系只占 1~5 与 7~9，没有任何一只妖灵落在 6。
# ⚠️ 2026-11-27 修的旧错：以前这张表尾段写成 坚→06 / 绝→07 / 暴→08（整体错位 1~2 格），
#   导致 6xxx/7xxx/8xxx 妖灵 nature_of() 返回错误槽位，界面上「装上了却进了别的槽」。
#   现在按抓包值写死，别再按 UI 顺序猜。
# （PetEquip 名称：1101 血·共工 / 2101 攻·白光虎 / 3101 防·图腾王 / 4101 命·魍魉 /
#   5101 闪·精卫鸟 / 6101 暴·火烈鸟 / 7101 坚·醒狮 / 8101 绝·九尾）

# 947 妖灵区 8 个格子的固定排列顺序（= 槽位码从小到大，06 空档不占格）。
# 面板视觉顺序不是这个：面板是 血/攻/防/命 四角 + 闪/坚/绝/暴 一排，
# 但协议里的格子只按码值排，谁是谁靠每条自带的部位字节区分。
# ★ 2026-11-27 18:2x 真机实测推翻上面这句：**客户端是按格子位置(cell)上色的**，
#   部位字节只决定「+气血/+防御」那行小字。用户实测（001 号，八系全装）：
#     放 pos1 的共工(血) → 血 ✔   pos2 的魍魉(命) → 显示在「攻」 ✘
#     pos3 的白光虎(攻) → 显示在「防」 ✘   pos4 的图腾王(防) → 显示在「命」 ✘
#   三条全部指向同一个结论：**格子位置 = 血/攻/防/命/闪/坚/绝/暴（面板从左到右、
#   从上到下）**，与属性码无关。所以：
#     · spirit_slot 的 key 一律用「格子位置」1~8；
#     · 947 每条里的部位字节仍然写**属性码**（真服 6101→07 / 7101→08 / 8101→09）。
#   旧代码把属性码当格子位置用 → 魍魉进「攻」、白光虎进「防」、图腾王进「命」，
#   而 暴/坚 被塞到 7/8 号格（面板上是 绝/暴），用户点自己的格子发 1414 时
#   位置对不上 → 「装上去了脱不下来」。用户实测反馈原话见 2026-11-27 18:2x。
SLOT_ORDER = (1, 2, 3, 4, 5, 6, 7, 8)
MAX_SLOT_CODE = 8
# 属性码 -> 格子位置（面板顺序）
TYPE_SLOT = {1: 1, 3: 2, 4: 3, 2: 4, 5: 5, 7: 6, 8: 7, 9: 8}


def slot_of(iid):
    """妖灵该上哪个格子（1~8，面板顺序）。0 = 不认识。"""
    return TYPE_SLOT.get(nature_of(iid), 0)


def slot_name(s):
    """给日志用：1血 2攻 3防 4命 5闪 6暴 7坚 8绝。

    ★ 2026-11-27 19:3x 用户实测纠偏：底部三格是 **6=暴 7=坚 8=绝**。
      旧表写的是 6=坚 7=绝 8=暴，结果三只各错一格（用户原话：
      「暴你放到了绝 绝你放到了坚 坚你放到了暴」）。这三个格子的顺序
      与上面的属性码 07/08/09 不是一条线，别再按属性码大小推。
    """
    return {1: "血", 2: "攻", 3: "防", 4: "命",
            5: "闪", 6: "暴", 7: "坚", 8: "绝"}.get(int(s), "?")


# ---------------------------------------------------------------- 妖灵属性
# 947 头部数值区（名字之后、skin `cd2710` 之前）的下标，0 = 宠物等级。
# 全部由 relay.log 真服装卸实测反推（work/probe_947_attrs.py）：
#   装 血·共工(53,550)   -> idx6 +550, idx13 +11
#   装 攻·白光虎(5,90)   -> idx4 +90,  idx13 +36
#   装 防·图腾王(7,120)  -> idx8 +120, idx13 +12
#   装 命·魍魉(11,25)    -> idx10 +25, idx13 +12
#   装 闪·精卫鸟(9,25)   -> idx11 +25, idx13 +13
#   装 暴·火烈鸟(13,25)  -> idx5 +25,  idx13 +13
#   装 坚·醒狮(19,25)    -> idx9 +25,  idx13 +12
#   装 绝·九尾(25,35)    -> idx12 +35, idx13 +52
# 卸下时逐项原样回退（21:56:48 卸 1101：idx6 2612->2062、idx13 752->741）。
ATTR_947_IDX = {5: 4, 13: 5, 53: 6, 7: 8, 19: 9, 11: 10, 9: 11, 25: 12}
POWER_IDX_947 = 13
# 每点属性折多少战力：由上面 8 条增量反解，整数舍入后全部吻合
# （550*0.02=11 / 90*0.4=36 / 120*0.1=12 / 25*0.48=12 / 25*0.52=13 / 35*1.5=52.5→52）。
ATTR_POWER_W = {53: 0.02, 5: 0.40, 7: 0.10, 11: 0.48,
                9: 0.52, 13: 0.52, 19: 0.48, 25: 1.5}


def spirit_bonus(strap):
    """已上阵妖灵的属性合计 -> {属性id: 值}（basic 直接相加）。"""
    out = {}
    for iid in (strap or {}).values():
        try:
            d = SPIRIT.get(int(iid)) or {}
        except (TypeError, ValueError):
            continue
        for k, v in (d.get("basic") or {}).items():
            out[int(k)] = out.get(int(k), 0.0) + float(v)
    return {k: int(round(v)) for k, v in out.items()}


def spirit_power(strap):
    """上阵妖灵贡献的战力（29[25] / 28[31] / 17 战力字段都要加上它）。"""
    tot = 0.0
    for k, v in spirit_bonus(strap).items():
        tot += v * ATTR_POWER_W.get(k, 0.0)
    return int(tot)


def _attrs(s):
    """'53,550;11,25' -> {53:550.0, 11:25.0}"""
    out = {}
    for part in (s or "").split(";"):
        part = part.strip()
        if not part or "," not in part:
            continue
        k, v = part.split(",", 1)
        try:
            out[int(k)] = out.get(int(k), 0.0) + float(v)
        except ValueError:
            pass
    return out


def _items(path):
    if not os.path.exists(path):
        return
    txt = open(path, encoding="utf-8", errors="ignore").read()
    for m in re.finditer(r'<item ([^>]*?)/>', txt):
        yield dict(re.findall(r'([\w]+)="([^"]*)"', m.group(1)))


def load():
    for d in _items(EQUIP_XML):
        try:
            iid = int(d["id"])
        except (KeyError, ValueError):
            continue
        SPIRIT[iid] = {
            "name": d.get("name", ""),
            "basic": _attrs(d.get("basic", "")),
            "level": int(d.get("level") or 1),
            "color": int(d.get("color") or 1),
        }
    for d in _items(PET_XML):
        try:
            pid = int(d["id"])
        except (KeyError, ValueError):
            continue
        PET[pid] = {
            "name": d.get("name", ""),
            "level": int(d.get("level") or 1),
            "effect": _attrs(d.get("image_effect", "")),
        }
    if os.path.exists(TPL_JSON):
        with open(TPL_JSON, encoding="utf-8") as f:
            d = json.load(f)
        TPL["962"] = bytes.fromhex(d.get("962") or "")
        TPL["947"] = {k: bytes.fromhex(v) for k, v in (d.get("947") or {}).items()}
    return SPIRIT, PET, TPL


def name_of(iid):
    return (SPIRIT.get(int(iid)) or {}).get("name", "妖灵%d" % int(iid))


def active_pet(pets=None):
    """当前展示宠物的 名字/等级，取自 947 真服原包的第一条记录。

    947 头部：[01][0xA0|名字字节数][名字][等级]...
    本地 947 是回放真服模板，所以这里读到的就是客户端宠物界面里那只。
    151 开战包要按同一只宠物拼「宠物段」，否则战斗画面里没有宠物。

    ★ 2026-11-24 14:4x：必须传角色自己的宠物列表。
    以前不传参，任何号（包括一只宠物都没有的新号）都会从 947 模板第一条
    读出「功夫熊猫」，151 就带宠物上场 —— 用户看到的「还是有宠物」就是这个：
    地图上 947 已经跳过，但一进战斗 151 里照样拼了熊猫段。
    pets 传空列表 → 返回 None → build_pet_seg 输出空段，战斗里才真没宠物。
    """
    if pets is not None and not list(pets):
        return None
    for key in ("empty", "0", "1", "2", "3"):
        b = TPL["947"].get(key)
        if not b or len(b) < 4 or b[0] != 1:
            continue
        if (b[1] & 0xF0) != 0xA0:
            continue
        n = b[1] & 0x0F
        try:
            name = b[2:2 + n].decode("utf-8")
        except Exception:
            continue
        if not name:
            continue
        return {"name": name, "level": max(1, b[2 + n])}
    return None


def basic_of(iid):
    return (SPIRIT.get(int(iid)) or {}).get("basic", {})


def pick_spirit(keywords):
    """按名字片段找妖灵 id，例如 pick_spirit(['魍魉']) -> [4101..]。"""
    out = []
    for iid in sorted(SPIRIT):
        nm = SPIRIT[iid]["name"]
        if any(w and w in nm for w in keywords):
            out.append(iid)
    return out


def find(text):
    """GM 用：先当 id，再按全名，最后按名字片段。返回 id 列表。"""
    text = (text or "").strip()
    if not text:
        return []
    try:
        iid = int(text)
        return [iid] if iid in SPIRIT else []
    except ValueError:
        pass
    exact = [i for i, v in SPIRIT.items() if v["name"] == text]
    if exact:
        return sorted(exact)
    return pick_spirit([text])


def sum_strap(strap):
    """已上阵妖灵的属性合计（只给宠物面板用，不进玩家 29）。"""
    agg = {}
    for _slot, iid in (strap or {}).items():
        for a, v in basic_of(iid).items():
            agg[a] = agg.get(a, 0.0) + v
    return agg


def pack_962(pets=None, out=None):
    """宠物栏列表。

    结构：`[u8 n1][n1×(uid,00)] [u8 n2][n2×(uid,00)] [u8 04 容量][u8 余]`

    真服各档原包（relay.log 逐字节抄的，不是推的）：
      4B   00 00 04 03
      10B  01 [4569 00] 00 04 02
      22B  03 [4569][5c75][6445] 00 04 00
      28B  01 [6c15 00] 03 [4569][5c75][6445] 04 02
      34B  02 [6c15][7061] 03 [...] 04 01
      40B  03 [6c15][7061][4181] 03 [4569][5c75][6445] 04 00

    ★ 默认（TPL["962"]）用真服 20:52:31 那份 **22B**：
      `00 03 [4569][5c75][6445] 04 03` —— n1=0（本次会话还没领取新形象）、n2=3（已拥有的三只）、
      尾字节 03 = 3-n1 = 剩余可领取位。这一刻真服宠物栏是正常的（961+946+973 一次发出、窗口有内容）。
    ★ 排除 40B：`03 [6c15][7061][4181] 03 [...] 04 00` 是 **963 领取过程中**的产物
      —— 真服 21:02:28/38/39 连点三次 963，每点一次 n1 +1 并推一次 962（28→34→40B），
      n1 就是「本次已领取的形象数」。发 40B 等于告诉客户端"已经领完了"，
      客户端因此不发 973、只发 1 次 946，宠物界面全空（2026-11-23 23:13 实测就是这个）。
    ★ uid 编码：800000 + 形象id*1000 + 序号（101/201）。
    """
    if pets is None and out is None:
        return TPL["962"] if TPL.get("962") else b"\x00\x00\x04\x03"
    pets = PET_UIDS if pets is None else list(pets)
    out = [] if out is None else list(out)
    # ★ 2026-11-24 15:0x 修正：空列表必须发 **4B `00 00 04 03`**。
    # 22B 的 `00 03 [804457][810101][812101] 04 03` 是「已拥有 3 个宠物形象」，
    # 发给新号等于凭空塞 3 个形象：客户端会把第一个挂到玩家身上当跟随，
    # 而形象本身没有名字（名字在 947 里），于是地图上出现一只「？？」的熊猫。
    # 真服 15:41:16 现场（交任务刚拿到功夫熊猫）就是 962=4B + 947=83B：
    #   [15:41:16] S->C cmd=962 len=4 hex=00000403
    #   [15:41:16] S->C cmd=947 len=83 hex=01ac"功夫熊猫"...
    # 说明「有没有宠物」由 947 决定，962 只表示「拥有几个宠物形象/皮肤」。
    if not pets and not out:
        return b"\x00\x00\x04\x03"
    body = bytearray([len(out) & 0xFF])
    for u in out:
        body += b"\xce" + int(u).to_bytes(4, "big") + b"\x00"
    body.append(len(pets) & 0xFF)
    for u in pets:
        body += b"\xce" + int(u).to_bytes(4, "big") + b"\x00"
    body += bytes([0x04, max(0, 3 - len(out)) & 0xFF])
    return bytes(body)


def pack_964(pets=None, sel=None, extra=None):
    """宠物出战确认（963 -> 964）。

    结构（真服 16/22/28/34/40/46B 六档全部吻合）：
      [u8 01][varint 当前选中 uid][u8 00]
      [u8 n1][n1 × (varint uid + u8 00)]     ← 出战列表
      [u8 n2][n2 × (varint uid + u8 00)]     ← 仓库/其他（962 的第二段）
      [u8 3-n1 剩余出战位]

    与 962 同构，仅多一个 `01 + 选中 uid + 00` 头、尾部没有 `04` 容量字节。
    证据：
      16B  01 4569 00 | 01 4569                | 00       | 02
      28B  01 6445 00 | 03 4569 5c75 6445      | 00       | 00
      46B  01 4181 00 | 03 6c15 7061 4181      | 03 4569 5c75 6445 | 00
    """
    pets = PET_UIDS if pets is None else list(pets)
    extra = [] if extra is None else list(extra)
    sel = (pets[-1] if pets else 0) if sel is None else int(sel)
    body = bytearray([0x01])
    body += b"\xce" + int(sel).to_bytes(4, "big") + b"\x00"
    body.append(len(pets) & 0xFF)
    for u in pets:
        body += b"\xce" + int(u).to_bytes(4, "big") + b"\x00"
    body.append(len(extra) & 0xFF)
    for u in extra:
        body += b"\xce" + int(u).to_bytes(4, "big") + b"\x00"
    body.append(max(0, 3 - len(pets)) & 0xFF)
    return bytes(body)


def _num(v):
    """协议里的变长整数：<0x80 单字节 / 0xCC+1B / 0xCD+u16BE / 0xCE+u32BE。"""
    v = int(v) & 0xFFFFFFFF
    if v < 0x80:
        return bytes([v])
    if v <= 0xFF:
        return b"\xcc" + bytes([v])
    if v <= 0xFFFF:
        return b"\xcd" + v.to_bytes(2, "big")
    return b"\xce" + v.to_bytes(4, "big")


# 947 头部：名字之后是 7 个变长整数，第 7 个是宠物气血
#   （功夫熊猫 Lv12 模板 2062 / Lv19 抓包 2617，两种模板位置一致）。
_HP_FIELD_IDX = 7
# 形象卡描述「获得该形象的宠物气血 +1000」。真服实测是 +1002（2617→3619），
# 多出的 2 点应从气血成长派生，本地没有养成系统，按描述写 +1000。
def _hp_span(base):
    """返回 (起点, 终点, 气血值)；结构对不上返回 None。

    947 = [01][0xA0|名长][名字][7 个变长整数…]，第 7 个是气血。
    抓包对照：Lv12 模板 2062(cd080e)、Lv19 真服 2617(cd0a39) 都落在这一格。
    """
    if not base or base[0] != 0x01:
        return None
    n = base[1] - 0xa0
    if n <= 0 or 2 + n >= len(base):
        return None
    i = 2 + n
    for idx in range(1, _HP_FIELD_IDX + 1):
        start = i
        c = base[i]
        if c < 0x80:
            end = i + 1
        elif c == 0xcc:
            end = i + 2
        elif c == 0xcd:
            end = i + 3
        elif c == 0xce:
            end = i + 5
        else:
            return None
        if end > len(base):
            return None
        if idx == _HP_FIELD_IDX:
            raw = base[start:end]
            val = int.from_bytes(raw[1:], "big") if len(raw) > 1 else raw[0]
            return start, end, val
        i = end
    return None


SKIN_HP_BONUS = 1000


def _vread(b, i):
    """读一个协议变长整数，返回 (值, 下一个下标)。"""
    c = b[i]
    if c < 0x80:
        return c, i + 1
    if c == 0xCC:
        return b[i + 1], i + 2
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    raise ValueError("未知变长整数前缀 0x%02x" % c)


def _head_nums(seg, start):
    """把 947 头部数值区解成 list，失败返回 ([], False)。"""
    out = []
    i = start
    while i < len(seg):
        try:
            v, i = _vread(seg, i)
        except Exception:  # noqa: BLE001
            return [], False
        out.append(v)
    return out, bool(out)


def pack_947(strap, skin=10000):
    """选中宠物详情。

    妖灵区结构（从真服 5 档样本逐字节拆出来）：
      8 个槽位，**有妖灵的槽 6B = `cd + u16 id + 部位 + 01 01`，
      空槽 4B = `00 00 00 00`**。
      empty(98B) = 32B 全空；1 只(101B) = 6+7×4 = 34B；3 只(105B) = 38B。
    以前只认 0/1/2/3 只这四档模板，4 只以上找不到键 → 回落 empty（0 只），
    用户把 8 个槽全装上后客户端反而显示「一个都没有」。
    现在按 `spirit_slot`（槽位号 1~8）动态拼 8 槽，任意组合都能表达。

    头部第 15 字节：无妖灵 `02`（empty 档）、有妖灵 `03`（`0` 档），跟着基底走。

    ★ skin = 宠物形象 id（Pet.xml 的 item id）。线上服 2026-11-27 16:05 实证：
      用掉美人鱼形象卡后，947 的 `cd 27 10 08`（10000）变成 `cd 27 21 08`（10017），
      同时宠物气血 2617→3619（+1002）、宠物战力 855→875（+20），
      名字「功夫熊猫」不变（换形象只换模型）。所以这里把 `cd2710` 那段换成变长
      编码的 skin，后面的 `08` 原样保留；skin ≠ 10000 时给气血补 +1000。
    """
    # ★ 2026-11-27 18:2x：格子 = 面板位置 1~8（血/攻/防/命/闪/坚/绝/暴），
    #   每条里的字节写**属性码**（6101→07、7101→08、8101→09，与真服 947 一致）。
    slots = [0] * 8
    for k, v in (strap or {}).items():
        try:
            s = int(k)
        except (TypeError, ValueError):
            continue
        if 1 <= s <= 8:
            slots[s - 1] = int(v)
    ids = [x for x in slots if x]
    base = TPL["947"].get("0") if ids else TPL["947"].get("empty")
    if not base:
        base = TPL["947"].get("empty") or b""
    i = base.find(b"\xcd\x27\x10\x08")
    j = base.rfind(b"\x03\xce\x00\x0c\x45\x69")
    if i < 0 or j < 0:      # 结构没对上就退回原包，别拼坏
        return base
    # ★ 2026-11-27：头部数值段必须**重新编码**再加妖灵属性。
    #   以前 head = base[:i] 原样照抄模板，所以装上/卸下妖灵后 947 里
    #   气血、攻击、命中、战力一动不动（用户反馈「穿和脱不加减宠物属性」）。
    #   真服是按 basic 逐项加进这组数字的（映射见 ATTR_947_IDX），
    #   数值跨 varint 宽度（如 186→306）时长度会变，所以只能解码→改→重编码。
    n = base[1] - 0xA0
    nums, ok = _head_nums(base[:i], 2 + n)
    if not ok:
        return base
    for k, v in spirit_bonus(strap).items():
        idx = ATTR_947_IDX.get(int(k))
        if idx is not None and idx < len(nums):
            nums[idx] += v
    pw = spirit_power(strap)
    if pw and POWER_IDX_947 < len(nums):
        nums[POWER_IDX_947] += pw
    # 形象卡「获得该形象的宠物气血 +1000」：真服 2617→3619(+1002)，按描述 +1000。
    if int(skin) != 10000 and ATTR_947_IDX[53] < len(nums):
        nums[ATTR_947_IDX[53]] += SKIN_HP_BONUS
    # nums[0] = 宠物等级。模板是别人号的 0x0c(Lv12)，本地没有养成系统，统一写 1，
    # 与 957「刚获得」档（01）对齐，否则宠物栏 Lv1、详情 Lv12 自相矛盾。
    # （以前写死 out[14]，那个偏移只在名字正好 12 字节时成立，改名宠物就会错位。）
    if nums:
        nums[0] = 1
    head = base[:2 + n] + b"".join(_num(x) for x in nums) + _num(skin) + b"\x08"
    tail = base[j:]
    body = bytearray()
    for s in slots:
        if s:
            body += b"\xcd" + s.to_bytes(2, "big")
            body.append(nature_of(s) & 0xFF)
            body += b"\x01\x01"
        else:
            body += b"\x00" * 4
    out = bytearray(head + body + tail)
    return bytes(out)


def strap_key(strap):
    ids = [int(i) for i in (strap or {}).values()]
    return "+".join(str(i) for i in sorted(ids)) if ids else "0"


def has_tpl(strap):
    return strap_key(strap) in (TPL["947"] or {})


def pack_1410(bag, strap):
    """1410 = **未上阵**妖灵列表（真人服实测反推，证据链见文件头）。

    bag  = {妖灵id: {"idx":获得次序}}，strap = {槽位: 妖灵id}
    每条 = [u8 序号-1][varint 妖灵id][u8 部位][01][01]

    证据（真服 21:03）：
      上阵前 3 只   cc80 03 | 00 1101 01 | 01 4101 02 | 02 2101 03   (24B)
      上阵 1101 后  cc80 02 | 01 4101 02 | 02 2101 03                (17B)
      上阵 2101 后  cc80 01 | 01 4101 02                             (10B)
      三只全上阵    cc80 00                                          (3B)
    所以第 3 个字节是妖灵的「部位」（血=1 命=2 攻=3，与 1411 的槽位字节一致），
    列表里只剩没上阵的那些。
    """
    equipped = set(int(i) for i in (strap or {}).values())
    rows = []
    for iid, info in (bag or {}).items():
        iid = int(iid)
        if iid in equipped:
            continue
        info = info if isinstance(info, dict) else {}
        rows.append((int(info.get("idx") or 1), iid, nature_of(iid)))
    rows.sort()
    body = bytearray(b"\xcc\x80")
    body.append(len(rows) & 0xFF)
    for i, (idx, iid, part) in enumerate(rows):
        # 序号必须连续 0..n-1：以前直接用 idx，任务奖励的 idx 会跳号
        # （比如 5 号缺失），客户端按序号建列表时会对不上。
        body.append(i & 0xFF)
        body += _venc(iid)
        body.append(part & 0xFF)
        body += b"\x01\x01"
    return bytes(body)


def nature_of(iid):
    """妖灵部位：按千位查 NATURE（八系全部实证，见表上注释）。

    返回 0 = 不认识这只妖灵（配置里没有的千位），调用方应把它留在背包，
    **不要**拿「上次点开的槽位」顶替 —— 那正是「装上了却进错槽」的原因。
    """
    return NATURE.get(int(iid) // 1000, 0)


def pack_1412(slot, iid):
    """上阵/卸下应答：[槽位][妖灵id][00][00]。卸下时 iid 传 0。"""
    return (bytes([int(slot) & 0xFF]) + _venc(int(iid))
            + b"\x00\x00")


def strap_tail(strap):
    """947 末尾的已上阵妖灵段（仅结构参考，实际用原包回放）。"""
    out = bytearray()
    for slot in sorted(strap or {}):
        out += _venc(int(strap[slot]))
        out += bytes([int(slot) & 0xFF, 0x01, 0x01])
    return bytes(out)


def _venc(v):
    v = int(v)
    if v >= 0x10000:
        return b"\xce" + v.to_bytes(4, "big")
    if v >= 0x100:
        return b"\xcd" + v.to_bytes(2, "big")
    if v >= 0x80:
        return b"\xcc" + bytes([v])
    return bytes([v])


if __name__ == "__main__":
    print("妖灵 %d 个 / 宠物 %d 个 / 947 模板 %s"
          % (len(SPIRIT), len(PET), sorted((TPL["947"] or {}).keys())))
    for iid in (1101, 2101, 4101, 1401):
        print(" ", iid, name_of(iid), basic_of(iid))
    print("  含「魍魉/共工/白虎」:", pick_spirit(["魍魉", "共工", "白虎"])[:12])

    bag = {1101: {"idx": 1}, 4101: {"idx": 2}, 2101: {"idx": 3}}
    g0 = pack_1410(bag, {}).hex()
    w0 = "cc800300cd044d01010101cd100502010102cd0835030101"
    print("1410 三只未上阵:", g0, "OK" if g0 == w0 else "不匹配")
    g1 = pack_1410(bag, {1: 1101}).hex()
    w1 = "cc800201cd100502010102cd0835030101"
    print("1410 上阵1101后:", g1, "OK" if g1 == w1 else "不匹配")
    g2 = pack_1410(bag, {1: 1101, 3: 2101}).hex()
    w2 = "cc800101cd1005020101"
    print("1410 上阵两只后:", g2, "OK" if g2 == w2 else "不匹配")
    g3 = pack_1410(bag, {1: 1101, 2: 4101, 3: 2101}).hex()
    print("1410 三只全上阵:", g3, "OK" if g3 == "cc8000" else "不匹配")
    for st in ({}, {1: 1101}, {1: 1101, 3: 2101}, {1: 1101, 3: 2101, 2: 4101}):
        print("947", strap_key(st), "len=%d tpl=%s"
              % (len(pack_947(st)), has_tpl(st)))
    print("1412:", pack_1412(1, 1101).hex())


# 模块导入即加载（服务端 main() 里再调一次也不影响）
load()
