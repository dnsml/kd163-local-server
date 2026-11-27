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

   怪物没有攻击字段，按 **玩家最大生命的百分比** 建模（真服抓包反推，见下）
   等级压制：gap = clamp(1 + 0.020 * (mob_lv - lv), 0.60, 1.60)

   pdmg = atk_avg * ATK_SCALE * crit_mult               （服务端真实输出）
   mdmg = player_hp * MOB_TAKE_PCT * gap * type_mult    （服务端真实承伤，整组合计）
   kill = ceil(mob_hp / pdmg)     die = ceil(player_hp / mdmg)
   kill <= die 才能赢；否则被怪打死。

   ★ 2026-11-27 20:0x：`mdmg` 以前是 `mob_atk = 4 + 1.2*lv` 再按 `def/(def+120)` 减伤，
     Lv25 的怪对 Lv39 玩家只剩 **7 点**（用户：「世界BOSS打我伤害为什么那么低」）。
     现在按真服 148 反推（logs/relay.log 2026-11-27 17:3x~17:5x，Lv39 御剑 vs
     Lv25 九尾狐/玄蜂，monster_type=4）：
       29 面板 生命 7415 / 攻 1385-1503 / 防 1816
       148 主怪打玩家 -636 ~ -1513（随机），侍从恒定 -145
     即一组三只合计 ≈ 1240 = **16.7% 最大生命/回合**，主怪占 76.6%、侍从各 11.7%。
     同一批抓包里玩家打怪 -1220 ~ -3075，而本地面板只有 434 → 真实装备/强化
     把攻击抬了约 3.7 倍（面板 1444 vs 本地 385），所以加 ATK_SCALE 让两边同量级，
     否则「怪血量用真值、玩家伤害用低配值」必然打十几回合还打不死还被磨死。

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
# 宠物每回合那一下（148 宠物条目 `04 01 07 ce000f69b5 … d1fea6`）。
# d1 前缀 = 有符号 16 位（真服 2026-11-27 抓包：d1feae=-338 / d1fd7e=-642 会浮动），
# 本地模板写死 d1fea6 = -346，客户端就按这个数扣怪血 —— 所以 151 的怪血
# 必须把它算进每回合总量，否则第一回合血条就被打空。
PET_DMG = 338

# ★ 2026-11-24 18:2x：151 的血量重新走「回合数折算」，恢复文件头第 37 行的原设计。
# 16:2x 因为「打心魔闪退」把 max_hp 改成了 Monster.xml 真值，但真凶其实是
# enc_val 的整数分档（132 发成单字节被读成负数），不是折算本身。
# 改成真值后的后果（2026-11-24 18:0x logcat 实证）：
#   摄心魔王 1500 血，151 发 1500，客户端按 16/回合扣 —— 要 94 回合才空；
#   而服务端 5 回合就结算 → 血条只掉 5% 就弹「战斗胜利」= 用户说的「莫名其妙胜利」。
# 现在：怪血 = CLIENT_PDMG × 服务端回合数，玩家血 = CLIENT_MDMG × 可扛回合数，
# 双方血条都恰好在结算那一回合归零。BATTLE_HP_TRUE=1 可整体回退到配置真值。
HP_TRUE = os.environ.get("BATTLE_HP_TRUE", "1") not in ("0", "", "false", "False")
# ★ 2026-11-27 18:4x：真值模式下的回合上限。折算模式是 20（血条刚好见底），
#   真值模式下怪血是 Monster.xml 真数（11228），低等级号可能要打几十回合，
#   不能再用 20 —— 20 回合就判「力竭」等于把「打得慢」误判成「打不过」。
MAX_ROUNDS_TRUE = max(5, int(os.environ.get("BATTLE_MAX_ROUNDS", "60")))

# ---- 一组多只（真服 2026-11-27 17:40 抓包）----
# 151 数量字节=03，units 7/8/9：主怪 = 900001(11228 血)，
# 两只「侍从」血 = ceil(11228/3)=3743（= Monster.xml 的 90000101.hp_max），
# 151 里它们的 4 字节不是怪物 id，而是**实例 oid**（ce055d4ae5 / ce055d4b49，
# 113 刷怪包里根本没有这两个 id，是战斗包自己带的分身 oid）。
MOB_UNITS = {900001: 3, 900002: 3}
CLONE_OID = {900001: 0x055D4AE5, 900002: 0x055D4B49}
CLONE_OID_DEFAULT = 0x055D4AE5
# 强制所有怪都按 N 只生成（测试用，0=按 MOB_UNITS 表）
MOB_UNITS_FORCE = int(os.environ.get("BATTLE_MOB_UNITS", "0") or 0)
# ★ 2026-11-27 19:2x：出手顺序按敏捷。Monster.xml 没有敏捷字段，
#   怪物敏捷按「同级玩家」（Levelup.xml 的 agile）估算；BATTLE_MOB_AGI=0 关掉，
#   关掉后退回老的「玩家 → 宠物 → 怪」固定顺序。
AGI_MOB_BY_LEVEL = os.environ.get("BATTLE_MOB_AGI", "1") not in ("0", "", "false", "False")
# 怪物敏捷估算：Levelup.xml 的 agile 是**恒定 40**（1~60 级都一样），不能直接拿来用，
# 所以按「基础 40 + 每级 4 点」估：lv1 = 40（与玩家持平，玩家先手），
# lv25 九尾狐 = 136（远高于 5 级玩家面板的 40 → 怪先出手，符合用户预期）。
# 主怪用这个值，侍从取 80%。
AGI_MOB_BASE = float(os.environ.get("BATTLE_MOB_AGI_BASE", "40"))
AGI_MOB_PER_LV = float(os.environ.get("BATTLE_MOB_AGI_PER_LV", "4"))
AGI_MOB_CLONE = float(os.environ.get("BATTLE_MOB_AGI_CLONE", "0.8"))
# 三只一组时「怪打玩家」的伤害分配：主怪 76.6%，两只侍从各 11.7%。
# 真服 148 多回合样本：主怪 -636/-954/-1223…（有随机浮动），侍从恒定 -145；
# 取 -954 那一档＝76.6% / 11.7% / 11.7%。主怪随机浮动到 -636 时就是 64%。
# BATTLE_MOB_HIT_MAIN 可改。
MOB_HIT_MAIN = float(os.environ.get("BATTLE_MOB_HIT_MAIN", "0.766"))

# ★ 2026-11-27 19:2x：单只怪默认也走「按条目构造」（与三只同一套编码），
#   不再用 50B/71B 模板。模板里伤害字段的宽度是写死的（1~3 字节），
#   真实伤害是 d1+u16 可变长，硬塞会顶掉后面的字段；而且模板顺序固定，
#   没法按敏捷把怪排到玩家前面。BATTLE_TPL=1 可回退到模板老路。
TPL_ROUND = os.environ.get("BATTLE_TPL", "0") in ("1", "true", "True", "yes")
# 有宠回合包开关（2026-11-27）：1=按真服 71B 骨架带宠物出手，0=回到无宠 50B 模板。
# 出现异常（画面卡住/闪退）时置 0 即可整段回退，不影响其它逻辑。
PET_ACT = os.environ.get("BATTLE_PET_ACT", "1") not in ("0", "", "false", "False")
# ★ 2026-11-27 17:0x 已判定并删除「BATTLE_PET_HP 强制 151 宠物气血」实验开关：
#   实测把它压到 122 后，怪每回合仍只掉 16（玩家那一下）、玩家仍只挨 13，
#   宠物出手记录照旧 —— 说明 151 宠物段里的气血**不参与伤害/承伤计算**，
#   客户端只是拿它画宠物血条。宠物每回合伤害是客户端按自身数据算的，
#   协议里没有可写的字段，别再往这里加环境变量开关。


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
    """【已废弃，仅保留给离线探针】旧版怪物攻击基准线。

    ★ 2026-11-27 20:0x 起怪物伤害改走 `MOB_TAKE_PCT`（玩家最大生命百分比），
    这条曲线（lv25→34）配上 def/(def+120) 减伤后只剩个位数，正是
    「世界BOSS打我伤害太低」的根因。别再拿它算承伤。
    """
    return 4.0 + 1.2 * float(mob_lv)


# ---- 怪物承伤模型（真服 148 反推，见模块说明）----
# 一组三只合计占玩家最大生命的比例；单只时整份都给它。
MOB_TAKE_PCT = float(os.environ.get("BATTLE_MOB_TAKE_PCT", "0.13"))
# 单次伤害随机浮动（真服 148 里主怪 -636/-954/-1223，侍从恒定 → 只有主怪抖）
MOB_DMG_JITTER = (float(os.environ.get("BATTLE_MOB_JITTER_MIN", "0.88")),
                  float(os.environ.get("BATTLE_MOB_JITTER_MAX", "1.12")))
# monster_type：1~3 是普通/精英，4 起是世界BOSS/首领。真服样本只有 type=4。
MOB_TAKE_TYPE = {1: 0.55, 2: 0.72, 3: 0.85, 4: 1.0, 5: 1.15, 6: 1.3}
# 玩家打怪的伤害倍率：真服 29 面板攻 1385-1503（Lv39 全装备）vs 本地同号 385，
# 光面板就差 3.7 倍，再加上真服还有技能%/暴击等口径差。取 5.5 是为了复现真服
# 那场的节奏：Lv25 世界BOSS ≈ 5 回合打死、自己掉 60% 血（AOE 全中）。
# 不补这一截，「怪血用真值、玩家用低配值」就是十几回合打不死还被磨死的假仗。
ATK_SCALE = float(os.environ.get("BATTLE_ATK_SCALE", "5.5"))


MAX_ROUNDS = 20          # 力竭：20 回合还没分出胜负判负（纯防御磨到这儿结束）
# 防御回合的进度折算（2026-11-24 19:2x 定稿）。
# 以前两个都是 1.0，等于「点防御 = 玩家全力输出」，一直点防御也能赢（用户实测「离谱」）。
# 现在：防御回合**完全不推进击杀进度**（宠物只做动画表演，不结算伤害），
# 玩家受伤减半。所以纯防御永远打不死怪，只会在 MAX_ROUNDS 力竭判负 ——
# 想赢必须出手攻击。宠物挥爪的动画由客户端按 148 自己演，不需要服务端给伤害。
PET_ATK_SHARE = 0.0      # 防御时怪掉血权重（0 = 只表演不掉血）
PET_DMG_FROM_ATTR = int(os.environ.get("BATTLE_PET_ATTR", "1"))
# ★ 2026-11-27 20:4x：PET_DMG=338 是 **Lv12 宠物** 的真服实测值
#   （947 模板第 14 字节 = 0x0c = 12）。基准点写错会成倍放大，
#   真机 20:37 就因为按 Lv1 锚定把宠物伤害推到 784。
PET_DMG_REF_LV = int(os.environ.get("BATTLE_PET_REF_LV", "12"))
PET_DMG_GROWTH = float(os.environ.get("BATTLE_PET_GROWTH", "0.12"))
DEF_TAKE_SHARE = 0.50    # 防御时玩家受伤比例（减伤一半）
DMG_LEVEL_GAIN = 0.14    # 每级输出增益（装备/宠物/境界的综合折算）
GAP_SLOPE = 0.035        # 每级等级压制的伤害差
GAP_MIN, GAP_MAX = 0.55, 1.8

OPEN_TMPL = bytes.fromhex(
    "010c06a6e8908ce696b001004c4c2f2f00a00000000001ce0001b1fd50500000020107")
ROUND_TMPL = bytes.fromhex(
    "000002010107cdeac50100020705000701f000000000070101ce00a986d5"
    "0100020105000101f30000000002010d01070d02")
# ★ 2026-11-27 17:0x：**有宠物在场**的回合包（真服 relay.log 23:39:51 原包，71B，首字节 00）。
#   ⚠️ 别照抄 15:42:25/15:42:53/15:43:09 那三份 56B：它们首字节是 01，
#   而 **01 = 结算位**（END_TMPL 也是 010001…）。抄过去客户端第一回合就
#   当成战斗结束、直接发 149，服务端只能按「未分出胜负」结 150 失败 ——
#   本机实测「打一下就失败」就是这么来的。
#   只有 [0]=00 的这份才是「回合中间」的有宠包，里面那条
#   `04 01 07 ce000f69b5` 就是宠物出手记录（宠物单位 id，同一账号固定）。
ROUND_TMPL_PET = bytes.fromhex(
    "000003010107cd27d90000010102d0b500000000"
    "040107ce000f69b50100020705000701d1fea600000000"
    "070101ce00c859d90100020105000101f700000000"
    "02010d01070d02")
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
PET_SEG_EMPTY = bytes.fromhex("00a000000000")
PET_HP_LV15 = 2612          # 真服样本：Lv15 熊猫血 2612
# ★ 2026-11-27 16:5x：这一段最后那个变长整数**不是攻击力，是形象 id**。
#   真服 151（relay.log 15:42:22）宠物段：
#     01 ac(0xA0|12) e58a9fe5a4abe7868ae78cab(功夫熊猫) 02 cd080a cd080a cd2710 01
#   `cd2710` = 10000 = Pet.xml 的功夫熊猫，与 947 off48 同一个字段。
#   以前写死成 10000（还注释成"攻击力"），所以玩家换了美人鱼形象卡后，
#   地图上/面板是美人鱼，一进战斗又变回功夫熊猫 —— 用户实测「战斗里形象还是旧的」。
PET_SKIN_DEFAULT = 10000
# 战斗里宠物单位的实例 oid（真服 148 每条宠物记录都是 ce000f69b5 = 1010101）
PET_OID = 1010101


def build_pet_seg(pet=None):
    """pet = {"name","level","hp","skin"}；None/空 = 不带宠物上场。"""
    if not pet:
        return PET_SEG_EMPTY
    name = (pet.get("name") or "").encode("utf-8")
    if not name or len(name) > 15:
        return PET_SEG_EMPTY
    lv = max(1, int(pet.get("level") or 1))
    hp = int(pet.get("hp") or 0) or int(round(PET_HP_LV15 * lv / 15.0))
    try:
        skin = int(pet.get("skin") or PET_SKIN_DEFAULT)
    except (TypeError, ValueError):
        skin = PET_SKIN_DEFAULT
    return (b"\x01" + bytes([0xA0 | len(name)]) + name
            + bytes([lv & 0xFF])
            + enc_val(hp) + enc_val(hp) + enc_val(skin))


def build_open(mob_id, hp=None, player_hp=None, player_mp=None, cls=1,
               pet=None, name=None, sex=1, units=None):
    """151 开战包（结构对齐真服 39B 样本）。

    [12:14] 玩家当前/最大血、[14:16] 当前/最大蓝、[22] 怪物数、
    [24:28] 怪物 ID、[28:..] 怪物当前/最大血 都是变长整数。

    ★ units = [(uid, oid, hp), ...]，多只一组时按真服 89B 结构逐只写：
        [数量][每只: ce+oid4 + 当前血 + 最大血][00 00 数量(含玩家) 各 unit id]
      真服 九尾狐 89B：
        ... 03 ce000dbba1 cd2bdc cd2bdc  ce055d4ae5 cd0e9f cd0e9f  ce055d4ae5 cd0e9f cd0e9f
            00 00 04 07 01 09 08
      主怪 oid = 怪物 id，侍从 oid 是战斗包自带的实例号（0x055D4AE5）。

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
    if units:
        seg = bytearray(bytes([len(units) & 0xFF]))
        for _uid, _oid, _hpv in units:
            _hpv = max(1, int(_hpv))
            seg += b"\xce" + int(_oid).to_bytes(4, "big")
            seg += enc_val(_hpv) + enc_val(_hpv)
        _ids = [int(u[0]) for u in units]
        if len(units) == 1:
            tail = b"\x00\x00\x02" + bytes([1, 7])
        else:
            # 真服顺序：7 1 9 8（主怪、玩家、第三只、第二只）
            _order = [7, 1] + _ids[:0:-1] if len(_ids) >= 3 else [1] + _ids
            tail = b"\x00\x00" + bytes([1 + len(_ids)]) + bytes(_order)
        return (bytes(head)
                + enc_val(player_hp) + enc_val(player_hp)
                + enc_val(player_mp) + enc_val(player_mp)
                + build_pet_seg(pet)
                + bytes(seg) + tail)
    return (bytes(head)
            + enc_val(player_hp) + enc_val(player_hp)
            + enc_val(player_mp) + enc_val(player_mp)
            + build_pet_seg(pet)
            + b"\x01"      # 怪物数量（单只）
            + b"\xce" + mid.to_bytes(4, "big")
            + enc_val(hp) + enc_val(hp)
            + OPEN_TMPL[30:])


# 148 里的伤害字段（**按回合重写，不再抄模板**）
#   ROUND_TMPL_PET(71B) 三个伤害字段的固定偏移：
#     [14:16] 玩家打怪 d0b5 = -75
#     [36:39] 宠物打怪 d1fea6 = -346
#     [59:60] 怪打玩家 f7 = -9
#   ROUND_TMPL(50B) 无宠版是 f0 = -16 / f3 = -13。
# 真服 2026-11-27 抓包同构（172B 一场）：
#   `07 01 01 ce055d4ae6 … d1fc46` 怪(7)→玩家(1) -620
#   `01 01 07 cd2775 … d1f3fd` 玩家(1)→怪(7) -3075
#   `04 01 07 ce000f69b5 … d1feae` 宠物(4)→怪(7) -338
#   `09 01 01 ce055d4ae5 … d1ff6f` 分身(9)→玩家(1) -145
# 所以 [攻击者][01][目标][攻击者oid][…][伤害] 是固定形状，伤害值必须按回合算。
PDMG_TMPL = 75           # ROUND_TMPL_PET 里玩家那一下
MDMG_TMPL = 9            # ROUND_TMPL_PET 里怪打玩家那一下


def enc_signed(v):
    """有符号变长整数：d0+1B（-128~127）/ d1+u16 补码（其余）。

    ★ 2026-11-27 19:2x 修正（用户反馈「怪打我怎么还加血」的根因）：
      旧实现是 `n = -int(v)`，而调用方传的是 `enc_signed(-dmg)`，
      两次取负把伤害写成了**正数**：-338 变成 d10152(+338)、-145 变成 d10091，
      客户端把正数当回血 → 怪打玩家反而涨血、怪自己也在回血。
      真服是 d1feae(-338) / d1ff6f(-145) / d0b5(-75)，即标准补码。
    """
    v = int(v)
    # 真服小伤害是**单字节短码**：0xF0~0xFF = -16~-1（148 样本 f7=-9 / f3=-13 / f8=-8 / f0=-16）
    if -16 <= v <= -1:
        return bytes([0xF0 | (16 + v)])
    if -0x80 <= v <= 0x7F:
        return b"\xd0" + (v & 0xFF).to_bytes(1, "big")
    if v < 0:
        return b"\xd1" + (max(0, 0x10000 + v)).to_bytes(2, "big")
    return b"\xd1" + min(v, 0xFFFF).to_bytes(2, "big")


def build_round(action_payload, end=False, cls=None, pet=False, dmg=None):
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
    # 有宠物在场且本回合是「攻击」→ 用真服的有宠 71B 骨架，
    # 客户端才会演宠物挥爪。防御/结束仍用原模板（真服那两种样本没抓到有宠版）。
    _pet_tpl = bool(pet and not end and PET_ACT and act and act[0] == 1)
    if _pet_tpl:
        t = bytearray(ROUND_TMPL_PET)
    if act:
        act = (act + b"\x00" * 5)[:5]
        t[4:9] = act
        # act = [动作][07][CD/CC/CE + 技能号]，第 3 字节是变长前缀
        has_skill = act[2] in (0xCC, 0xCD, 0xCE)
    if cls and not has_skill and act and act[0] == 1:
        sk = int(cls) * 10000 + 101
        if 0 < sk <= 0xFFFF:
            t[6:9] = b"\xcd" + sk.to_bytes(2, "big")
    # ★ 2026-11-27 18:3x：伤害按回合重写（真服就是 d0/d1 负数逐回合变）。
    #   dmg = (玩家打怪, 宠物打怪, 怪打玩家)，单位「点」，这里翻成 -n 写进去。
    #   从后往前替换，前面字段的偏移才不会被长度变化顶掉。
    if dmg and not end:
        p, petd, m = [max(0, int(x)) for x in dmg]
        if _pet_tpl:
            marks = [(59, 60, m), (36, 39, petd), (14, 16, p)]
        else:
            marks = [(38, 39, m), (17, 18, p)]
        out = bytes(t)
        for s, e, v in marks:
            out = out[:s] + enc_signed(-v) + out[e:]
        return out
    return bytes(t)


def _skill3(action_payload, cls=None):
    """148 里要回显的技能号（字节形式），与 build_round 同一口径。"""
    act = bytes(action_payload or b"")
    if act:
        act = (act + b"\x00" * 5)[:5]
        if act[2] in (0xCC, 0xCD, 0xCE):
            return act[2:5]
    if cls:
        sk = int(cls) * 10000 + 101
        if 0 < sk <= 0xFFFF:
            return b"\xcd" + sk.to_bytes(2, "big")
    return b"\xcd\x27\xd9"


def _ent_player(target, skill3, dmg):
    """玩家那一下（真服 23B 形状）：
    `01 01 <目标> <技能3> 01 00 02 <目标> 05 00 <目标> 01 <伤害> 00 00 00 00`"""
    t = bytes([int(target) & 0xFF])
    return (b"\x01\x01" + t + bytes(skill3)
            + b"\x01\x00\x02" + t + b"\x05\x00" + t + b"\x01"
            + enc_signed(-int(dmg)) + b"\x00\x00\x00\x00")


def _ent_pet(target, oid, dmg):
    """宠物那一下：`04 01 <目标> ce+oid4 01 00 02 <目标> 05 00 <目标> 01 <伤害> 00000000`"""
    t = bytes([int(target) & 0xFF])
    return (b"\x04\x01" + t + b"\xce" + int(oid).to_bytes(4, "big")
            + b"\x01\x00\x02" + t + b"\x05\x00" + t + b"\x01"
            + enc_signed(-int(dmg)) + b"\x00\x00\x00\x00")


def _ent_mob(uid, oid, target, dmg):
    """怪打玩家：`<uid> 01 <目标> ce+oid4 01 00 02 <目标> 05 00 <目标> 01 <伤害> 00000000`"""
    t = bytes([int(target) & 0xFF])
    return (bytes([int(uid) & 0xFF]) + b"\x01" + t
            + b"\xce" + int(oid).to_bytes(4, "big")
            + b"\x01\x00\x02" + t + b"\x05\x00" + t + b"\x01"
            + enc_signed(-int(dmg)) + b"\x00\x00\x00\x00")


def build_round_units(action_payload, units, pet_oid, p_hits, pet_dmg, m_dmg,
                      end=False, cls=None, order=None, pet_target=None):
    """一组怪的 148 回合包（按真服 175B 样本的条目录拼）。

    units   = [(uid, oid, hp), ...]（开战时那份）
    p_hits  = {uid: 玩家对这只造成的伤害}
    pet_dmg = 宠物对主怪造成的伤害（0=本回合宠物没出手）
    m_dmg   = {uid: 这只怪对玩家的伤害}；数字表示每只都一样
    order   = [("mob", uid) / ("player",) / ("pet",), ...] 出手顺序（按敏捷排好）
              ★ 2026-11-27 19:2x：真服 148 的条目顺序**就是实际出手顺序**
              （样本里怪 7 的条目排在三只玩家 AOE 条目之前），
              客户端照着逐条演，所以敏捷低的必须排在后面。
    """
    if isinstance(m_dmg, dict):
        m_hits = {int(k): int(v) for k, v in m_dmg.items()}
    else:
        m_hits = {u[0]: int(m_dmg) for u in units}
    skill3 = _skill3(action_payload, cls)
    acts = []
    if order:
        for it in order:
            acts.append((it[0], it[1] if len(it) > 1 else None))
    else:
        # 没给顺序：老的「玩家 → 宠物 → 怪」
        acts = [("player", uid) for uid, _o, _h in units if int(p_hits.get(uid, 0)) > 0]
        if pet_dmg > 0 and units:
            acts.append(("pet", None))
        acts += [("mob", uid) for uid, _o, _h in units if m_hits.get(uid, 0) > 0]

    entries = bytearray()
    n = 0
    # 真服样本里「怪打玩家」条目的 actor oid 都是侍从的实例号（ce055d4ae5），
    # 连主怪 7 也用它，不是 151 里主怪的怪物 id。
    _moid = units[1][1] if len(units) > 1 else units[0][1]
    for kind, uid in acts:
        if kind == "player":
            # ★ 玩家是 AOE：一次出手对每只活着的怪各写一条（真服三只样本如此）。
            #   order 里的 ("player", None) 只是占位，uid 为空表示「对所有目标」。
            _tgt = [uid] if uid is not None else [u[0] for u in units]
            for _u in _tgt:
                d = int(p_hits.get(_u, 0))
                if d > 0:
                    entries += _ent_player(_u, skill3, d)
                    n += 1
        elif kind == "pet":
            if pet_dmg > 0 and units:
                _pt = int(pet_target) if pet_target else units[0][0]
                entries += _ent_pet(_pt, pet_oid, pet_dmg)
                n += 1
        elif kind == "mob":
            d = int(m_hits.get(uid, 0))
            if d > 0:
                entries += _ent_mob(uid, _moid, 1, d)
                n += 1
    body = bytearray()
    body.append(1 if end else 0)
    body.append(0)
    body.append(n & 0xFF)
    body += entries
    # 尾部：[数量(含玩家)] + 每单位 (id, 0d, 状态)
    vals = []
    if end:
        vals.append((1, 0x0D, 0x01))
        for uid, _oid, _hp in units:
            vals.append((uid, 0x0D, 0x00))
    else:
        # 单只（真服 50B/71B 模板尾）：玩家 01 / 怪 02
        # 三只（真服 172B）：玩家 02 / 7→01 / 8→04 / 9→03
        if len(units) <= 1:
            vals.append((1, 0x0D, 0x01))
            for uid, _oid, _hp in units:
                vals.append((uid, 0x0D, 0x02))
        else:
            _mid = {0: 0x02, 1: 0x01, 2: 0x04, 3: 0x03}
            vals.append((1, 0x0D, _mid[0]))
            for i, (uid, _oid, _hp) in enumerate(units):
                vals.append((uid, 0x0D, _mid.get(i + 1, 0x01)))
    body.append(len(vals) & 0xFF)
    for uid, k, v in vals:
        body += bytes([int(uid) & 0xFF, int(k) & 0xFF, int(v) & 0xFF])
    return bytes(body)


def target_from_request(payload):
    """145 请求 payload: ce + u32BE 目标ID -> (mob_id, ok)"""
    if len(payload) >= 5:
        return int.from_bytes(payload[1:5], "big"), True
    return 0, False


def skill_id_of(payload):
    """146 payload `[动作][目标][CC/CD/CE + 技能号]` → 技能 id（取不到返回 0）。"""
    act = bytes(payload or b"")
    if len(act) < 4 or act[2] not in (0xCC, 0xCD, 0xCE):
        return 0
    n = {0xCC: 1, 0xCD: 2, 0xCE: 4}[act[2]]
    raw = act[3:3 + n]
    if len(raw) < n:
        return 0
    return int.from_bytes(raw, "big")


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
                # ★ 2026-11-27 19:2x：出手顺序要用敏捷（Levelup.xml 的 agile 字段）。
                #   Monster.xml 没有敏捷字段，怪物按「同级玩家的敏捷」估算。
                "agile": self._num(body, "agile", 0),
            }

    def _load_skill(self):
        for m in re.finditer(r'<item id="(\d+)"([^>]*?)/>', self._read("Skill.xml")):
            sid, body = m.group(1), m.group(2)
            self.skill[sid] = {
                "name": self._attr(body, "name"),
                "damage_percent": self._num(body, "damage_percent", 1.0) or 1.0,
                # ★ 2026-11-27 19:3x：`target_type=5` 是**群体**技能（九天剑阵 10201、
                #   万剑归宗 10211、流星火雨 40201…）。单体=1/2/4，其它是辅助/封印。
                "target_type": int(self._num(body, "target_type", 1)),
                "class": int(self._num(body, "class", 0)),
                "level": int(self._num(body, "level", 1)),
                "mp": int(self._num(body, "consume_mp", 0)),
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

        # ---- 一只还是多只（真服 151 数量字节）----
        # uid 7 = 主怪（oid = 怪物 id），uid 8/9 = 侍从（oid 是战斗包自带的实例号）。
        self.unit_count = max(1, int(MOB_UNITS_FORCE or MOB_UNITS.get(self.mob_id, 1)))
        self.units = self._mk_units()
        self.mob_hp = {u[0]: u[2] for u in self.units}

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
        # ★ 2026-11-27 22:5x：panel_based 以前是 bool(at)，只要传了 hp_max 也算
        #   「面板口径」，于是攻击回落到 Levelup 裸值却仍乘 ATK_SCALE=5.5，
        #   伤害被凭空放大 5 倍。现在必须真的拿到面板攻击上下限才算面板口径。
        self.panel_based = bool(at.get("attack_min") or at.get("attack_max"))
        self.player_hp_max = max(1, int(hp_base * max(0.05, hp_ratio)))
        self.player_mp_max = max(0, int(at.get("mp_max") or st["mp_max"]))
        self.player_mp = self.player_mp_max

        # ---- 服务端真实数值 ----
        atk_avg = (atk_min + atk_max) / 2.0
        self.crit_mult = 1.0 + min(0.30, crit / 400.0)
        # 面板口径（29 同一套）要乘 ATK_SCALE：真服面板攻约是本地的 3.7 倍
        # （装备/强化/套装全在里面），不补这截就跟 Monster.xml 的真值血量对不上。
        _gain = ATK_SCALE if self.panel_based else (1.0 + DMG_LEVEL_GAIN * self.lv)
        # dmg_mult：GM「伤害」倍率（测试用，只影响服务端折算的回合数，
        # 不动面板 29、不动 151 的血量公式）。倍率越大 → rounds_to_kill 越小。
        self.dmg_mult = max(0.01, float(dmg_mult or 1.0))
        self.base_deal = max(1.0, atk_avg * _gain * self.crit_mult * self.dmg_mult)
        # ---- 承伤（玩家最大生命百分比，真服 148 反推）----
        # 等级压制用 (怪等级/玩家等级)^2：同级 1.0，Lv3 小怪打 Lv39 只剩 0.5%
        # 量级（挠痒），Lv25 打 Lv39 约 41%。
        # ★ 但 **BOSS（type>=4）不吃等级压制**：真服 Lv39 打 Lv25 九尾狐照样
        #   挨 16.7%/回合，说明首领是按玩家血量走的。
        self.type_mult = MOB_TAKE_TYPE.get(int(self.mob_type or 1), 1.0)
        _ratio = float(self.mob_lv) / max(1.0, float(self.lv))
        self.gap = max(0.05, min(1.50, _ratio * _ratio))
        self.mit = dfn / (dfn + 120.0) if dfn else 0.0    # 仅日志展示，不再参与承伤
        _gap_take = 1.0 if int(self.mob_type or 1) >= 4 else self.gap
        self.raw_take = self.player_hp_max * MOB_TAKE_PCT * self.type_mult * _gap_take
        self.take = max(1.0, self.raw_take)

        # ---- 出手顺序（敏捷）----
        # 玩家敏捷取 29 面板（含装备/妖灵）；怪物没有配置项，按同级玩家估。
        # 用户反馈「我敏捷比他低应该他先出手」—— 148 的条目顺序就是演出顺序，
        # 所以这里算出来的顺序会直接决定客户端先演谁。
        self.agile = int(at.get("agile") or st.get("agile") or 0)
        self.mob_agile = self.agile
        self.unit_agile = {u[0]: self.agile for u in self.units}
        if AGI_MOB_BY_LEVEL:
            self.mob_agile = int(AGI_MOB_BASE + AGI_MOB_PER_LV * max(0, self.mob_lv - 1))
            for i, (uid, _o, _h) in enumerate(self.units):
                self.unit_agile[uid] = (self.mob_agile if i == 0
                                        else max(1, int(self.mob_agile * AGI_MOB_CLONE)))
        self.pet_agile = self.agile
        # 三只一组时怪打玩家的分配（主怪重、侍从轻），合计仍等于 self.take
        self.mob_hit = self._split_take()

        # ---- 回合账 ----
        # ★ 2026-11-27 18:4x：真值模式（默认）。怪血 = Monster.xml 真值，
        #   148 里每回合写真实伤害（d0/d1 负数），血条就按真数一条条掉。
        #   主怪最大，一只组三只时以主怪为准算回合数（侍从血只有 1/3）。
        self.deal = int(round(self.base_deal))
        self.skill_pct = 1.0        # on_action 里按 Skill.xml 的 damage_percent 覆盖
        self.pet_dmg = self._pet_damage()
        if HP_TRUE:
            _per = max(1.0, self.base_deal + self.pet_dmg)
            self.rounds_to_kill = max(1, int(math.ceil(self.cfg_hp / _per)))
        else:
            self.rounds_to_kill = max(1, int(math.ceil(self.cfg_hp / self.base_deal)))
        self.rounds_to_die = max(1, int(math.ceil(self.player_hp_max / self.take)))
        # ★ 2026-11-27 22:5x：真值模式的回合上限放宽到 MAX_ROUNDS_TRUE（默认 60），
        #   折算模式仍是 20（客户端血条 16/回合只能表达 20 回合）。
        _cap = MAX_ROUNDS_TRUE if HP_TRUE else MAX_ROUNDS
        self.fight_rounds = max(1, min(self.rounds_to_kill, _cap))
        # ★ can_win 以前写 rounds_to_die >= fight_rounds：需 100 回合、能扛 70 回合、
        #   上限 60 时也算「可胜」，实际只能力竭。赢的前提必须是「打得完」。
        self.can_win = (self.rounds_to_kill <= _cap
                        and self.rounds_to_die >= self.rounds_to_kill)

        # ---- 血量账 ----
        self.player_hp_left = self.player_hp_max
        self.target = self.units[0][0]   # 当前目标（146 里的目标 uid）
        self.atk_rounds = 0         # 我方出手次数（= 对怪造成伤害的次数）
        self.hit_rounds = 0         # 我方挨打次数

        self.round = 0
        self.over = False
        self.result = None          # "win" / "lose" / "draw"
        self.detail = []            # 每回合明细，便于复盘

    def _pet_damage(self):
        """宠物每回合伤害。

        ★ 2026-11-27 20:4x：以前固定 PET_DMG=338，所有宠物一律 338。
          真服 148 样本里同一只宠物打出 -338 / -346 / -642 不等。
          **关键校正**：947 模板里那只宠物是 Lv12，而 -338 正是它打出来的，
          所以 338 对应的是 **Lv12**，不是 Lv1。上一版按 Lv1 锚定，
          Lv12 被放大成 784（真机 20:37 玄蜂实测：主怪每回合多吃 446）。
          现在以 Lv12 为基准、每级 ±12%：Lv12=338（与真服一致），
          Lv18≈581、Lv20≈662（贴近真服那条 -642）。
          BATTLE_PET_ATTR=0 可退回纯固定值。
        """
        if not self.pet:
            return 0
        if not PET_DMG_FROM_ATTR:
            return PET_DMG
        _plv = 1
        try:
            if isinstance(self.pet, dict):
                _plv = int(self.pet.get("level") or self.pet.get("lv") or 1)
        except Exception:
            _plv = 1
        _plv = max(1, _plv)
        _v = PET_DMG * max(0.35, 1.0 + PET_DMG_GROWTH * (_plv - PET_DMG_REF_LV))
        return max(1, int(round(_v)))

    def _split_take(self, mult=1.0):
        """每只怪这回合打玩家多少：主怪 MOB_HIT_MAIN(76.6%)，侍从平分其余
        （真服 175B 样本：主怪 509 + 侍从 145 + 145，合计约等于 take）。"""
        n = len(self.units)
        if n <= 1:
            return {self.units[0][0]: max(0, int(round(self.take * mult)))}
        main = max(1.0, self.take * MOB_HIT_MAIN) * mult
        each = max(1.0, self.take * max(0.0, 1.0 - MOB_HIT_MAIN) / (n - 1)) * mult
        out = {}
        for i, (uid, _o, _h) in enumerate(self.units):
            out[uid] = max(0, int(round(main if i == 0 else each)))
        return out

    def turn_order(self, only_alive=True):
        """按敏捷从高到低排出手顺序：[(kind, uid), ...]。

        同敏捷时按 玩家(0) → 宠物(1) → 主怪(2+) 的老顺序，避免数值相等时抖动。
        返回的顺序会被 build_round_units 直接当成 148 的条目顺序（= 客户端演出顺序）。
        """
        acts = []
        for i, (uid, _o, _h) in enumerate(self.units):
            if only_alive and self.mob_hp.get(uid, 0) <= 0:
                continue
            acts.append((self.unit_agile.get(uid, self.mob_agile), 2 + i, "mob", uid))
        if self.player_hp_left > 0:
            acts.append((self.agile, 0, "player", None))
            if self.pet:
                acts.append((self.pet_agile, 1, "pet", None))
        acts.sort(key=lambda x: (-int(x[0]), int(x[1])))
        return [(a[2], a[3]) for a in acts]

    def _mk_units(self):
        """(uid, oid, hp_max) 列表。uid 7=主怪，8/9=侍从（血 = 主怪/3，真服同）。"""
        n = max(1, int(self.unit_count))
        if n <= 1:
            return [(7, self.mob_id, self.cfg_hp)]
        _oid = CLONE_OID.get(self.mob_id, CLONE_OID_DEFAULT)
        _chp = int(math.ceil(self.cfg_hp / float(n)))
        out = [(7, self.mob_id, self.cfg_hp)]
        for i in range(2, n + 1):
            out.append((6 + i, _oid, _chp))
        return out

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
            # ★ 2026-11-27 18:3x：真值模式（默认）。真服 151 就是 Monster.xml 的
            #   hp_max（900001=11228 / 900002=13280 / 900003=18896），客户端血条按
            #   148 里 d0/d1 的伤害一条条掉，两边一致。
            return max(1, int(self.cfg_hp))
        # 折算模式（BATTLE_HP_TRUE=0 回退用）：怪物血 = 每回合真实伤害 × 回合数。
        # ★ 这里以前写 CLIENT_PDMG(16)，但**有宠模板 148 里玩家那一下是 -75**
        #   （d0b5），宠物 -346，合计 421/回合。用 16+346=362 去折算，
        #   血条每回合掉 421 却只有 362 的总量 → 第 17 回合就见底，
        #   而服务端还要打到第 20 回合（"血条空了怪还活着"）。
        per = (PDMG_TMPL + (PET_DMG if self.pet else 0))
        return max(1, int(per * self.fight_rounds))

    @property
    def hp(self):
        """剩余血量：真值模式取各只剩余之和；折算模式按回合进度等比缩放。"""
        if HP_TRUE:
            return int(sum(max(0, v) for v in self.mob_hp.values()))
        left = max(0.0, self.fight_rounds - self.atk_rounds)
        return int(round(self.max_hp * left / float(self.fight_rounds)))

    @property
    def disp_player_hp_max(self):
        """151 里的玩家血量：真值模式发面板血；折算模式按 CLIENT_MDMG×可扛回合。
        """
        if HP_TRUE:
            return max(1, int(self.player_hp_max))
        return max(1, int(CLIENT_MDMG * self.rounds_to_die))

    @property
    def disp_player_hp(self):
        """显示用剩余血量。"""
        if HP_TRUE:
            return max(0, int(self.player_hp_left))
        left = max(0, self.rounds_to_die - self.hit_rounds)
        if self.rounds_to_die <= 0:
            return 0
        return int(round(self.disp_player_hp_max * left / float(self.rounds_to_die)))

    # ---------- 对外 ----------
    def open_packet(self):
        if len(self.units) > 1:
            return build_open(self.mob_id, self.max_hp,
                              self.disp_player_hp_max, self.player_mp_max,
                              cls=self.cls, pet=self.pet,
                              name=self.player_name, sex=self.sex,
                              units=[(u[0], u[1], self.mob_hp[u[0]]) for u in self.units])
        return build_open(self.mob_id, self.max_hp,
                          self.disp_player_hp_max, self.player_mp_max,
                          cls=self.cls, pet=self.pet,
                          name=self.player_name, sex=self.sex)

    def on_action(self, payload):
        """处理一次 146，返回 (148 bytes, is_end, result)。

        ★ 2026-11-27 22:5x：改成 **按敏捷逐行动者结算**（ChatGPT 指出的 P0 问题：
          旧版先把玩家/宠物伤害全部结算，再补算怪物，敏捷高的怪先手打死玩家时
          仍会被玩家「先扣的血」反杀，出现死后反杀）。
          现在按 turn_order() 排序逐个出手，每次出手前先确认行动者还活着、
          目标还活着；一旦玩家死亡立即中断，后面的条目不再产生，
          所以 148 的条目顺序与实际结算完全一致。

        每次 146 都是一回合：普通攻击每回合的 payload 字节完全相同（01 07 cd27d9），
        所以不能用 payload 去重，只能按调用次数推进。
        """
        if self.over:
            if len(self.units) == 1 and TPL_ROUND:
                b = build_round(payload, end=True, cls=self.cls, pet=bool(self.pet))
            else:
                b = build_round_units(payload, self.units, PET_OID, {}, 0, {},
                                      end=True, cls=self.cls)
            return b, True, self.result

        self.round += 1
        action = payload[0] if payload else 1
        p_hits, pet_d = {}, 0
        mob_hits = {}

        # 目标：146 = [动作][目标uid][技能…]。目标已死/没带目标就跟第一只活着的。
        _tgt = int(payload[1]) if len(payload) > 1 else 0
        if _tgt not in self.mob_hp or self.mob_hp.get(_tgt, 0) <= 0:
            _tgt = next((u[0] for u in self.units if self.mob_hp.get(u[0], 0) > 0), 0)
        self.target = _tgt

        # ---- 本回合要做什么（不结算，只记参数）----
        aoe = False
        if action == 1:
            # ★ 单体/群体按 Skill.xml 的 target_type=5 判断（九天剑阵 10201 是群攻）。
            self.skill_id = skill_id_of(payload)
            _sk = self.tables.skill.get(str(self.skill_id)) or {}
            aoe = (_sk.get("target_type") == 5)
            self.aoe = aoe
            # 技能倍率真正接进伤害（以前只识别单体/群攻，伤害仍是基础值）
            self.skill_pct = max(0.05, float(_sk.get("damage_percent") or 1.0))
        _hit_tab = self._split_take(DEF_TAKE_SHARE if action == 4 else 1.0)

        # ---- 按敏捷逐个结算 ----
        order = self.turn_order()
        done = []
        for kind, uid in order:
            if self.over:
                break
            if kind == "player":
                if self.player_hp_left <= 0 or action != 1:
                    continue
                _targets = ([u[0] for u in self.units if self.mob_hp.get(u[0], 0) > 0]
                            if aoe else ([self.target] if self.target else []))
                for _u in _targets:
                    left = max(0, self.mob_hp.get(_u, 0))
                    if left <= 0:
                        continue
                    d = min(max(1, int(round(self.deal * self.skill_pct))), left)
                    p_hits[_u] = d
                    self.mob_hp[_u] = left - d
                self.atk_rounds += 1
                done.append(("player", None))
                if all(v <= 0 for v in self.mob_hp.values()):
                    self.over, self.result = True, "win"
                    break
            elif kind == "pet":
                if action != 1 or not self.pet or self.player_hp_left <= 0:
                    continue
                _pt = self.target or (self.units[0][0] if self.units else 0)
                left2 = max(0, self.mob_hp.get(_pt, 0))
                if _pt and left2 > 0:
                    pet_d = min(self.pet_dmg, left2)
                    self.mob_hp[_pt] = left2 - pet_d
                    done.append(("pet", None))
                if all(v <= 0 for v in self.mob_hp.values()):
                    self.over, self.result = True, "win"
                    break
            else:  # mob
                if self.player_hp_left <= 0 or self.mob_hp.get(uid, 0) <= 0:
                    continue
                _v = random.uniform(*MOB_DMG_JITTER)
                d = min(int(round(_hit_tab.get(uid, 0) * _v)), self.player_hp_left)
                if d <= 0:
                    continue
                mob_hits[uid] = d
                self.player_hp_left -= d
                done.append(("mob", uid))
                if self.player_hp_left <= 0:
                    self.over, self.result = True, "lose"
                    break

        if action == 1:
            self.hit_rounds += 1
        elif action == 4:
            # 防御：玩家不出手，受伤减半；宠物只做表演不结算（PET_ATK_SHARE=0）。
            if self.pet:
                self.atk_rounds += PET_ATK_SHARE
            self.hit_rounds += DEF_TAKE_SHARE
        m_d = sum(mob_hits.values())
        self.detail.append((self.round, sum(p_hits.values()) + pet_d, m_d))

        # ---- 判定 ----
        if not self.over:
            if all(v <= 0 for v in self.mob_hp.values()):
                self.over, self.result = True, "win"
            elif self.player_hp_left <= 0:
                self.over, self.result = True, "lose"
            elif self.round >= (MAX_ROUNDS_TRUE if HP_TRUE else MAX_ROUNDS):
                self.over, self.result = True, "draw"

        # ★ 已有胜者时（例如玩家先手把怪清完），148 只写实际发生的条目，
        #   不再补「怪打玩家」的死后条目。done 就是真实出手序列。
        if len(self.units) == 1 and TPL_ROUND:
            _p = p_hits.get(self.units[0][0], 0)
            body = build_round(payload, end=self.over, cls=self.cls,
                               pet=bool(self.pet), dmg=(_p, pet_d, m_d))
        else:
            body = build_round_units(payload, self.units, PET_OID,
                                     p_hits, pet_d, mob_hits,
                                     end=self.over, cls=self.cls, order=done,
                                     pet_target=self.target)
        return body, self.over, self.result

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
