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
# 千位 -> 部位（五系全部确认，真服 23:35:20 的 1410 直接给到 3101=4 / 5101=5）
NATURE = {1: 1, 4: 2, 2: 3, 3: 4, 5: 5, 7: 6, 8: 7, 6: 8}
# 部位编号 = 真服 1411 的槽位字节。已实证三个：1101 血→01 / 4101 命→02 / 2101 攻→03。
# 其余按 UI 顺序「血 攻 防 命 闪 坚 绝 暴」推：防→04、闪→05、坚→06、绝→07、暴→08。
# （PetEquip 名称：1101 血·共工 / 2101 攻·白光虎 / 3101 防·图腾王 / 4101 命·魍魉 /
#   5101 闪·精卫鸟 / 6101 暴·火烈鸟 / 7101 坚·醒狮 / 8101 绝·九尾）


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


def pack_947(strap):
    """选中宠物详情。

    妖灵区结构（从真服 5 档样本逐字节拆出来）：
      8 个槽位，**有妖灵的槽 6B = `cd + u16 id + 部位 + 01 01`，
      空槽 4B = `00 00 00 00`**。
      empty(98B) = 32B 全空；1 只(101B) = 6+7×4 = 34B；3 只(105B) = 38B。
    以前只认 0/1/2/3 只这四档模板，4 只以上找不到键 → 回落 empty（0 只），
    用户把 8 个槽全装上后客户端反而显示「一个都没有」。
    现在按 `spirit_slot`（槽位号 1~8）动态拼 8 槽，任意组合都能表达。

    头部第 15 字节：无妖灵 `02`（empty 档）、有妖灵 `03`（`0` 档），跟着基底走。
    """
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
    head, tail = base[:i + 4], base[j:]
    body = bytearray()
    for s in slots:
        if s:
            body += b"\xcd" + s.to_bytes(2, "big")
            body.append(nature_of(s) & 0xFF)
            body += b"\x01\x01"
        else:
            body += b"\x00" * 4
    out = bytearray(head + body + tail)
    # 头部第 14 字节 = 宠物等级（真服样本 11/12 跟着宠物等级走）。
    # 模板是从别人号抄的 0x0c(Lv12)，本地宠物没有养成系统，统一写 1，
    # 与 957「刚获得」档（01）对齐，否则宠物栏 Lv1、详情 Lv12 自相矛盾。
    if len(out) > 14:
        out[14] = 1
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
    """妖灵部位：血系(1xxx)=1 命系(4xxx)=2 攻系(2xxx)=3，其余 0（未确认，只能留在背包）。

    实证来源：真服 1410 里 1101→01、4101→02、2101→03，
    且 1411 请求的槽位字节正好是 01/03/02，与这三只一一对应。
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
