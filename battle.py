# -*- coding: utf-8 -*-
"""
KD163 战斗内核 v2 —— 服务端权威的属性制战斗

一、客户端的能力边界（实测，不能绕）
------------------------------------
1. 伤害由客户端本地结算，数值来源是登录模板里那个 2 级号的属性：
     logcat: set object(111101) hp damage = 16   玩家每回合打怪 16
             set object(12)     hp damage = 13   怪每回合打玩家 13（lv1 怪）
   打世界BOSS 也是 16，所以「每回合打出去多少」服务端改不了。
2. 客户端受伤跟怪物等级走：九尾狐(lv25) 实测约 44/回合（22:34 截图第 3 回合
   玩家血条 65%，380 * 35% / 3 ≈ 44），lv1 怪是 13。拟合 client_take(lv)=12+1.3*lv。
3. 客户端血条只显示百分比（截图实证 65% / 95%，没有绝对数值），初始值取 151。
   **这就是抓手**：把 151 的血量填成「回合数 × 客户端每回合伤害」，
   血条就会在服务端判定的那一回合刚好归零，玩家看不出任何差别。
4. 判死权在服务端（真服：S->C 148 带结束位，客户端才回 149）。

二、真实数值模型（全部来自客户端配置表）
--------------------------------------
   玩家：Levelup.xml  class+level -> hp_max / mp_max / attack_min,max / defense / crit
   怪物：Monster.xml  hp_max / level / monster_type
   技能：Skill.xml    damage_percent

   怪物没有攻击字段，按等级给基准线：mob_atk = 12 + 1.3 * mob_lv（与客户端一致）
   等级压制：gap = clamp(1 + 0.035 * (mob_lv - lv), 0.55, 1.8)

   pdmg = atk_avg * skill% * (1 + 0.10 * lv) * crit_mult     （服务端真实输出）
   mdmg = mob_atk * gap * (1 - 减伤)                          （服务端真实承伤）
   kill = ceil(mob_hp / pdmg)     die = ceil(player_hp / mdmg)
   kill <= die 才能赢；否则被怪打死。

三、为什么服务端伤害不再等于 16
------------------------------
   16 是「2 级模板号」在客户端算出来的，跟真实的 10 级/25 级属性无关。
   如果服务端也照着 16 算，176308 血的穷奇要打一万多回合，永远打不过，
   最后只能靠「自动判死」作弊 —— 那正是上一版「永远胜利」的根因。
   现在服务端按真实属性算输出，再用 151 的血量把回合数翻译成客户端血条，
   于是等级、职业、技能、怪物强度全部真实生效，血条也完全同步。
"""
import os
import re
import math
import random

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(HERE, "res_cfg")

# 客户端固定的每回合输出（打怪），见文件头第 1 条
CLIENT_PDMG = 16

# ★ 2026-11-24 18:2x：151 的血量重新走「回合数折算」，恢复文件头第 37 行的原设计。
# 16:2x 因为「打心魔闪退」把 max_hp 改成了 Monster.xml 真值，但真凶其实是
# enc_val 的整数分档（132 发成单字节被读成负数），不是折算本身。
# 改成真值后的后果（2026-11-24 18:0x logcat 实证）：
#   摄心魔王 1500 血，151 发 1500，客户端按 16/回合扣 —— 要 94 回合才空；
#   而服务端 5 回合就结算 → 血条只掉 5% 就弹「战斗胜利」= 用户说的「莫名其妙胜利」。
# 现在：怪血 = CLIENT_PDMG × 服务端回合数，玩家血 = CLIENT_MDMG × 可扛回合数，
# 双方血条都恰好在结算那一回合归零。BATTLE_HP_TRUE=1 可整体回退到配置真值。
HP_TRUE = os.environ.get("BATTLE_HP_TRUE", "0") not in ("0", "", "false", "False")


# 客户端每回合承伤（固定 13，与怪物等级无关）
# 2026-11-22 23:06 logcat 逐回合实测（九尾狐 lv25）：
#   set object(12) hp damage = 13, m_hp = 343 -> 330 -> 317 -> 304 ...
# 之前按「怪物等级」猜的 44 是错的（那条截图是别的地图状态）。
CLIENT_MDMG = 13


def client_take(mob_lv):
    """客户端每回合承伤。只用于**折算显示血条**：151 的玩家血量填成
    13 × 可扛回合数，客户端按 13/回合掉，血条就在判死那一回合归零。"""
    return float(CLIENT_MDMG)


def mob_atk(mob_lv):
    """服务端用的怪物攻击基准线。

    怪物表里没有攻击字段，用等级拟合一条低等级更平缓的曲线：
    lv1 的怪只有 5 点伤害（1 级号 48 血也能打），lv25 约 34，lv55 约 70。
    再乘等级压制系数 —— 越级打才真的会被打死。"""
    return 4.0 + 1.2 * float(mob_lv)


MAX_ROUNDS = 20          # 力竭：20 回合还没分出胜负判负（纯防御磨到这儿结束）
# 防御回合的进度折算（2026-11-24 19:2x 定稿）。
# 以前两个都是 1.0，等于「点防御 = 玩家全力输出」，一直点防御也能赢（用户实测「离谱」）。
# 现在：防御回合**完全不推进击杀进度**（宠物只做动画表演，不结算伤害），
# 玩家受伤减半。所以纯防御永远打不死怪，只会在 MAX_ROUNDS 力竭判负 ——
# 想赢必须出手攻击。宠物挥爪的动画由客户端按 148 自己演，不需要服务端给伤害。
PET_ATK_SHARE = 0.0      # 防御时怪掉血权重（0 = 只表演不掉血）
DEF_TAKE_SHARE = 0.50    # 防御时玩家受伤比例（减伤一半）
DMG_LEVEL_GAIN = 0.14    # 每级输出增益（装备/宠物/境界的综合折算）
GAP_SLOPE = 0.035        # 每级等级压制的伤害差
GAP_MIN, GAP_MAX = 0.55, 1.8

OPEN_TMPL = bytes.fromhex(
    "010c06a6e8908ce696b001004c4c2f2f00a00000000001ce0001b1fd50500000020107")
ROUND_TMPL = bytes.fromhex(
    "000002010107cdeac50100020705000701f000000000070101ce00a986d5"
    "0100020105000101f30000000002010d01070d02")
END_TMPL = bytes.fromhex(
    "010001010107cdeac50100030705000701f8070c000000000002010d01070d00")


def enc_val(v):
    """协议变长整数。

    ★ 2026-11-24 16:3x：阈值以前写成 `<0xCD 单字节`，把 132/143/244 全都
    塞进 1 字节或错误地升到 3 字节，客户端按有符号字节读成负数 → 收到 151
    后 55ms 干净退出（打心魔闪退的真凶）。
    真服样本给的规则（三档全部逐字节印证）：
      76  -> 4c           (1B)
      104 -> 68           (1B)
      132 -> cc 84        (2B, 0xCC + 1B)
      1067-> cd 04 2b     (3B, 0xCD + u16)
    """
    v = int(v)
    if v < 0x80:
        return bytes([v])
    if v < 0x100:
        return b"\xCC" + bytes([v])
    if v < 0x10000:
        return b"\xCD" + v.to_bytes(2, "big")
    return b"\xCE" + v.to_bytes(4, "big")


# 宠物段（151 里决定战斗画面里有没有宠物上场）
#   无宠物：00 a0 00 00 00 00 01
#   有宠物：01 (0xA0|名字字节数) 名字 等级 血 血 攻 01
#   真服 23:45:03 锁妖塔样本（功夫熊猫 Lv15）：
#     01 ac e58a9fe5a4abe7868ae78cab 0f cd0a34 cd0a34 cd2710 01
#   本地以前整段写死成「无宠物」，所以地图上熊猫跟着跑、进战斗就消失。
PET_SEG_EMPTY = bytes.fromhex("00a00000000001")
PET_HP_LV15 = 2612          # 真服样本：Lv15 熊猫血 2612
PET_ATK_FIXED = 10000       # 真服样本里的 cd2710，四个样本一致


def build_pet_seg(pet=None):
    """pet = {"name","level","hp","atk"}；None/空 = 不带宠物上场。"""
    if not pet:
        return PET_SEG_EMPTY
    name = (pet.get("name") or "").encode("utf-8")
    if not name or len(name) > 15:
        return PET_SEG_EMPTY
    lv = max(1, int(pet.get("level") or 1))
    hp = int(pet.get("hp") or 0) or int(round(PET_HP_LV15 * lv / 15.0))
    atk = int(pet.get("atk") or PET_ATK_FIXED)
    return (b"\x01" + bytes([0xA0 | len(name)]) + name
            + bytes([lv & 0xFF])
            + enc_val(hp) + enc_val(hp) + enc_val(atk) + b"\x01")


def build_open(mob_id, hp=None, player_hp=None, player_mp=None, cls=1,
               pet=None, name=None, sex=1):
    """151 开战包（结构对齐真服 39B 样本）。

    [12:14] 玩家当前/最大血、[14:16] 当前/最大蓝、[22] 怪物数、
    [24:28] 怪物 ID、[28:..] 怪物当前/最大血 都是变长整数。

    ★ [2] 是我方职业/形象 ID（2026-11-23 真服转职样本实证）：
      未转职抓包 = 06，转职御剑（cmd=35 payload=01）之后 = 01。
      客户端的战斗模型和技能组全按这个字节选，固定成 06 就是「假转职」——
      跑图时是转职后的皮肤，一进战斗就变回初始职业。
    """
    mid = int(mob_id)
    hp = max(1, int(hp if hp is not None else 80))
    player_hp = max(1, int(player_hp if player_hp is not None else 76))
    player_mp = max(0, int(player_mp if player_mp is not None else 47))
    # ★ 2026-11-26 18:5x：[10] 是**性别**（1 男 / 0 女），模板恒为 01。
    #   女号进战斗一直演成男新手，就是这里固定抄了模板的 01。
    _sx = 1 if int(sex or 1) == 1 else 0
    head = bytearray(OPEN_TMPL[:12])
    head[2] = int(cls) & 0xFF
    head[10] = _sx
    # ★ 2026-11-24 14:05：模板头 `01 0c 06 a6"萌新" 01 00` 里的 0xA6+「萌新」
    # 是**玩家昵称**。以前整段抄模板，新建角色一进战斗，左边单位名还叫「萌新」
    # （用户实测）。现在按实际昵称动态拼，昵称为「萌新」时与原模板逐字节一致。
    if name is not None:
        # ★ 2026-11-24 16:1x：151 里的昵称**只能 6 字节**。
        # 真服 relay.log 里全部 10 档长度（35/37/39/59/61/63/65/143/197/201）的
        # 头部都是 `01 0c 06 a6 e8908ce696b0 01 00`，昵称固定「萌新」6 字节。
        # 本地发 12 字节（「图门霁驿」）时，客户端收到 151 后 55ms 干净退出
        # （无 Fatal signal、无 tombstone = 协议自检失败主动 exit）——
        # 用户实测「打心魔直接闪退」。这里按 UTF-8 边界截到 ≤6 字节。
        # ★ 2026-11-24 16:4x：昵称发**完整**的，不要再截到 6 字节。
        # 之前截断是为了规避「151 后闪退」，真凶其实是 enc_val 的整数分档
        # （132 发成单字节 84 被读成负数）。编码修好后长名字完全正常：
        # 真服 151 的昵称是 `0xA0|len + UTF-8`，本来就支持变长。
        # 上限按 0xA0|31 取 31 字节，并且只在完整字符边界累加，避免切出半个汉字。
        nm = b""
        for _ch in name:
            _cb = _ch.encode("utf-8")
            if len(nm) + len(_cb) > 31:
                break
            nm += _cb
        head = bytearray(b"\x01\x0c"
                         + bytes([int(cls) & 0xFF, 0xA0 | len(nm)]) + nm
                         + bytes([_sx, OPEN_TMPL[11]]))   # [10]=性别 [11]=固定 00
    return (bytes(head)
            + enc_val(player_hp) + enc_val(player_hp)
            + enc_val(player_mp) + enc_val(player_mp)
            + build_pet_seg(pet)
            + b"\xce" + mid.to_bytes(4, "big")
            + enc_val(hp) + enc_val(hp)
            + OPEN_TMPL[30:])


def build_round(action_payload, end=False, cls=None):
    """148 回合结算：回显 146 的动作；end=True 用结束模板。

    结束模板第 2 字节恒为 00（真服 4 份样本一致）。改成 01 想让客户端走
    battle_lose 会直接闪退 —— 这套客户端的 PVE 只有「怪死」一种结算画面，
    失败不能靠这个字节表达，只能靠结算数据和惩罚（见 settle() 与上层）。

    ★ [6:9] 是本次出手的技能 ID（CD + u16），必须**原样回显 146 里的技能号**：
      未转职 60101「仙人指路」，转职御剑后 10101「仙心弈剑」/10201/10301/11001。
      真服样本（除技能号外逐字节一致）：
        146 0107cdeac5 -> 148 000002010107cdeac5...
        146 0107cd27d9 -> 148 010002010107cd27d9...
        146 040000     -> 148 000002010400000...
      2026-11-23 20:14 用户反馈「新学的技能点了还是放第一个」：
      原因是这里无条件用 cls*10000+101 覆写 [6:9]，把 10201/11001 改回 10101。
      cls 只在 146 **完全没带技能号**（如防御 04 或老短包）时做兜底，且只对攻击(01)生效。
    """
    t = bytearray(END_TMPL if end else ROUND_TMPL)
    act = bytes(action_payload)
    has_skill = False
    if act:
        act = (act + b"\x00" * 5)[:5]
        t[4:9] = act
        # act = [动作][07][CD/CC/CE + 技能号]，第 3 字节是变长前缀
        has_skill = act[2] in (0xCC, 0xCD, 0xCE)
    if cls and not has_skill and act and act[0] == 1:
        sk = int(cls) * 10000 + 101
        if 0 < sk <= 0xFFFF:
            t[6:9] = b"\xcd" + sk.to_bytes(2, "big")
    return bytes(t)


def target_from_request(payload):
    """145 请求 payload: ce + u32BE 目标ID -> (mob_id, ok)"""
    if len(payload) >= 5:
        return int.from_bytes(payload[1:5], "big"), True
    return 0, False


class BattleTables(object):
    """客户端配置表读取器（只读）。"""

    def __init__(self, cfg_dir=CFG_DIR):
        self.dir = cfg_dir
        self.monster = {}
        self.level = {}
        self.skill = {}

    def load(self):
        self._load_monster()
        self._load_level()
        self._load_skill()
        return self

    def _read(self, name):
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()

    @staticmethod
    def _attr(block, key, default=""):
        m = re.search(r"\b" + key + r'="([^"]*)"', block)
        return m.group(1) if m else default

    @classmethod
    def _num(cls, block, key, default=0.0):
        try:
            return float(cls._attr(block, key, str(default)) or default)
        except ValueError:
            return default

    def _load_monster(self):
        for m in re.finditer(r'<item id="(\d+)"([^>]*?)/>', self._read("Monster.xml")):
            mid, body = m.group(1), m.group(2)
            self.monster[mid] = {
                "name": self._attr(body, "name"),
                "hp_max": int(self._num(body, "hp_max", 1)),
                "level": int(self._num(body, "level", 1)),
                "type": int(self._num(body, "monster_type", 1)),
            }

    def _load_level(self):
        for m in re.finditer(r'<item class="(\d+)" level="(\d+)"([^>]*?)/>',
                             self._read("Levelup.xml")):
            cls, lv, body = m.group(1), m.group(2), m.group(3)
            self.level[(cls, lv)] = {
                "hp_max": self._num(body, "hp_max", 76),
                "mp_max": self._num(body, "mp_max", 47),
                "atk_min": self._num(body, "attack_min", 11),
                "atk_max": self._num(body, "attack_max", 17),
                "defense": self._num(body, "defense", 0),
                "crit": self._num(body, "crit", 0),
            }

    def _load_skill(self):
        for m in re.finditer(r'<item id="(\d+)"([^>]*?)/>', self._read("Skill.xml")):
            sid, body = m.group(1), m.group(2)
            self.skill[sid] = {
                "name": self._attr(body, "name"),
                "damage_percent": self._num(body, "damage_percent", 1.0) or 1.0,
            }

    # ---- 查询 ----
    def mob(self, mob_id):
        return self.monster.get(str(mob_id)) or {
            "name": "", "hp_max": 100, "level": 1, "type": 1}

    def player(self, cls=1, lv=2):
        """该职业该等级的属性；表里没有就向下取最近一档。"""
        key = (str(cls), str(lv))
        if key in self.level:
            return self.level[key]
        best = None
        for (c, l), st in self.level.items():
            if c != str(cls):
                continue
            if int(l) <= int(lv) and (best is None or int(l) > best[0]):
                best = (int(l), st)
        if best:
            return best[1]
        return {"hp_max": 76.0, "mp_max": 47.0, "atk_min": 11.0,
                "atk_max": 17.0, "defense": 0.0, "crit": 0.0}

    def skill_pct(self, skill_id):
        return self.skill.get(str(skill_id), {}).get("damage_percent", 1.0)


# ---------------- 掉落（真服 cmd=150 实证，2026-11-24 01:0x）----------------
# 真服 58 份 150 样本逐字节对齐后的结构：
#   [经验 变长][3B 未知(暂发0)][铜钱 变长][1B 未知(暂发0)][u8 件数]
#   {[物品ID 变长][u8 数量][u8 标记=1/2]}[4B 0]
# 实测每次 1~3 件，全是强化灵石/仙玉、见闻录、洗炼石、成长丹、橙水晶、
# 藏宝图、精魄、坐骑洗髓丹这类材料；同一种物品的「标记」在 1/2 之间跳，
# 与件数、等级都不相关（20142 有 1 也有 2），所以按 1 下发，不影响客户端显示。
# 世界BOSS（monster_type 4/6）没有专属物品 id，只是「件数更多 + 稀有池权重更高」。
DROP_COMMON = [            # type1 普通怪
    (20142, 30), (20139, 18), (20141, 14), (20369, 10),
    (20052, 6), (20053, 4), (20155, 5), (20156, 3),
]
DROP_ELITE = [             # type2 狂暴 / type3 被镇压 / 副本BOSS
    (20142, 16), (20139, 12), (20141, 8), (20369, 8),
    (20053, 6), (20156, 5), (20217, 6), (20347, 5),
    (20739, 6), (20160, 4), (20161, 3), (20163, 3),
    (20218, 2), (20348, 2), (20730, 3), (20747, 6),
]
DROP_BOSS = [              # type4/6 世界BOSS
    (20160, 20), (20161, 16), (20163, 12), (20348, 8),
    (20218, 8), (20219, 5), (20347, 6), (20730, 6),
    (20732, 5), (20735, 6), (20741, 8), (20742, 6), (20743, 3),
    (20755, 4), (20747, 6), (30999, 2),
]


def _pick(pool):
    """按权重抽一件。"""
    total = sum(w for _, w in pool)
    r = random.uniform(0, total)
    upto = 0.0
    for iid, w in pool:
        upto += w
        if r <= upto:
            return iid
    return pool[-1][0]


def roll_drops(mob_type, mob_lv):
    """生成掉落：普通 1~2 件，精英/被镇压 2~3 件，世界BOSS 2~3 件。

    真服 58 份 150 里件数只出现过 0 / 2 / 3，没有 1 也没有 4，
    所以这里上限锁 3，避免客户端结算页出现真服从未有过的行数。
    返回 [(item_id, count), ...]；同一件最多叠 2 个，避免一次性给太多。
    """
    if mob_type >= 4:
        pool, n = DROP_BOSS, random.randint(2, 3)
    elif mob_type >= 2:
        pool, n = DROP_ELITE, random.randint(2, 3)
    else:
        pool, n = DROP_COMMON, random.randint(1, 2)
    got = []
    for _ in range(n):
        iid = _pick(pool)
        for k, (gid, cnt) in enumerate(got):
            if gid == iid:
                if cnt < 2:
                    got[k] = (gid, cnt + 1)
                break
        else:
            got.append((iid, 1))
    return got


class BattleSession(object):
    """一场战斗。服务端算两边真实血量，客户端只负责把血条演出来。"""

    def __init__(self, mob_id, tables, cls=1, lv=2, hp_ratio=1.0, pet=None,
                 name=None, attrs=None, dmg_mult=1.0, sex=1):
        self.tables = tables
        self.pet = pet or None
        self.player_name = name
        # ★ 2026-11-26 18:5x：1 男 / 2 女（内部值），build_open 里转成协议 1/0。
        self.sex = int(sex or 1)
        self.mob_id = int(mob_id)
        info = tables.mob(self.mob_id)
        self.name = info["name"]
        self.mob_lv = info["level"]
        self.mob_type = info["type"]
        self.cfg_hp = max(1, info["hp_max"])
        self.cls, self.lv = int(cls), int(lv)

        st = tables.player(cls, lv)
        # ★ 2026-11-24 17:5x：伤害必须用**面板真值**（含装备/强化/妖灵），
        # 不能用 Levelup 裸值再乘等级系数。用户反馈「莫名其妙胜利」——
        # 原来 base_deal = atk_avg * (1 + 0.14*lv)：lv38 裸攻 177 ×6.32 ≈ 1119，
        # 一个 1500 血的摄心魔王两回合就死，任何怪都是「需1回合」，
        # 装备脱了也一样，所以看着像战斗没打就赢了。
        # 现在优先吃面板（29 包同一口径）：lv38 攻 258~285 → 271/回合，
        # 1500 血 ≈ 6 回合，脱武器立刻掉到 5 折以下，装备才真的起作用。
        # attrs 为空时退回旧公式（离线测试/探针没有面板上下文）。
        at = attrs or {}
        hp_base = at.get("hp_max") or st["hp_max"]
        atk_min = at.get("attack_min") or st["atk_min"]
        atk_max = at.get("attack_max") or st["atk_max"]
        dfn = at.get("defense") or st["defense"]
        crit = at.get("crit") or st["crit"]
        self.panel_based = bool(at)
        self.player_hp_max = max(1, int(hp_base * max(0.05, hp_ratio)))
        self.player_mp_max = max(0, int(at.get("mp_max") or st["mp_max"]))
        self.player_mp = self.player_mp_max

        # ---- 服务端真实数值 ----
        atk_avg = (atk_min + atk_max) / 2.0
        self.crit_mult = 1.0 + min(0.30, crit / 400.0)
        _gain = 1.0 if self.panel_based else (1.0 + DMG_LEVEL_GAIN * self.lv)
        # dmg_mult：GM「伤害」倍率（测试用，只影响服务端折算的回合数，
        # 不动面板 29、不动 151 的血量公式）。倍率越大 → rounds_to_kill 越小。
        self.dmg_mult = max(0.01, float(dmg_mult or 1.0))
        self.base_deal = max(1.0, atk_avg * _gain * self.crit_mult * self.dmg_mult)
        self.gap = max(GAP_MIN, min(GAP_MAX,
                                    1.0 + GAP_SLOPE * (self.mob_lv - self.lv)))
        self.mit = dfn / (dfn + 120.0) if dfn else 0.0
        self.raw_take = mob_atk(self.mob_lv) * self.gap
        self.take = max(1.0, self.raw_take * (1.0 - self.mit))

        # ---- 回合账 ----
        self.deal = int(round(self.base_deal))
        self.rounds_to_kill = max(1, int(math.ceil(self.cfg_hp / self.base_deal)))
        self.rounds_to_die = max(1, int(math.ceil(self.player_hp_max / self.take)))
        # ★ 2026-11-25 00:4x：真正打的回合数 = min(算术回合, MAX_ROUNDS)。
        #   以前直接拿 rounds_to_kill 当胜利条件，29884 血的 Lv46 翼魔要 74 回合，
        #   而 20 回合就判「力竭」→ 怪永远死不了，用户看到的就是「打死了怪物还在」
        #   （其实是每场都力竭判负）。客户端血条 16/回合，最多也只能表达 20 回合左右，
        #   所以把战斗压进 MAX_ROUNDS：只要这 20 回合内玩家不会先倒下，就按胜利结算，
        #   血条同一回合见底。纯防御仍然是 0 进度 → 照样力竭判负。
        self.fight_rounds = max(1, min(self.rounds_to_kill, MAX_ROUNDS))
        self.can_win = self.rounds_to_die >= self.fight_rounds

        # ---- 两本账都是「回合数」，血量只是显示用的折算 ----
        self.atk_rounds = 0         # 我方出手次数（= 对怪造成伤害的次数）
        self.hit_rounds = 0         # 我方挨打次数

        self.round = 0
        self.over = False
        self.result = None          # "win" / "lose" / "draw"
        self.detail = []            # 每回合明细，便于复盘

    # ---- 显示血量 ----
    # ★ 2026-11-24 16:2x 曾因为「打心魔闪退」把这里改成发 Monster.xml 真值，
    # 但真正的崩溃原因是 enc_val 整数分档（132 发成单字节被读成负数），
    # 折算本身没问题。发真值反而让 1500 血的怪只掉 5% 血条就结算。
    # 18:2x 恢复折算，并留 BATTLE_HP_TRUE=1 作为回退开关。
    @property
    def max_hp(self):
        """151 里的怪物血量。

        ★ 2026-11-24 18:2x：默认走「折算」——CLIENT_PDMG × 服务端回合数。
        客户端每回合固定扣 16 点，只有把它能打满的回合数折成血量，
        血条才会在服务端判死的同一回合归零。发配置真值（1500）时
        客户端血条只掉 5% 就弹胜利，正是「莫名其妙胜利」的观感来源。
        """
        if HP_TRUE:
            return max(1, int(self.cfg_hp))
        return max(1, int(CLIENT_PDMG * self.fight_rounds))

    @property
    def hp(self):
        """剩余血量：按回合进度等比缩放（基数必须跟 max_hp 同一个，否则当前值会超过上限）。"""
        left = max(0.0, self.fight_rounds - self.atk_rounds)
        return int(round(self.max_hp * left / float(self.fight_rounds)))

    @property
    def disp_player_hp_max(self):
        """151 里的玩家血量：同理折算成 CLIENT_MDMG × 可扛回合数。

        直接发面板血（2132）时，客户端按 13/回合扣，撑到血条空要 164 回合，
        而服务端 60 多回合就结束了 —— 玩家血条看着几乎没动。
        """
        if HP_TRUE:
            return max(1, int(self.player_hp_max))
        return max(1, int(CLIENT_MDMG * self.rounds_to_die))

    @property
    def disp_player_hp(self):
        """显示用剩余血量：同样按回合进度等比缩放到 151 的血量上限。"""
        left = max(0, self.rounds_to_die - self.hit_rounds)
        if self.rounds_to_die <= 0:
            return 0
        return int(round(self.disp_player_hp_max * left / float(self.rounds_to_die)))

    # ---------- 对外 ----------
    def open_packet(self):
        return build_open(self.mob_id, self.max_hp,
                          self.disp_player_hp_max, self.player_mp_max,
                          cls=self.cls, pet=self.pet,
                          name=self.player_name, sex=self.sex)

    def on_action(self, payload):
        """处理一次 146，返回 (148 bytes, is_end, result)。

        action=1 攻击：双方各扣一次血；action=4 防御：本回合双方都不掉血
        （客户端表现一致，不影响血条同步）。
        """
        if self.over:
            return build_round(payload, end=True, cls=self.cls), True, self.result

        self.round += 1
        action = payload[0] if payload else 1
        if action == 1:
            # 不吃技能倍率：151 的血量是按「恒定每回合伤害」折算的，
            # 某回合打得更疼，血条就会在怪死之前先空（或反过来），
            # 所以技能只做表现，节奏保持恒定。
            self.atk_rounds += 1
            self.hit_rounds += 1
            self.detail.append((self.round, int(round(self.base_deal)),
                                int(round(self.take))))
        elif action == 4:
            # ★ 2026-11-24 19:2x：防御以前把「宠物出手」按**满回合**计
            # （atk_rounds += 1），等于防御一次也算玩家全力输出 → 一直点防御
            # 照样能在 rounds_to_kill 内把怪打死，用户实测「防御也能赢，离谱」。
            # 真服防御是「玩家不出手、受伤减半」，宠物那点输出远小于本体：
            #   怪物掉血速度 = 宠物占比 PET_ATK_SHARE（约三成）
            #   玩家掉血速度 = 减伤后 DEF_TAKE_SHARE（约五成）
            # 于是纯防御几乎打不死同级的怪，只会被慢慢磨死或打到回合上限。
            if self.pet:
                self.atk_rounds += PET_ATK_SHARE
            self.hit_rounds += DEF_TAKE_SHARE
            self.detail.append((self.round, 0, int(round(self.take * DEF_TAKE_SHARE))))

        if self.atk_rounds >= self.fight_rounds:
            self.over, self.result = True, "win"
        elif self.hit_rounds >= self.rounds_to_die:
            self.over, self.result = True, "lose"
        elif self.round >= MAX_ROUNDS:
            self.over, self.result = True, "draw"
        return build_round(payload, end=self.over, cls=self.cls), self.over, self.result

    # ---------- 结算 ----------
    def settle(self):
        """150 用的结算数据。

        胜利：经验 = 6 + 怪等级*4（lv1 小怪正好 10 点，与真服 150 的 0x0a 一致），
              精英/世界BOSS（monster_type>=4）×2.5。
        失败/力竭：0 经验 0 铜钱，扣经验由上层做。
        """
        if self.result == "win":
            exp = 6 + self.mob_lv * 4
            if self.mob_type >= 4:
                exp = int(exp * 2.5)
            return {"exp": exp, "coin": self.mob_lv * 2, "win": True,
                    "drops": roll_drops(self.mob_type, self.mob_lv)}
        return {"exp": 0, "coin": 0, "win": False, "drops": []}

    def summary(self):
        return ("%s(lv%d) 玩家血%d 输出%.0f/回合 承伤%.0f/回合 "
                "算术需%d回合 实打%d回合 可扛%d回合 -> %s"
                % (self.name or self.mob_id, self.mob_lv, self.player_hp_max,
                   self.base_deal, self.take, self.rounds_to_kill,
                   self.fight_rounds, self.rounds_to_die,
                   "可胜" if self.can_win else "打不过"))


if __name__ == "__main__":
    import sys
    t = BattleTables().load()
    print("怪物表 %d / 等级表 %d / 技能表 %d"
          % (len(t.monster), len(t.level), len(t.skill)))
    for mid in (sys.argv[1:] or ["111101", "900001", "900003", "900013"]):
        for lv in (10, 25, 40, 60):
            s = BattleSession(mid, t, 1, lv)
            print("lv%-3d %-55s 151=%dB" % (lv, s.summary(), len(s.open_packet())))
        s = BattleSession(mid, t, 1, 10)
        n = 0
        while not s.over and n < 100:
            s.on_action(bytes.fromhex("0107cdeac5")); n += 1
        print("   lv10 实打 %d 回合 -> %s（怪剩血%d 玩家剩血%d）\n"
              % (n, s.result, s.hp, s.disp_player_hp))
