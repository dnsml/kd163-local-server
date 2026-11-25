#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd163 自建服务端 v3（不再依赖真服）
- 9000 端口同端口双协议，按首字节分流：
    0x16 → TLS：用 patch/srv.pem 出示证书，本地应答 /auth（任何账号都发令牌）
    其它 → 明文游戏协议：按 replay_sc.json 回放
- 客户端已被改包（信任根换成 srv.pem），所以 TLS 校验能过

用法：python local_server3.py [port] [cert] [key]
"""
import os
import sys
import json
import time
import base64
import random
import socket
import struct
import threading
import copy

import battle
import bag_proto      # 背包编解码（178/181 包体）
import grant          # 28 包字段级读写（货币在 idx18~21）
import item_rules     # 按 Item.xml 描述推断道具效果 / 商城单价
import wing_proto     # 翅膀形象卡（1922/1931/1932 三个穿戴结果包）
import equip_attr     # Equip.xml/StrengthenAddOn/Suite/Formula/Mall 全配置驱动
import pet_proto      # 宠物 / 妖灵（961/962、946/947、1410~1413）
import player as player_mod   # 每个连接一份角色状态（threading.local）
from player import cur, new as new_player, ctx as player_ctx

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(HERE, "replay_sc.json")
TEMPLATES = os.path.join(HERE, "templates.json")
LOGFILE = os.path.join(HERE, "logs", "server.log")
os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
CERT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "patch", "srv.pem")
KEY = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "patch", "srv.key")

# 通用提示（cmd=1）payload = [u16BE 模板id][UTF-8 文本]，真服抓包实测格式（模板 1211）
NOTICE_TEXT = "该功能暂未开放"
NOTICE = struct.pack(">H", 1211) + NOTICE_TEXT.encode("utf-8")

lock = threading.Lock()


def log(msg):
    with lock:
        line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        try:
            with open(LOGFILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def load_seq():
    if not os.path.exists(CAP):
        return []
    raw = json.load(open(CAP, encoding="utf-8"))
    return [(c, s, bytes.fromhex(h)) for c, s, h in raw]


SEQ = load_seq()
TPL = {}
TPL_PUSH = {}

# 42 的响应（真服原包）：818 完整装备 850B + 43 角色详情 345B。
# 缺这对包时客户端拿不到完整角色数据，装备面板转圈、宠物界面空。
ROLE_FULL = {}
_rf_path = os.path.join("res_cfg", "role_full.json")
if os.path.exists(_rf_path):
    try:
        ROLE_FULL = json.load(open(_rf_path, encoding="utf-8"))
    except Exception:
        ROLE_FULL = {}
if os.path.exists(TEMPLATES):
    raw = json.load(open(TEMPLATES, encoding="utf-8"))
    for k, v in raw.items():
        if k.startswith("push_"):
            # 真服对某些请求不以 cmd+1/seq=N 回复，而是用 seq=0 的「推送」形式。
            # 例如请求 177 → 推送 178、529 → 530、536 → 538。
            TPL_PUSH[int(k[5:])] = bytes.fromhex(v)
        else:
            TPL[int(k)] = bytes.fromhex(v)

ACCOUNTS = {}          # username -> account_id
next_id = [13]

# 任务链：Quest.xml 的 next_quest 映射（交完任务后推下一环状态，真服 15:37:08 实证）
QUEST_NEXT = {}
# task 类型（首数字）：4=对话任务（211 应答尾 0101，接了即可交），其余=0001
# 真服 20:52 实证：400803/400901(对话) -> 0101；400902(采集)/400101(打怪) -> 0001
QUEST_TASK = {}
# 任务奖励：reward="add_exp,1300;add_coin_bound,1000" -> (exp, coin)
QUEST_REWARD = {}
# 任务奖励物品：add_item,物品id,绑定,数量 -> [(iid, qty), ...]
# ★ 2026-11-24 16:5x：以前只解析 add_exp / add_coin_bound，54 条带物品的任务
# （欧冶子那条强化石就是）交完什么都不给，用户反馈「强化石没到背包」。
QUEST_ITEMS = {}
try:
    import re as _re
    _qx = os.path.join(HERE, "res_cfg", "Quest.xml")
    if os.path.exists(_qx):
        _qtxt = open(_qx, encoding="utf-8").read()
        for _m in _re.finditer(r'<item id="(\d+)"([^>]*?)/>', _qtxt):
            _qid, _body = int(_m.group(1)), _m.group(2)
            _nq = _re.search(r'next_quest="(\d+)"', _body)
            if _nq:
                QUEST_NEXT[_qid] = int(_nq.group(1))
            _tk = _re.search(r'\btask="(\d+)', _body)
            if _tk:
                QUEST_TASK[_qid] = int(_tk.group(1))
            _rw = _re.search(r'reward="([^"]*)"', _body)
            if _rw:
                _exp = _coin = 0
                _items = []
                for _p in _rw.group(1).split(";"):
                    _kv = _p.split(",")
                    if len(_kv) >= 2:
                        if _kv[0] == "add_exp":
                            _exp = int(_kv[1])
                        elif _kv[0] == "add_coin_bound":
                            _coin = int(_kv[1])
                        elif _kv[0] == "add_item" and len(_kv) >= 4:
                            try:
                                _items.append((int(_kv[1]), int(_kv[3])))
                            except ValueError:
                                pass
                if _exp or _coin:
                    QUEST_REWARD[_qid] = (_exp, _coin)
                if _items:
                    QUEST_ITEMS[_qid] = _items
    print("[quest] 任务链 %d / 类型 %d / 奖励 %d 条 / 带物品 %d 条"
          % (len(QUEST_NEXT), len(QUEST_TASK), len(QUEST_REWARD),
             len(QUEST_ITEMS)))
except Exception as _e:
    print("[quest] Quest.xml 解析失败:", _e)

# ★ PetEquip.xml 里的 id 是「妖灵」（1101 血·共工 / 2101 命·魍魉 / 4101 攻·白光虎 …）。
# 任务 reward 里写成 add_item 时，**不能进普通背包**，要进 spirit_bag。
# 2026-11-24 16:5x 用户反馈「血·共工那些妖灵没有」，就是被当成物品塞背包了。
PET_EQUIP_IDS = set()
try:
    _px = os.path.join(HERE, "res_cfg", "PetEquip.xml")
    if os.path.exists(_px):
        PET_EQUIP_IDS = {
            int(_m) for _m in __import__("re").findall(
                r'<item[^>]*\bid="(\d+)"', open(_px, encoding="utf-8").read())}
        print("[spirit] 妖灵(PetEquip) %d 只" % len(PET_EQUIP_IDS))
except Exception as _e:
    print("[spirit] PetEquip.xml 解析失败:", _e)


# ---------------- 场景表 / 跳点表 ----------------
# 服务端只做「跳点 ID -> 目标场景」的映射，坐标/对象都来自客户端自带配置：
#   Scene.xml     : <item id="102" name="天音谷" jump_points="1002,3,7;1003,60,6;10201,53,8"/>
#   Jumppoint.xml : <item id="1002" name="蓬莱仙境" scene="101"/>
SCENES = {}      # scene_id -> {name,width,height,jump_points...}
SCENE_JUMP = {}  # scene_id -> [(jump_id, x, y), ...]
JUMPS = {}       # jump_id  -> target scene_id
JUMP_CLEAR = {}  # jump_id  -> clear_needed（要清掉几只本层怪才给走）
try:
    import re as _re2
    _sx = os.path.join(HERE, "res_cfg", "Scene.xml")
    if os.path.exists(_sx):
        for _m in _re2.finditer(r'<item ([^>]*?)/>', open(_sx, encoding="utf-8").read()):
            _d = dict(_re2.findall(r'(\w+)="([^"]*)"', _m.group(1)))
            if "id" in _d:
                SCENES[int(_d["id"])] = _d
    for _sid, _d in SCENES.items():
        _lst = []
        for _p in (_d.get("jump_points") or "").split(";"):
            _f = _p.split(",")
            if len(_f) >= 3:
                try:
                    _lst.append((int(_f[0]), int(_f[1]), int(_f[2])))
                except ValueError:
                    pass
        SCENE_JUMP[_sid] = _lst
    _jx = os.path.join(HERE, "res_cfg", "Jumppoint.xml")
    if os.path.exists(_jx):
        for _m in _re2.finditer(r'<item ([^>]*?)/>', open(_jx, encoding="utf-8").read()):
            _d = dict(_re2.findall(r'(\w+)="([^"]*)"', _m.group(1)))
            if "id" in _d and "scene" in _d:
                JUMPS[int(_d["id"])] = int(_d["scene"])
                try:
                    JUMP_CLEAR[int(_d["id"])] = int(_d.get("clear_needed") or 0)
                except ValueError:
                    JUMP_CLEAR[int(_d["id"])] = 0
    print("[scene] 场景 %d / 跳点 %d" % (len(SCENES), len(JUMPS)))
except Exception as _e:
    print("[scene] 场景表解析失败:", _e)

DEFAULT_SCENE = 101   # 蓬莱仙境（回放里登录快照 65/101、113 也是 101）

# ---- 技能解锁表（SkillLearn.xml）----
# 表里一个技能有很多「等级行」：10101~10110 都是「仙心弈剑」，10201~ 都是「九天剑阵」。
# 306 下发的是**技能槽**，每个系列只取最低那一档，且要满足 min_level <= 当前等级。
# 真服 19:20~19:21 实证（同一次登录内从 1 个变 2 个）：
#   Lv10 御剑 -> 01 10101          （仙心弈剑 min_level=10）
#   Lv12 御剑 -> 02 10101 10201    （九天剑阵 min_level=12）
#   未转职   -> 02 60101 60201     （class 6 两个系列都是 min_level=1）
SKILL_SERIES = {}     # (class, 系列号) -> (最小技能id, min_level, 名字, skill_tape)
try:
    import re as _re4
    _slx = os.path.join(HERE, "res_cfg", "SkillLearn.xml")
    if os.path.exists(_slx):
        for _m in _re4.finditer(r'<item ([^>]*?)/>', open(_slx, encoding="utf-8").read()):
            _d = dict(_re4.findall(r'(\w+)="([^"]*)"', _m.group(1)))
            if "id" not in _d or "class" not in _d:
                continue
            _iid, _cls = int(_d["id"]), int(_d["class"])
            _lv = int(_d.get("min_level") or 1)
            _tape = int(_d.get("skill_tape") or 1)
            _key = (_cls, _iid // 100)
            _old = SKILL_SERIES.get(_key)
            if _old is None or _iid < _old[0]:
                SKILL_SERIES[_key] = (_iid, _lv, _d.get("name", ""), _tape)
        print("[skill] 技能系列 %d 个" % len(SKILL_SERIES))
except Exception as _e4:
    print("[skill] 技能表解析失败:", _e4)


# skill_tape 语义（SkillLearn.xml 实证，御剑为例）：
#   1 = 普攻（10101 仙心弈剑 min_level 10）
#   2 = 主动（10201 九天剑阵 12）
#   3 = 主动（10301 眩晕剑气 30）
#   4 = 大招（10401 破天一剑 40，consume_mp 1200，需要技能点/书单独学）
#   5 = 被动（11001 通经活络 等，永久加属性，不进快捷栏）
# 以前按 min_level 把所有系列都塞进 306，结果 43 级号一次下发 7 个技能：
# 破天一剑没学过却能点（还因为 1200 蓝 > 854 弹「法力值不足」），
# 被动技能也占了快捷栏。真服 306 实测只有 1~2 条，所以 4/5 一律不下发。
NO_BAR_TAPE = (4, 5)


def skills_for(cls, level):
    """按职业+等级算出技能栏（306）里该有几个技能。"""
    cls, level = int(cls), int(level)
    if not (1 <= cls <= 5):
        cls = 6                       # 未转职走 class 6（回风流雪 / 混元气刃）
    out = []
    for (c, _series), (sid, need, _name, tape) in sorted(SKILL_SERIES.items()):
        if c == cls and level >= need and tape not in NO_BAR_TAPE:
            out.append(sid)
    return out


# 113 = 场景对象列表，真服实测结构（15:40/15:41/21:48 三份样本逐字节对齐）：
#   [u8/0xCD+u16 场景ID][u8 刷怪点数]{CE u32 刷怪点ID x y 00}[u8 采集点数]{CE u32 采集点ID x y}
# 场景 101 蓬莱仙境：3 个普通刷怪点 + 1 个采集点，**没有世界BOSS**
# 场景 102 天音谷  ：九尾狐 900001(21,0) + 玄蜂 900002(42,1) + 奇石 102102 + 蛋 102004
# 场景 1021 天音谷底：3 个刷怪点 + 采集点
# 以前 113 只有一份 25B 的天音谷模板且所有地图都发它，
# 结果蓬莱仙境里也刷出了玄蜂（世界BOSS 位置错乱的根因）。
SCENE_113_REAL = {
    101: bytes.fromhex("6503ce0001b1fd0f0000ce0001b2c51f0100ce0001b32a2f000001ce00018aed330a"),
    102: bytes.fromhex("6602ce000dbba1150000ce000dbba22a010002ce00018ed62f0ace00018e741f08"),
    1021: bytes.fromhex("cd03fd03ce0001d9d60f0000ce0001d9d71f0100ce0001e0dd2f000001ce00018e75180a"),
    # 锁妖塔（紫精魂塔普通 1~3 层）。真服 logs/relay.log 23:43:27 / 23:44:06 / 23:45:00。
    # 结构 = [场景][n=1 刷怪][ce+2120101][19][00][00][n=0 采集]，三层的怪坐标都是 (19,0)。
    # 必须用真服原文：塔层的 jump_point 带 clear_needed="1"，客户端是拿「113 里的怪
    # 与之后收到的 121」对账来决定这一层清没清、下一层传送点亮不亮，坐标/个数不对就不亮。
    21201: bytes.fromhex("cd52d101ce002059a513000000"),
    21202: bytes.fromhex("cd52d201ce00205a0913000000"),
    21203: bytes.fromhex("cd52d301ce00205a6d13000000"),
}

# 锁妖塔：客户端点「进入副本」发 cmd=130，塔 id → 首层场景。
# 真服实证只有一条（2026-11-24 23:43:27 C->S 130 ccdd）：221 = 紫精魂塔一层(普通) 21201。
# 其余塔按 Scene.xml 的连号规律推断（紫精魂塔 21201~21204、神器之魄塔 21205~21208 ...），
# **未在真服验证**，日志里会标 "推断"。若某层进错图，先看这条日志再改表，别改判定逻辑。
TOWER_SCENE = {
    221: 21201,  # 实证：151 系带的塔，普通难度第 1 层
    222: 21202,
    223: 21203,
    224: 21204,
    225: 21205,
    226: 21206,
    227: 21207,
    228: 21208,
    229: 21209,
    230: 21210,
    231: 21211,
    232: 21212,
    233: 21213,
    234: 21214,
    235: 21215,
    236: 21216,
    237: 21217,
    238: 21218,
    239: 21219,
    240: 21220,
}
# 真服 131/659 里除场景 id 外还有两个常量，样本各只出现一次，按原样回放。
TOWER_131_TAIL = bytes.fromhex("0408")
TOWER_659 = bytes.fromhex("ce0003313f01")


def enc_scene(sid):
    """场景 ID 编码：< 0xCD 单字节；否则 0xCD + u16BE（129 的 cd03fd = 1021 实证）。"""
    if 0 <= sid < 0xCD:
        return bytes([sid])
    return b"\xCD" + struct.pack(">H", sid & 0xFFFF)


def dec_scene(buf):
    if not buf:
        return None
    if buf[0] == 0xCD and len(buf) >= 3:
        return int.from_bytes(buf[1:3], "big")
    return buf[0]


def build_113(sid):
    """按场景生成 113 对象表。

    有真服样本的场景直接用逐字节样本（101 / 102 / 1021）。
    其余场景用 Scene.xml 的 spawn_points / gather_point 生成：
    真服 4 份样本反推出的排布规律是「刷怪点在 63 格宽的地图上等距均分，y 交替 0/1」：
        n=3 -> 15,31,47   （101 的 111101/111301/111402、1021 的 121302/121303/123101 实测）
        n=2 -> 21,42      （102 的 900001 九尾狐 / 900002 玄蜂 实测）
    公式对 8 个已知点全部命中，所以珊瑚海/不周山/神剑冢等地图的世界BOSS按同样规则铺开；
    采集点放在 y=10 的采集带（101/102/1021 的采集点都在这条线附近）。
    以前未知地图直接回「空表」，表现就是别的场景一只世界BOSS都不刷。
    """
    real = SCENE_113_REAL.get(sid)
    if real is not None:
        return real
    d = SCENES.get(sid)
    if not d:
        return enc_scene(sid) + b"\x00\x00"

    def _ids(key):
        out = []
        for tok in (d.get(key) or "").split(","):
            tok = tok.strip()
            if tok.isdigit():
                out.append(int(tok))
        return out

    spawns = _ids("spawn_points")
    gathers = _ids("gather_point")
    body = bytearray(enc_scene(sid))
    body.append(len(spawns) & 0xFF)
    n = len(spawns)
    for i, oid in enumerate(spawns):
        body += b"\xCE" + struct.pack(">I", oid) + bytes([63 * (i + 1) // (n + 1), i & 1, 0])
    body.append(len(gathers) & 0xFF)
    m = len(gathers)
    for i, oid in enumerate(gathers):
        body += b"\xCE" + struct.pack(">I", oid) + bytes([63 * (i + 1) // (m + 1), 10])
    return bytes(body)


def kill_notice(mob_id):
    """cmd=121 载荷：`ce + u32BE 对象ID` —— **怪物消失/死亡通知**，不是刷怪。

    真服证据（logs/relay.log）：
      01:23:23 148 致死后紧跟 `S->C cmd=121 len=5 hex=ce000dbba7`（刚打死的 900007），
               玩家随后 01:23:35 才 145 打下一只 900008 —— 所以 121 一定是「这只没了」；
      23:43:59 塔一层 `150` 之后紧跟 `S->C cmd=121 hex=ce002059a5`（=2120101）；
      23:44:54 塔二层同款 `ce00205a09`（=2120201）。
    以前把它当「刷怪」用（GM 刷怪 就发 121），实际是反的：发一次就等于告诉
    客户端「这只已经死了」，模型当场消失。别再按刷怪用。
    """
    return b"\xce" + struct.pack(">I", int(mob_id) & 0xFFFFFFFF)


def arrival_pos(target, prev):
    """129 的 x/y = 目标场景里「回上一个场景」的跳点坐标，再朝地图中心偏 2 格。
    实证：101->102 = 66 05 07（1002 跳点 3,7）；102->101 = 65 3a 06（1001 跳点 60,6）；
          102->1021 = cd03fd 05 07；1021->102 = 66 33 08（10201 跳点 53,8）。"""
    for jid, x, y in SCENE_JUMP.get(target, []):
        if JUMPS.get(jid) == prev:
            try:
                center = int(SCENES.get(target, {}).get("width", 2048)) // 64
            except (TypeError, ValueError):
                center = 32
            return (x + 2 if x < center else x - 2) & 0xFF, y & 0xFF
    return 5, 7


def scene_name(sid):
    return SCENES.get(sid, {}).get("name", "?")


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def bag_view():
    """cur().bag{pos: [id, num]} -> bag_proto 需要的 {pos: {pos, id, num}}"""
    return {p: {"pos": p, "id": v[0], "num": v[1]} for p, v in cur().bag.items()}


def bag_one_desc(v):
    """给日志用：[id, num] -> 「名字×数量」，空槽写「空」。"""
    if not v:
        return "空"
    iid, num = int(v[0]), int(v[1])
    return "%s(%d)×%d" % (bag_proto.item(iid)["name"], iid, num)


def bag_digest(bag):
    """整包一行摘要：槽0 名字×n / 槽1 ...（按槽升序）"""
    if not bag:
        return "空包"
    return " / ".join("%d:%s" % (p, bag_one_desc(bag[p])) for p in sorted(bag))


def build_88():
    """87 -> 88「成就 / 称号列表」。

    真服两档样本（logs/relay.log）：
      新号（一个成就都没有）  19B  `d0 88 00 07` + 14×`00` + `00`（空 varint 占位）
      有成就的号              21B  `d0 88 00 07` + 14×`00` + `cd XXXX`（varint 顶掉那个 00）

    称号表里 `title_id=1019 achievement_name="神界新丁"`，条件
    `task="30,5,1"` = 角色等级达到 5 级。以前不管等级一律发主号那份 21B，
    结果新建的 Lv1 号头顶就挂着「神界新丁」。
    """
    b = bytearray(b"\xd0\x88\x00\x07" + b"\x00" * 14)   # 18B 固定段
    if cur().level >= 5:
        b += bytes.fromhex("cd0b7c")      # 主号抓包值（21B）
    else:
        b += b"\x00"                      # 空 → 19B
    return bytes(b)


def bag_add(iid, n, stack_max=99):
    """入包：同 id 且未满先叠，否则放最小空格。"""
    for p, v in cur().bag.items():
        if v[0] == iid and v[1] + n <= stack_max:
            v[1] += n
            return p
    p = 0
    while p in cur().bag:
        p += 1
    cur().bag[p] = [iid, n]
    return p


def send_push(conn, cid, cmd):
    """下发模板推送（seq=0）。28/178 是动态的，必须由当前状态构造。

    多开后状态是每连接一份：push_all / GM 会在别的线程里遍历连接，
    所以这里先把 threading.local 切到该连接的角色，拼完再恢复。
    """
    pl = CONN_PLAYERS.get(cid)
    if pl is not None and pl is not cur():
        with player_ctx(pl):
            return send_push(conn, cid, cmd)
    if cmd == 28:
        body = build_28()
        conn.sendall(pack(28, 0, body))
        log("c%03d .. push cmd=28     len=%d [动态 Lv%d cls%d exp=%d 绑铜%d 绑元%d 铜钱%d 元宝%d VIP%d/%.1f天]"
            % (cid, len(body), cur().level, cur().cls, cur().exp,
               cur().coin_bound[0], cur().bind_yuanbao[0], cur().tongqian[0], cur().yuanbao[0],
               cur().vip_level[0], vip_days10() / 10.0))
        return
    if cmd == 178:
        body = bag_proto.build_178(bag_view())
        conn.sendall(pack(178, 0, body))
        log("c%03d .. push cmd=178    len=%d [动态背包 %d件]"
            % (cid, len(body), len(cur().bag)))
        # 「背包里的东西忽然变成别的」这类反馈，光看件数查不出来：
        # 件数没变、槽位内容变了才是现场。所以这里记一份快照做差异，
        # 首次推全量清单，之后只打变化行（槽x A → B / → 空）。
        prev = getattr(cur(), "bag_sent", None)
        now = {int(k): list(v) for k, v in cur().bag.items()}
        if prev is None:
            log("c%03d .. 背包快照 %s" % (cid, bag_digest(now)))
        else:
            diff = []
            for p in sorted(set(prev) | set(now)):
                if prev.get(p) != now.get(p):
                    diff.append("槽%d %s → %s"
                                % (p, bag_one_desc(prev.get(p)), bag_one_desc(now.get(p))))
            if diff:
                log("c%03d .. 背包变动 %d 格：%s" % (cid, len(diff), "；".join(diff)))
        cur().bag_sent = now
        return
    if cmd == 818:
        body = build_818(equip_slots())
        conn.sendall(pack(818, 0, body))
        # 头部 hex 打出来：客户端「穿戴后面板还是空的」这类问题，
        # 只靠 len 和装备名对不出来，必须能看到真正的槽位/id 记录。
        log("c%03d .. push cmd=818    len=%d [动态装备 %s] hex=%s"
            % (cid, len(body), sorted(cur().equip.items()) or "空",
               body[:48].hex()))
        return
    if cmd in wing_proto.WING_CMDS:
        body = wing_proto.body(cmd, cur().wing[0])
        if body is None:
            log("c%03d .. 跳过 cmd=%d（当前没装备翅膀，真服也不推这个包）" % (cid, cmd))
            return
        conn.sendall(pack(cmd, 0, body))
        log("c%03d .. push cmd=%-6d len=%d [动态翅膀 %s]"
            % (cid, cmd, len(body),
               ("形象卡%d 外观%d(%s)" % (cur().wing[0], wing_proto.model_of(cur().wing[0]),
                                        wing_proto.wing_name(cur().wing[0])))
               if wing_proto.supported(cur().wing[0]) else "空面板"))
        return
    if cmd in (87, 88):
        # 称号列表必须动态：Lv<5 是 19B（无称号），Lv>=5 才是 21B（带 cd0b7c
        # 「神界新丁」）。以前 send_push 走 TPL_PUSH 的 21B 固定包，
        # 低等级号被推 88 就会凭空多一个称号；而升级跨过 5 级时没人推 88，
        # 客户端称号栏又停在「没有称号」——用户看到的就是「称号消失」。
        body = build_88()
        conn.sendall(pack(88, 0, body))
        log("c%03d .. push cmd=88     len=%d [称号 Lv%d %s]"
            % (cid, len(body), cur().level, "神界新丁" if cur().level >= 5 else "无"))
        return
    if cmd == 29:
        body = build_29()
        if body:
            conn.sendall(pack(29, 0, body))
            log("c%03d .. push cmd=29     len=%d [面板 Lv%d 生命%d 攻%d-%d 防%d]"
                % (cid, len(body), cur().level, power_now(),
                   panel_attr("attack_min"), panel_attr("attack_max"),
                   panel_attr("defense")))
        return
    if cmd == 30:
        if cur().wing[0]:
            # 真服实证：装上翅膀后 cmd=30 从 82B 变 86B（偏移 62 多一个角色对象 ID）。
            body = wing_proto.BODY_30_WING
            tag = "翅膀版 86B"
        elif cur().equip:
            body = build_30()
            tag = "动态装备 %s" % (sorted(cur().equip.items()),)
        else:
            # 真服空装备号（15:37:08 未转职号）交完任务只推 29/28/210，
            # **一次 30 都没有**。空槽写 0 也会被客户端当成 id=0 的外观，
            # 所以这里干脆不发，等真有装备再推。
            body = None
            tag = "空装备，跳过"
        if body:
            conn.sendall(pack(30, 0, body))
            log("c%03d .. push cmd=30     len=%d [%s]" % (cid, len(body), tag))
        else:
            log("c%03d .. 跳过 cmd=30（身上没有装备/翅膀，真服也不推）" % cid)
        return
    if cmd == 306:
        body = build_306()
        conn.sendall(pack(306, 0, body))
        log("c%03d .. push cmd=306    len=%d [动态技能 职业%d]"
            % (cid, len(body), cur().cls))
        return
    if cmd == 1410:
        push_spirit(conn, cid)
        return
    if cmd == 947:
        body = pet_proto.pack_947(spirit_strap())
        if body:
            conn.sendall(pack(947, 0, body))
            log("c%03d .. push cmd=947    len=%d [宠物详情 妖灵%s]"
                % (cid, len(body), pet_proto.strap_key(spirit_strap())))
        return
    ph = TPL_PUSH.get(cmd)
    if not ph:
        log("c%03d .. 无推送模板 cmd=%d" % (cid, cmd))
        return
    conn.sendall(pack(cmd, 0, ph))
    log("c%03d .. push cmd=%-6d len=%d [模板]" % (cid, cmd, len(ph)))


# ---------------------------------------------------------------- GM 通道
# 网页后台（admin_server.py）把指令写进 logs/gm.cmd，这里每秒扫一次执行。
# 走文件而不是 socket：不用给游戏服再开端口、不用重启，后台随时能改数值。
GAME_CONNS = {}                                    # cid -> conn（在线连接）
CONN_PLAYERS = {}                                  # cid -> Player（每个连接一份角色）
# cid -> battle.BattleSession。战斗会话必须挂在这儿：GM（另一个线程）也要能开战，
# 而 146/149 的应答在主循环里读。以前只放主循环局部变量 → GM 推的 151 收不到回合。
BATTLE_SESS = {}
# 测试用伤害倍率（GM「伤害 3」/ 网页后台）。只作用在服务端折算的回合数上：
# base_deal × 倍率 → rounds_to_kill 变小 → 一场战斗 1~2 回合结束。
# 不改面板 29、不改 151 的血量公式，所以不会出现「面板数字和打出来的不符」。
DMG_MULT = float(os.environ.get("KD163_DMG_MULT", "1") or 1)
# 账号 -> (进塔前的场景, x, y)。cmd=132「离开副本」时原路送回；
# 重启/换号丢了就按真服样本回洛水城 (55,5)。
TOWER_BACK = {}
GAME_LOCK = threading.Lock()
# GM 指令文件。测试脚本用 KD163_GM_CMD 指到临时文件，避免和正在跑的服抢同一条指令
# （两个进程盯同一个 gm.cmd 时，谁先扫到谁执行，回执也就落在各自的日志里）。
GM_CMD = os.environ.get("KD163_GM_CMD") or os.path.join(HERE, "logs", "gm.cmd")
STATUS_JSON = os.path.join(HERE, "logs", "status.json")

GM_ALIAS = {
    "元宝": "yuanbao", "绑定元宝": "bind_yuanbao",
    "铜钱": "tongqian", "绑定铜钱": "bind_coin", "绑铜": "bind_coin",
    "阅历": "yueli",
}


def push_all(cmds):
    """把指定推送发给所有在线连接（GM 改完数值后让客户端立刻刷新）。"""
    with GAME_LOCK:
        items = list(GAME_CONNS.items())
    for cid, conn in items:
        for c in cmds:
            try:
                send_push(conn, cid, c)
            except Exception as e:
                log("[gm] 推送 c%03d cmd=%d 失败 %s" % (cid, c, e))
    write_status()


# ---- 妖灵 / 宠物 ----
def spirit_strap():
    """{槽位: 妖灵id}（只取背包里真的有的，避免存档残留脏 id）。"""
    return {s: i for s, i in cur().spirit_slot.items() if i in cur().spirit_bag}


def spirit_bag_view():
    """喂给 pet_proto.pack_1410 的视图：{id: {"idx": 获得次序}}"""
    return {iid: {"idx": int(info.get("idx") or 1)}
            for iid, info in cur().spirit_bag.items()}


def spirit_free_list():
    """未上阵的妖灵（按获得次序）：[(id, idx)]，与 1410 列出来的顺序一致。"""
    eq = set(spirit_strap().values())
    rows = [(iid, int(info.get("idx") or 1))
            for iid, info in cur().spirit_bag.items() if iid not in eq]
    rows.sort(key=lambda kv: kv[1])
    return rows


def spirit_1410():
    return pet_proto.pack_1410(spirit_bag_view(), spirit_strap())


def push_spirit(conn, cid):
    """推 1410（未上阵妖灵列表）。真服每次变更都会推这一包。"""
    body = spirit_1410()
    conn.sendall(pack(1410, 0, body))
    # 带上 hex：真服 1410 每条 7B = [序号-1][varint 妖灵id][部位][01][01]，
    # 部位字节就是「这只妖灵该上哪个槽」。目前只确认 血=1/命=2/攻=3，
    # 防系/闪系还没样本——打这两只的时候靠这行 hex 直接读出来，别再猜。
    log("c%03d .. push cmd=1410   len=%d [背包 %d只 未上阵%d只 上阵%s] hex=%s"
        % (cid, len(body), len(cur().spirit_bag), len(spirit_free_list()),
           spirit_strap() or "无", body.hex()))


def spirit_add(iid):
    """获得一只妖灵。已拥有返回 False。序号 = 已有最大序号 + 1。"""
    iid = int(iid)
    if iid in cur().spirit_bag:
        return False
    idx = max([int(v.get("idx") or 1) for v in cur().spirit_bag.values()] or [0]) + 1
    cur().spirit_bag[iid] = {"idx": idx}
    return True


def spirit_equip(slot, iid):
    """上阵：同槽位旧妖灵被顶掉，同一只妖灵先从别的槽位撤下。

    返回 (被顶掉的旧妖灵, 实际放上去的妖灵)。部位不匹配返回 (旧, None)。
    """
    slot, iid = int(slot), int(iid)
    if iid not in cur().spirit_bag:
        return cur().spirit_slot.get(slot), None
    if pet_proto.nature_of(iid) and pet_proto.nature_of(iid) != slot:
        return cur().spirit_slot.get(slot), None
    for s in [s for s, v in cur().spirit_slot.items() if v == iid]:
        cur().spirit_slot.pop(s, None)
    old = cur().spirit_slot.pop(slot, None)
    cur().spirit_slot[slot] = iid
    return old, iid


def spirit_unequip(slot):
    return cur().spirit_slot.pop(int(slot), None)


def spirit_pick_for_slot(slot):
    """1411 用：该部位第一个未上阵的妖灵（真服 1412 回的就是「将要放上去的那只」）。"""
    slot = int(slot)
    old = cur().spirit_slot.get(slot)
    if old:
        return old
    for iid, _idx in spirit_free_list():
        if pet_proto.nature_of(iid) == slot:
            return iid
    for iid, _idx in spirit_free_list():
        if pet_proto.nature_of(iid) == 0:
            return iid
    return 0


def apply_gm(line):
    """执行一条 GM 指令，返回 (成功?, 说明)。"""
    global DMG_MULT     # 伤害倍率是本进程级开关（角色状态走 cur()，不需要 global）
    line = line.strip()
    # ---- 建角 / 改名 / 看角色（后台「建角」按钮走这三条）----
    # 用法：`建角 小林 女`、`@1234567 建角 小林 男`、`改名 阿浅`
    if line.startswith("建角") or line.startswith("createchar"):
        parts = line.split(None, 2)
        head = parts[0]
        if head in ("建角", "createchar") and len(parts) >= 2:
            nm = parts[1]
            sx = 1
            if len(parts) >= 3:
                sx = 2 if parts[2].strip() in ("女", "2", "female", "f") else 1
            return create_character(nm, sx)
        return False, "用法：建角 <昵称> [男|女]"
    if line.startswith("改名"):
        parts = line.split(None, 1)
        if len(parts) >= 2:
            return rename_character(parts[1])
        return False, "用法：改名 <新昵称>"
    if line in ("看角色", "whochar"):
        return True, ("账号=%s 昵称=%r 性别=%s 已建角=%s Lv%d %s"
                      % (cur().acct or "主号", cur().name,
                         "女" if cur().sex == 2 else "男",
                         "是" if cur().has_char else "否", cur().level, cur().cls))
    # 无参指令先处理
    if line in ("清包", "清空背包", "clearbag"):
        cur().bag.clear()
        save_state()
        push_all([178])
        return True, "背包已清空"
    # 重新下发翅膀三包（1922/1931/1932）。客户端只在登录时自己请求 1921，
    # 穿戴之后想立刻看到效果、或者想验证「有没有翅膀数据」都用这条。
    if line in ("翅膀", "推送翅膀", "wing"):
        push_all([1922, 1931, 1932])
        return True, "已推送翅膀数据（形象卡 %s）" % cur().wing[0]
    # 摘掉翅膀
    if line in ("摘翅膀", "脱翅膀", "unwing"):
        cur().wing[0] = None
        save_state()
        push_all([1922, 1931, 1932])
        return True, "已取下翅膀"
    # 穿上职业神装（400501 物归原主那套）。
    # 各职业套装编号：10000 + 职业*1000 + 108 起共 7 件
    #   御剑 11108~11114 / 逍遥 12108~ / 幻羽 13108~ / 法尊 14108~ / 破军 15108~
    # 本地角色走到 400903「强化神装」时装备栏是空的，锻造界面就提示「没有东西强化」，
    # 用这条指令把整套穿上（真服 400501 交完 30 包里就是这 7 件已穿戴）。
    if line in ("神装", "穿神装", "套装", "godset"):
        cls = cur().cls if 1 <= cur().cls <= 5 else 1
        base = 10000 + cls * 1000 + 108
        for slot in range(1, 8):
            cur().equip[slot] = base + slot - 1
        save_state()
        push_all([17, 28, 30])
        return True, "已穿上职业%d神装 %d~%d" % (cls, base, base + 6)
    # 强化必定成功（测试材料/上限用）；「概率」恢复按等级+石头判定
    if line in ("必成", "强化必成", "luckon"):
        FORGE_LUCK[0] = False
        return True, "强化改为必定成功（测上限用，记得发「概率」改回来）"
    if line in ("概率", "正常概率", "luckoff"):
        FORGE_LUCK[0] = True
        return True, "强化恢复成功率判定"
    if line in ("战力", "power", "看战力", "看面板"):
        return True, ("Lv%d 面板：生命%d 法力%d 攻击%d-%d 防御%d 闪避%d 命中%d "
                      "暴击%d 精神%d 敏捷%d ｜ 强化来源 %s"
                      % (cur().level, panel_attr("hp_max"), panel_attr("mp_max"),
                         panel_attr("attack_min"), panel_attr("attack_max"),
                         panel_attr("defense"), panel_attr("dodge"),
                         panel_attr("hit"), panel_attr("crit"),
                         panel_attr("spirit"), panel_attr("agile"),
                         enh_breakdown()))
    # `引路` —— 替玩家过到「本层下一层」（等价于自己走到跳点发 128）。
    # 塔层跳点坐标是写死在 Scene.xml 里的（紫精魂塔一层 (35,5)、二层 (35,5)），
    # 客户端清怪后才会在跳点位置给引导。这个命令用来直接验证「下一层通不通」，
    # 不用为了找点在地图上来回跑，也不用截图。
    if line in ("引路", "下一层", "next"):
        _cm = int(cur().scene or DEFAULT_SCENE)
        _jid = None
        for _j, _x, _y in SCENE_JUMP.get(_cm, []):
            if JUMP_CLEAR.get(_j, 0) and JUMPS.get(_j):
                _jid = _j
                break
        if _jid is None:
            return False, "当前场景 %s(%d) 没有「要清怪才给走」的跳点" % (scene_name(_cm), _cm)
        _tgt = JUMPS[_jid]
        _pos = arrival_pos(_tgt, _cm)
        cur().scene = _tgt
        cur().pos_x, cur().pos_y = _pos
        body113 = build_113(_tgt)
        with GAME_LOCK:
            items = list(GAME_CONNS.items())
        for cid, conn in items:
            conn.sendall(pack(129, 0, enc_scene(_tgt) + bytes(_pos)))
            conn.sendall(pack(113, 0, body113))
            conn.sendall(pack(117, 0, b"\x00"))
            log("c%03d .. GM 引路 129 [跳点%d %s → %s 落点%s]"
                % (cid, _jid, scene_name(_cm), scene_name(_tgt), bytes(_pos).hex()))
        return True, "已引路 %s → %s" % (scene_name(_cm), scene_name(_tgt))

    # `出塔` —— 等价于客户端点「离开副本」发 132（回进塔前的场景/坐标）。
    # 客户端那个退出按钮不灵时用它兜底。
    if line in ("出塔", "离开副本", "leave"):
        _key = cur().acct or ""
        back = TOWER_BACK.get(_key) or (103, 55, 5)
        bsc, bx, by = int(back[0]), int(back[1]) & 0xFF, int(back[2]) & 0xFF
        if bsc not in SCENES or SCENES[bsc].get("scene_type") == "3":
            bsc, bx, by = 103, 55, 5
        cur().scene = bsc
        cur().pos_x, cur().pos_y = bx, by
        body133 = enc_scene(bsc) + bytes([bx, by])
        with GAME_LOCK:
            items = list(GAME_CONNS.items())
        for cid, conn in items:
            conn.sendall(pack(133, 0, body133))
            conn.sendall(pack(113, 0, build_113(bsc)))
            conn.sendall(pack(117, 0, b"\x00"))
            log("c%03d .. GM 出塔 133 [→ %s(%d) 落点(%d,%d)]"
                % (cid, scene_name(bsc), bsc, bx, by))
        return True, "已送出塔：%s(%d) (%d,%d)" % (scene_name(bsc), bsc, bx, by)

    # ---- 场景怪物（2026-11-25 纠错：121 的语义以前搞反了）----
    #   `消怪 [id]`  推 121 = 这只怪从场景里消失（真服致死/结算后就是这个包）
    #   `刷怪`       重推 113 + 117 = 让客户端按对象表把怪重新摆出来（GM 专用）
    # 之前的 `刷怪` 发的是 121，等于「把怪全判死」，越刷越空；
    # 这也解释了为什么「刷怪」之后点上去还是打不了 —— 怪已经被这条包删掉了。
    if line.startswith("消怪") or line.startswith("despawn"):
        parts = line.split()
        ids = []
        _cm = int(cur().scene or DEFAULT_SCENE)
        if len(parts) > 1 and parts[1].strip().isdigit():
            ids = [int(parts[1].strip())]
        else:
            d = SCENES.get(_cm) or {}
            ids = [int(t) for t in (d.get("spawn_points") or "").split(",")
                   if t.strip().isdigit()]
        if not ids:
            return False, "当前场景 %s(%d) 没有刷怪点" % (scene_name(_cm), _cm)
        with GAME_LOCK:
            items = list(GAME_CONNS.items())
        for cid, conn in items:
            for mid in ids:
                conn.sendall(pack(121, 0, kill_notice(mid)))
                log("c%03d .. GM 消怪 cmd=121 len=5 [%d]" % (cid, mid))
        return True, "已推 121 消怪 %s（%d 个连接）" % (ids, len(items))

    if line.startswith("刷怪") or line.startswith("spawn"):
        _cm = int(cur().scene or DEFAULT_SCENE)
        body = build_113(_cm)
        with GAME_LOCK:
            items = list(GAME_CONNS.items())
        if not items:
            return False, "当前没有在线连接"
        for cid, conn in items:
            conn.sendall(pack(113, 0, body))
            conn.sendall(pack(117, 0, b"\x00"))
            log("c%03d .. GM 刷怪重推 113 len=%d hex=%s" % (cid, len(body), body.hex()))
        return True, "已重推 113/117（%s %d，%d 个连接）" % (scene_name(_cm), _cm, len(items))

    # `打怪 2090101` —— 直接推 151 开战（不依赖客户端寻路/点怪）。
    # 用来验证「多回合战斗时血条是否跟着服务端回合走空」，也方便压测结算。
    if line.startswith("打怪") or line.startswith("fight"):
        parts = line.split()
        if len(parts) < 2 or not parts[1].strip().isdigit():
            return False, "用法：打怪 <怪物id>（如 2090101 / 111402）"
        if BT is None:
            return False, "战斗配置表未加载"
        mob_id = int(parts[1].strip())
        with GAME_LOCK:
            items = list(GAME_CONNS.items())
        if not items:
            return False, "当前没有在线连接"
        n = 0
        for cid, conn in items:
            _ba = {k: panel_attr(k) for k in
                   ("hp_max", "mp_max", "attack_min", "attack_max",
                    "defense", "crit")}
            s = battle.BattleSession(
                mob_id, BT, cur().cls, cur().level,
                pet=pet_proto.active_pet(cur().pets), name=cur().name,
                attrs=_ba, dmg_mult=DMG_MULT)
            s.player_mp_max = max(0, panel_attr("mp_max"))
            s.player_mp = s.player_mp_max
            BATTLE_SESS[cid] = s
            _p = s.open_packet()
            conn.sendall(pack(151, 0, _p))
            log("c%03d .. GM 打怪推 151 [%s] len=%d" % (cid, s.summary(), len(_p)))
            n += 1
        return True, "已开战 mob=%d（%d 个连接）" % (mob_id, n)

    # `发包 120 0100` —— 给所有在线连接直接发一个原包。
    # 协议分析时没有比这更快的探针：改一个字节就能看客户端反应，不用重启、不用抓包。
    # 只给 GM 用，别接进业务流程。
    if line.startswith("发包") or line.startswith("send "):
        parts = line.split()
        if len(parts) < 2 or not parts[1].strip().isdigit():
            return False, "用法：发包 <cmd> [hex]，如 `发包 120 0100`"
        _cmd = int(parts[1].strip())
        try:
            _raw = bytes.fromhex(parts[2]) if len(parts) > 2 else b""
        except ValueError:
            return False, "hex 不合法：%s" % parts[2]
        with GAME_LOCK:
            items = list(GAME_CONNS.items())
        if not items:
            return False, "当前没有在线连接"
        for cid, conn in items:
            conn.sendall(pack(_cmd, 0, _raw))
            log("c%03d .. GM 发包 cmd=%d len=%d hex=%s"
                % (cid, _cmd, len(_raw), _raw.hex()))
        return True, "已发 cmd=%d len=%d 给 %d 个连接" % (_cmd, len(_raw), len(items))

    # ---- 测试用伤害倍率（后台「伤害倍率」按钮走这条）----
    #   伤害           看当前倍率
    #   伤害 3         打怪伤害 ×3 → 一场 1~2 回合
    #   伤害 1         恢复原样
    # 只作用在服务端折算回合数的 base_deal 上；面板 29 与 151 的血量公式都不动，
    # 所以「面板攻击力没变」是正常的——这个倍率本来就是为了让测试打得快。
    if line.startswith("伤害") or line.startswith("dmg"):
        parts = line.split()
        if len(parts) < 2:
            return True, ("当前伤害倍率 ×%.2f（`伤害 3` 设 3 倍，`伤害 1` 恢复）"
                          % DMG_MULT)
        try:
            v = float(parts[1])
        except ValueError:
            return False, "倍率要写数字，如 `伤害 3`"
        if v < 0.01 or v > 1000:
            return False, "倍率范围 0.01 ~ 1000"
        DMG_MULT = v
        return True, "伤害倍率已设为 ×%.2f（下一场战斗生效）" % DMG_MULT

    if line in ("属性", "attrs", "看属性"):
        a = all_attrs()
        tk = panel_now()
        return True, ("装备属性合计 %s ｜ 29包 %s"
                      % ({k: round(v, 2) for k, v in sorted(a.items())}, tk))

    # ---- 妖灵 / 宠物（真服 1410~1413 那条线）----
    #   妖灵              列出身家
    #   妖灵 魍魉         获得（名字全名 / 片段 / id 都行），自动找空槽上阵
    #   妖灵 4101 2       获得并指定槽位 2
    #   妖灵 三只         直接发真服样本那三只：血·共工1 / 命·魍魉2 / 攻·白光虎3
    #   卸妖灵 2          卸下槽位 2
    if line.startswith("卸妖灵") or line.startswith("unequipspirit"):
        arg = line.split(None, 1)
        if len(arg) < 2 or not arg[1].strip().isdigit():
            return False, "用法：卸妖灵 <槽位1-3>"
        old = spirit_unequip(int(arg[1]))
        save_state()
        push_all([947, 1410, 29])
        return True, ("槽%s 卸下 %s" % (arg[1].strip(),
                                       pet_proto.name_of(old) if old else "空"))

    if line.startswith("妖灵") or line.startswith("spirit"):
        arg = line.split(None, 1)
        arg = arg[1].strip() if len(arg) > 1 else ""
        if not arg:
            owned = sorted(cur().spirit_bag.items(),
                           key=lambda kv: int(kv[1].get("idx") or 1))
            return True, ("妖灵 %d只：%s ｜ 上阵 %s ｜ 947模板 %s"
                          % (len(cur().spirit_bag),
                             "、".join("%s(%d)" % (pet_proto.name_of(i), i)
                                      for i, _ in owned) or "无",
                             spirit_strap() or "无",
                             "有" if pet_proto.has_tpl(spirit_strap()) else "无"))
        if arg in ("三只", "三", "all"):
            for s, iid in ((1, 1101), (2, 4101), (3, 2101)):
                if iid not in cur().spirit_bag:
                    spirit_add(iid)
                spirit_equip(s, iid)
            save_state()
            push_all([947, 1410, 29])
            return True, ("已发真服那三只：槽1 血·共工 / 槽2 命·魍魉 / 槽3 攻·白光虎"
                          "（947 会走真服原包 105B）")
        parts = arg.split()
        ids = pet_proto.find(parts[0])
        if not ids:
            return False, "没找到妖灵：%s（试「妖灵 魍魉」或「妖灵 4101」）" % parts[0]
        slot = 0
        if len(parts) > 1 and parts[1].isdigit():
            slot = int(parts[1])
            if not (1 <= slot <= MAX_SPIRIT_SLOT):
                return False, "槽位只能是 1~%d" % MAX_SPIRIT_SLOT
        iid = ids[0]
        if not slot:
            # 优先放它自己的部位；该槽被占或没部位时再找空槽
            nat = pet_proto.nature_of(iid)
            used = set(spirit_strap())
            if nat and nat not in used:
                slot = nat
            else:
                slot = next((s for s in range(1, MAX_SPIRIT_SLOT + 1)
                             if s not in used), 0)
        added = spirit_add(iid)
        if not slot:
            save_state()
            push_all([1410])
            return True, ("获得 %s(%d) id=%d，但三个槽都满了，没上阵"
                          "（用「卸妖灵 N」腾一个）"
                          % (pet_proto.name_of(iid), iid, iid))
        _old, _put = spirit_equip(slot, iid)
        save_state()
        if not _put:
            push_all([1410])
            return False, ("%s(%d) 部位=%d 与槽%d 不匹配，没上阵（已进背包）"
                           % (pet_proto.name_of(iid), iid,
                              pet_proto.nature_of(iid), slot))
        push_all([947, 1410, 29])
        return True, ("%s %s(%d) → 槽%d（947模板 %s）"
                      % ("获得并上阵" if added else "已拥有，重新上阵",
                         pet_proto.name_of(iid), iid, slot,
                         "有" if pet_proto.has_tpl(spirit_strap()) else "无"))
    parts = line.replace("=", " = ").replace("+", " + ").split()
    if len(parts) < 2:
        return False, "指令太短：%s" % line
    key, rest = parts[0], parts[1:]
    op = "="
    if rest[0] in ("=", "+", "-"):
        op, rest = rest[0], rest[1:]
    if not rest:
        return False, "缺少数值：%s" % line
    try:
        val = int(rest[0])
    except ValueError:
        return False, "数值非法：%s" % rest[0]

    if key in ("物品", "item", "发物品"):
        n = int(rest[1]) if len(rest) > 1 else 1
        iid = val
        p = bag_add(iid, n)
        save_state()
        push_all([28, 178])
        return True, "发放 %s x%d 到格%02x" % (bag_proto.item(iid)["name"], n, p)

    if key in ("删除", "del", "清格"):
        if val in cur().bag:
            nm = bag_proto.item(cur().bag[val][0])["name"]
            del cur().bag[val]
            save_state()
            push_all([178])
            return True, "删除 格%02x 的 %s" % (val, nm)
        return False, "格%02x 是空的" % val

    if key in ("等级", "level"):
        cur().level = val if op == "=" else cur().level + val
        save_state()
        push_all([28])
        return True, "等级 -> %d" % cur().level

    if key in ("经验", "exp"):
        cur().exp = val if op == "=" else cur().exp + val
        save_state()
        push_all([28])
        return True, "经验 -> %d" % cur().exp

    # 额外法力加成：`法力 2000` 设加成 2000，`法力 +500` / `法力 -500` 增减。
    # 用于把面板法力抬到能覆盖高级技能消耗（破天一剑 lv10 = 1200）。
    if key in ("法力", "mp", "法力上限", "加蓝"):
        if op == "+":
            cur().mp_add[0] += val
        elif op == "-":
            cur().mp_add[0] = max(0, cur().mp_add[0] - val)
        else:
            cur().mp_add[0] = max(0, val)
        save_state()
        push_all([29, 30])
        return True, ("额外法力 +%d，面板法力 %d（Lv%d 基础+装备）"
                      % (cur().mp_add[0], panel_attr("mp_max"), cur().level))

    # VIP 直改：`vip 184` 开通 184 天（默认等级 4），`vip 30 3` = 30 天/等级 3，`vip 0` 清除。
    # 真服买卡用卡链路已实现（371 购买 / 182 使用），这条只是方便不走背包直接验证 28 字段。
    if key in ("vip", "VIP"):
        if val <= 0:
            cur().vip_level[0], cur().vip_until[0] = 0, 0.0
            msg = "VIP 已清除（idx22=0 idx23=0）"
        else:
            lv = int(rest[1]) if len(rest) > 1 else 4
            lv = max(0, min(4, lv))
            cur().vip_until[0] = time.time() + val * 86400.0
            cur().vip_level[0] = lv or 4
            msg = ("VIP 等级%d 剩余%d天（idx22=%d idx23=%d，到期 %s）"
                   % (cur().vip_level[0], val, cur().vip_level[0], vip_days10(),
                      time.strftime("%Y-%m-%d", time.localtime(cur().vip_until[0]))))
        save_state()
        push_all([28])
        return True, msg

    # 装备栏直改：`装备 11108` 按 Item.xml 的 kind2 自动找槽位并推 818；
    # `脱装备 1` 按槽位卸下。用于不走客户端 182 也能验证装备栏显示。
    if key in ("装备", "穿装备", "equip"):
        it = bag_proto.item(val)
        slot = equip_slot_for(it.get("kind2"))
        if slot is None:
            return False, "%s kind2=%s 没有对应装备槽" % (it["name"], it.get("kind2"))
        cur().equip[slot] = val
        save_state()
        push_all([29, 28, 30, 178])
        return True, "槽%d 穿上 %s(id=%d)" % (slot, it["name"], val)

    if key in ("脱装备", "卸装备", "unequip"):
        if val in cur().equip:
            old = cur().equip.get(val)
            cur().equip.pop(val, None)
            save_state()
            push_all([29, 28, 30, 178])
            return True, "槽%d 卸下 id=%d" % (val, old)
        return False, "槽%d 本来就是空的" % val

    pkey = GM_ALIAS.get(key, key)
    if pkey in PURSES():
        purse = PURSES()[pkey]
        if op == "+":
            purse[0] += val
        elif op == "-":
            purse[0] = max(0, purse[0] - val)
        else:
            purse[0] = max(0, val)
        save_state()
        push_all([29, 28, 30, 178])
        return True, "%s -> %d" % (PURSE_NAME[pkey], purse[0])

    return False, "未知指令：%s" % key


def gm_target(line):
    """GM 指令作用在谁身上。默认最新连上来的号，可用 `@账号 ` 前缀指定。

    用法：`@1234567 元宝 +5000`。账号不在线时按账号现有存档改（先读档）。
    """
    line = line.strip()
    if line.startswith("@"):
        parts = line.split(None, 1)
        acct = parts[0][1:]
        rest = parts[1] if len(parts) > 1 else ""
        for _c, pl in online_players():
            if pl.acct == acct or (not pl.acct and acct == PRIMARY_ACCOUNT):
                return pl, rest
        pl = player_mod.Player()
        pl.acct = "" if acct == PRIMARY_ACCOUNT else acct
        with player_ctx(pl):
            load_state(pl)
        pl.authed = True        # 离线改档也要能落盘
        return pl, rest
    return focus_player(), line


def gm_watch():
    """后台线程：扫 logs/gm.cmd，执行完删除，结果写 logs/gm.log。"""
    last = 0.0
    while True:
        try:
            if os.path.exists(GM_CMD):
                mtime = os.path.getmtime(GM_CMD)
                if mtime != last:
                    with open(GM_CMD, encoding="utf-8") as f:
                        lines = [x.strip() for x in f if x.strip() and not x.startswith("#")]
                    # Windows 上 os.remove 偶尔抛 SHFileOperationW 0x2（文件刚被别的
                    # 进程碰过/杀毒扫描中）。实测：异常落在 os.remove 这一行，而后面的
                    # apply_gm 还没跑 —— 整条 GM 指令被静默吞掉（网页后台同样走这条路）。
                    # 所以删除失败必须吞掉：删不掉就清空内容，指令照常执行。
                    if lines:
                        try:
                            os.remove(GM_CMD)
                        except OSError as _e:
                            log("[gm] 删除指令文件失败（%s），改为清空内容后继续执行" % _e)
                            try:
                                open(GM_CMD, "w").close()
                            except Exception:
                                pass
                        for ln in lines:
                            try:
                                tgt, ln = gm_target(ln)
                                with player_ctx(tgt):
                                    ok, msg = apply_gm(ln)
                            except Exception as e:
                                ok, msg = False, "异常 %s: %s" % (type(e).__name__, e)
                            log("[gm] %s %s -> %s" % ("OK" if ok else "NG", ln, msg))
                    # 清空/删除后 mtime 会变，若还把 last 停在上一次的值，就会对
                    # 同一个空文件每秒重扫一遍（实测刷了几十条日志）。
                    try:
                        last = os.path.getmtime(GM_CMD)
                    except OSError:
                        last = 0.0          # 已被删除：下次新建文件必然是新 mtime
        except Exception as e:
            log("[gm] 扫描异常 %s" % e)
        time.sleep(1)


def online_players():
    """在线角色列表（按连接顺序）。"""
    with GAME_LOCK:
        cids = sorted(GAME_CONNS)
    return [(c, CONN_PLAYERS[c]) for c in cids if c in CONN_PLAYERS]


def focus_player():
    """网页后台/GM 默认操作哪个角色：最后一个连上来的号。

    多开时状态是每连接一份，后台没有「当前角色」的概念，所以约定用最新连接。
    """
    ps = online_players()
    return ps[-1][1] if ps else cur()


def write_status():
    """给网页后端读的在线状态快照。"""
    pl = focus_player()
    try:
        with GAME_LOCK:
            online = len(GAME_CONNS)
        with player_ctx(pl):
            snapshot = {"online": online, "time": time.strftime("%H:%M:%S"),
                        "level": cur().level, "exp": cur().exp, "need": cur().exp_need,
                        "bind_coin": cur().coin_bound[0], "bind_yuanbao": cur().bind_yuanbao[0],
                        "tongqian": cur().tongqian[0], "yuanbao": cur().yuanbao[0],
                        "yueli": cur().yueli[0],
                        "vip_level": cur().vip_level[0], "vip_days10": vip_days10(),
                        "bag": {str(k): v for k, v in sorted(cur().bag.items())}}
            snapshot["accounts"] = [{"cid": c, "acct": pl2.acct or "主号",
                                     "level": pl2.level, "online": True}
                                    for c, pl2 in online_players()]
        with open(STATUS_JSON, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
    except Exception as e:
        log("[status] 写状态失败：%s" % e)


EXTRA_PATH = os.path.join(HERE, "tpl_extra.json")

# 战斗数值表（客户端自带配置，res_cfg/ 由 mhsj.mpkg 解出）
try:
    BT = battle.BattleTables().load()
except Exception as _e:      # 表缺失时退化为旧回放，不阻塞登录
    BT = None
    print("[battle] 配置表加载失败，退化为回放模式:", _e)

# 角色属性：真服实测 Lv2（hp 76 / atk 11~17），与 Levelup.xml 完全一致
# 职业：1御剑 2逍遥 3幻羽 4法尊 5破军；6 = 未转职
# 真服未转职号 151 的 b[2] = 06、技能栏 306 = 60101/60201，所以默认必须是 6。
# 以前默认 1，新建档的角色一出生就带着御剑皮肤和 10101 技能，
# 表现就是「一级号却是转职后的状态」。
# 状态不再放模块全局：见 player.py。这里只留常量与表。
# PLAYER_CLASS 默认 6（未转职）、等级/经验/背包等都在 Player.__init__ 里给默认值。

# 额外法力加成（GM 补偿用，不属于配置表）。
# 背景：破天一剑 10401~10410 的 consume_mp 是 300 起、每级 +100（lv10=1200），
# 而 Lv43 的 Levelup.mp_max 只有 854 —— 客户端在本地比对「当前法力 < 技能消耗」
# 就直接弹 2301「法力值不足，不能使用该技能」，146 根本不会发到服务端。
# 真服同等级裸装也是 854，所以这是数值门槛不是协议 bug；本地服用这条补足。
# MP_ADD 也随角色走（见 player.Player.mp_add）。
# 当前所在地图由每条连接自己维护（handle_game 里的 cur_map）。
# 不能放全局：上一条会话停在通天神塔，下一次登录就会被带成 107，
# 结果人在蓬莱、服务端却下发通天神塔的对象表。
# 默认 101 蓬莱仙境：回放里登录应答 cmd=17 带 65 10 07（场景 101 坐标 16,7）。

# ---- 动态 cmd=28（修复"卡 9 级"）----
# 真服 20:46-20:54 三份 73B 样本逐字节 diff 实证：
#   [16]        = 职业 ID（1御剑 2逍遥 3幻羽 4法尊 5破军，见 Class.xml）
#   [17]        = 性别（1 男 / 0 女，见 Class.xml 的 sex 字段）
#   [18]        = 等级（10）
#   [25..26]    = 当前经验 u16BE（765/2765/5765，与经验条 8.95%/32.34%/67.43% 吻合）
#   [41..42]    = 绑定铜钱 u16BE（4240/5240/10240，增量=任务绑铜奖励 1000/5000 精确吻合）
#   [69..71]    = cd 21 66 -> 升级需求经验 = 0x2166+1 = 8549
# 之前固定推 9 级旧快照，把客户端已升级的显示打回去 = 卡 9 级根因。
# 经验/货币/VIP 的初值都在 player.Player.__init__（exp=5765 10级67.43%、绑铜10240…）。
# ---- 货币（真服 28 包 idx18~21，见文档第 19 节）----
# 顺序固定：绑定铜钱 / 绑定元宝 / 铜钱 / 元宝
# ---- VIP（商城 20130 VIP半年卡 / 20380 VIP体验卡）----
# 真服 2026-11-24 00:23:32 买卡、00:23:39 用卡，前后 cmd=28 逐 token 对齐：
#   41 个 token 数量不变，只有两个槽位动：
#     idx22  0 -> 4       VIP 等级：0 = 无，4 = 至尊VIP
#     idx23  0 -> 1840    VIP 剩余时长，单位 0.1 天（184 天 = 半年卡）
#   29（战力）前后完全一致 ⇒ VIP 只走 28，没有别的包携带 VIP 状态。
#   ★ 1378 里的「至尊VIP」是聊天频道前缀，不是 VIP 状态包，别拿它当依据。
#   1840 在 00:26~00:33 的 9 个 28 包里一直不变 ⇒ 它不是倒计时秒数，
#   而 184 天正好是 11-23 加半年，所以按「剩余天数 × 10」落地。
# 卡 -> (天数, VIP等级)
#   20130 半年卡 184 天 / 等级 4 ← 真服 00:23:39 用卡实证（idx22 0->4 idx23 0->1840）
#   20128 周卡 / 20129 月卡：Item.xml 写的是「黄金VIP / 钻石VIP」，
#     真服抓包里 327 份 cmd=28 只出现过 idx22 = 0 和 4，**没有周/月卡等级样本**，
#     所以 2 / 3 是按「黄金<钻石<至尊」的档位顺序推的，标注为推测，等真服样本再改。
#   20380 体验卡：Item.xml 没写时长，按 1 天 / 等级 1 落地（同样是假设）。
VIP_CARDS = {
    20128: (7, 2),      # VIP周卡   黄金VIP（等级推测）
    20129: (30, 3),     # VIP月卡   钻石VIP（等级推测）
    20130: (184, 4),    # VIP半年卡 至尊VIP（实证）
    20380: (1, 1),      # VIP体验卡（天数/等级均为假设）
}


def vip_days10():
    """VIP 剩余时长，单位 0.1 天（真服半年卡下发 1840）。过期/未开通返回 0。"""
    if not cur().vip_until[0] or not cur().vip_level[0]:
        return 0
    left = cur().vip_until[0] - time.time()
    if left <= 0:
        return 0
    # 用四舍五入而不是向下取整：真服刚用卡那一刻就是 1840，
    # 用 // 会因为延迟几秒变成 1839（客户端可能显示 183.9 天）。
    return int(round(left / 8640.0))          # 8640 秒 = 0.1 天


def vip_grant(days, level=4):
    """开通/续期 VIP。真服用半年卡后 idx22 直接变 4（至尊VIP）。

    level 取「已有等级」和「这张卡档位」中的较大值：先用半年卡再用月卡时
    时长叠加、等级不往下降（真服没抓到这个组合，按不降级处理更保守）。
    """
    base = max(cur().vip_until[0], time.time())
    cur().vip_until[0] = base + days * 86400.0
    cur().vip_level[0] = max(cur().vip_level[0], int(level))
    log("[vip] 开通/续期 %d 天 -> 等级%d 剩余%.1f天（idx22=%d idx23=%d）"
        % (days, cur().vip_level[0], (cur().vip_until[0] - time.time()) / 86400.0,
           cur().vip_level[0], vip_days10()))
# 阅历到底落在 28 包的哪个字段：还没定死。
# 曾猜是 idx12（真服样本只出现过 0/100/500/600/700，步长和见闻录对得上），
# 但实测把它写 0 会让客户端【进战斗闪退】，所以现在只记账、不下发。
YUELI_SLOT = None
# ---- 背包模型 ----
# 之前没有背包状态：181/182 全是「回放模板」，用掉的道具不会消失、效果也不生效。
# 现在用 {格子: [item_id, 数量]} 维护真实背包，178/181 都由它动态生成。
# [player] 状态已移入 player.Player：BAG
# ---- 装备栏：槽位 -> 物品 id ----
# 槽位顺序由 Item.xml 的 item_kind2 决定（真服 17/817/818 包对齐结果）：
#   kind2 1~5 = 武器 -> 槽7（五职业五种武器：1剑 2匕首 3法杖 4弓 5重剑）
#   kind2 6=战甲->槽1 / 7=腰带->槽2 / 8=护腕->槽3
#   kind2 9=护手->槽4 / 10=长裤->槽5 / 11=长靴->槽6
#   kind2 13=戒指->槽9 / 12=护符->槽11（首饰槽 9~12，见下面 ACC_SLOTS）
SLOT_OF_KIND2 = {1: 7, 2: 7, 3: 7, 4: 7, 5: 7,      # 武器只有槽 7 一个
                 6: 1, 7: 2, 8: 3, 9: 4, 10: 5, 11: 6,
                 # ★ 2026-11-25：首饰槽补进来。以前只有 7 个槽，背包里点
                 # 10305 飞仙戒指 / 10306 飞仙护符（kind2=13/12）会走到
                 # 「这件装备的槽位还没对上，道具已保留」——就是玩家反馈的
                 # 「系统送的护符戒指穿不上」。
                 # 真服 817（客户端上报装备栏）实证 00:15:10：
                 #   0b cd2842 0b / cd2842 0c / ... / cd2841 09 / cd2841 0a
                 #   => 首饰槽 = 9/10（戒指×2）、11/12（护符×2）。
                 # 本地服只用一个戒指槽 + 一个护符槽，够用且不会重复算属性。
                 13: 9, 12: 11}
# ★ 2026-11-25 23:0x：以前只有 kind2=1 映射到槽 7，另外四种武器（匕首/法杖/
# 弓/重剑，kind2=2~5）全都落到「槽位还没对上」——玩家在背包里点逍遥匕首，
# 客户端弹「该物品不能直接使用」，武器槽一直是空的。真服 30 包只有 1 个武器槽
# （槽 7，96B 样本里是 11328 玉衡剑），所以 1~5 一律进槽 7。


# 首饰槽（30 包槽 6 与槽 7 之间那 16 字节就是这 4 个槽：空 4B、有装备 6B）
ACC_SLOTS = (9, 10, 11, 12)

# ★ 2026-11-25 23:25 真服抓包定性（推翻「成对镜像」的旧结论）：
#   23:25:24  C->S 182 hex=3a01  → 30 只多了一条 cd28420c0101（护符，槽12）
#   23:25:26  C->S 182 hex=3b01  → 30 又只多一条 cd28420b0101（护符，槽11）
#   23:25:27  C->S 182 hex=3801  → 只多 cd2841090101（戒指，槽9）
#   23:25:29  C->S 182 hex=3901  → 只多 cd28410a0101（戒指，槽10）
# 同一个 178 里这四件各占一个独立包格（38/39/3a/3b）。
# 也就是说 9/10 是**两个戒指槽**、11/12 是**两个护符槽**，四件独立物品，
# 不是「一件同时写两格」。以前按镜像处理 → 面板多一件、属性翻倍、
# 卸下时两格一起消失、178 少回一件，客户端对不上账直接 SIGABRT 闪退。
# 下面这张表只剩「同类的另一个槽」这一个用途：装备时优先找空的那格。
ACC_PAIR = {9: 10, 10: 9, 11: 12, 12: 11}

# ★ 2026-11-25 23:24 真服实测（决定性）：11 格逐格点击 + 每次点击后的 30 包
# 逐件对比「点了哪格、哪个槽消失」，得到精确映射：
#   格号   00 01 02 03 04 05 06 07 08 09 0a
#   槽位    1  2  3  4  5  6 11 12 10  9  7
# 原始证据（30 包实装槽位，去掉坐骑 13）：
#   00→[2,3,4,5,6,11,12,10,9,7]  01→[3,4,5,6,...]  05→[11,12,10,9,7]
#   06→[]  07→[11]  08→[11,12]  09→[11,12,10]  0a→[11,12,10,9]
# 所以第 7 格是护符 11、第 11 格才是武器 —— 以前把武器排在第 7 位，
# 玩家点护符就脱成戒指，就是「戒指和护符弄错了」。
CELL_SLOTS = (1, 2, 3, 4, 5, 6,      # 00~05 主装备（槽 1~6）
              11, 12,                # 06,07 护符（槽 11,12）
              10, 9,                 # 08,09 戒指（槽 10,9）
              7)                     # 0a    武器（槽 7）


def acc_slots_of(slot):
    """某个槽卸下时**实际要清的槽**。

    首饰四格是四件独立物品（真服 23:25:24~29 四个 182 各占一格），
    所以永远只清点中的那一格 —— 以前返回 (slot, 配对槽) 会把玩家另一件
    首饰一起吞掉，客户端收到 178 少一件就对不上账（SIGABRT 闪退）。
    """
    return (slot,)


def equip_slot_for(kind2):
    """按 kind2 找该穿进哪个槽。首饰同类有两格（戒指 9/10、护符 11/12），
    优先放空的那格，两格都满就换主槽（9 / 11），旧的那件回背包。"""
    base = SLOT_OF_KIND2.get(kind2)
    if base is None:
        return None
    pair = ACC_PAIR.get(base)
    if not pair:
        return base
    if not cur().equip.get(base):
        return base
    if not cur().equip.get(pair):
        return pair
    return base


def equip_slots():
    """当前要下发给客户端的槽位列表（1~7 固定 + 已穿首饰）。"""
    out = [1, 2, 3, 4, 5, 6, 7]
    for s in ACC_SLOTS:
        if cur().equip.get(s):
            out.append(s)
    return out
# [player] 状态已移入 player.Player：EQUIP

# 强化等级：槽位 -> +N。真服 818 单条记录 offset 6 就是这个值
# （18:20:20 强化前后对比：07cd2b6a010000... -> 07cd2b6a010001...，只差这一字节）
# [player] 状态已移入 player.Player：ENHANCE

# ---- 强化石表（mirror/assets/configure/Item.xml 20138~20154 原样抄） ----
# item_kind2=20 强化仙玉：武器/戒指/坐骑；item_kind2=21 强化灵石：防具/护符/时装/翅膀/法宝
# 描述里写死了能强化到几级，这就是「初级石只能到+3」的出处：
#   初级 20138/20141  可强化+1~+3   +5% 成功率
#   中级 20139/20142  可强化+1~+6   +8%
#   高级 20140/20143  可强化+1~+9   +12%
#   特级 20456/20457  可强化+1~+12  +15%
# 装备自身上限也是 +12（Equip.xml 只有 strengthen_1..strengthen_12）。
STONE = {
    20138: ("仙玉", 3, 5), 20139: ("仙玉", 6, 8),
    20140: ("仙玉", 9, 12), 20456: ("仙玉", 12, 15),
    20141: ("灵石", 3, 5), 20142: ("灵石", 6, 8),
    20143: ("灵石", 9, 12), 20457: ("灵石", 12, 15),
}
ENH_MAX = 12

# ---- 强化石合成链（831）----
# 真服 19:23:02 唯一实证：4 个初级强化灵石 -> 1 个中级强化灵石。
#   C->S 831 len=6 hex=01 cd4ead 01 04
#     [0] 01 = 合成模式  [1..] cd4ead = 材料 20141
#     倒数第 2 字节 01 = 合成次数   最后 1 字节 04 = 每次消耗 4 个
#   178 全量背包 diff（19:22:52 -> 19:23:03）：
#     初级强化灵石 6 -> 2（扣 4 个）、新增 中级强化灵石 x1
# 仙玉链同理（20138 初级 -> 20139 中级 -> 20140 高级 -> 20456 特级），
# 灵石链 20141 -> 20142 -> 20143 -> 20457。两条链都是 4 合 1。
STONE_UP = {20138: 20139, 20139: 20140, 20140: 20456,
            20141: 20142, 20142: 20143, 20143: 20457}

# ---- 锻造面板结果码（chinese.xml 的 Forge_xx，客户端自己出文案）----
# 之前所有失败都回 cmd=1 文本，客户端的锻造回调等不到 822，
# 只能兜底弹 Forge_22「很遗憾，强化失败了」，所以「没钱也只会说强化失败」。
# 正确做法：回 822 + 1 字节结果码，客户端就能弹出对应那句。
FORGE_CODE_MODE = True      # False = 退回 cmd=1 文本（万一 1 字节应答崩客户端就关掉）
FORGE_OK = 21               # 恭喜，强化成功！强化等级加1
FORGE_FAIL = 22             # 很遗憾，强化失败了
FORGE_NO_EQUIP = 23         # 装备不存在
FORGE_NO_YUANBAO = 24       # 你的元宝不足，不能自动购买材料
FORGE_NO_COIN = 25          # 你的铜钱不够
FORGE_NO_MAT = 26           # 你的材料不够
FORGE_MAX = 45              # 此装备强化等级已达上限，请升阶装备后再强化

# ---- 强化成功率 ----
# 客户端只负责显示「成功率」，判定在服务端。基础值按强化等级递减，
# 再叠加强化石自带的加成（Item.xml：初级+5% 中级+8% 高级+12% 特级+15%）。
# 真服 18:30 连做 18 次 +1~+3 全成功，所以 +0~+2 的基础值必须很高。
BASE_RATE = {0: 100, 1: 95, 2: 90, 3: 85, 4: 80, 5: 75,
             6: 70, 7: 65, 8: 60, 9: 55, 10: 50, 11: 45}
# 失败掉 1 级，但 +3 / +6 / +9 是保底档，掉不下来：
#   0~2 -> 保底 0；3~5 -> 保底 3；6~8 -> 保底 6；9~11 -> 保底 9
def enh_floor(lv):
    return (int(lv) // 3) * 3


def forge_decide(lv, luck):
    """一次强化的判定：返回 (是否成功, 实际成功率, 结果等级)。

    成功率 = 等级基础值 + 强化石加成，封顶 100。
    失败掉 1 级，但 enh_floor 保证了 +3/+6/+9 掉不下去（3 失败仍是 3）。
    """
    lv = int(lv)
    rate = min(100, BASE_RATE.get(lv, 40) + int(luck))
    ok = (not FORGE_LUCK[0]) or (random.randint(1, 100) <= rate)
    return ok, rate, (lv + 1 if ok else max(enh_floor(lv), lv - 1))


FORGE_LUCK = [True]         # False = 强化必成（GM「必成」切，方便测上限/材料）


def item_name(iid):
    """道具名，查不到就回 id，日志和提示都用它。"""
    try:
        return bag_proto.item(int(iid))["name"]
    except Exception:
        return "道具%d" % int(iid)

# ---- 已装备的翅膀形象（形象卡 item_id，None = 没翅膀）----
# 真服实证：用掉 41002 后回 1922(48B)/1931(35B)/1932(13B)，背包里那张卡消失。
# 登录时真服只推 33B 的空 1922，所以「有没有翅膀」必须由服务端记住并在登录时补。
# [player] 状态已移入 player.Player：WING

# ---- 衣柜（时装，kind2=14）----
# 真服 837 -> 838 实证（logs/relay.log 13:50:37）：
#   C->S 837 空包 -> S->C 838 len=65
#   09 + 9 × [ce+u32 时装ID][00][ff] + 末尾 00
#     = 1 + 9*7 + 1 = 65B
#   样本里的 90033 / 90053 就是背包里「快乐圣诞 男 / 白色浪漫 男」的 item_id，
#   说明 838 里放的是「已拥有的时装 item_id」，格式是定长 7B 一条。
# 时装卡本身在真服点了会进衣柜（不能直接穿），所以这里用 WARDROBE 记拥有集合。
# [player] 状态已移入 player.Player：WARDROBE

# ---- 妖灵 / 宠物（真服 2026-11-23 21:03 实测）----
# SPIRIT_BAG  = {妖灵id: {"idx": 获得次序(1起), "slot": 槽位(0=未上阵)}}
# SPIRIT_SLOT = {槽位: 妖灵id}   槽位 1/2/3，与 1411 请求的字节一致
# 注意：真服上阵妖灵后 cmd=29 面板不变，妖灵属性只进宠物自己的 947 面板，
# 所以这里不往 all_attrs() 里合，避免把玩家面板算错。
# [player] 状态已移入 player.Player：SPIRIT_BAG

# 坐骑解锁开关：本地存档里没有坐骑数据，必须保持 False（1346 回 34B 未解锁）。
# 真服 23:50 解锁后 1346 变 40B 并跟推 1362/1363；本地要等坐骑数据也落地了再打开，
# 否则客户端按「有坐骑」去取不存在的数据，登录直接闪退（2026-11-24 00:00 实测）。
MOUNT_UNLOCKED = False
# [player] 状态已移入 player.Player：SPIRIT_SLOT
MAX_SPIRIT_SLOT = 3

# 任务状态：以前只放在 handle_game 的局部变量 active_qid 里，服务端一重启 /
# 客户端一重连就丢，导致打完怪不再推 210「可交」，客户端就卡在
# 「点提交没反应、任务一直重复」。现在跟存档一起持久化。
# [player] 状态已移入 player.Player：QUEST_ACTIVE
# [player] 状态已移入 player.Player：QUEST_READY
# [player] 状态已移入 player.Player：DONE_QUESTS

# 可直接生效的消耗品：item_id -> (货币键, 单位增量)
USE_EFFECTS = {
    20190: ("bind_coin", 10000),     # 绑定铜钱卡 +10000（真服 15:38 实测）
    20432: ("bind_yuanbao", 10),     # 10绑定元宝（真服 00:01 实测 +20/2张）
    20434: ("bind_yuanbao", 1),
    22059: ("bind_yuanbao", 500),
    22066: ("yuanbao", 500),
    22065: ("yuanbao", 800),
    22084: ("yuanbao", 1000),
    22074: ("yuanbao", 1200),
    22085: ("yuanbao", 1500),
}
# 礼包：item_id -> [(物品, 数量), ...]（真服 00:00 实测 5级新丁礼包）
GIFTS = {
    21001: [(30059, 1), (20717, 2), (20053, 5), (21002, 1)],
}
# 「用不了」的准确原因（按 item_kind2 / item_id）。
# 这些子系统的协议还没抓到正样本，服务端【不消耗】道具 —— 免得像天使羽翼那样
# 点一下就没了、也没有任何效果。提示要说清楚卡在哪，别让玩家以为是服务器坏了。
WHY_BY_KIND2 = {
    34: "宠物形象系统尚未实现，道具已保留",
    36: "妖灵系统尚未实现，道具已保留",
    61: "坐骑系统尚未实现，道具已保留",
    68: "妖灵礼包需要妖灵系统，道具已保留",
    83: "称号系统尚未实现，道具已保留",
    21: "装备强化界面尚未实现，道具已保留",
}
WHY_BY_ID = {
    20130: "VIP 系统尚未实现，道具已保留",
    20458: "经验丹数据异常",      # 正常走 add_reward，不会到这里
}
# 真服样本 C（20:54:33，交 400902 后，73B 全量）：除上述动态位外原样回放
PLAYER_28_TPL = bytes.fromhex(
    "0ca731373834333636a6e8908ce696b001010acd043bccd2cd16856627"
    "0acd02bc0000000500ce00232800cd0fbe00cd03650000000000000100"
    "00cd03ed0000000001000000cd2166")


def err_text(msg):
    """cmd=1 文本提示（真服 21 份样本格式）：04 da + u16BE 长度 + UTF-8。"""
    b = msg.encode("utf-8")
    return b"\x04\xda" + len(b).to_bytes(2, "big") + b


def PURSES():
    """当前角色的货币表（每次取，不能缓存 —— 换了角色引用就变了）。"""
    return cur().purses()


PURSE_NAME = {"bind_coin": "绑定铜钱", "bind_yuanbao": "绑定元宝",
              "tongqian": "铜钱", "yuanbao": "元宝", "yueli": "阅历"}


def add_purse(key, amount):
    """给某种货币/阅历加量。key 见 PURSES()。"""
    p = PURSES().get(key)
    if p is None:
        return False
    p[0] += amount
    return True


# ---- 进度存档（等级/经验/铜钱/职业），避免每次重启回档 ----
STATE_FILE = os.path.join(HERE, "player_state.json")
# 多账号：主号（QQ 1784366）继续用 player_state.json，不动老存档；
# 其它账号各自一个 player_state_<账号>.json，首次登录时用主号存档做模板复制一份。
# 账号现在记在 Player.acct 上（每个连接一份），不再共用进程级 ACCOUNT_STATE。
PRIMARY_ACCOUNT = "1784366"


def state_path(pl=None):
    """某个角色的存档路径。不传就用当前线程的角色。"""
    pl = pl or cur()
    a = pl.acct
    if not a or a == PRIMARY_ACCOUNT:
        return STATE_FILE
    return os.path.join(HERE, "player_state_%s.json" % a)


def switch_account(acct):
    """把【当前连接的角色】切到某个账号并从磁盘读档。返回是否真的切了。

    ★ 2026-11-24：取消「新账号复制主号存档当模板」。
    那是错的——新账号应该从建角流程(Lv1 空档)开始，复制主号等于把 Lv45 的装备、
    任务、元宝直接送给新号。现在没存档就 reset_newbie()：Lv1 / 未转职 / 空背包 / 钱 0，
    且 has_char=False、loaded=False —— 不写盘，等「建角」落定后才允许落盘。
    """
    pl = cur()
    acct = str(acct or "").strip()
    if not acct or acct == pl.acct:
        return False
    pl.acct = acct
    p = state_path(pl)
    if not os.path.exists(p):
        player_mod.reset_newbie(pl)
        pl.acct = acct
        log("[acct] 账号 %s 还没有角色存档（%s），已置为 Lv1 空号，等待建角"
            % (acct, os.path.basename(p)))
        return True
    load_state()
    save_state()
    log("[acct] 切换到账号 %s（存档 %s）Lv%d %s"
        % (acct, os.path.basename(p), pl.level,
           bag_proto.item(pl.equip.get(1))["name"] if pl.equip.get(1) else "无武器"))
    return True


def create_character(name, sex=1, pl=None):
    """给某个账号建一个 Lv1 角色并落盘（群主在后台代玩家建角）。

    ★ 这是「没有自助注册」下的正解：账号由群主发，角色由群主建。
    真服的建角界面（CreateRoleScene）要先让客户端相信「这号没角色」才会弹，
    而那个信号在 17 应答里还没解出来（变体 6 一律被当成有角色，见
    build_17_fresh 的注释）。所以先走服务端代建，玩家登录即进游戏。

    昵称规则与客户端 create_role.xml 的输入框一致：1~10 个字符、不能有空格。
    """
    pl = pl or cur()
    name = str(name or "").strip()
    if not name:
        return False, "昵称不能为空"
    if len(name) > 10:
        return False, "昵称最多 10 个字（客户端输入框 maxlength=10）"
    if any(c.isspace() for c in name):
        return False, "昵称不能含空格"
    player_mod.reset_newbie(pl)
    pl.name = name
    pl.sex = 2 if int(sex or 1) == 2 else 1
    pl.has_char = True
    pl.loaded = True
    pl.authed = True
    save_state(force=True)          # 新角色必须强制落盘（闸门会拦住普通写入）
    log("[char] ★ 建角 账号=%s 昵称=%r 性别=%d -> Lv1 未转职"
        % (pl.acct or "主号", pl.name, pl.sex))
    return True, "已建角：账号 %s 昵称 %r 性别 %s，Lv1 未转职" % (
        pl.acct or "主号", pl.name, "女" if pl.sex == 2 else "男")


def rename_character(name, pl=None):
    """改角色名（建角后改名用）。"""
    pl = pl or cur()
    name = str(name or "").strip()
    if not name:
        return False, "昵称不能为空"
    if len(name) > 10 or any(c.isspace() for c in name):
        return False, "昵称 1~10 字且不能有空格"
    old = pl.name
    pl.name = name
    save_state()
    return True, "%r -> %r" % (old, name)


def account_from_login(payload):
    """cmd=17 登录包里的账号串。

    真服样本：`01 cc9702 a8 "80000001" da00 2b <43B token>`
    字符串字段编码是 `0xA0|len` + UTF-8：七位账号是 `a7`，八位是 `a8`，空串是 `a0`。

    ★ 2026-11-24 02:40 修：以前写死只认 `0xA7` 且只收数字，
    结果 8 位账号 / `test123` 全部解析成空串 → 不切账号 → 默认角色写盘。
    """
    for i in range(len(payload) - 1):
        b = payload[i]
        if not (0xA0 <= b <= 0xBF):
            continue
        n = b & 0x1F
        if n < 4 or i + 1 + n > len(payload):
            continue
        try:
            s = payload[i + 1:i + 1 + n].decode("ascii")
        except Exception:
            continue
        if all(c.isalnum() or c in "_-." for c in s):
            return s
    return ""


def load_state(pl=None):
    """从磁盘读【当前角色】的存档。

    先把角色重置为 Player 的默认值再套存档：换账号 / 重连时不能残留上一个号的
    等级、背包、VIP，否则两个号数据会糊在一起。
    """
    pl = pl or cur()
    dflt = player_mod.Player()
    for k in player_mod.Player.__slots__:
        if k in ("acct", "cid", "authed", "loaded", "saved_sig"):
            continue
        setattr(pl, k, copy.deepcopy(getattr(dflt, k)))
    pl.loaded = False           # 读盘失败就不能算「已加载」，save_state 会拒绝写
    pl.saved_sig = None
    try:
        with open(state_path(pl), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return
    pl.loaded = True            # ★ 只有真读到文件才算加载成功
    pl.name = str(d.get("name") or pl.name)
    pl.sex = int(d.get("sex", pl.sex) or pl.sex)
    pl.has_char = bool(d.get("has_char", True))
    pl.exp = int(d.get("exp", pl.exp))
    pl.level = int(d.get("level", pl.level))
    pl.cls = int(d.get("class", pl.cls))
    pl.coin_bound[0] = int(d.get("coin", pl.coin_bound[0]))
    pl.exp_need = int(d.get("need", pl.exp_need))
    pl.bind_yuanbao[0] = int(d.get("bind_yuanbao", pl.bind_yuanbao[0]))
    pl.tongqian[0] = int(d.get("tongqian", pl.tongqian[0]))
    pl.yuanbao[0] = int(d.get("yuanbao", pl.yuanbao[0]))
    pl.yueli[0] = int(d.get("yueli", pl.yueli[0]))
    pl.vip_level[0] = int(d.get("vip_level", 0) or 0)
    pl.vip_until[0] = float(d.get("vip_until", 0) or 0)
    if pl.vip_until[0] and pl.vip_until[0] <= time.time():
        log("[vip] 存档里的 VIP 已于 %s 到期，状态清零"
            % time.strftime("%Y-%m-%d %H:%M", time.localtime(pl.vip_until[0])))
        pl.vip_level[0], pl.vip_until[0] = 0, 0.0
    pl.mp_add[0] = int(d.get("mp_add", 0) or 0)
    w = d.get("wing")
    pl.wing[0] = int(w) if w else None
    wd = d.get("wardrobe")
    if isinstance(wd, list):
        pl.wardrobe.clear()
        pl.wardrobe.update(int(x) for x in wd)
    pl.quest_active = int(d.get("quest_active", 0) or 0)
    pl.quest_ready = bool(d.get("quest_ready", False))
    # ★ 场景 + 坐标（老号回到最后一次下线的地点）。老存档没有 scene/pos_* 字段，
    #   走默认 101 / 21,7 —— 与这轮改动之前的表现完全一致，不会把老号扔到别处。
    #   场景 id 必须校验存在：配置表删过地图或者手改坏存档时，宁可回蓬莱也别进虚空。
    try:
        _sc = int(d.get("scene", pl.scene) or pl.scene)
    except (TypeError, ValueError):
        _sc = DEFAULT_SCENE
    pl.scene = _sc if _sc in SCENES else DEFAULT_SCENE
    if pl.scene != _sc:
        log("[spawn] 存档场景 %s 不在 Scene.xml，回退蓬莱仙境(%d)" % (_sc, pl.scene))
    try:
        pl.pos_x = int(d.get("pos_x", pl.pos_x)) & 0xFF
        pl.pos_y = int(d.get("pos_y", pl.pos_y)) & 0xFF
    except (TypeError, ValueError):
        pl.pos_x, pl.pos_y = 21, 7
    dq = d.get("quest_done")
    if isinstance(dq, list):
        pl.done_quests.clear()
        pl.done_quests.update(int(x) for x in dq)
    eq = d.get("equip")
    if isinstance(eq, dict):
        pl.equip.clear()
        for k, v in eq.items():
            pl.equip[int(k)] = int(v)
    # ★ 旧版本把一件首饰同时写进成对两格（9+10 / 11+12），读档时清掉多出来的那格。
    # 首饰四格是四件独立物品，同一件不可能同时占两格；留着会让面板多一件、
    # 卸下时两格一起消失、178 和面板对不上账（客户端 SIGABRT 闪退）。
    for _a, _b in ((9, 10), (11, 12)):
        if pl.equip.get(_a) and pl.equip.get(_a) == pl.equip.get(_b):
            pl.equip.pop(_b, None)
    eh = d.get("enhance")
    if isinstance(eh, dict):
        pl.enhance.clear()
        for k, v in eh.items():
            pl.enhance[int(k)] = int(v)
    bag = d.get("bag")
    # ★ 空背包也必须清空：以前写成 `and bag`，空号存档（bag={}）会被跳过，
    # 于是 Lv1 新角色读档后继承了 Player() 默认那 3 件演示道具。
    if isinstance(bag, dict):
        pl.bag.clear()
        for k, v in bag.items():
            pl.bag[int(k)] = [int(v[0]), int(v[1])]
    sb = d.get("spirit_bag")
    if isinstance(sb, dict):
        pl.spirit_bag.clear()
        for k, v in sb.items():
            v = v if isinstance(v, dict) else {}
            pl.spirit_bag[int(k)] = {"idx": int(v.get("idx") or 1),
                                     "slot": int(v.get("slot") or 0)}
    ss = d.get("spirit_slot")
    if isinstance(ss, dict):
        pl.spirit_slot.clear()
        for k, v in ss.items():
            if int(v):
                pl.spirit_slot[int(k)] = int(v)
    # 宠物：新号必须一只都没有（真服新号身边不跟熊猫）。
    # 老存档（这轮改动之前落的）没有 pets 字段 —— 只有主号补种，其余账号一律当空，
    # 否则用户新建的号会连带继承主号那三只。
    pt = d.get("pets")
    if isinstance(pt, list):
        pl.pets = [int(x) for x in pt]
    elif pl.acct in ("", PRIMARY_ACCOUNT):
        pl.pets = list(pet_proto.PET_UIDS)
        log("[pet] 老存档无 pets 字段（主号），补种真服已有的三只形象")
    else:
        pl.pets = []
    # 妖灵播种：达到 20 级才播（低等级新角色不该一出生就带三只没做任务拿到的妖灵，
    # 用户反馈过「宠物像快照数据」）。以前只给主号播，其他号到 30 多级也一只没有，
    # 用户反馈「血·共工那些妖灵没有」，所以按等级放开，仍然**不自动上阵**。
    if (pl.has_char and pl.level >= 20
            and not pl.spirit_bag and not d.get("spirit_bag")):
        # 新存档：只播种「拥有」，**不自动上阵**。
        # 真服 2026-11-23 20:52 打开宠物界面的现场是：962 第一组 0 只、
        # 947 是 98B（妖灵区 `08` + 32×00 全空）——即 0 只上阵。
        # 之前自动上阵 3 只导致 947 走 105B，客户端解析不出列表连 963 都不发。
        for _i, _iid in enumerate((1101, 4101, 2101), 1):
            pl.spirit_bag[_iid] = {"idx": _i, "slot": 0}
        log("[spirit] 播种妖灵 血·共工/命·魍魉/攻·白光虎（均未上阵，与真服 20:52 现场一致）")
        try:
            save_state()      # 立刻落盘，免得下次重启又播一次
        except Exception:
            pass
    log("[state] 账号%s 读档 Lv%d exp=%d/%d 绑铜=%d 绑元=%d 铜钱=%d 元宝=%d 背包=%d件"
        " 任务=%s(%s) 已交=%d条"
        % (pl.acct or "主号", pl.level, pl.exp, pl.exp_need, pl.coin_bound[0],
           pl.bind_yuanbao[0], pl.tongqian[0], pl.yuanbao[0], len(pl.bag),
           pl.quest_active or "无", "可交" if pl.quest_ready else "进行中",
           len(pl.done_quests)))
    # 记下刚读到的内容指纹：登录后如果什么都没改，退出时不会重复写盘/重复备份
    with player_ctx(pl):
        pl.saved_sig = json.dumps(state_dict(), ensure_ascii=False, sort_keys=True)


BACKUP_DIR = os.path.join(HERE, "backups")
BACKUP_EVERY = 600.0        # 同一角色最多 10 分钟存一份快照
BACKUP_KEEP = 20            # 每个角色保留最近 20 份


def backup_state(path):
    """写盘前留一份快照。

    2026-11-24 01:43 出过事：一个没登录的连接用默认值（Lv10/3 件）把 Lv45 存档
    整个盖掉，玩家进度直接没了。刷盘前留快照是最省事的保险，别省这一步。
    """
    if not os.path.exists(path):
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        base = os.path.basename(path).replace(".json", "")
        now = time.time()
        old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(base + "_"))
        if old:
            last = os.path.join(BACKUP_DIR, old[-1])
            if now - os.path.getmtime(last) < BACKUP_EVERY:
                return
        dst = os.path.join(BACKUP_DIR, "%s_%s.json"
                           % (base, time.strftime("%Y%m%d_%H%M%S")))
        with open(path, "rb") as src, open(dst, "wb") as out:
            out.write(src.read())
        old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(base + "_"))
        for f in old[:-BACKUP_KEEP]:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
            except OSError:
                pass
    except Exception as e:
        log("[state] 备份失败：%s" % e)


def state_dict():
    """当前角色的可持久化字段（存档 + 指纹共用同一份，避免两处漏字段）。"""
    return {"name": cur().name, "sex": cur().sex, "has_char": cur().has_char,
            "level": cur().level, "exp": cur().exp,
            "need": cur().exp_need, "coin": cur().coin_bound[0],
            "class": cur().cls,
            "bind_yuanbao": cur().bind_yuanbao[0],
            "tongqian": cur().tongqian[0],
            "yuanbao": cur().yuanbao[0],
            "yueli": cur().yueli[0],
            "vip_level": cur().vip_level[0],
            "vip_until": cur().vip_until[0],
            "mp_add": cur().mp_add[0],
            "wing": cur().wing[0],
            "wardrobe": sorted(cur().wardrobe),
            "quest_active": cur().quest_active,
            "quest_ready": cur().quest_ready,
            "quest_done": sorted(cur().done_quests),
            # ★ 场景 + 坐标：老号登录要回到最后一次下线的地点（用户 2026-11-24 要求）。
            #   以前只存了 scene，坐标每次都是 17 模板里的 21,7，落点永远一样。
            "scene": int(cur().scene or DEFAULT_SCENE),
            "pos_x": int(getattr(cur(), "pos_x", 21) or 0),
            "pos_y": int(getattr(cur(), "pos_y", 7) or 0),
            "equip": {str(k): v for k, v in cur().equip.items()},
            "enhance": {str(k): v for k, v in cur().enhance.items()},
            "bag": {str(k): v for k, v in cur().bag.items()},
            "pets": list(cur().pets),
            "spirit_bag": {str(k): v for k, v in cur().spirit_bag.items()},
            "spirit_slot": {str(k): v for k, v in cur().spirit_slot.items()}}


def save_state(force=False):
    # 三重闸门（2026-11-24 按复盘建议补强）：
    #   authed  —— 收到过 cmd=17（半开连接不许写）
    #   loaded  —— 真的从磁盘读到了这个角色的存档（登录了但角色还是默认值时不许写）
    #   acct    —— 必须知道这份数据属于哪个账号（否则会写进主号文件）
    # 之前只有 authed 一道，02:08 那次默认态覆盖就是这么漏过去的。
    pl = cur()
    if not force:
        if not pl.authed or not pl.loaded or not pl.acct or not pl.has_char:
            return
    path = state_path(pl)
    d = state_dict()
    sig = json.dumps(d, ensure_ascii=False, sort_keys=True)
    if not force and sig == pl.saved_sig:
        return                              # 内容没变，不用写盘也不用备份
    backup_state(path)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(sig)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)               # 原子替换，断电不会留半截 JSON
        pl.saved_sig = sig
    except Exception as e:
        log("[state] 存档失败：%s" % e)
        try:
            os.remove(tmp)
        except OSError:
            pass


# 启动时不读档：状态是每连接一份，等 cmd=17 拿到账号后再 load_state()。


def set_quest(qid_int, ready=False):
    """记录当前任务状态并立即落盘（重连/重启后还能推对 210）。"""
    pass  # [player] 状态走 cur()，无需 global
    cur().quest_active = int(qid_int or 0)
    cur().quest_ready = bool(ready)
    save_state()
    log("[quest] 状态 active=%s ready=%s" % (cur().quest_active or "无", cur().quest_ready))


def qid_of(active_qid):
    """5 字节任务 ID（ce + u32BE）→ int。"""
    try:
        return int.from_bytes(active_qid[1:5], "big")
    except Exception:
        return 0


def quest_210_ready(qid_int):
    """210 的「可交」形态（9B，真服 15:37 样本）：01 + 任务ID5B + 01 01 00"""
    return b"\x01" + (b"\xce" + qid_int.to_bytes(4, "big")) + b"\x01\x01\x00"


def quest_210_doing(qid_int):
    """210 的「进行中」形态（8B，真服 15:37:08 样本）：00 01 + 任务ID5B + 01"""
    return b"\x00\x01" + (b"\xce" + qid_int.to_bytes(4, "big")) + b"\x01"


def add_reward(exp, coin):
    """交任务奖励入账 + 按 cur().exp_need 升级（升级需求 11 级以上按 1.18 倍近似，
    真服只实证到 8549=10->11）。"""
    pass  # [player] 状态走 cur()，无需 global
    cur().exp += exp
    while cur().exp >= cur().exp_need and cur().level < 80:
        cur().exp -= cur().exp_need
        cur().level += 1
        cur().exp_need = int(cur().exp_need * 1.18)
        log("[player] 升级 -> Lv%d (exp=%d need=%d)"
            % (cur().level, cur().exp, cur().exp_need))
    cur().coin_bound[0] += coin
    save_state()


def venc(v):
    """协议 varint：>=0x10000 走 ce+4B，>=0x100 走 cd+2B，>=0x80 走 cc+1B，其余单字节。"""
    v = int(v)
    if v >= 0x10000:
        return b"\xce" + v.to_bytes(4, "big")
    if v >= 0x100:
        return b"\xcd" + v.to_bytes(2, "big")
    if v >= 0x80:
        return b"\xcc" + bytes([v])
    return bytes([v])


def sfield(s):
    """短字符串字段：0xA0|len + UTF-8 字节（len<32）。

    实证：真服 17 包里账号 "1784366" 是 `a7`+7B、角色名 "萌新" 是 `a6`+6B，
    即 0xA0 与长度的或。空串就是 `a0`。
    """
    b = s.encode("utf-8")
    n = len(b)
    if n < 32:
        return bytes([0xA0 | n]) + b
    # 长串没实证过，先按 0xB0|低4位 + 一个长度字节的猜法，只在调试里出现
    return bytes([0xB0 | (n & 0x0F), n & 0xFF]) + b


# 「账号存在但还没有角色」的 17 应答试验包。
# 用 FRESH_ACCT/FRESH_V 环境变量在本地服上试，不用等真服发新号。
FRESH_ACCT = os.environ.get("FRESH_ACCT", "")
FRESH_V = int(os.environ.get("FRESH_V", "1"))
# 没有角色的账号登录时要不要自动补一个 Lv1 角色。
# 开着（默认）：群主把账号发出去，玩家登录就能玩；昵称先用账号名，后台「改名」再改。
# 关掉（AUTO_NEWBIE=0）：走无角色试验分支，卡在 loading 等建角协议。
AUTO_NEWBIE = os.environ.get("AUTO_NEWBIE", "0") == "1"


def build_17_fresh(acct, variant=1):
    """「账号在、角色空」的 17 应答。

    真服 197B 结构（TPL17_LIVE）逐字段：
        [0] 0x0c 标记
        [1] 0xa7 账号长度标记 → [2..8] "1784366"
        [9] 0xa6 昵称长度标记 → [10..15] "萌新"
        [16..] 职业/等级/属性/装备/外观

    ★ 2026-11-24 03:0x 加变体 6：以前那几种都是「截断成 11~20 字节」的猜法，
    真服从没出现过这么短的 17 应答，客户端解析必然失败（多半直接显示登录失败，
    而不是跳建角）。变体 6 改为「真服原包为底，只把账号换成实际账号、昵称换成
    空串 a0」，其余字段原样保留——长度只随账号名长短变化，客户端能正常走完解析，
    唯一异常就是「昵称是空」，这才是让上层判断「没角色」的正确信号。
    """
    a = sfield(acct or "")
    if variant == 6:
        body = TPL17_LIVE
        n = body[1] & 0x1F                     # 模板里账号长度（7）
        rest = body[1 + 1 + n:]                # 从昵称长度标记开始
        if rest[:1] == b"\xa6" and len(rest) >= 7:
            rest = rest[7:]                    # 跳过 0xa6 + 6 字节「萌新」
        return body[:1] + a + b"\xa0" + rest
    if variant == 1:
        return b"\x0c" + a + b"\xa0"          # 账号 + 空角色名（截断版，仅留作对照）
    if variant == 2:
        return b"\x0c" + a                    # 只有账号
    if variant == 3:
        return b"\xa0"                        # 什么都空
    if variant == 4:
        return b""                            # 空包
    if variant == 5:
        return b"\x0c" + a + b"\xa0" + b"\x00" * 8
    return b"\x0c" + a + b"\xa0"


def parse_create_role(payload):
    """从 cmd=20 建角请求里抽「昵称 / 性别」。

    真服实测 payload（73B，2026-11-24 12:38）：
        01 01 02 | a9 + 账号(9B) | da 00 2b + 43B token | ac + 昵称(12B) | 01(男)
    本地客户端实测（2026-11-24 12:55，选「女」）末尾是 **00**，不是 02：
        ... ac + 昵称(12B) | 00
    所以性别编码是 **1=男 / 0=女**（02 也当女，兼容旧猜测）。
    昵称是**最后一个** sfield，紧跟其后 1 字节是性别。所以这里取最后一个候选，
    不要取第一个（第一个是账号，字母账号会被误当成昵称）。
    """
    best = ("", -1)
    for i in range(len(payload) - 1):
        b = payload[i]
        if not (0xA0 <= b <= 0xBF):
            continue
        n = b & 0x1F
        if n < 2 or i + 1 + n > len(payload):
            continue
        try:
            s = payload[i + 1:i + 1 + n].decode("utf-8")
        except Exception:
            continue
        if not all(c.isprintable() and not c.isspace() for c in s):
            continue
        if s.isdigit():
            continue
        raw = payload[i + 1 + n] if i + 1 + n < len(payload) else 1
        # 1=男；0=女（本地客户端实选「女」发的 00）；2 也按女处理
        sex = 1 if raw == 1 else 2
        best = (s, sex)
    return best


def _utf8_runs(b):
    """把包里连续的 UTF-8 片段挑出来（认昵称用）。"""
    out, cur, i = [], bytearray(), 0
    while i < len(b):
        c = b[i]
        n = 1 if 0x20 <= c < 0x7F else (2 if 0xC2 <= c <= 0xDF else
                                        (3 if 0xE0 <= c <= 0xEF else
                                         (4 if 0xF0 <= c <= 0xF4 else 0)))
        if n and i + n <= len(b):
            try:
                b[i:i + n].decode("utf-8")
                cur += b[i:i + n]
                i += n
                continue
            except Exception:
                pass
        if len(cur) >= 2:
            out.append(cur.decode("utf-8", "replace"))
        cur = bytearray()
        i += 1
    if len(cur) >= 2:
        out.append(cur.decode("utf-8", "replace"))
    return out



# 登录应答 cmd=17：本地 152B 模板的装备区只有 45 个 0，跟真服结构对不上
# （真服有装备的号是 197B）。硬拼字节会让长度漂移 → 客户端解析错位。
# 改用真服 197B 样本原包做底，只做「等长字段替换」，长度恒 197B。
#
# 真服 197B 逐字节结构（2026-11-23 实测样本）：
#   0..106    头部：名字 / 等级 / 职业 / 各属性
#   107..109  战力 varint（cd03fc = 1020）
#   110..145  槽 1..6，每条 6B = [id 3B (0xCD+2B)][槽位][01][01]
#   146..161  16 个 0x00
#   162..167  槽 7（武器）6B
#   168..171  4 个 0x00
#   172..176  外观 5B = 0xCE + 外观 ID 4B（ce00061a82 = 400002）
#   177..195  19 个 0x00
#   196       0xA0 结束
TPL17_LIVE = bytes.fromhex(
    "0ca731373834333636a6e8908ce696b001010a00ccd2cd1685cd03fd1507cd02bc0000000500ce00232800cd0fd200cd06d50000cd03fb000000010000cd03fc0000000001000000cd2166cd04f7ccd2cc97ccb9ccaa353d3200133204026602020200000000000000ccf0cd03fccd2b64010101cd2b65020101cd2b66030101cd2b67040101cd2b68050101cd2b6906010100000000000000000000000000000000cd2b6a07010100000000ce00061a8200000000000000000000000000000000000000a0")


# ===== 建角（真服 2026-11-24 12:37-12:38 实测，账号 954259122）=====
# 结论先说：**「账号在、角色空」不是回 17 的变体，是回 cmd=1 的 `01 a0`。**
#   实测时序：
#     C->S cmd=17  len=60    登录
#     S->C cmd=1   len=2     01 a0   ← 客户端收到就切 CreateRoleScene
#     C->S cmd=20  len=73    建角：01 01 02 + sfield(账号) + da 00 2b + 43B token
#                                    + sfield(昵称) + [性别 01男/02女]
#     S->C cmd=20  len=160   建角应答：14 + sfield(账号) + sfield(昵称) + [性别] + [职业 06]
#                                    + 固定尾 134B
#     S->C cmd=113 len=34 / cmd=117 len=1（不带 seq 的推送）
# 之前整整一天在猜「截断 17」「17 里昵称置空」，方向全错，别再回去。
CREATE_ROLE_TPL = os.path.join(HERE, "res_cfg", "create_role_tpl.json")


def _load_create_tpl():
    try:
        with open(CREATE_ROLE_TPL, encoding="utf-8") as f:
            d = json.load(f)
        return bytes.fromhex(d["resp20_tail"]), d.get("resp20", "")
    except Exception as e:
        log("[create] 建角模板读取失败（%s），建角应答会退化为空包" % e)
        return b"", ""


CREATE_ACK_TAIL, _CREATE_TPL_RAW = _load_create_tpl()
# 建角应答之后真服立刻推的场景对象表（34B）与 117（1B）。照抄，别自己拼。
CREATE_PUSH_113 = bytes.fromhex(
    "6503ce0001b1fd0f0000ce0001b2c51f0100ce0001b32a2f000001ce00018aed330a")
CREATE_PUSH_117 = b"\x00"


def build_20_ack(acct, name, sex=1, cls=6):
    """建角应答 cmd=20（160B 样本 = 1+10+13+2+134）。

    结构和真服逐字节对齐：0x14 开头 + 账号 + 昵称 + 性别 + 职业 + 固定尾。
    尾里那些数字（等级/属性/坐标）是 Lv1 未转职号的现场值，直接用。
    """
    return (b"\x14" + sfield(acct or "") + sfield(name or "")
            + bytes([int(sex) & 0xFF, int(cls) & 0xFF]) + CREATE_ACK_TAIL)


def build_17(scene=None):
    """登录应答 17：在真服 197B 原包上等长替换装备 id 与外观 ID。
    物品 id 一律用 3 字节定长 varint（0xCD + u16），空槽写 0，
    这样无论穿几件包长都恒为 197B，不会因为 varint 变长/变短错位。

    ★ 头部三个字段也必须动态化（2026-11-23 17:40 修）：
    模板是抓包当时那个 10 级号的快照，off16=职业、off18=等级、
    off22/23:25 = 0xCD + u16 经验。以前只替换装备槽，结果新角色一登录
    就带着模板里的 10 级/旧职业/5765 经验，28 包再下发真实等级 →
    两包互相矛盾，客户端 UI 状态机错位，表现为「提交个任务突然变成
    转职后的状态」并闪退。三处都是单字节/定长替换，长度恒 197B。

    ★ 2026-11-24：账号与昵称改为动态拼，并按位移重算所有偏移。
    以前整包是模板原样拷贝，昵称恒为「萌新」——所有号在游戏里都叫萌新，
    后台建角改了名字也不会显示。现在 b = 0x0c + 账号 + 昵称 + 模板[16:，
    偏移按 shift = (账号字段长-8) + (昵称字段长-7) 平移，主号(7位账号+"萌新")
    的 shift=0，与历史行为逐字节一致。
    """
    acct = cur().acct or PRIMARY_ACCOUNT
    name = cur().name or ""
    af = sfield(acct)
    nf = sfield(name) if name else b"\xa0"
    shift = (len(af) - 8) + (len(nf) - 7)
    b = bytearray(b"\x0c" + af + nf + TPL17_LIVE[16:])

    def at(o):
        return o + shift

    off = at(110)
    for slot in (1, 2, 3, 4, 5, 6):
        iid = cur().equip.get(slot, 0) & 0xFFFF
        # ★ 17 是**定长 6B 步进**结构：客户端固定按 [id3B][槽号][01][01] 读，
        #   第 4 字节当槽号。2026-11-24 15:0x 曾把空槽整项写 6 个 0，
        #   客户端读到「槽号 0」直接越界 → 登录即闪退（c008 17(202B) 后对端关闭）。
        #   所以这里只替换 id，槽号/01 01 保留模板值，绝不改长度与槽号。
        b[off:off + 3] = b"\xcd" + iid.to_bytes(2, "big")
        off += 6
    b[at(162):at(165)] = b"\xcd" + (cur().equip.get(7, 0) & 0xFFFF).to_bytes(2, "big")
    # 外观槽：翅膀模型 ID 由 Wing.xml 反查（wing_proto.model_of）。
    # 41002 天使羽翼形象 -> 400002；40002 蜻蜓点水 -> 40002；41012 -> 400012。
    ap = (wing_proto.model_of(cur().wing[0]) or 0) & 0xFFFFFFFF
    b[at(172)] = 0xCE
    b[at(173):at(177)] = ap.to_bytes(4, "big")
    # 头部：性别 / 职业 / 等级 / 经验，与 28 包保持同一套真值，避免两包打架。
    # ★ 2026-11-24 14:0x：模板头 16/17/18 的真实语义是
    #     rel0(16)=性别(1男/0女)、rel1(17)=职业、rel2(18)=等级
    #   实证两份真服 17：
    #     主号 1784366「萌新」Lv10御剑  0c a7"1784366" a6"萌新" 01 01 0a 00 cc d2 …
    #     新号 954259122「夏侯健远」Lv1 0c a9"954259122" ac"夏侯健远" 01 06 01 30 1f …
    #   以前把职业写进 16（性别位）、职业位留着模板值 06/01，
    #   于是新号一收到 17/28 就被读成「御剑男」，升级后模型直接变转职样。
    _sex_b = 1 if int(cur().sex or 1) == 1 else 0
    b[at(16)] = _sex_b
    b[at(17)] = cur().cls & 0xFF
    b[at(18)] = cur().level & 0xFF
    b[at(22)] = 0xCD
    b[at(23):at(25)] = min(cur().exp, 0xFFFF).to_bytes(2, "big")
    # 战力 off107..109：真服 29.field25 与 17 这个字段恒等（948 / 1020 同号实测），
    # 模板里写死的 cd03fc=1020 会让战力永远停在 1020。定长 CD+u16，长度不变。
    b[at(107)] = 0xCD
    b[at(108):at(110)] = (equip_attr.power_of(panel_attr("hp_max")) & 0xFFFF).to_bytes(2, "big")
    # ★ 场景 id（off25，定长 CD+u16）：模板恒为 1021 天音谷底。
    #   客户端收到 17 会**先按这个场景把地图和怪物建出来**，之后 113 到了
    #   再把对象叠上去 —— 于是 101 的「仙界守卫 Lv.1」(111101) 和
    #   1021 的「作乱小妖 Lv.13」(121302, Scene.xml 1021.spawn_points)
    #   同时出现在蓬莱仙境里，还多出一只没名字的熊猫。
    #   101 写成 cd 00 65（与 cd 03 fd 同长度，定长结构不破）。
    _sc = int(scene if scene is not None else (cur().scene or DEFAULT_SCENE)) & 0xFFFF
    b[at(25)] = 0xCD
    b[at(26):at(28)] = _sc.to_bytes(2, "big")
    # ★ 落点坐标（off28/29，各 1 字节）：与场景同源 —— 场景对得上才用存档坐标，
    #   否则用本场景默认落点(5,7)。这样「老号回下线地点」和「新号在蓬莱出生」
    #   是同一条规则的两个分支，不会出现「场景是塔、坐标却是城外」的错位。
    if _sc == int(cur().scene or DEFAULT_SCENE):
        _px = int(getattr(cur(), "pos_x", 21) or 0) & 0xFF
        _py = int(getattr(cur(), "pos_y", 7) or 0) & 0xFF
    else:
        _px, _py = 5, 7
    b[at(28)] = _px
    b[at(29)] = _py
    return bytes(b)

def build_306(cls=None):
    """306 技能快捷栏：[数量] + n×([CD+u16 技能ID] + 5×00) + 00。

    真服实证（19:20~19:21 同一次登录内从 1 条变 2 条）：
      未转职 / 模板  : 02 + 60101 + 60201  (18B)
      御剑 Lv10      : 01 + 10101          (10B)
      御剑 Lv12      : 02 + 10101 10201    (18B)
    => 技能不是「转职给一条」，而是**按等级自动解锁**（SkillLearn.xml 的 min_level）。
    以前写死成 cls*10000+101，升到 12 级也不会多出「九天剑阵」，技能栏永远只有一招。
    """
    if cls is None:
        cls = cur().cls
    sks = skills_for(cls, cur().level)
    if not sks:                       # 表没读到就退回老规则，别把技能栏清空
        if 1 <= int(cls) <= 5:
            sks = [int(cls) * 10000 + 101]
        else:
            sks = [60101, 60201]
    out = bytearray([len(sks)])
    for sk in sks:
        out += venc(sk) + b"\x00" * 5
    out += b"\x00"
    return bytes(out)


def synth_cost(out_id):
    """合成费用（铜钱）。Formula.xml 里就写着 cost，不用猜。

    真服 19:23:02 实测：初级灵石 x4 -> 中级灵石，28 包 idx18 绑定铜钱
    2252500 -> 2244500，正好 -8000，与 Formula.xml 的 cost=8000 完全一致。
    该号铜钱=0，所以实际扣的是绑定铜钱 → 先扣铜钱、不足再扣绑铜。
    """
    f = equip_attr.FORMULA.get(int(out_id))
    return int(f["cost"]) if f else 0


def synth_apply(mat, times, per):
    """强化石合成：扣 times*per 个 mat + 铜钱，产出 times 个高一档石头。

    返回 (产物id 或 None, 说明)。只动 cur().bag/货币，不碰网络，方便单测。
    """
    mat = int(mat)
    out = STONE_UP.get(mat)
    if not out:
        return None, "该材料不能合成"
    times = max(1, int(times))
    per = max(1, int(per))
    # 配方以 Formula.xml 为准（20141,4 / cost 8000 这类）
    f = equip_attr.FORMULA.get(int(out))
    if f:
        fper = f["mats"].get(mat)
        if fper:
            per = int(fper)
    need = times * per
    have = sum(v[1] for v in cur().bag.values() if v[0] == mat)
    if have < need:
        return None, "材料不足（需%d 有%d）" % (need, have)
    _cost = synth_cost(out) * times
    _pay_tq = min(cur().tongqian[0], _cost)
    _pay_bc = _cost - _pay_tq
    if cur().coin_bound[0] < _pay_bc:
        return None, "铜钱不足（需%d 铜钱%d+绑铜%d）" % (_cost, _pay_tq, _pay_bc)
    left = need
    for p in sorted(cur().bag):
        if left <= 0:
            break
        if cur().bag[p][0] != mat:
            continue
        take = min(cur().bag[p][1], left)
        cur().bag[p][1] -= take
        left -= take
        if cur().bag[p][1] <= 0:
            del cur().bag[p]
    cur().tongqian[0] -= _pay_tq
    cur().coin_bound[0] -= _pay_bc
    bag_add(out, times)
    return out, "消耗 %s x%d -> %s x%d，扣铜钱 %d（铜%d+绑铜%d，余绑铜%d）" % (
        item_name(mat), need, item_name(out), times, _cost,
        _pay_tq, _pay_bc, cur().coin_bound[0])


def vread(b, i):
    """读一个 varint，返回 (值, 新下标)。"""
    c = b[i]
    if c == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    if c == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if c == 0xCC:
        return b[i + 1], i + 2
    return c, i + 1


def _enc30(v):
    """30 包内的整数编码：<0x80 单字节 / cc / cd / ce+4B（与协议一致）。"""
    v = int(v)
    if v < 0x80:
        return bytes([v])
    if v <= 0xFF:
        return b"\xcc" + bytes([v])
    if v <= 0xFFFF:
        return b"\xcd" + v.to_bytes(2, "big")
    return b"\xce" + (v & 0xFFFFFFFF).to_bytes(4, "big")


def build_30():
    """cmd=30 外观/装备展示包：7 个装备项，**变长**（真服 80/82/86/88/96 都出现过）。

    ★ 空槽的编码是这次的关键（2026-11-24 15:1x，真服 20:46:30 的 80B 原包）：
        有装备  `cd + u16 id` + `槽号` + `01 01`   → 6B
        空槽    `00 00 00 00`                      → 4B（没有槽号、没有 01 01）
      以前空槽写 `cd 00 00` + `槽号` + `01 01`，长度虽然是 6B，但客户端把它当成
      **一件 id=0 的有效外观**，于是地图上画出一个默认模型（就是那只熊猫/
      称号外观），而战斗里 151 已经不带宠物，所以只有地图上有。
      同时 400501 给神装后会推 30，槽 7 被写成 id=0 → 头顶的称号跟着消失。
    空槽改成 4 个 0 之后，客户端才会读成「这个槽是空的」。
    7 个槽的排布与真服一致：6 个装备槽 + 16 字节填充 + 槽 7 + 24 字节填充。
    """
    tpl = TPL_PUSH.get(30)
    if not tpl or len(tpl) < 58:
        return None
    out = bytearray()
    for slot in (1, 2, 3, 4, 5, 6):
        iid = cur().equip.get(slot, 0) & 0xFFFF
        if iid:
            out += b"\xcd" + iid.to_bytes(2, "big") + bytes([slot]) + b"\x01\x01"
        else:
            out += b"\x00\x00\x00\x00"
    # ---- 首饰区：4 个槽（戒指 9/10、护符 11/12）----
    # 真服 82B 原包里这 16 字节全 0 = 4 个空首饰槽（空槽编码 4B，与上面空装备槽同理）。
    # 真服 96B 样本（23:52:25）在同一位置是 4 个实槽，顺序 11/12/9/10：
    #   cd2842 0b 0101 / cd2842 0c 0101 / cd2841 09 0101 / cd2841 0a 0101
    # 所以 30 **本来就是变长**的（80/82/86/88/96 都出现过），不能锁死 82B。
    # 早先为了「保持 82B」去裁尾部填充，等于把后面的字段整体前移，
    # 客户端按尾部结构解析就会读错 —— 表现为穿戒指当场闪退。
    # 现在按真服语义来：有货 6B、空槽 4B，尾部填充保持 24B 不动。
    for _as in (11, 12, 9, 10):           # 与真服 96B 原包顺序一致（见 CELL_SLOTS）
        _aid = cur().equip.get(_as, 0) & 0xFFFF
        if _aid:
            out += b"\xcd" + _aid.to_bytes(2, "big") + bytes([_as]) + b"\x01\x01"
        else:
            out += b"\x00\x00\x00\x00"
    iid7 = cur().equip.get(7, 0) & 0xFFFF
    if iid7:
        out += b"\xcd" + iid7.to_bytes(2, "big") + b"\x07\x01\x01"
    else:
        out += b"\x00\x00\x00\x00"
    # ---- 尾部：真服固定 17 槽结构（2026-11-25 23:24~23:26 实时抓包逐包解析）----
    # 全空基准 72B = 11 个装备槽块(11×4) + 空块(4) + 翅膀块(8) + 空块×3(12) + 坐骑块(4)
    #   74B = 只剩坐骑     76B = 护符+坐骑    82B = 4 首饰+坐骑
    #   92B = 2 槽空       96B = 11 件全满 + 翅膀 + 坐骑
    # 老实现尾部写死 24 个 0 = 68B，比真服少 4 字节，整个尾部前移，
    # 客户端按 17 槽解析会读串 —— 表现就是「装备穿上了、面板还是空的」。
    out += b"\x00\x00\x00\x00"                     # 第 12 块：空
    _wing = (cur().wing[0] if getattr(cur(), "wing", None) else 0) or 0
    _wing = int(_wing) & 0xFFFFFFFF
    if _wing:
        # 真服：`ce00061a82 00 00 00` = 天使羽翼 400002 + 槽 0 + 00 00
        out += _enc30(_wing) + b"\x00\x00\x00"
    else:
        out += b"\x00\x00\x00\x00"
    out += b"\x00" * 12                            # 第 13~15 块：空
    _mount = (cur().mount[0] if getattr(cur(), "mount", None) else 0) or 0
    _mount = int(_mount) & 0xFFFF
    if _mount:
        # 真服：`cd2733 0d 01 01` = 筋斗云 10035 + 槽 13
        out += b"\xcd" + _mount.to_bytes(2, "big") + b"\x0d\x01\x01"
    else:
        out += b"\x00\x00\x00\x00"                 # 第 16 块：坐骑槽
    return bytes(out)


def build_818(pairs):
    """装备栏应答 818：每条 28 字节 = [槽][物品 id][01][21 个 0]。

    21 个 0 是强化/洗炼/宝石/套装位，真服全空时就是 0，
    客户端拿物品 id 自己去 Item.xml 查名字和基础属性。
    """
    out = bytearray([len(pairs) & 0xFF])
    for slot in pairs:
        iid = cur().equip.get(slot, 0) & 0xFFFF
        # 每条恒 28B = [槽位 1B][物品 id 3B 定长 varint][0x01][23 个 0x00]
        # 真服 7 条 = 197B、1 条 = 29B，长度必须严格对上。
        # offset 6 是强化等级（真服强化前后只差这一字节：00 -> 01）。
        rec = bytearray(bytes([slot & 0xFF]) + b"\xcd" + iid.to_bytes(2, "big")
                        + b"\x01" + b"\x00" * 23)
        rec[6] = cur().enhance.get(slot, 0) & 0xFF
        out += rec
    return bytes(out)


PROBE_820_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "probe_820.hex")
_probe_cache = {"mtime": None, "data": None}


def probe_820():
    """820 结构实验开关：读 probe_820.hex（十六进制，# 后面当注释）。

    文件不存在 → 返回 None，走原来的「818 全量 + 28」兜底，不发 820。
    文件存在 → 在 818 之后再补一个 cmd=820，包体就是文件里的字节。

    每次请求都看 mtime，改文件立即生效，不用重启服务端。
    用途：820 没有正样本，空包和 00 都会让客户端 SIGABRT，
    只能靠「给一坨字节 → 看客户端活不活/显示什么」来二分。
    """
    try:
        st = os.stat(PROBE_820_PATH)
    except OSError:
        return None
    if _probe_cache["mtime"] == st.st_mtime:
        return _probe_cache["data"]
    try:
        with open(PROBE_820_PATH, "r", encoding="utf-8",
                  errors="ignore") as f:
            raw = f.read().split("#", 1)[0]
        hexs = "".join(c for c in raw if c in "0123456789abcdefABCDEF")
        if len(hexs) % 2:
            hexs = hexs[:-1]
        data = bytes.fromhex(hexs)
        _probe_cache.update(mtime=st.st_mtime, data=data)
        log("[equip] probe_820.hex 已加载 len=%d hex=%s"
            % (len(data), data.hex()[:64]))
        return data
    except Exception as exc:
        log("[equip] probe_820.hex 解析失败：%s" % exc)
        return None


def build_838():
    """衣柜应答 838（真服 13:50:37 样本 65B）：

        09 | 9 × [0xCE + u32 时装id][0x00][0xFF] | 00

    即 [数量] + 每件 7 字节定长条目 + 末尾 1 字节 0。
    样本里的 90033(快乐圣诞 男) / 90053(白色浪漫 男) 就是 Item.xml 里的
    时装 item_id，所以直接放 item_id，不要再折算对象号。
    """
    items = sorted(cur().wardrobe)
    out = bytearray([len(items) & 0xFF])
    for iid in items:
        out += b"\xce" + (iid & 0xFFFFFFFF).to_bytes(4, "big") + b"\x00\xff"
    out += b"\x00"
    return bytes(out)


def build_28():
    """动态构造 cmd=28 角色属性快照（73B，结构见 PLAYER_28_TPL 注释）。

    货币顺序固定（真服 96 包回归，文档第 19 节）：
        idx18 绑定铜钱 / idx19 绑定元宝 / idx20 铜钱 / idx21 元宝
    字节偏移会随数值宽度变化（cd=2B / ce=4B），所以不能按固定偏移写，
    统一 tokenize -> 改值 -> 整包重编码，包长自动正确。
    """
    b = bytearray(PLAYER_28_TPL)
    # ★ 2026-11-24 12:58：账号/昵称必须动态拼。
    # 模板里写死 a7"1784366" a6"萌新"，建角出来的新角色在场景里
    # 名牌还挂着「萌新」（用户实测），因为 28 才是客户端显示角色名的地方。
    # 与 build_17 同一套位移规则：shift = (账号字段长-8) + (昵称字段长-7)，
    # 主号 shift=0，与历史行为逐字节一致。
    _af = sfield(cur().acct or PRIMARY_ACCOUNT)
    _nf = sfield(cur().name) if cur().name else b"\xa0"
    shift = (len(_af) - 8) + (len(_nf) - 7)
    b = bytearray(b"\x0c" + _af + _nf + PLAYER_28_TPL[16:])

    def at(o):
        return o + shift

    # ★ 头部三连与 build_17 同一套语义（2026-11-24 14:0x 修正）：
    #   rel0=性别(1男/0女) rel1=职业 rel2=等级
    # 真服新号 954259122 的 28（64B）头就是 `01 06 01` = 男 / 新手6 / Lv1；
    # 主号（84B）是 `01 01 24` = 男 / 御剑1 / Lv36。
    # 以前 `b[16]=cls` 把职业塞进性别位、职业位留着模板的 01，
    # 新号交完第一个任务收到 28 就被刷成「御剑」，模型瞬间变转职样。
    _sex_b = 1 if int(cur().sex or 1) == 1 else 0
    b[at(16)] = _sex_b
    b[at(17)] = cur().cls & 0xFF
    b[at(18)] = cur().level
    b[at(25):at(27)] = min(cur().exp, 65535).to_bytes(2, "big")
    try:
        tk = grant.tokenize(bytes(b))
        # 之前的 b[41:43] 写法只覆盖了 u32 的低 16 位，
        # 真实值 = 0x0023xxxx + 低16位，会出现 2305802 这种鬼数字，别再用了。
        tk[18]["value"] = cur().coin_bound[0]
        tk[19]["value"] = cur().bind_yuanbao[0]
        tk[20]["value"] = cur().tongqian[0]
        tk[21]["value"] = cur().yuanbao[0]
        # idx22/23 = VIP 等级 / 剩余时长(0.1天)，真服 00:23:39 用卡实证（见 VIP_LEVEL 注释）
        tk[22]["value"] = cur().vip_level[0]
        tk[23]["value"] = vip_days10()
        # ！！千万别把 YUELI 写进 idx12 ！！
        # 2026-11-23 01:00 踩过：模板里 idx12 原本是 700，我按「idx12 可能是阅历」
        # 的猜测把它写成 0，结果客户端【一进战斗就闪退】——
        #   01:02:05 call cmd=145 -> got 145 -> got 151 -> 进程直接没了
        #   tombstone: SIGSEGV / null pointer dereference in GLThread
        # 真服 96 个 28 包里 idx12 只取过 0/100/500/600/700，看着像阅历，
        # 但它同时被战斗初始化用到，清零就崩。阅历先只在服务端记账，不下发。
        return grant.render(tk)
    except Exception as e:                 # 兜底：宁可少改货币，也别把属性包搞坏
        log("[28] 货币写入失败，回退原模板：%s" % e)
        return bytes(b)


# ---- 装备强化属性表（Equip.xml strengthen_N）----
# strengthen_N 是「累计值」不是增量：strengthen_3 描述的是 +3 那一刻的总属性。
# 真服 18:20:20~18:30:58 连续 16 次强化的 29 包逐条反推（误差 0）：
#   战力  = 基数 + Σ已穿装备 strengthen_当前等级[53]
#   攻下限= 基数 + Σ[51]   攻上限= 基数 + Σ[52]   防御= 基数 + Σ[7]
#   强化汇总(12/14/15/16) = 基数 + Σ[57]（13 = 12 + 100）
# 校验：18:30:58 全套(六件+3/11113+1) 实测 29 = [1594,210,190,235,230,...]，
#   按上式算 1271+323=1594 / 171-20+39=190 / 210-25+50=235 / 170+60=230 / 2+162=164
#   —— 五个字段全中，所以这套算法可以放心用于本地服。
#
# 2026-11-23 补充（用户反馈「+3/+6/+9/+12 有套装强化加成」）：
#   配置里确实还有三套加成，之前都没实现：
#     ① strengthen_N_gift   单件礼物档（武器 +5/+6/+7、防具 +8~+12）
#     ② StrengthenAddOn.xml 强化大师（按全身最低强化等级取档，7~12 有数据）
#     ③ Suite.xml           套装件数效果（2/3/4/6 件）
#   全部交给 equip_attr.py 算，这里只取合计。
POWER_ATTR = equip_attr.POWER_ATTR   # 53 = 生命（旧名沿用，语义见 equip_attr）
EQUIP_STR = equip_attr.EQUIP_STR     # 兼容旧引用：(iid,lv) -> 属性表（同一份 dict）


def _load_equip_str():
    """读 Equip.xml / StrengthenAddOn / Suite / Formula / Mall（一次到位）。"""
    equip_attr.init()
    return EQUIP_STR_SRC[0]


EQUIP_STR_SRC = [equip_attr.EQUIP_XML]


def all_attrs():
    """全身强化相关属性合计：strengthen + gift + 强化大师 + 套装件数。

    四个首饰槽是四件独立物品（真服 23:25:24~29 四个 182 各占一格，
    178 里也是四个独立包格），所以每格都要算，没有「配对槽去重」这回事。
    以前剔掉 10/12 是因为把一件首饰写进了两格；现在一格一件，
    再剔就会少算玩家真正戴着的第二枚戒指/护符。
    """
    eq = dict(cur().equip.items())
    return equip_attr.total_attrs(eq, cur().enhance)


def enh_power_add():
    """当前装备强化带来的生命加成（attr53 合计）。"""
    return int(round(all_attrs().get(POWER_ATTR, 0.0)))


def enh_breakdown():
    """日志用：把战力加成的来源拆开，方便对真服数字。"""
    a = equip_attr.sum_strengthen(cur().equip, cur().enhance)
    g = equip_attr.sum_gift(cur().equip, cur().enhance)
    ad, tier = equip_attr.addon_effect(cur().equip, cur().enhance)
    su, sid, n = equip_attr.suite_effect(cur().equip)

    def _p(d):
        return int(round(d.get(POWER_ATTR, 0.0)))

    return ("强化%d + 礼物%d + 大师%d(档%d) + 套装%d(套%s/%d件)"
            % (_p(a), _p(g), _p(ad), tier, _p(su), sid or "-", n))


def _tpl_tokens(cmd):
    """把模板包按 varint 拆成字段表（29 这类纯数值包专用）。"""
    b = TPL_PUSH.get(cmd)
    if not b:
        return None
    out = []
    i = 0
    while i < len(b):
        try:
            v, i = vread(b, i)
        except Exception:
            return None
        out.append(v)
    return out


# 29 包字段下标（真服 26 字段，2026-11-23 20:30 实证修正语义）
FIELD_INDEX = {
    "hp_max": 0, "mp_max": 1, "attack_min": 2, "attack_max": 3, "defense": 4,
    "dodge": 5, "hit": 6, "crit": 7, "tenacity": 8, "spirit": 9, "agile": 10,
}


def min_enh_lv():
    """全身最低强化等级（29.field11 / 强化大师取档口径）。空装备栏算 0。"""
    vals = [int(v) for v in cur().enhance.values() if int(v) > 0]
    if not vals or len(vals) < len(cur().equip):
        return 0
    return min(vals)


def panel_now():
    """按当前等级 + 装备算出的 26 字段面板（和 build_29 同一份数据）。"""
    toks = _tpl_tokens(29) or []
    return equip_attr.compose29(toks, cur().level, all_attrs(),
                                cur().cls, min_enh_lv(), cur().mp_add[0]) or []


def panel_attr(key):
    out = panel_now()
    i = FIELD_INDEX.get(key)
    if not out or i is None or i >= len(out):
        return 0
    return int(out[i])


def power_now():
    """当前生命上限（29 包 field0）。

    ★ 2026-11-23 20:30 更正：Equip.xml 里的 53 号属性是**生命**，不是战力。
    真服 2 级新号 29.field0 = 76 = Levelup 2 级 hp_max，本地旧模板的 1716
    也正好 = Levelup 9 级 272 + 装备 1444。以前把它当战力取名，误导过一阵。
    """
    return panel_attr("hp_max")


def build_29():
    """动态构造 cmd=29 属性面板（真服 26 字段结构）。

    以前只改 field0，而且基础值是写死的 9 级数据 → 升级后面板纹丝不动
    （用户反馈「面板什么的都不加，都这么多级了」就是这个）。
    现在按 Levelup.xml 的当前等级重算 hp/mp/攻上下限/防/闪避/命中/暴击/
    精神/敏捷，再叠加装备强化，最后按真服结构补齐 [11..16]。
    """
    toks = _tpl_tokens(29)
    if not toks:
        return TPL_PUSH.get(29)
    agg = all_attrs()
    out = equip_attr.compose29(toks, cur().level, agg, cur().cls,
                               min_enh_lv(), cur().mp_add[0])
    if not out:
        return TPL_PUSH.get(29)
    try:
        return b"".join(venc(t) for t in out)
    except Exception:
        return TPL_PUSH.get(29)

# 战斗三件套（真服抓包原样，勿手改）
# 151 开战包：01 | 0c | [职业cls] | [0xA0|昵称长] 昵称 | 尾巴 26B
# 真服原包："010c06a6e8908ce696b001..." 里 06=职业、a6=0xA0|6 是「萌新」的长度标记；
# 以前整条写死，改名后战斗里显示的仍是「萌新」，所以拆成 头(动态) + 尾巴(定长)。
BATTLE_OPEN_TAIL = ("01004c4c2f2f00a00000000001"
                    "ce0001b1fd50500000020107")


def battle_open_body():
    """151 开战包（昵称长度可变，整包长度随之变化）。"""
    nm = (cur().name or "").encode("utf-8")[:31]
    return bytearray(b"\x01\x0c" + bytes([cur().cls & 0xFF, 0xA0 | len(nm)]) + nm
                     + bytes.fromhex(BATTLE_OPEN_TAIL))
# 148 回合结算：真服一次完整战斗的 6 个回合原样序列（按序回放）
# 最后一个是"最后一击"（len 32，结构不同），客户端收到后会发 149 结束
BATTLE_ROUNDS = [
    "000002010107cdeac50100020705000701f000000000070101ce00a986d5"
    "0100020105000101f30000000002010d01070d02",
    "000002010107cdeac50100020705000701f400000000070101ce00a986d5"
    "0100020105000101f30000000002010d01070d02",
    "000002010107cdeac50100020705000701f500000000070101ce00a986d5"
    "0100020105000101f30000000002010d01070d02",
    "000002010107cdeac50100020705000701f000000000070101ce00a986d5"
    "0000010101000000000002010d01070d02",
    "000002010107cdeac50100020705000701ef00000000070101ce00a986d5"
    "0100020105000101f30000000002010d01070d02",
    "010001010107cdeac50100030705000701f8070c000000000002010d01070d00",
]
# 150 结束
BATTLE_END = "0a00000000000000000000"


def extra_for(cmd):
    """热加载的人工响应表，格式：
        {"211": {"cmd": 212, "seq": "echo", "payload": "00"}}
    改完这个文件不用重启服务端，下一次请求就生效——调数据结构时省掉重登。"""
    try:
        raw = json.load(open(EXTRA_PATH, encoding="utf-8"))
        v = raw.get(str(cmd))
        if not v:
            return None
        return int(v.get("cmd", cmd + 1)), v.get("payload", ""), v.get("seq", "echo")
    except Exception:
        return None


# 真服（他的服）亲口承认没做的功能：decode_unimpl.py 统计自 relay.log，
# 这些请求真服一律回 cmd=1「本服尚未恢复此功能」。
UNIMPL_CMDS = {116, 22, 2134, 1125, 1134, 1384, 663, 1587}

# 迷宫/秘境：1859 进副本成功后置位，主循环据此把当前的 task=35 任务推成可交。
# 400802 回梦迷境的目标就是「进迷宫 1 次」（task="35,1"），入口门槛 20 级。
maze_ready = [False]


def builtin_resp(cmd, payload):
    """已实测通过的功能应答，不依赖 tpl_extra.json。

    cmd=211（接受任务）：请求 payload = [u8 0xCE][u32BE 任务ID]，实测
        ce 00 06 1a e5  →  任务ID 400101
    应答必须给足字节：
        只回 1 字节 → 客户端 SIGABRT（signal 6），36ms 内被杀；
        回 22 字节 → m_depth 归零、无 accept quest failed、对话正常关闭、
                     NPC 头顶「!」消失（任务确实接上了）。
    所以这里按「结果码 0 + 回填任务ID + 16 字节零填充」构造，既对齐又不写死任务号。
    """
    pass  # [player] 状态走 cur()，无需 global
    if cmd == 211:
        # 真服实证（20:43/20:49/20:52 三样本）：211 = 接任务，同号回显任务 ID。
        # 尾部 2 字节随任务类型变化：
        #   对话任务(task=4, 400803/400901) -> 01 01（接了即满足条件）
        #   其他任务(400902 采集 / 400101 打怪) -> 00 01
        qid = payload[:5] if len(payload) >= 5 else b"\xce\x00\x00\x00\x00"
        qid_int = int.from_bytes(qid[1:5], "big") if len(qid) == 5 else 0
        tail = b"\x01\x01" if QUEST_TASK.get(qid_int) == 4 else b"\x00\x01"
        return 211, qid + tail
    if cmd == 214:
        # 真服实证（20:46/20:50/20:54 三样本）：214 = 交任务，同号**空应答**，
        # 随后服务端按序推：29(货币) -> 28(角色属性,动态) -> 30 -> [178] -> 210(下一环进行中 8B)。
        # 28 必须动态（等级/经验/绑铜），在主循环特判处理（builtin_resp 只管固定应答），
        # 这里返回 None 让主循环走 214 专用分支。
        return None
    if cmd == 124:
        # 真服实证（15:52/15:34）：124 = 点击场景对象（NPC/采集点/掉落物）。
        # payload = [u8 0xCE][u32BE 对象ID]，如 ce00018ed6 = 天降奇石(102102)。
        # 正常交互应答 = 同号 4 字节 00 ff 00 00；
        # 条件不满足时改回 cmd=1 提示（如「当前任务不需要采集此物」）。
        return 124, b"\x00\xff\x00\x00"
    if cmd == 35:
        # 真服实证（15:39:02）：35 = 转职确认，payload 单字节 = 目标职业 ID。
        #   C->S 35 len=1 hex=01（御剑）/ 04（法尊）/ 02（逍遥）—— 与 Class.xml 的
        #   id 1御剑 2逍遥 3幻羽 4法尊 5破军 完全对应。
        #   S->C 36 同号空包，随后推 29/28/30/178 刷新角色属性。
        # 此前只回 36 不写职业 → 客户端界面切了，服务端 28 里 [16] 仍是旧职业，
        # 表现就是「转职完进战斗还是原来的人物」。
        new_cls = payload[0] if payload else cur().cls
        if 1 <= new_cls <= 5:
            if new_cls != cur().cls:
                log("[class] 转职 %d -> %d（%s）" % (cur().cls, new_cls,
                      {1: "御剑", 2: "逍遥", 3: "幻羽", 4: "法尊", 5: "破军"}.get(new_cls, "?")))
            cur().cls = new_cls
        save_state()
        # 真服 15:39:02 转职样本只有 29 -> 28 两个推送，没有 30/178。
        # 多推 30（外观包）会在转职瞬间用旧装备快照重刷一次人物，
        # 客户端那里看起来就是「刚换的职业皮肤又被刷回去」，
        # 所以这里严格对齐真服，只跟 29/28。
        return 36, b"", [29, 28]
    if cmd == 116:
        # 真服实证（15:38:58 / 14:52:45 等多次）：116 → 同号 cmd=1 toast，
        # 内容固定 29B（「此功能服务器暂未恢复」类提示）。
        # UNIMPL_CMDS 是死代码（主循环从未引用），此前 116 被静默丢弃。
        # 注意：cmd=1 toast 只在进场景后的游戏内回是安全的；
        # 加载期回 cmd=1 会闪退（见主循环注释），116 只会在游戏内出现。
        #
        # ★ 2026-11-24 18:4x 真机实测：116 = 点击目标（怪/NPC）的「自动寻路 + 交互」。
        # 回 toast 之后客户端只发 115 挪了两步就停，**永远不发 145**，
        # 于是玩家点怪打不了怪（雷电 + 他服 MuMu 两边都一样）。
        # 这里先用环境变量做开关：BATTLE_116_ACK=1 时回同号空包，让交互继续走，
        # 观察客户端贴近后会不会自己发 145。默认仍是 toast（与真服逐字节一致）。
        if os.environ.get("BATTLE_116_ACK") == "1":
            log("[116] 回同号空 ACK（BATTLE_116_ACK=1）payload=%s" % payload.hex())
            return 116, b""
        return 1, bytes.fromhex(
            "04bbe69cace69c8de5b09ae69caae681a2"
            "e5a48de6ada4e58a9fe883bd")
    if cmd == 1857:
        # 1857 → 1858：迷宫/秘境面板查询。
        # 真服 18:11:23 / 18:11:49 / 18:11:58 三次点开都是 `1858 len=1 hex=02`，
        # 字节恒定为 02（推断为今日剩余次数 2 次，与 400802 对白「一日只能进 2 次」吻合）。
        # 以前这里完全没处理 → 本地服点迷宫面板是静默丢弃，界面一直转圈。
        return 1858, b"\x02"
    if cmd == 1859:
        # 1859 = 选档进入秘境/迷宫（payload 单字节：01 / 03 各档）。
        # 真服 18:11:56 / 18:12:00 / 18:12:03 实证：等级不够时**不换场景**，
        # 只回 cmd=1 文本「此模式请达到20级后再来」（36B，04da0020 + 32B UTF-8）。
        # 这是进场景之后的交互，回 cmd=1 不会触发加载期闪退。
        # ★ 2026-11-24 16:4x：门槛从 20 降到 8。
        # 任务 400802「回梦迷境」show_level=8、task=35（进迷宫1次），
        # 但真服这里回的是「此模式请达到20级后再来」——任务 8 级可接却要
        # 20 级才能做，玩家会卡在「提交不了」。本地服按任务开放等级放行。
        if cur().level < 8:
            log("[maze] 1859 档位%s 等级 Lv%d < 8 -> 拒绝进入"
                % (payload.hex() or "-", cur().level))
            return 1, err_text("此模式请达到20级后再来")
        # >=20 的成功包还没有正样本（真服那个号只有 Lv10，进不去）。
        # 但 400802 的目标是「进迷宫 1 次」（task="35,1"），
        # 所以这里把「点了进副本」直接当成目标达成：同号空 ACK 关掉面板，
        # 再由主循环把当前 task=35 的任务推成可交，任务链就能往下走。
        maze_ready[0] = True
        log("[maze] 1859 档位%s 等级 Lv%d 达门槛 -> 记为已进迷宫（待真服正样本校准场景切换）"
            % (payload.hex() or "-", cur().level))
        return 1859, b""
    if cmd == 37:
        # 37 = 点装备栏某一格。载荷是该格在**客户端面板上的下标（0 基）**，
        # 不是槽位号 —— 2026-11-25 22:32~22:33 真机 4 连击直接验证：
        #   22:32:50 hex=03 → 槽 4 逍遥护手 ✓   22:32:51 hex=02 → 槽 3 ✓
        #   22:32:53 hex=01 → 槽 2 ✓           22:32:54 hex=00 → 槽 1 ✓
        # 主装备 0~6 = 槽 1~7，这没歧义；首饰 4 格（7~10）才是坑：
        # 真服 96B 的 30 原包首饰顺序是 11,12,9,10，面板画的就是
        # 「护符 / 护符 / 戒指 / 戒指」，所以下标 8 是护符 12、下标 9 是戒指 9。
        # 以前按「格号 + 1」当槽位：hex=08 → 槽 9 → 点护符却脱了戒指，
        # 就是玩家反馈的「戒指和护符弄错了」。映射统一走 CELL_SLOTS。
        raw = payload[0] if payload else 0
        slot = CELL_SLOTS[raw] if raw < len(CELL_SLOTS) else 0
        have = cur().equip.get(slot) if slot else 0
        if have:
            for _s in acc_slots_of(slot):
                cur().equip.pop(_s, None)
            bp = bag_add(have, 1)
            save_state()
            log("[equip] 37 第%d格 -> 槽%d 卸下 %s(id=%d)，回背包格%d（装备 %s）"
                % (raw, slot, bag_proto.item(have)["name"], have, bp,
                   "/".join("%d:%d" % (s, cur().equip.get(s, 0) or 0)
                            for s in sorted(cur().equip)) or "空"))
        else:
            log("[equip] 37 第%d格 -> 槽%d 没有装备，只回 ACK（装备 %s）"
                % (raw, slot,
                   "/".join("%d:%d" % (s, cur().equip.get(s, 0) or 0)
                            for s in sorted(cur().equip)) or "空"))
        # 真服 37 完整回包（23:24:38 起 11 次逐格实测，全部一致）：
        #   29(属性变小) -> 28 -> 30(外观少一件) -> 178(背包多一件)
        #   -> 37 空 ACK(同 seq) -> 306
        # 37 就是**真的卸下**，而且必须推 30 客户端才会重绘面板和模型。
        # 以前只推 818/29/28/178，30 不推，面板停在「已穿上」的旧状态。
        return 37, b"", [29, 28, 30, 178]
    if cmd == 819:
        # 819 = 客户端对装备栏某一格的操作请求，真服载荷是 [物品 id varint][槽位]：
        #   23:52:30 C->S cmd=819 len=4 hex=cd28420b → 护符 10306 / 槽 11
        #   23:52:31 C->S cmd=819 len=4 hex=cd28420c → 槽 12（另一件护符）
        # 真服只会回 cmd=1 的文本包（04 bb + 「本服尚未恢复此功能」，len=29），
        # **从不回 cmd=819 本身**。本地照回 819 空包时，客户端刚收到就
        # SIGABRT 把进程带走（23:41:01，Zygote: exited due to signal (6)）——
        # 它没有「819 成功」这条分支，空包体解析直接断言。
        # 所以这里：本地照玩家预期真的卸下（只卸点中的那一格），
        # 但回包用线上服同形的 cmd=1 文本，绝不发 819 应答。
        slot = payload[-1] if payload else 0
        iid = 0
        if payload:
            if payload[0] == 0xCE and len(payload) >= 5:
                iid = int.from_bytes(payload[1:5], "big")
            elif payload[0] == 0xCD and len(payload) >= 3:
                iid = int.from_bytes(payload[1:3], "big")
            elif payload[0] == 0xCC and len(payload) >= 2:
                iid = payload[1]
            else:
                iid = payload[0]
        have = cur().equip.get(slot)
        if have and (not iid or have == iid):
            cur().equip.pop(slot, None)
            bag_add(have, 1)
            save_state()
            nm = bag_proto.item(have)["name"]
            log("[equip] 819 槽%d 卸下 %s(id=%d) → 回 cmd=1 文本 + 29/28/30/178"
                % (slot, nm, have))
            return 1, err_text("已脱下%s" % nm), [29, 28, 30, 178]
        log("[equip] 819 槽%d 没有可卸装备（payload=%s），只回文本"
            % (slot, payload.hex()))
        return 1, err_text("这个位置没有可脱下的装备"), []
    if cmd == 837:
        # 837 = 打开衣柜（C->S 空包）→ 838 已拥有时装列表。
        # 以前这个号完全没处理：客户端发了 837 什么都没回来，衣柜界面就一直转圈。
        # 包体格式用真服 13:50:37 的 65B 样本对齐（见 build_838）。
        body = build_838()
        log("[wardrobe] 837 -> 838 衣柜 %d 件 len=%d %s"
            % (len(cur().wardrobe), len(body), sorted(cur().wardrobe)))
        return 838, body
    if cmd == 177:
        # 177 = 打开背包 → 真服回 178 全量。
        # 以前这里没有分支，落到通用「cmd+1 有模板就回模板」那一档，
        # 而 TPL_PUSH[178] 是录制下来的**新号三件套**：
        #   绑定铜钱卡×10 / 10绑定元宝 / 5级新丁礼包（23B）
        # 于是每次开背包都把这三件盖到槽 0/1/2 上 —— 服务端根本没这些东西，
        # 点「使用」自然没反应；等打完怪 / 交任务推了动态 178，
        # 同一格子又变回强化仙玉，表现就是「东西会变成别的物品」。
        body = bag_proto.build_178(bag_view())
        log("[bag] 177 打开背包 -> 178 全量 %d 件 len=%d" % (len(cur().bag), len(body)))
        return 178, body, []
    if cmd == 181:
        # 181 = 打开 / 整理背包。真服回「同号 + [0x40][n] 全量」：
        #   23:35:47 打开背包 → 181 + 114B(16件)
        #   00:01:39 点整理   → 181 + 114B(16件，顺序重排)
        # 以前这里回的是 182 空包，客户端等 181 却拿到错号，本地排序函数
        # 拿不到列表直接 abort（signal 6）—— 「点整理闪退」的根因就在这。
        body = bag_proto.build_181(bag_view())
        log("[bag] 181 全量 %d 件 len=%d" % (len(cur().bag), len(body)))
        return 181, body, []
    if cmd == 182:
        # 182 = 对该格物品执行默认动作：[格子 1B][数量 1B]，数量 0 = 全部使用。
        # 真服 15:38 实证：182 00 01 用掉 1 张卡、182 00 00 把剩 9 张一次用光。
        # 以前只回空包 + 重放固定样本 → 道具不消失、数值也不加，表现就是「用了没反应」。
        try:
            pos, num = bag_proto.parse_182(payload)
        except Exception as e:
            log("[bag] 182 载荷异常 %s: %s" % (payload.hex(), e))
            return 1, err_text("背包数据异常"), []
        cell = cur().bag.get(pos)
        if not cell:
            # ★ 2026-11-24 17:2x：正服「开始旅程」发的就是 `182 00 01`
            # （背包 0 格是新手武器 11235，一键穿上）。本地服新号 0 格不是
            # 那件武器（空的或别的东西），以前直接回「此背包位置没有可使用物品」，
            # 客户端就弹「不可用」，新手流程卡在第一步。
            # 这里对 0 格静默成功，只刷新面板，不报错。
            if pos == 0:
                log("[bag] 182 00 %02x 视为「开始旅程」：该格无可用物品，静默成功 + 刷新" % num)
                return 182, b"", [29, 28, 178]
            return 1, err_text("此背包位置没有可使用物品"), []
        iid, have = cell
        use = have if num == 0 else min(num, have)
        if use <= 0:
            return 1, err_text("此背包位置没有可使用物品"), []

        # ★ 「开始旅程」固定发 `182 00 01`（正服背包 0 格是新手武器 11235，一键穿上）。
        # 本地服新号 0 格可能是强化石/空，既不能真消耗，也不能报「不可用」。
        # 不是装备/时装/翅膀就按「开始旅程」静默成功，只刷新面板。
        if pos == 0 and num == 1:
            _it0 = bag_proto.item(iid)
            if _it0.get("kind1") != 1 and _it0.get("kind2") not in (14, 15):
                log("[bag] 182 00 01 = 开始旅程（0 格是 %s，非装备）→ 静默成功 + 刷新"
                    % _it0["name"])
                return 182, b"", [29, 28, 178]
        eff = USE_EFFECTS.get(iid)
        follow = [29, 28, 30, 178]      # 使用成功后要跟的推送；翅膀会换成带 1922/1931/1932
        if iid in VIP_CARDS:
            # 真服 00:23:38 `182 1301` 使用 VIP半年卡 的完整时序：
            #   182 空ACK -> 29 -> 28(80B, idx22=4 idx23=1840) -> 30 -> 178 -> 306
            # 以前 20130 落到「VIP 系统尚未实现，道具已保留」，所以点卡没反应。
            days, lv = VIP_CARDS[iid]
            vip_grant(days, lv)
            follow = [29, 28, 30, 178, 306]
            log("[vip] 格%02x %s x%d -> 等级%d（idx22=%d idx23=%d 到期 %s）"
                % (pos, bag_proto.item(iid)["name"], use, cur().vip_level[0],
                   cur().vip_level[0], vip_days10(),
                   time.strftime("%Y-%m-%d", time.localtime(cur().vip_until[0]))))
        elif eff:
            key, per = eff
            amount = per * use
            add_purse(key, amount)
            log("[bag] 使用 格%02x %s x%d -> %s +%d（现有 绑铜%d 绑元%d 铜钱%d 元宝%d）"
                % (pos, bag_proto.item(iid)["name"], use, key, amount,
                   cur().coin_bound[0], cur().bind_yuanbao[0], cur().tongqian[0], cur().yuanbao[0]))
        elif iid in GIFTS:
            got = []
            for gid, gn in GIFTS[iid]:
                for _ in range(use):
                    bag_add(gid, gn)
                got.append("%s x%d" % (bag_proto.item(gid)["name"], gn * use))
            log("[bag] 开包 格%02x %s x%d -> %s"
                % (pos, bag_proto.item(iid)["name"], use, "、".join(got)))
        else:
            it = bag_proto.item(iid)
            if it["kind2"] == 15:
                # 翅膀 / 翅膀形象卡（kind2=15）。注意不能只看 use_kind：
                #   41012 孔明王翼形象 kind1=17 -> use_kind=wing_unlock
                #   40002 蜻蜓点水   kind1=1  -> use_kind=equip（被装备分支抢走，
                #                                然后因为槽位表里没有 15 而报"槽位没对上"）
                # 所以这里统一按 kind2 判定，两条都能进翅膀分支。
                # 真服实证 23:59:50：
                #   182 空ACK -> 29 -> 28 -> 30 -> 1922(48B) -> 1931(35B)
                #   -> 1932(13B) -> 178（该格消失）
                model = wing_proto.model_of(iid)
                if model is None:
                    log("[wing] 格%02x %s 在 Wing.xml 里找不到对应模型，保留不消耗"
                        % (pos, it["name"]))
                    return 1, err_text("该翅膀形象的外观数据还没对上，道具已保留"), []
                cur().wing[0] = iid
                follow = [29, 28, 30, 1922, 1931, 1932, 178]
                log("[wing] 格%02x 装备 %s -> 模型 %d(%s)（道具 x%d 消耗）"
                    % (pos, it["name"], model, wing_proto.wing_name(iid), use))
            elif it["kind2"] == 14:
                # 时装（kind1=1 / kind2=14）。真服里点时装卡是「收进衣柜」，
                # 不是直接穿上：837 -> 838 的衣柜列表里就是这些 item_id。
                # 以前这里被 `kind2 != 14` 排除，直接掉到最后的未实现兜底，
                # 表现就是「背包里的时装点了没反应」。
                cur().wardrobe.add(iid)
                follow = [29, 28, 30, 178]
                log("[wardrobe] 格%02x %s x%d 收进衣柜（现有 %d 件：%s）"
                    % (pos, it["name"], use, len(cur().wardrobe), sorted(cur().wardrobe)))
            elif iid == 20458:
                # 经验丹：描述只写「根据使用者的等级获得一定的经验」，没有具体数字。
                # 取值按当前升级需求的 10% 折算（need 本身随等级走，等价于按等级给经验），
                # 这样至少不会被判成「需在对应界面使用」而用不了。
                gain = max(500, cur().exp_need // 10) * use
                before = (cur().level, cur().exp)
                add_reward(gain, 0)
                follow = [29, 28, 30, 178]
                log("[bag] 使用 格%02x 经验丹 x%d -> 经验 +%d（Lv%d %d/%d -> Lv%d %d/%d）"
                    % (pos, use, gain, before[0], before[1], cur().exp_need,
                       cur().level, cur().exp, cur().exp_need))
            elif it["kind1"] == 1:
                # 装备（kind2=15 翅膀、kind2=14 时装 已在上面分流，不会走到这里）。
                # 真服装备栏协议：17 登录带一份全量，817 拉取时回 818，
                # 818 里每条 = [槽位][物品 id][01][21 个 0]（强化/洗炼/宝石位全 0）。
                slot = equip_slot_for(it["kind2"])
                if slot is None:
                    log("[equip] 格%02x %s kind2=%d 没有对应槽位，保留不消耗"
                        % (pos, it["name"], it["kind2"]))
                    return 1, err_text("这件装备的槽位还没对上，道具已保留"), []
                old = cur().equip.get(slot)
                # 换下来的旧装备一律回背包 —— 包括「同 id 再穿一件」。
                # 以前写了 `old != iid`，同 id 时旧那件既不回包也不保留，
                # 等于白吃玩家一件装备。
                if old:
                    bag_add(old, 1)          # 换下来的旧装备回背包
                    log("[equip] 槽%d 换下 %s 已回包" % (slot, old))
                # 一件只占一格。首饰四格是四件独立物品，绝不同时写两格。
                cur().equip[slot] = iid
                # 真服 15:38:17~15:38:22 三个 182 穿戴样本：只跟 29 / 28 / 178。
                # 不推 30（外观广播）、不推 818（817 的回执）—— 见 37 分支注释。
                # 真服 23:25:22~23:25:36 连续穿 11 件，每件都是：
                #   182 空ACK -> 29 -> 28 -> 30(外观多一件) -> 178 -> 306
                # 老实现少了 30，客户端面板就不会跟着长出来。
                follow = [29, 28, 30, 178]
                log("[equip] 槽%d 穿戴 %s(id=%d)，当前装备=%s"
                    % (slot, it["name"], iid, sorted(cur().equip.items())))
            else:
                # 描述驱动的兜底规则（item_rules）：Item.xml 里 666 个礼包、31 张货币卡
                # 都能靠 desc 自动解析，不用再一个个手写 USE_EFFECTS/GIFTS。
                r = item_rules.rule(iid)
                if r["kind"] == "gift":
                    got = []
                    for gid, gn in r["items"]:
                        for _ in range(use):
                            if isinstance(gid, str):      # 描述里找不到实体，按货币折算
                                add_purse(gid, gn)
                            else:
                                bag_add(gid, gn)
                        # 注意：不能写成 dict.get(gid, bag_proto.item(gid)[...])——
                        # dict.get 的默认值是"立即求值"的，gid 是字符串时那行照样会炸。
                        nm = PURSE_NAME[gid] if isinstance(gid, str) else bag_proto.item(gid)["name"]
                        got.append("%s x%d" % (nm, gn * use))
                    log("[bag] 开包 格%02x %s x%d -> %s"
                        % (pos, it["name"], use, "、".join(got)))
                    if r["missing"]:
                        log("[bag] ！！礼包 %s 有未识别掉落：%s（需补 ALIAS）"
                            % (it["name"], r["missing"]))
                elif r["kind"] == "currency":
                    add_purse(r["cur"], r["amount"] * use)
                    log("[bag] 使用 格%02x %s x%d -> %s +%d"
                        % (pos, it["name"], use, PURSE_NAME.get(r["cur"], r["cur"]),
                           r["amount"] * use))
                elif r["kind"] == "yueli":
                    add_purse("yueli", r["amount"] * use)
                    log("[bag] 使用 格%02x %s x%d -> 阅历 +%d（现有 %d）"
                        % (pos, it["name"], use, r["amount"] * use, cur().yueli[0]))
                else:
                    # 妖丹 / 称号卡 / 形象卡 / 坐骑仙草这些子玩法的数值还没入账。
                    # 【绝对不能消耗】：之前这里是"照常消耗并回成功"，结果玩家买的
                    # 天使羽翼形象、百年妖丹点一下就没了、也没有任何效果 ——
                    # 这比报错还糟（东西没了才知道）。现在保留道具并说明原因。
                    why = WHY_BY_ID.get(iid)
                    if not why and r["kind"] != "ui":
                        why = {
                            "wing_unlock": "翅膀系统服务端尚未实现，道具已保留",
                            "pet_spirit": "妖灵系统服务端尚未实现，道具已保留",
                            "title": "称号系统服务端尚未实现，道具已保留",
                            "mount_exp": "坐骑系统服务端尚未实现，道具已保留",
                            "pet_growth": "宠物养成尚未实现，道具已保留",
                        }.get(r["kind"])
                    if not why:
                        # item_rules 只能从描述里认「货币/阅历/礼包/称号/妖丹」，
                        # 坐骑、宠物形象、强化灵石这些描述里没有关键词，会落到 unknown/ui。
                        # 按 item_kind2 给出准确原因，别再一律说"需要去对应界面"。
                        why = WHY_BY_KIND2.get(it["kind2"]) \
                            or ("该道具需要在对应界面里使用，道具已保留"
                                if r["kind"] == "ui" else None) \
                            or "该道具的玩法服务端尚未实现，道具已保留"
                    log("[bag] 格%02x %s x%d 未实现(k1=%d k2=%d rule=%s)，保留不消耗"
                        % (pos, it["name"], use, it["kind1"], it["kind2"], r["kind"]))
                    return 1, err_text(why), []

        cur().bag[pos][1] -= use
        if cur().bag[pos][1] <= 0:
            del cur().bag[pos]
        save_state()
        return 182, b"", follow
    if cmd == 371:
        # 371 = 商城购买。真服多次实证：
        #   payload = [商品ID varint][u8 商城分区 area][u8 数量]
        # ★ 第二个字节是**分区**，不是货币码（之前理解错了，白改过两轮）：
        #   15:51:25 cd50ff 01 01 -> 20735 百年妖丹，Mall area=1 price=1
        #             → 元宝 870 -> 869（-1）  ✅ area1 用元宝
        #   19:22:51 cd4ead 02 05 -> 20141 初级灵石，Mall area=2 price=3
        #             → 元宝 1749 -> 1734（-15 = 3×5） ✅ area2 用元宝
        #   15:32:55 cd4f61 07 01 -> 20321 1级宝石盒，Mall area=7 price=30
        #             → 元宝 880 却回「对应货币不足」⇒ area7 不是元宝
        # 所以价格一律查 (area, item) 的 price，货币按 AREA_CUR 分区表取。
        try:
            iid, i = bag_proto.dec_val(payload, 0)
            area = payload[i] if len(payload) > i else 1
            cnt = payload[i + 1] if len(payload) > i + 1 else 1
        except Exception as e:
            log("[shop] 371 载荷异常 %s: %s" % (payload.hex(), e))
            return 1, err_text("商城数据异常"), []
        cnt = cnt or 1
        unit, hit = equip_attr.mall_price(area, iid)
        if not hit:
            unit, _pc = item_rules.price(iid)
            log("[shop] 371 area=%d 商品%d 不在 Mall.xml，退回 Item.xml 单价 %s"
                % (area, iid, unit))
        ckey = equip_attr.AREA_CUR.get(area, "yuanbao")
        purse = PURSES()[ckey]
        key = PURSE_NAME[ckey]
        total = int(unit) * int(cnt)
        if purse[0] < total:
            log("[shop] 购买失败：area=%d 商品%d x%d 需 %s %d，只有 %d"
                % (area, iid, cnt, key, total, purse[0]))
            return 1, err_text("对应货币不足"), []
        purse[0] -= total
        bag_add(iid, cnt)
        save_state()
        log("[shop] 购买 area=%d %s x%d，单价%d 扣 %s %d（余 %d）"
            % (area, bag_proto.item(iid)["name"], cnt, unit, key, total, purse[0]))
        return 371, b"\x01", [29, 28, 30, 178]
    # 注意：不要在这里给「未实现 cmd」回自制 cmd=1！
    # 真服抓包回放（SEQ）里本来就带这些 cmd 的 toast 应答，内置再回一份会变成
    # 双重 toast + 回放指针错位，客户端进场景前被重复提示包砸中直接闪退
    # （2026-11-22 17:22 实测崩溃，删掉后恢复）。663 真服根本不回，更不能回。
    return None


def new_token():
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def handle_tls(raw_sock, cid):
    """本地 TLS 终结 + 应答 /auth"""
    ctx = ssl_ctx()
    try:
        conn = ctx.wrap_socket(raw_sock, server_side=True)
    except Exception as e:
        log("c%03d TLS 握手失败 %s" % (cid, e))
        try:
            raw_sock.close()
        except Exception:
            pass
        return

    try:
        conn.settimeout(30)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        line = head.split(b"\r\n")[0].decode("latin1") if head else ""
        log("c%03d TLS 请求: %s" % (cid, line))

        # 读满 Content-Length
        clen = 0
        for h in head.split(b"\r\n"):
            if h.lower().startswith(b"content-length:"):
                clen = int(h.split(b":")[1].strip())
        while len(body) < clen:
            chunk = conn.recv(4096)
            if not chunk:
                break
            body += chunk  # noqa

        path = line.split(" ")[1] if " " in line else "/"
        if path.startswith("/auth"):
            user = ""
            try:
                j = json.loads(body.decode("utf-8", "replace") or "{}")
                user = j.get("username") or ""
            except Exception:
                pass
            if user and user not in ACCOUNTS:
                ACCOUNTS[user] = next_id[0]
                next_id[0] += 1
            tok = new_token()
            # 字段与真服完全一致：account_id 是数字，uid 字符串，token 43 字符
            resp = json.dumps({
                "account_id": int(ACCOUNTS.get(user, 13)),
                "uid": user or "0",
                "token": tok,
                "expires_at": int(time.time()) + 30 * 86400,
                "ok": True,
            }, separators=(",", ":")).encode()
            log("c%03d /auth 放行 uid=%s account_id=%s token=%s…"
                % (cid, user or "?", ACCOUNTS.get(user, 13), tok[:10]))
        else:
            resp = b'{"ok":false,"msg":"not found"}'
        conn.sendall(b"HTTP/1.1 200 OK\r\nServer: nginx\r\n"
                     b"Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\n"
                     b"Content-Length: " + str(len(resp)).encode() + b"\r\n"
                     b"Connection: close\r\n\r\n" + resp)
    except Exception as e:
        log("c%03d TLS 处理异常 %s" % (cid, e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


_ctx = [None]


def ssl_ctx():
    import ssl
    if _ctx[0] is None:
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        c.load_cert_chain(CERT, KEY)
        # 老客户端只发 TLS1.0/1.1。新版 OpenSSL 默认最低 TLS1.2，会直接回
        # alert protocol version，客户端只显示「安全连接验证失败」，看起来像
        # 证书或时间问题。这里把下限放开到 TLS1.0，并放宽容错级别。
        try:
            c.minimum_version = ssl.TLSVersion.TLSv1
        except Exception:
            pass
        c.set_ciphers("ALL:@SECLEVEL=0")
        _ctx[0] = c
        log("TLS 证书已加载：%s（兼容 TLS1.0~1.3）" % CERT)
    return _ctx[0]


def handle_game(conn, cid):
    # 每条连接一份角色状态（本线程独占）。以前是模块级全局，两个客户端一起在线
    # 会互相覆盖：A 捡的掉落进 B 的背包、B 的元宝把 A 的刷掉。
    pl = new_player()
    pl.cid = cid
    with GAME_LOCK:
        CONN_PLAYERS[cid] = pl
    # ★ 2026-11-24 15:1x：登录时先用**存档里的场景**，不要一律 101。
    #   17 里的场景字段和随后 113 的对象表必须是同一个场景，否则客户端
    #   先按 17 建图、再把 113 的对象叠上去 → 两张图的怪同屏
    #   （蓬莱的「仙界守卫 Lv.1」+ 天音谷底的「作乱小妖 Lv.13」+ 一只无名熊猫）。
    cur_map = int(cur().scene or DEFAULT_SCENE)
    spirit_slot_sel = 0    # 妖灵界面里最后点开的槽位（1411 设，1413 用）
    spirit_pick = [0]      # 该槽位将要放上去的妖灵（1412 已告诉客户端的那只）
    pet_sel = [-1]         # 963 连点时的「选中第几只」游标（-1 表示还没选过）
    ptr = 0
    buf = b""
    fresh_mode = False     # 本次会话是否在跑「无角色 → 建角」试验
    sent_cmds = set()      # 本次会话已发出的响应/推送 cmd，用于去重
    battle_idx = 0         # 旧回放模式下的回合指针（BT 加载失败时兜底）
    battle_sess = None     # 当前战斗会话（battle.BattleSession）
    # 当前进行中的任务ID（5B，如 ce00061bad）。以前是纯局部变量，重连即丢，
    # 现在从存档恢复，登录时还能把 210 补推回去。
    active_qid = (b"\xce" + cur().quest_active.to_bytes(4, "big")) if cur().quest_active else None
    quest_pushed = False   # 登录回放结束后是否已补推过任务状态
    # 真服登录应答 17 之后会**主动**推 113(场景对象表) + 117(1B)，不等客户端请求。
    # 本服以前要等 1265 才补 113，登录加载条就一直转圈（2026-11-23 实证 21:48:09）。
    login_pushed = [False]
    # 登录回放里的 113/1266 会被跳过（由 1265 统一按 cur_map 下发），
    # 所以初始视为「未下发」，第一次 1265 一定补一份当前场景的对象表。
    last_113 = None
    conn.settimeout(180)
    with GAME_LOCK:
        GAME_CONNS[cid] = conn
    write_status()
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                log("c%03d 对端关闭（回放指针 %d/%d）" % (cid, ptr, len(SEQ)))
                break
            buf += data
            while len(buf) >= 8:
                total, cmd, seq = struct.unpack_from(">HHI", buf[:8])
                if total < 6 or total > 60000:
                    log("c%03d 非法长度 %d" % (cid, total))
                    return
                need = 2 + total
                if len(buf) < need:
                    break
                payload = buf[8:need]
                buf = buf[need:]
                log("c%03d <- cmd=%-6d seq=%-4d len=%d%s"
                    % (cid, cmd, seq, len(payload),
                       ("  hex=" + payload[:24].hex()) if payload else ""))
                if fresh_mode:
                    log("c%03d [fresh] <- cmd=%d len=%d hex=%s text=%s"
                        % (cid, cmd, len(payload), payload.hex(),
                           _utf8_runs(payload)))

                if fresh_mode and cmd != 17:
                    # 无角色试验：这个连接上客户端「还没有角色」，所以任何业务包都不能
                    # 按回放应答——回放用的是默认 Lv10 角色（背包/装备/属性全套），
                    # 客户端会变成「半有角色半没有」，轻则界面错乱重则 native 段错误。
                    # 只留建角请求（cmd=20）做观察，其余一律只记录不回。
                    if cmd == 20:
                        _n, _sx = parse_create_role(payload)
                        log("c%03d [fresh] ★★ 建角请求 cmd=20 len=%d 昵称=%r 性别候选=%s"
                            " hex=%s" % (cid, len(payload), _n, _sx, payload.hex()))
                        try:
                            conn.sendall(pack(21, seq, b""))
                            log("c%03d [fresh] -> 21 空 ACK（先只观察客户端反应，"
                                "拿到真实 21 结构后再补内容）" % cid)
                        except OSError:
                            pass
                    else:
                        log("c%03d [fresh] cmd=%d 只记录不应答（无角色状态）" % (cid, cmd))
                    continue

                # 多账号：登录包带账号串，按账号切存档（主号 1784366 仍用原文件）。
                # 必须在下面的回放之前切，否则 17 之后那些动态包（28/30/178）
                # 还是上一个账号的数据。
                if cmd == 17 and payload:
                    _acct = account_from_login(payload)
                    if _acct:
                        log("c%03d [acct] 登录账号 %s" % (cid, _acct))
                        switch_account(_acct)
                    pl.authed = True
                    # 读档后按存档场景重设本会话地图：17 与 113 必须同源。
                    cur_map = int(cur().scene or DEFAULT_SCENE)
                    # 读档后把任务状态同步到本次会话的局部变量，
                    # 否则重连进来 active_qid 还是 None，打完怪不推 210「可交」。
                    active_qid = ((b"\xce" + cur().quest_active.to_bytes(4, "big"))
                                  if cur().quest_active else None)
                    # ---- 无角色试验：指定账号只回「账号在、角色为空」的 17 ----
                    if FRESH_ACCT and _acct == FRESH_ACCT:
                        fresh_mode = True
                        pl.authed = False    # 不许写盘，别把主号存档冲了
                        _p = build_17_fresh(_acct, FRESH_V)
                        conn.sendall(pack(17, seq, _p))
                        log("c%03d -> 17 [无角色试验 v%d] len=%d hex=%s"
                            % (cid, FRESH_V, len(_p), _p.hex()))
                        continue
                    # ---- 账号在、角色没建：回 cmd=1 `01a0`，客户端自己跳建角界面 ----
                    # 真服 2026-11-24 12:38 实测：不是 17 的变体，是 cmd=1 两字节。
                    # AUTO_NEWBIE=1 时才退化成「服务端代建」，正常不要开。
                    if not cur().has_char:
                        if AUTO_NEWBIE:
                            _ok, _msg = create_character(_acct, 1)
                            log("c%03d [acct] AUTO_NEWBIE 自动建角 -> %s" % (cid, _msg))
                            active_qid = None
                        else:
                            conn.sendall(pack(1, seq, b"\x01\xa0"))
                            log("c%03d -> cmd=1 01a0 [无角色] 账号=%s，等客户端发 cmd=20 建角"
                                % (cid, _acct))
                            continue


                # ---- 建角（cmd=20）：真服原样流程，回 20 应答 + 推 113/117 ----
                if cmd == 20:
                    _nm, _sx = parse_create_role(payload)
                    _acct2 = account_from_login(payload) or cur().acct or ""
                    log("c%03d [create] ★ 建角请求 账号=%s 昵称=%r 性别候选=%s hex=%s"
                        % (cid, _acct2, _nm, _sx, payload.hex()))
                    if _acct2 and _acct2 != cur().acct:
                        switch_account(_acct2)
                    _ok, _msg = create_character(_nm, _sx if _sx in (1, 2) else 1)
                    if _ok:
                        _ack = build_20_ack(cur().acct, cur().name, cur().sex, cur().cls)
                        conn.sendall(pack(20, seq, _ack))
                        conn.sendall(pack(113, 0, CREATE_PUSH_113))
                        conn.sendall(pack(117, 0, CREATE_PUSH_117))
                        log("c%03d [create] -> cmd=20 建角应答 %dB + 113/117；%s"
                            % (cid, len(_ack), _msg))
                    else:
                        log("c%03d [create] 建角失败：%s" % (cid, _msg))
                        conn.sendall(pack(1, seq, b"\x01\xa0"))
                    continue

                # 按真服原始顺序回放：定位到该 seq 的响应，把途中所有包按序全发。
                # 只发「推送+匹配包」会漏掉客户端依赖的中间包，导致初始化不完整，
                # 最后在 native 层静默段错误（没有 tombstone、没有 crash 日志）。
                # 人工响应优先：调未实现功能（任务/商店等）时用 tpl_extra.json 热改，
                # 不用重启服务端、不用重新登录。
                # ★ 2026-11-24 19:0x 实验：点怪直接开战（BATTLE_116_BATTLE=1）
                # 真服是「点怪 → 116(00) → 客户端自己寻路（一串 115）→ 走到跟前发 145」。
                # 本地服补了 118 位置广播后客户端仍只走两步就停（115 seq=45/46），
                # 走不到怪跟前就永远不发 145。所以这里试「服务端直接推 151 开战」：
                # 很多线上服就是推送型开战，客户端收到 151 自己切战斗界面。
                # 开关默认关；开了之后点任意目标（116 payload=00）都按当前场景第一个
                # 刷怪点开战，用来验证「151 一推就能进战斗 + 血条折算是否同步走空」。
                if (cmd == 116 and os.environ.get("BATTLE_116_BATTLE") == "1"
                        and (payload or b"") == b"\x00" and BT is not None):
                    _ids = [int(t) for t in ((SCENES.get(cur_map) or {})
                                             .get("spawn_points") or "").split(",")
                            if t.strip().isdigit()]
                    if _ids:
                        _ba = {k: panel_attr(k) for k in
                               ("hp_max", "mp_max", "attack_min", "attack_max",
                                "defense", "crit")}
                        battle_sess = battle.BattleSession(
                            _ids[0], BT, cur().cls, cur().level,
                            pet=pet_proto.active_pet(cur().pets), name=cur().name,
                            attrs=_ba)
                        battle_sess.player_mp_max = max(0, panel_attr("mp_max"))
                        battle_sess.player_mp = battle_sess.player_mp_max
                        BATTLE_SESS[cid] = battle_sess
                        _o151 = battle_sess.open_packet()
                        conn.sendall(pack(151, 0, _o151))
                        log("c%03d .. 116→直接推 151 [开战 %s] len=%d hex=%s"
                            % (cid, battle_sess.summary(), len(_o151), _o151.hex()))
                        continue

                # ---- 战斗（真服 124.223.56.189 抓包实证，2026-11-22）----
                # 145 = 客户端发起战斗；服务端回 145 空 ACK + 151 开战包
                # 146 = 客户端回合操作（不是服务端包！）；服务端回 148 结算
                # 149 = 客户端结束确认；服务端回 150
                # 详见 战斗协议_真服抓包_20261122.md
                if cmd == 145:
                    mob_id, ok = battle.target_from_request(payload)
                    if ok and BT is not None:
                        # ★ 2026-11-24 17:5x：把**面板真值**（29 同一口径，含装备/
                        # 强化/妖灵）交给战斗内核。以前战斗只按 Levelup 裸属性算，
                        # 于是被迫乘 (1+0.14*lv) 的假系数去凑伤害 → 任何怪「需1回合」，
                        # 用户看到「莫名其妙胜利」，脱了武器也一样赢。
                        _ba = {k: panel_attr(k) for k in
                               ("hp_max", "mp_max", "attack_min", "attack_max",
                                "defense", "crit")}
                        battle_sess = battle.BattleSession(
                            mob_id, BT, cur().cls, cur().level,
                            pet=pet_proto.active_pet(cur().pets), name=cur().name,
                            attrs=_ba, dmg_mult=DMG_MULT)
                        # 151 里的蓝必须与 29 面板同源：客户端是拿「当前法力」跟
                        # Skill.xml 的 consume_mp 比，面板/战斗蓝不一致会出现
                        # 「面板有蓝却提示法力不足」。含 MP_ADD 补偿。
                        battle_sess.player_mp_max = max(0, panel_attr("mp_max"))
                        battle_sess.player_mp = battle_sess.player_mp_max
                        BATTLE_SESS[cid] = battle_sess
                        conn.sendall(pack(145, seq, b""))
                        _o151 = battle_sess.open_packet()
                        conn.sendall(pack(151, 0, _o151))
                        log("c%03d -> cmd=145 ACK + 151 [开战 %s]"
                            % (cid, battle_sess.summary()))
                        # 151 一崩就是「收到包 55ms 后干净退出」，必须留原包对比真服
                        log("c%03d .. 151 len=%d hex=%s"
                            % (cid, len(_o151), _o151.hex()))
                    else:
                        battle_idx = 0
                        battle_sess = None
                        open_body = battle_open_body()
                        # [2] 是职业/形象 ID，固定 06 会让战斗里变回未转职模型
                        open_body[2] = cur().cls & 0xFF
                        if len(payload) == 5:
                            # 目标 ID 在尾巴第 13 个字节起（昵称长度可变，偏移要算）
                            _o = 4 + len((cur().name or "").encode("utf-8")) + 13
                            open_body[_o:_o + 5] = payload
                        conn.sendall(pack(145, seq, b""))
                        conn.sendall(pack(151, 0, bytes(open_body)))
                        log("c%03d -> cmd=145 ACK + 151 [回放模式 target=%s]"
                            % (cid, payload.hex()))
                    continue
                if cmd == 146:
                    battle_sess = BATTLE_SESS.get(cid)   # GM 开战的会话也在这
                    if battle_sess is not None:
                        body, is_end, result = battle_sess.on_action(payload)
                        conn.sendall(pack(148, 0, body))
                        log("c%03d -> cmd=148 [回合%d 怪剩血%d 玩家剩血%d%s%s] echo=%s"
                            % (cid, battle_sess.round, battle_sess.hp,
                               battle_sess.disp_player_hp,
                               " 结束" if is_end else "",
                               " " + result if result else "", payload.hex()))
                    else:
                        h = BATTLE_ROUNDS[min(battle_idx, len(BATTLE_ROUNDS) - 1)]
                        body = bytearray(bytes.fromhex(h))
                        if len(payload) == 5 and len(body) >= 9:
                            body[4:9] = payload
                        battle_idx += 1
                        conn.sendall(pack(148, 0, bytes(body)))
                        log("c%03d -> cmd=148 [回放%d] echo=%s"
                            % (cid, battle_idx, payload.hex()))
                    continue
                if cmd == 149:
                    # 结算：150 = [经验][3B][铜钱][1B][件数]{[ID][数量][标记]}[4B]（真服实证）
                    battle_sess = BATTLE_SESS.pop(cid, None)
                    rw = (battle_sess.settle() if battle_sess is not None
                          else {"exp": 0, "coin": 0, "win": True})
                    name = battle_sess.name if battle_sess is not None else "对手"
                    if rw["win"]:
                        lv_before = cur().level
                        add_reward(rw["exp"], rw["coin"])
                        # 150 真服结构（58 份样本对齐）：
                        #   [经验][3B 0][铜钱][1B 0][u8 件数]{[物品ID][数量][标记]}[4B 0]
                        # 以前写的是 exp+coin+9×00：铜钱落在了「3B 未知」里，
                        # 客户端读到的铜钱恒为 0，而且完全没有掉落在结算页显示。
                        drops = rw.get("drops") or []
                        body150 = bytearray(battle.enc_val(rw["exp"]))
                        body150 += b"\x00\x00\x00"
                        body150 += battle.enc_val(rw["coin"])
                        body150 += b"\x00" + bytes([len(drops) & 0xFF])
                        got = []
                        for iid, cnt in drops:
                            body150 += battle.enc_val(iid) + bytes([cnt & 0xFF, 0x01])
                            bag_add(iid, cnt)
                            got.append("%s x%d" % (bag_proto.item(iid)["name"], cnt))
                        body150 += b"\x00" * 4
                        conn.sendall(pack(150, 0, bytes(body150)))
                        log("c%03d -> cmd=150 [胜利 +%d经验 +%d铜钱%s 掉落%d件：%s]"
                            % (cid, rw["exp"], rw["coin"],
                               " 升级!" if cur().level != lv_before else "",
                               len(drops), "、".join(got) or "无"))
                        # ★ 怪物消失：真服在塔里是 150 之后紧跟 121（23:43:59 / 23:44:54），
                        # 野外是致死那回合 148 之后紧跟 121（01:23:23）。两种顺序都有，
                        # 这里按塔里那份、放在 150 后面。
                        # 不发这个包，客户端就一直认为那只怪还活着：模型留在场上、
                        # 塔层「清怪数」永远不达标 → 下一层的引导传送点不亮。
                        _mob_dead = int(getattr(battle_sess, "mob_id", 0) or 0)
                        if _mob_dead:
                            conn.sendall(pack(121, 0, kill_notice(_mob_dead)))
                            log("c%03d .. push cmd=121 len=5 [怪物消失 %d]"
                                % (cid, _mob_dead))
                            # 塔层下一层跳点带 clear_needed，客户端是「已清怪数够不够」
                            # 来决定跳点位置给不给引导。清完这一只后把坐标打进日志：
                            # 万一客户端没画引导，至少知道该往哪走（角标就是 Scene.xml 的坐标）。
                            _sc_now = int(cur().scene or DEFAULT_SCENE)
                            for _jid, _jx, _jy in SCENE_JUMP.get(_sc_now, []):
                                if JUMP_CLEAR.get(_jid, 0) and JUMPS.get(_jid):
                                    log("c%03d .. 本层已清：引导传送点 %s → %s 在 (%d,%d)"
                                        % (cid, scene_name(_sc_now),
                                           scene_name(JUMPS[_jid]), _jx, _jy))
                        # 真服 149 之后紧跟 178（掉落进包后刷新背包），再 29/28/30
                        if drops:
                            send_push(conn, cid, 178)
                        conn.sendall(pack(28, 0, build_28()))
                        # 升级必须重推面板：29 是唯一的属性快照包，客户端不会自己
                        # 按等级重算。以前只在 active_qid 存在时推，打完怪升级
                        # 却没有进行中任务 → 面板原地不动。
                        _p29 = cur().level != lv_before
                        if _p29:
                            send_push(conn, cid, 29)
                            log("c%03d .. 升级补推 cmd=29 [Lv%d]" % (cid, cur().level))
                        # 真服战斗结束只推「进行中」，客户端要重接任务+再点 NPC
                        # 才出交任务入口。本地服直接推 9B「可交」。
                        if active_qid:
                            if not _p29:
                                send_push(conn, cid, 29)
                            conn.sendall(pack(210, 0, quest_210_ready(qid_of(active_qid))))
                            log("c%03d .. push cmd=210 len=9 [任务可交 %s]"
                                % (cid, active_qid.hex()))
                            if not cur().quest_ready:
                                set_quest(qid_of(active_qid), True)
                        else:
                            # 服务端不知道在跑哪条任务（重连过 / 存档里没记），
                            # 这时客户端就永远收不到「可交」，表现为「提交不了」。
                            log("c%03d .. 警告：打完怪但 active_qid 为空，"
                                "客户端收不到 210 可交（让它重新接一次任务即可）" % cid)
                        # ★ 2026-11-25：进战斗的瞬间客户端会把整屏 NPC 销毁
                        #   （logcat 实证 21:37:50.779，和 ObjectBattleView created 同一毫秒：
                        #    NPCMainView: 龙太子/哪吒/龙虾大将/托塔天王 destroyed），
                        #   战后它自己不会重建 —— 玩家看到的就是「打完怪野外 NPC 全没了」。
                        #   重推 113 + 117 让客户端按对象表把场景视图重建一遍。
                        _sc_now2 = int(cur().scene or DEFAULT_SCENE)
                        conn.sendall(pack(113, 0, build_113(_sc_now2)))
                        conn.sendall(pack(117, 0, b"\x00"))
                        log("c%03d .. 战后重推 113+117 [%s 重建场景对象/NPC]"
                            % (cid, scene_name(_sc_now2)))
                    else:
                        # 失败/力竭：什么都不给（0 经验 0 铜钱），但不扣任何已有收益。
                        # 客户端 PVE 没有失败结算画面（结束包结果位置 01 会闪退），
                        # 所以用 cmd=1 文本提示把结果讲清楚。
                        conn.sendall(pack(150, 0, b"\x00" * 11))
                        why = ("力竭脱离战斗" if getattr(battle_sess, "result", "")
                               == "draw" else "被%s击败" % name)
                        conn.sendall(pack(1, 0, err_text("%s，没有获得奖励" % why)))
                        conn.sendall(pack(28, 0, build_28()))
                        log("c%03d -> cmd=150 [失败 %s 无收益]"
                            % (cid, why))
                    battle_idx = 0
                    battle_sess = None
                    continue
                if cmd == 124:
                    # 真服实证（15:37:00）：124 = 点场景对象，应答 00ff0000。
                    # 关键：点 NPC 后真服会跟推 210（9B 完成态）——
                    #   01 + 任务ID5B + 01 01 00
                    # 客户端收到这条才会把任务标成「可交」，之后才肯发 214。
                    # 之前本地服缺这一步，表现就是「任务打完了也提交不了」。
                    conn.sendall(pack(124, seq, b"\x00\xff\x00\x00"))
                    log("c%03d -> cmd=124 seq=%d [场景交互成功]" % (cid, seq))
                    if active_qid:
                        conn.sendall(pack(210, 0, quest_210_ready(qid_of(active_qid))))
                        log("c%03d .. push cmd=210 len=9 [任务可交 %s]"
                            % (cid, active_qid.hex()))
                        if not cur().quest_ready:
                            set_quest(qid_of(active_qid), True)
                    continue

                if cmd == 130:
                    # 130 = 锁妖塔「进入副本」。真服 23:43:27 完整链（relay.log）：
                    #   C->S 130 ccdd              → S->C 131 ccdd cd52d1 04 08
                    #                               → 113(13B) / 117(00) / 659(ce0003313f01)
                    #   C->S 32513 cd52d1 ccdd 00  → 进场景
                    # 以前没实现 130，客户端收不到 131 就一直转圈（用户报的「锁妖塔进不去」）。
                    # 塔 id 用 cc+1B 编码（221 → ccdd），和 enc_val 的 0x80..0xFF 档一致。
                    tid = None
                    if len(payload) >= 2 and payload[0] == 0xCC:
                        tid = payload[1]
                    elif len(payload) >= 3 and payload[0] == 0xCD:
                        tid = int.from_bytes(payload[1:3], "big")
                    elif payload:
                        tid = payload[0]
                    tsc = TOWER_SCENE.get(tid)
                    if not tsc or tsc not in SCENES:
                        log("c%03d !! 未知锁妖塔 id=%s(hex=%s)，保持 %s(%d)"
                            % (cid, tid, payload.hex(), scene_name(cur_map), cur_map))
                        continue
                    # 进塔前的位置（离开副本要原路送回）
                    prev_scr = int(cur().scene or cur_map)
                    prev_x = int(getattr(cur(), "pos_x", 0) or 0)
                    prev_y = int(getattr(cur(), "pos_y", 0) or 0)
                    cur_map = tsc
                    cur().scene = tsc
                    # 进塔落点：真服紧接着就是 115 0305，所以记 (3,5)，重登也落在塔门口
                    cur().pos_x, cur().pos_y = 3, 5
                    last_113 = tsc
                    # 记住「从哪进来的」：cmd=132 离开副本时原路送回
                    TOWER_BACK[cur().acct or ("c%03d" % cid)] = (prev_scr, prev_x, prev_y)
                    body131 = payload + enc_scene(tsc) + TOWER_131_TAIL
                    conn.sendall(pack(131, seq, body131))
                    log("c%03d -> cmd=131 seq=%d len=%d [进塔 id=%s → %s(%d) %s]"
                        % (cid, seq, len(body131), tid, scene_name(tsc), tsc,
                           "实证" if tid == 221 else "推断"))
                    conn.sendall(pack(113, 0, build_113(tsc)))
                    conn.sendall(pack(117, 0, b"\x00"))
                    conn.sendall(pack(659, 0, TOWER_659))
                    log("c%03d .. push cmd=113/117/659 [进塔后场景对象表]" % cid)
                    continue

                if cmd == 132:
                    # 132 = 「离开副本 / 退出锁妖塔」。真服 23:46:17（relay.log）：
                    #   C->S 132（空 payload）→ S->C 133 = 67 37 05（洛水城 103 的 (55,5)）
                    #                         → 113(3B 空表) / 117(00)
                    #                         → C->S 115 3705 → 32513 670000
                    # 以前没实现 132，塔里点退出就卡住（用户报的「塔里没法退出去」）。
                    # 从哪进来回哪去：130 时把 (场景,坐标) 记进 TOWER_BACK；
                    # 没记录（重启过 / 换号）就照真服样本回洛水城 (55,5)。
                    _key = cur().acct or ("c%03d" % cid)
                    back = TOWER_BACK.get(_key) or (103, 55, 5)
                    bsc, bx, by = int(back[0]), int(back[1]) & 0xFF, int(back[2]) & 0xFF
                    # 记下来的「进塔前」本身就在塔里（登录点在塔内 / 旧档）→ 不能原地送回，
                    # 统一回真服样本的洛水城 (55,5)，否则玩家会觉得「退出没反应」。
                    if bsc not in SCENES or SCENES[bsc].get("scene_type") == "3":
                        bsc, bx, by = 103, 55, 5
                    cur_map = bsc
                    cur().scene = bsc
                    cur().pos_x, cur().pos_y = bx, by
                    last_113 = bsc
                    # 塔里没结算的战斗直接丢掉，否则客户端出塔后一按 146 还在打空气
                    BATTLE_SESS.pop(cid, None)
                    body133 = enc_scene(bsc) + bytes([bx, by])
                    conn.sendall(pack(133, 0, body133))
                    conn.sendall(pack(113, 0, build_113(bsc)))
                    conn.sendall(pack(117, 0, b"\x00"))
                    log("c%03d -> cmd=133 len=%d [离开副本 → %s(%d) 落点(%d,%d)] + 113(%dB)"
                        % (cid, len(body133), scene_name(bsc), bsc, bx, by,
                           len(build_113(bsc))))
                    continue

                if cmd == 128:
                    # 真服实证：128 = 点传送点（过图），payload = CD + u16BE 跳点ID
                    #   cd03e9(1001) 蓬莱->天音谷  129=66 05 07
                    #   cd03ea(1002) 天音谷->蓬莱  129=65 3a 06   ← 之前这里被写死回 66，所以「回不去」
                    #   cd03eb(1003) 天音谷->洛水城
                    #   cd27d9(10201) 天音谷->天音谷底 129=cd03fd 05 07
                    #   cd27e3(10211) 天音谷底->天音谷
                    # 129 后紧跟本场景的 113（对象表）+ 117(00)，客户端再发 32513 确认场景。
                    # prev 取角色档里的场景而不是主循环局部变量：GM「引路」改的是
                    # cur().scene，局部 cur_map 不会跟着变，用 cur_map 会把落点算回旧场景。
                    prev = int(cur().scene or cur_map)
                    jid = None
                    if len(payload) >= 3 and payload[0] == 0xCD:
                        jid = int.from_bytes(payload[1:3], "big")
                    tgt = JUMPS.get(jid)
                    # ★ 塔层（scene_type=3）同样走 Jumppoint 表，payload 是**跳点 id**，
                    #   而塔里「跳点 id」和「场景 id」是两套编号、数值会撞车：
                    #     Scene 21201 jump_points="21201,35,5"  → Jump 21201 → scene 21202
                    #     Scene 21202 jump_points="21202,1,5;21203,35,5"
                    #                                  21202 → 回一层 / 21203 → 去三层
                    #   真服实证（relay.log 23:44:06）：C->S 128 cd52d1 → S->C 129 cd52d2 03 05。
                    #   ⚠️ 上一版写的是「scene_type=3 就直接拿 jid 当目标场景」，21201
                    #   既是跳点又是场景 → 被覆盖成 21201，点「下一层」原地重进一层。
                    #   这就是用户反馈的「锁妖塔进不了下一层」。
                    #   只有 Jumppoint 里查不到、客户端直接发目标场景 id 时才退回 jid。
                    if tgt is None and jid in SCENES and SCENES[jid].get("scene_type") == "3":
                        tgt = jid
                    if tgt is None:
                        log("c%03d !! 未知跳点 %s，留在场景 %s(%d)"
                            % (cid, payload.hex(), scene_name(cur_map), cur_map))
                        tgt = cur_map
                    cur_map = tgt
                    cur().scene = tgt      # ★ 存进角色档：下次登录 17 用同一个场景
                    last_113 = tgt
                    body113 = build_113(tgt)
                    # 落点一律由 Jumppoint 反推（目标场景里「回上一个场景」的那个跳点 + 2 格）。
                    # 塔层也一样：21201→21202 得 (3,5)，与真服 129 cd52d2 03 05 逐字节一致；
                    # 以前塔内写死 (3,5)，往上走是对的、往下走却会落到墙上。
                    _pos = arrival_pos(tgt, prev)
                    cur().pos_x, cur().pos_y = _pos
                    conn.sendall(pack(129, 0, enc_scene(tgt) + bytes(_pos)))
                    conn.sendall(pack(113, 0, body113))
                    conn.sendall(pack(117, 0, b"\x00"))
                    # 249 是「某某到达 N 层」的全服广播，不是进塔必需的初始化包：
                    #   真服 249 len=12 = [05 塔组][0xA9 名字9字节][名字][0c 层数]
                    #   每 1~2 分钟来一次，内容是别的玩家的进度。本地不伪造这条，
                    #   免得把真服玩家名当成自己的广播出去。
                    log("c%03d -> cmd=129 [过图 跳点%s(%s) %s(%d) -> %s(%d) 落点%s] + 113(%dB)"
                        % (cid, jid, payload.hex(), scene_name(prev), prev,
                           scene_name(tgt), tgt,
                           (enc_scene(tgt) + bytes(_pos)).hex(),
                           len(body113)))
                    continue
                if cmd == 817:
                    # 817 = 装备栏拉取（不是转职！早期误判已修正）。
                    # 真服两份样本：
                    #   C->S len=29 hex=07 cd2b65 02 cd2b66 03 ...  → 7 个槽
                    #   C->S len=9  hex=02 cd2be3 08 cd2b64 01        → 护符槽8 + 槽1
                    # 格式 = [数量 n][对象 varint][槽位] × n
                    #   S->C 818 seq=0 = [数量 n][槽位][物品 id 3B][01][23×0] × n
                    #     7 条 = 197B、1 条 = 29B，是同一种结构的不同条数。
                    # 旧「转职」实现把 payload[4]（其实是槽位 8）写进 PLAYER_LEVEL，
                    # 还回 29B 错包导致客户端解析错位 —— 那段已彻底删除。
                    slots = []
                    n = payload[0] if payload else 0
                    i = 1
                    for _ in range(n):
                        try:
                            _obj, i = vread(payload, i)
                            slots.append(payload[i])
                            i += 1
                        except Exception:
                            break
                    if not slots:
                        slots = [1, 2, 3, 4, 5, 6, 7]
                    # ★ 2026-11-25：客户端 817 只上报 7 个主装备槽，首饰槽（9/10/11/12）
                    #   它不报；而真服 818 是带首饰的 11 条（len=309 样本实测）。
                    #   只按上报回 7 条，客户端装备栏的首饰格就一直是空的 ——
                    #   玩家反馈的「还有两个槽是空的」。
                    #   这里把服务端实际穿着的首饰槽补进来，顺序沿用客户端的。
                    for _s in equip_slots():
                        if _s not in slots:
                            slots.append(_s)
                    body = build_818(slots)
                    conn.sendall(pack(818, 0, body))
                    log("c%03d -> cmd=818 seq=0 len=%d [装备栏 槽%s] 装备=%s"
                        % (cid, len(body), slots, sorted(cur().equip.items()) or "空"))
                    continue
                if cmd == 42:
                    # 真服实证（20:51:33 / 20:51:52 / 20:52:23 三次）：
                    #   42 payload=06  →  818 seq=0 + 43 seq=同请求 (345B 角色详情)
                    # ★ 注意：42 触发的那份 818 是 850B / 14 条，载荷里
                    # 一个装备 id 都没有（cd2b65/cd2f4c/cd2841 全不出现，id 是
                    # cd353d、cd4f30、cd2745 这类），属于另一个子系统（心法类）。
                    # 它和「装备表 818」（count + 28B/条，如 197B=7 条）不是一回事，
                    # 所以这里仍旧原样回放录制包，不要拿 build_818 去替换 ——
                    # 换了等于给这个 handler 塞错结构。装备表走 30 / 817 的 818 回执。
                    if ROLE_FULL.get("818_full"):
                        _bfull = bytes.fromhex(ROLE_FULL["818_full"])
                        conn.sendall(pack(818, 0, _bfull))
                        log("c%03d -> cmd=818 seq=0 len=%d [42 触发 完整装备 真服原包]"
                            % (cid, len(_bfull)))
                    if ROLE_FULL.get("43"):
                        _b43 = bytes.fromhex(ROLE_FULL["43"])
                        conn.sendall(pack(43, seq, _b43))
                        log("c%03d -> cmd=43  seq=%-4d len=%d [角色详情 真服原包]"
                            % (cid, seq, len(_b43)))
                    continue
                if cmd == 1154:
                    # 货币目录：真服 9 次样本全是同一份 69B，21 项 id(1B)+值(u16) 全 0，
                    # 尾部 5B `04 00 00 00 00`。之前本地服静默丢弃 → 客户端等这份数据会转圈。
                    # ⚠️ 值改成非零客户端进场景即崩，必须保持全 0（见项目记忆第 13 条）。
                    _b = bytes.fromhex(
                        "15010000020000030000040000050000060000070000080000"
                        "0900000a00000b00000c00000d00000e00000f000010000011"
                        "00001200001300001400001500000400000000")
                    conn.sendall(pack(1154, seq, _b))
                    log("c%03d -> cmd=1154 seq=%-4d len=%d [货币目录 21项 全0]"
                        % (cid, seq, len(_b)))
                    continue
                if cmd == 219:
                    # 真服 23:36:51 / 23:50:08 两次样本：C->S 219(00) -> cmd=1
                    # "04bb 本服暂未开通此功能"（29B）。真服自己都没开这个入口，
                    # 本地不能静默丢——丢了客户端就一直转圈等人回。
                    _b = bytes.fromhex("04bbe69cace69c8de5b09ae69caae681a2e5a48de6ada4e58a9fe883bd")
                    conn.sendall(pack(1, seq, _b))
                    log("c%03d -> cmd=1 seq=%-4d len=%d [219 未开通提示]"
                        % (cid, seq, len(_b)))
                    continue
                if cmd in (1345, 1356, 1357):
                    # 坐骑系统（真服 2026-11-23 23:50 实证）
                    #   1345 打开面板 -> 1346：34B = 没坐骑；40B = 已解锁
                    #   已解锁 40B：01 00000000 [cd273305 坐骑id][0106 状态][cd2733ff]
                    #               [3232…][4646…][06 0a 05 02 03 02 0c 14 0a 04 06 04]
                    #   解锁后还会跟推 1362(28B 属性) + 1363(12B 成长条)
                    #   1356 上马 / 1357 下马：先空回包，再推 1346（只改 [9]：01/02）
                    #
                    # ⚠️ 本地账号没有坐骑存档，1345 必须回旧的 34B「未解锁」，
                    #    回 40B 会让客户端按「有坐骑」去取不存在的数据，登录即闪退（2026-11-24 00:00 实测）。
                    # ⚠️ 1345 **不能回显**：真服对 C->S 1345 只回 1346（14:49/15:28/23:50 样本都没有 1345 应答），
                    #    多回一个空 1345 客户端直接崩（00:06 实测）。
                    #    1356/1357 才是有回显的（真服 S->C 1356/1357 空包）。
                    if cmd in (1356, 1357):
                        conn.sendall(pack(cmd, seq, b""))
                    if MOUNT_UNLOCKED:
                        _b1346 = bytes.fromhex(
                            "0100000000cd273305010601cd2733ff"
                            "323232323232464646464646"
                            "060a050203020c140a040604")
                        if cmd == 1345:
                            conn.sendall(pack(1346, 0, _b1346))
                            conn.sendall(pack(1362, 0, bytes.fromhex(
                                "060a050203020c140a0406040100000000cd273305010601cd2733ff")))
                            conn.sendall(pack(1363, 0, bytes.fromhex(
                                "323232323232464646464646")))
                        else:
                            _st = bytearray(_b1346)
                            _st[9] = 0x02 if cmd == 1356 else 0x01
                            conn.sendall(pack(1346, 0, bytes(_st)))
                    else:
                        # 未解锁：与原模板一致，seq 回显（真服登录段 1346 seq=16）
                        conn.sendall(pack(1346, seq, bytes.fromhex(
                            "01000000000000010600000000000000000000000000000000000000000000000000")))
                    log("c%03d <- cmd=%-5d -> 1346 seq=%-4d [坐骑 %s%s]"
                        % (cid, cmd, seq,
                           {1345: "面板", 1356: "上马", 1357: "下马"}[cmd],
                           "" if MOUNT_UNLOCKED else "·未解锁"))
                    continue
                if cmd == 220:
                    # 修行任务面板（35 级开放，quest_type5 = [修行]）
                    # 真服 23:52:55 实证：
                    #   C->S 220(空) -> S->C 659(0000) + 659(cd03fb00) + 220(19B)
                    #   220 载荷 = [cd04b0 刷新倒计时1200][00 00 00][04 00]
                    #               + 6 组 [序号][难度/状态]，末组 0c 03（0c=12 个任务位）
                    #   紧接着再发一次 220，倒计时变 cd04af(1199)，说明客户端在轮询。
                    conn.sendall(pack(659, 0, bytes.fromhex("0000")))
                    conn.sendall(pack(659, 0, bytes.fromhex("cd03fb00")))
                    _b220 = bytes.fromhex(
                        "cd04b000000004000303010603020903030c03")
                    conn.sendall(pack(220, seq, _b220))
                    log("c%03d -> cmd=220 seq=%-4d len=%d [修行面板 12任务位]"
                        % (cid, seq, len(_b220)))
                    continue
                if cmd == 250:
                    # 修行任务明细：真服 23:52:56 实证 109B
                    #   [0c 12 条] + 每条 [varint 任务id][0100/ff00][0100/ff00][ff00]
                    #   任务 id 10001~10012（cd2711~cd271c）
                    _b250 = bytes.fromhex(
                        "0ccd271101000100ff00cd27120100ff00ff00cd271301000100"
                        "ff00cd27140100ff00ff00cd271501000100ff00cd2716ff00ff"
                        "00ff00cd2717ff00ff00ff00cd27180100ff00ff00cd27190100"
                        "0100ff00cd271a0100ff00ff00cd271b01000100ff00cd271c01"
                        "000100ff00")
                    conn.sendall(pack(250, seq, _b250))
                    log("c%03d -> cmd=250 seq=%-4d len=%d [修行任务 12 条]"
                        % (cid, seq, len(_b250)))
                    continue
                if cmd == 1282:
                    # 真服 13 次样本同一份 18B（仙蛋/仙灵相关，20:16 采集仙蛋时反复出现）
                    _b = bytes.fromhex("ce000186a2ce00151a03ce00151a03010001")
                    conn.sendall(pack(1282, seq, _b))
                    log("c%03d -> cmd=1282 seq=%-4d len=%d [仙蛋/仙灵 原包]"
                        % (cid, seq, len(_b)))
                    continue
                if cmd == 31:
                    # 真服实证（15:39:25）：31（接任务后客户端发的刷新，payload 0a）
                    # 无同号应答，只推 29/28/30/178。
                    send_push(conn, cid, 29)
                    conn.sendall(pack(28, 0, build_28()))
                    send_push(conn, cid, 30)
                    send_push(conn, cid, 178)
                    log("c%03d .. push 29/28(动态)/30/178 [cmd=31 刷新]" % cid)
                    continue
                if cmd == 214 and len(payload) >= 5 and payload[0] == 0xCE:
                    # 真服实证（20:46:30 三样本）：交任务 = 同号空应答，随后按序
                    # 推 29 -> 28(动态) -> 30 -> 178 -> 210(下一环进行中 8B)。
                    # 奖励入账（exp/绑铜）由 QUEST_REWARD 查表，28 动态构造
                    # ——这是"卡 9 级"的修复点：28 里等级/经验随交任务增长。
                    qid_int = int.from_bytes(payload[1:5], "big")
                    conn.sendall(pack(214, seq, b""))
                    log("c%03d -> cmd=214 seq=%d [交任务 %d 空应答]"
                        % (cid, seq, qid_int))
                    if qid_int in cur().done_quests:
                        # 同一个任务被反复提交（重登后客户端从本地进度重跑任务链
                        # 时必然出现）。以前这里只回 28，缺 29/30/178/210，
                        # 客户端任务面板就停在旧环：转职任务表现为「交不掉、反复接」。
                        # 现在走与正常交任务完全一致的推送序列，只是不发奖励。
                        log("c%03d .. 任务 %d 已交过，不发奖励但仍推进任务链"
                            % (cid, qid_int))
                        send_push(conn, cid, 29)
                        conn.sendall(pack(28, 0, build_28()))
                        if cur().equip or cur().wing[0]:
                            send_push(conn, cid, 30)
                        # 重复交任务也要把当前称号重发一次（同上，客户端刷新会丢）
                        send_push(conn, cid, 88)
                        nxt0 = QUEST_NEXT.get(qid_int)
                        if nxt0:
                            conn.sendall(pack(210, 0, quest_210_doing(nxt0)))
                            log("c%03d .. push cmd=210 len=8 [下一环 %d 进行中(重复交)]"
                                % (cid, nxt0))
                        active_qid = (b"\xce" + nxt0.to_bytes(4, "big")) if nxt0 else None
                        set_quest(nxt0 or 0, False)
                        continue
                    _sk_before = skills_for(cur().cls, cur().level)
                    _lv_before = cur().level
                    cur().done_quests.add(qid_int)
                    _rw = QUEST_REWARD.get(qid_int, (0, 0))
                    add_reward(_rw[0], _rw[1])
                    # ★ 2026-11-24 16:5x：任务奖励物品（add_item,id,绑定,数量）。
                    # 54 条任务带物品，以前全部丢掉 —— 欧冶子那几环的强化石
                    # （20138/20141 等）交完看不到就是这个原因。
                    # 只写服务端背包，客户端下次拉 181 会拿到全量，不用主动推。
                    _qitems = QUEST_ITEMS.get(qid_int) or []
                    for _iid, _qty in _qitems:
                        if _iid in PET_EQUIP_IDS:
                            # 妖灵：进 spirit_bag（保持未上阵），不占背包格
                            _nx = max([v.get("idx", 0)
                                       for v in cur().spirit_bag.values()] or [0])
                            for _ in range(max(1, _qty)):
                                _nx += 1
                                cur().spirit_bag[str(_iid)] = {"idx": _nx,
                                                               "slot": 0}
                            log("[spirit] 任务 %d 奖励妖灵 %d x%d（未上阵）"
                                % (qid_int, _iid, _qty))
                        else:
                            _slot = bag_add(_iid, _qty)
                            log("[quest] %d 奖励物品 %d x%d -> 槽%d"
                                % (qid_int, _iid, _qty, _slot))
                    # 400501「物归原主」= 剧情发神装。真服这一环交完，30 包里
                    # 立刻就是 11108~11114 已穿戴（cd2b64..cd2b6a），说明是
                    # 直接进装备栏、不是进背包。本地照做：装备栏空就整套穿上，
                    # 否则后面 400903「强化神装」锻造界面会是「没有东西强化」。
                    if qid_int == 400501 and not cur().equip:
                        _cls = cur().cls if 1 <= cur().cls <= 5 else 1
                        _bs = 10000 + _cls * 1000 + 108
                        for _s in range(1, 8):
                            cur().equip[_s] = _bs + _s - 1
                        save_state()
                        log("[quest] 400501 神装入装备栏 %d~%d（职业%d）"
                            % (_bs, _bs + 6, _cls))
                    # ★ 2026-11-24 16:0x：400702「前世仙宠」交任务发宠物。
                    # 真服 15:41:13 现场（relay.log 984 行）：
                    #   214(400702) -> 957(82B 功夫熊猫) -> 29 -> 28 -> 30 -> 210
                    # 957 就是「获得宠物」，客户端收到后宠物栏/地图跟随才出现；
                    # 本地服以前从来没发过 957，所以新号一直是干净的空号。
                    # 这里只在这一环发一次，重复交任务走上面的分支不会重复给。
                    if qid_int == 400702 and not cur().pets:
                        cur().pets.append(10000)      # Pet.xml 10000 = 功夫熊猫
                        save_state()
                        conn.sendall(pack(957, 0, bytes.fromhex(
                            # 前 80B 与真服 15:41:14 原包逐字节一致；
                            # relay.log 的 hex 只留 80 字节，尾部 2B 按 0 补齐到 len=82。
                            "01ace58a9fe5a4abe7868ae78cab010001ccdeccfc33cd0806"
                            "cd0322ccb633663300cd02b1010101010119191919cd271008"
                            "00000000000000000000000000000000000000000000000000"
                            "00000000000000")))
                        log("[pet] ★ 交任务 400702「前世仙宠」-> 957 获得功夫熊猫(10000)")
                    send_push(conn, cid, 29)
                    _b28 = build_28()
                    conn.sendall(pack(28, 0, _b28))
                    log("c%03d .. push cmd=28 len=%d [动态 Lv%d exp=%d coin=%d]"
                        % (cid, len(_b28), cur().level, cur().exp,
                           cur().coin_bound[0]))
                    # ★ 2026-11-24 14:3x：交任务后**不推 178**。
                    # 真服两个独立样本（15:37:08 未转职号 / 23:55:41 主号）都是
                    #   214 -> 29 -> 28 -> [30] -> 210，一次 178 都没有。
                    # 178 是背包全量（客户端按包整体替换），服务端背包记不全时
                    # 一推就把玩家看到的东西「洗掉」——用户反馈的「交完任务背包
                    # 空了」就是这么来的。背包变动只该由 181/182/149/831 触发。
                    # 同理 30 只在身上真有装备/翅膀时推；空装备推 82B 模板会把
                    # 客户端外观刷成抓包号的快照。
                    if cur().equip or cur().wing[0]:
                        send_push(conn, cid, 30)
                    # ★ 2026-11-24 15:2x：称号**不能只在跨 5 级时推**。
                    # 用户实测：第一次登录头顶有「神界新丁」，交完任务就没了。
                    # 登录那次 87->88 是 21B（有称号），交任务后客户端拿到新的
                    # 28（等级/经验变了）会把称号栏按「未知」重算，服务端不补
                    # 88 就一直空着。真服交任务序列里没有 88，是因为那边称号
                    # 状态另有来源；本地服只能靠每次刷新重发一份当前真值。
                    if cur().level != _lv_before:
                        send_push(conn, cid, 88)
                        log("[title] Lv%d -> Lv%d，补推 88（%s）"
                            % (_lv_before, cur().level,
                               "神界新丁" if cur().level >= 5 else "无称号"))
                    nxt = QUEST_NEXT.get(qid_int)
                    if nxt:
                        conn.sendall(pack(210, 0, quest_210_doing(nxt)))
                        log("c%03d .. push cmd=210 len=8 [下一环 %d 进行中]"
                            % (cid, nxt))
                    active_qid = (b"\xce" + nxt.to_bytes(4, "big")) if nxt else None
                    set_quest(nxt or 0, False)
                    # 升级带来新技能时补推 306。真服 19:21:02 实证：交完 401004
                    # 升到 12 级，紧接着 210 后面就是 306 = 02 10101 10201。
                    _sk_after = skills_for(cur().cls, cur().level)
                    if _sk_after != _sk_before:
                        send_push(conn, cid, 306)
                        log("[skill] 升级解锁 %s（Lv%d）"
                            % ([hex(x) for x in _sk_after], cur().level))
                    continue

                if cmd == 826:
                    # 826 = 把某件装备放上锻造台（点锻造面板里的装备格子）
                    # 真服 00:16:15~00:16:22 连续 6 次实证（每次只点一格）：
                    #   C->S 826 010a -> S->C 30 全量外观 + 818 单槽29B + 178
                    #                       + 29 属性 + 28 货币 + 826 空包
                    #   826 020a -> 818 0103 …（载荷[0] 直接就是槽位-1）
                    #   最后跟的 0a 是「装备锻造」这个面板类型，不是槽位。
                    # 本地以前完全不认 826：点了没反应，锻造台永远是空的，
                    # 右边「升阶 / 升品」按钮也就一直是灰的（点不动、不发包）。
                    _raw826 = payload or b""
                    # 部位码和 822 同一套（真服 826 000a/010a/020a/030a/040a/050a
                    # 分别对应 818 回包的 槽1~槽6；0a0a 是武器=槽7）。
                    _p826 = _raw826[0] if _raw826 else 0
                    if _p826 == 0x0A:
                        _slot826 = 7
                    elif 0 <= _p826 <= 5:
                        _slot826 = _p826 + 1
                    else:
                        _slot826 = 7
                    send_push(conn, cid, 30)
                    conn.sendall(pack(818, 0, build_818([_slot826])))
                    send_push(conn, cid, 178)
                    send_push(conn, cid, 29)
                    conn.sendall(pack(28, 0, build_28()))
                    conn.sendall(pack(826, seq, b""))
                    log("[forge] 826 放上锻造台 槽%d 物品%d +%d（载荷 %s）"
                        % (_slot826, cur().equip.get(_slot826, 0),
                           cur().enhance.get(_slot826, 0), _raw826.hex()))
                    continue

                if cmd == 822:
                    # 822 = 装备强化
                    #   C->S 822 len=6 hex=0a0a01cd4eaa   真服 18:20:20
                    #   S->C 822 空 -> 178 -> 818(单槽 29B) -> 29 -> 28 -> 30
                    #
                    # 载荷拆解（真服 18:30:40~58 连续 18 次强化逐条反查）：
                    #   [0] 部位码  [1] 材料槽/类型  [2] 数量  [3..] 材料 item id varint
                    #   部位码 → 装备槽（真服 818 回包对照，不是 1:1）：
                    #     00→槽1 11108 / 01→槽2 11109 / 02→槽3 11110
                    #     03→槽4 11111 / 04→槽5 11112 / 05→槽6 11113 / 0a→槽7 11114
                    #   cd4eaa=20138 初级强化仙玉，cd4ead=20141 初级强化灵石
                    #
                    # 铜钱消耗（真服 28 包 idx18 绑定铜钱逐次差额，误差 0）：
                    #   +0→+1 1500 / +1→+2 3000 / +2→+3 6000  => 1500 * 2^当前等级
                    #   该账号铜钱=0，实际扣的是「绑定铜钱」→ 先扣铜钱、不足再扣绑铜。
                    raw = payload or b""
                    if raw and raw[0] == 0x0A:
                        slot = 7
                    elif raw and 0 <= raw[0] <= 5:
                        slot = raw[0] + 1
                    else:
                        slot = 7
                    # 材料 id：真服从第 4 字节起是 varint；本地客户端短包(010a00)没有材料段
                    mat = 0
                    if len(raw) >= 5 and raw[3] == 0xCE:
                        mat = int.from_bytes(raw[4:8], "big")
                    elif len(raw) >= 4 and raw[3] == 0xCD:
                        mat = int.from_bytes(raw[4:6], "big")
                    elif len(raw) >= 4 and raw[3] == 0xCC:
                        mat = raw[4]

                    # ---- 失败提示必须说清是哪一条，不能再笼统「强化失败」 ----
                    def _forge_fail(code, detail, hint="", toast=False):
                        """回 822 + 结果码，客户端自己弹 Forge_<code> 的文案。

                        以前回 cmd=1 文本，锻造面板等不到 822 就兜底弹
                        Forge_22「很遗憾，强化失败了」，钱不够也说不清原因。
                        toast=True 时再补一条 04da 长文本（给「换更高级石头」
                        这类客户端没有固定文案的情况）。
                        """
                        if FORGE_CODE_MODE:
                            conn.sendall(pack(822, seq, bytes([code & 0xFF])))
                            if toast:
                                conn.sendall(pack(1, seq, err_text(detail)))
                        elif code == FORGE_NO_COIN:
                            # 真服商城原话：cmd=1 + 04b2「对应货币不足」
                            conn.sendall(pack(1, seq,
                                              bytes.fromhex("04B2")
                                              + "对应货币不足".encode("utf-8")))
                        else:
                            conn.sendall(pack(1, seq, err_text(detail)))
                        log("[forge] 822 槽%d +%d 拒绝[code=%d] %s%s（载荷 %s）"
                            % (slot, cur().enhance.get(slot, 0), code, detail,
                               (" 处理建议:%s" % hint) if hint else "", raw.hex()))

                    if not cur().equip.get(slot):
                        _forge_fail(FORGE_NO_EQUIP, "该部位没有装备")
                        continue

                    _lv = cur().enhance.get(slot, 0)
                    if _lv >= ENH_MAX:
                        _forge_fail(FORGE_MAX, "强化等级最高了（+%d）" % ENH_MAX)
                        continue

                    # 强化石：Item.xml 20138~20154
                    #   仙玉(武器/戒指/坐骑) 20138/20139/20140/20456 -> +3/+6/+9/+12
                    #   灵石(防具/护符/时装/翅膀/法宝) 20141/20142/20143/20457 -> 同上
                    if not mat:
                        _forge_fail(FORGE_NO_MAT, "请先放入强化石",
                                    "武器用强化仙玉，防具用强化灵石")
                        continue
                    _st = STONE.get(mat)
                    if _st is None:
                        _forge_fail(FORGE_NO_MAT,
                                    "%s 不能用于装备强化" % item_name(mat))
                        continue
                    _kind, _cap, _luck = _st
                    # 类别校验：槽7 是武器 → 仙玉；槽1~6 是防具 → 灵石
                    _need = "仙玉" if slot == 7 else "灵石"
                    if _kind != _need:
                        _forge_fail(FORGE_NO_MAT,
                                    "该部位只能用强化%s强化" % _need,
                                    "你现在放的是强化%s" % _kind, toast=True)
                        continue
                    if _lv >= _cap:
                        _forge_fail(FORGE_NO_MAT,
                                    "%s只能强化到+%d" % (item_name(mat), _cap),
                                    "请换更高一级的强化石", toast=True)
                        continue
                    _hit = None
                    for _p, _v in cur().bag.items():
                        if _v[0] == mat and _v[1] >= 1:
                            _hit = _p
                            break
                    if _hit is None:
                        _forge_fail(FORGE_NO_MAT,
                                    "%s 数量不足" % item_name(mat))
                        continue

                    # 铜钱：1500 * 2^当前强化等级
                    cost = 1500 << _lv if _lv < 16 else 0x7FFFFFFF
                    _pay_tq = min(cur().tongqian[0], cost)
                    _pay_bc = cost - _pay_tq
                    if cur().coin_bound[0] < _pay_bc:
                        _forge_fail(FORGE_NO_COIN, "对应货币不足",
                                    "本次需铜钱 %d" % cost)
                        continue
                    # ---- 成功率判定（服务端判，客户端只显示成功率）----
                    # 基础值随等级递减 + 强化石自带加成（Item.xml：初5% 中8% 高12% 特15%）
                    # 用户实证：失败会掉 1 级，但 +3/+6/+9 是保底档，不会掉下去。
                    _ok, _rate, _newlv = forge_decide(_lv, _luck)

                    if _hit is not None:
                        cur().bag[_hit][1] -= 1
                        if cur().bag[_hit][1] <= 0:
                            del cur().bag[_hit]
                    cur().tongqian[0] -= _pay_tq
                    cur().coin_bound[0] -= _pay_bc

                    if _ok:
                        cur().enhance[slot] = _newlv
                        save_state()
                        conn.sendall(pack(822, seq, b""))     # 真服成功 = 同号空包
                        log("[forge] 822 部位0x%02x 槽%d 物品%d +%d->+%d 成功"
                            " (率%d%% 含石+%d%%) 材料%d 扣铜%d(铜钱%d+绑铜%d)"
                            " 余 铜钱%d 绑铜%d 战力%d[%s]（载荷 %s）"
                            % (raw[0] if raw else -1, slot, cur().equip.get(slot, 0),
                               _lv, cur().enhance[slot], _rate, int(_luck), mat, cost,
                               _pay_tq, _pay_bc, cur().tongqian[0], cur().coin_bound[0],
                               power_now(), enh_breakdown(), raw.hex()))
                    else:
                        # 失败掉 1 级；0~2 掉到 0、3~5 掉到 3、6~8 掉到 6、9~11 掉到 9
                        _floor = enh_floor(_lv)
                        _nf = _newlv
                        cur().enhance[slot] = _nf
                        save_state()
                        # 22 = Forge_22「很遗憾，强化失败了」，客户端自己弹这句
                        conn.sendall(pack(822, seq, bytes([FORGE_FAIL])))
                        log("[forge] 822 部位0x%02x 槽%d 物品%d +%d->+%d 失败"
                            " (率%d%% 含石+%d%% 保底+%d) 材料%d 扣铜%d"
                            " 余 铜钱%d 绑铜%d 战力%d（载荷 %s）"
                            % (raw[0] if raw else -1, slot, cur().equip.get(slot, 0),
                               _lv, _nf, _rate, int(_luck), _floor, mat, cost,
                               cur().tongqian[0], cur().coin_bound[0],
                               power_now(), raw.hex()))
                    # 成功/失败都要整轮刷新：818 等级、29 战力、28 货币、178 背包、30 外观
                    send_push(conn, cid, 178)
                    conn.sendall(pack(818, 0, build_818([slot])))
                    send_push(conn, cid, 29)
                    conn.sendall(pack(28, 0, build_28()))
                    send_push(conn, cid, 30)
                    continue

                if cmd == 831:
                    # 831 = 材料合成（强化石升档，4 合 1）
                    # 真服 19:23:02 样本：01 cd4ead 01 04
                    #   先推 178 全量背包 -> 再回 831 空包（没有 28/29/30）
                    # 以前没实现：客户端点合成没反应，或者要等 5 秒超时。
                    _m = 0
                    _times = 1
                    _per = 4
                    try:
                        if len(payload) >= 4:
                            _m, _j = bag_proto.dec_val(payload, 1)
                            _times = payload[_j] if len(payload) > _j else 1
                            _per = payload[_j + 1] if len(payload) > _j + 1 else 4
                    except Exception as e:
                        log("[synth] 831 载荷异常 %s: %s" % (payload.hex(), e))
                    _out, _msg = synth_apply(_m, _times, _per)
                    if _out is None:
                        log("[synth] 831 拒绝：%s（载荷 %s）" % (_msg, payload.hex()))
                        conn.sendall(pack(1, seq, err_text(_msg)))
                        continue
                    save_state()
                    # 真服顺序：178 全量背包在前，831 空包在后
                    send_push(conn, cid, 178)
                    conn.sendall(pack(831, seq, b""))
                    log("[synth] 831 %s（载荷 %s）" % (_msg, payload.hex()))
                    continue

                if cmd == 1265:
                    # 1265 = 客户端要场景数据 → 1266 seq=0 推送（真服恒为推送形式）。
                    # 1266 是活动/福利表（1378B，以 "jingcaihuodong" 开头），与地图无关，
                    # 所有场景都用同一份；地图切换真正靠的是 129 的场景号 + 113 的对象表。
                    _scene = TPL_PUSH.get(1266)
                    if _scene:
                        conn.sendall(pack(1266, 0, _scene))
                        log("c%03d .. push cmd=1266 len=%d [活动表]"
                            % (cid, len(_scene)))
                    # 113 是「本场景刷怪点/采集点」，必须跟着 cur_map 走；
                    # 以前这里无条件发天音谷那份，蓬莱仙境才会冒出玄蜂。
                    if last_113 != cur_map:
                        body113 = build_113(cur_map)
                        conn.sendall(pack(113, 0, body113))
                        last_113 = cur_map
                        log("c%03d .. push cmd=113 len=%d [场景 %s(%d) 对象表]"
                            % (cid, len(body113), scene_name(cur_map), cur_map))
                    continue

                if cmd == 32513:
                    # 客户端确认「我已加载场景 X」（payload = 场景号 + 00 00）。
                    # ★ 2026-11-24 15:1x 定稿：**以服务端存档场景为准**。
                    # 客户端的本地存档会记着上次位置（天音谷底 1021），登录后它
                    # 先在 1021 把地图和怪建出来、再发 32513 报 1021；而 17/113
                    # 按存档发的是 101 → 蓬莱的「仙界守卫」叠到天音谷底的
                    # 「万年灵芝/作乱小妖」上，还多一只无名熊猫（三份证据：
                    # 采集点 102005=万年灵芝 vs 101101=朱果、
                    # 刷怪点 121302=作乱小妖属 1021、111101=仙界守卫属 101）。
                    # 所以这里不再跟着客户端跑，而是用 129 把它送回存档场景，
                    # 客户端重新加载 → 旧图对象全部销毁，不会叠加。
                    sid = dec_scene(payload)
                    if sid and sid != cur_map:
                        _pos = arrival_pos(cur_map, cur_map)
                        if _pos == (5, 7):
                            _pos = (21, 7)   # 蓬莱广场中心（101 的 (5,7) 贴左墙）
                        conn.sendall(pack(129, 0, enc_scene(cur_map) + bytes(_pos)))
                        conn.sendall(pack(113, 0, build_113(cur_map)))
                        conn.sendall(pack(117, 0, b"\x00"))
                        last_113 = cur_map
                        log("c%03d !! 客户端场景 %s(%d) 与存档 %s(%d) 不符，回 129 送回"
                            % (cid, scene_name(sid), sid,
                               scene_name(cur_map), cur_map))
                    continue

                ex = extra_for(cmd)
                if ex is not None:
                    ec, ep_hex, eseq = ex
                    body = bytes.fromhex(ep_hex) if ep_hex else b""
                    resp = pack(ec, seq if eseq == "echo" else int(eseq), body)
                    conn.sendall(resp)
                    log("c%03d -> cmd=%-6d seq=%-4d len=%d  [人工响应] hex=%s"
                        % (cid, ec, seq, len(body), body[:24].hex()))
                    continue

                if cmd == 1313:
                    # 1313 = 主界面左侧「角色快捷入口」点击，payload 单字节 = 页签号。
                    # 2026-11-23 实测本地服静默丢弃 → 客户端原地转圈，永远等不到数据。
                    # 真服 00:22:04~00:22:07 连点三个页签，完整回包是：
                    #   C->S 1313 <tab>  ->  S->C 2(空ACK,seq=echo) + 354(6B) + 1314(seq=0)
                    #   1314 = 0c <tab> 00      例如 1313 02 -> 0c0200、1313 0a -> 0c0a00
                    # 末尾 00 = 该页签没有条目（有称号/信息时后面才跟数据块，见 34B/48B 样本）。
                    # 354 的语义还没定位（6~7B，内容随状态变：ffffff/ccf20c/ccf30c0601），
                    # 不确定就不发 —— 乱发未知包比不发更容易把客户端打死。
                    tab = payload[0] if payload else 0x02
                    conn.sendall(pack(2, seq, b""))
                    body14 = bytes([0x0C, tab, 0x00])
                    conn.sendall(pack(1314, 0, body14))
                    log("c%03d -> cmd=2 ACK + 1314 seq=0 len=%d [角色页签 %02x] hex=%s"
                        % (cid, len(body14), tab, body14.hex()))
                    continue

                if cmd == 211 and len(payload) >= 5:
                    active_qid = payload[:5]   # 记住接的是哪个任务，供 124/149 推 210
                    set_quest(qid_of(active_qid), False)   # 落盘，重连后还能推 210

                bi = builtin_resp(cmd, payload)
                if bi is not None:
                    bc, body = bi[0], bi[1]
                    conn.sendall(pack(bc, seq, body))
                    log("c%03d -> cmd=%-6d seq=%-4d len=%d  [内置应答] hex=%s"
                        % (cid, bc, seq, len(body), body[:24].hex()))
                    # 对话型任务（task=4）接取即满足条件——真服 211 应答尾 01 01
                    # 就是这个语义。链上 327 环里有 195 环是 task=4。
                    # ★ 注意：可交状态只写在 211 应答里，**不能再补推 210**。
                    #   真服 15:38:09 / 15:39:24 两个样本都证明：211 回
                    #   ce..12 0101 之后没有任何 210 推送，客户端直接就能交。
                    #   以前这里补推了一个 210(9B 可交)，客户端把它当成「又接了一次」，
                    #   任务面板就出现同一条任务两份 → 用户反馈的「完成立即接两次」。
                    if cmd == 211 and QUEST_TASK.get(int.from_bytes(payload[1:5], "big")) == 4:
                        _qi = int.from_bytes(payload[1:5], "big")
                        set_quest(_qi, True)
                        log("c%03d .. 对话任务 %d 接取即满足（可交状态随 211 应答下发）"
                            % (cid, _qi))
                    # 400403「继续修行」= task=11「去蓬莱选一个职业」，
                    # 走的就是 35 转职 → 36。转完必须转「可交」，否则链断在这里。
                    # 放在 29/28/30/178 之后发，贴合真服转职样本的推送顺序。
                    # 820 结构实验：probe_820.hex 存在时，在 818 之后再补一个 820。
                    # 820 正样本一直没抓到，空包/00 都会 SIGABRT，
                    # 所以留一个「改文件即生效」的探针，方便二分包体。
                    if cmd == 819:
                        pr = probe_820()
                        if pr is not None:
                            conn.sendall(pack(820, seq, pr))
                            log("c%03d -> cmd=820    seq=%-4d len=%d  "
                                "[实验 820] hex=%s"
                                % (cid, seq, len(pr), pr.hex()[:96]))
                    # 第三段返回值 = 应答后要跟随的推送模板 cmd（如商城购买后推 29/28）
                    if len(bi) > 2:
                        for pc in bi[2]:
                            send_push(conn, cid, pc)
                            sent_cmds.add(pc)
                    # 400802 回梦迷境 = task="35,1"「进迷宫 1 次」。
                    # 20 级以后点进副本（1859）就算目标达成 -> 推 210 可交，
                    # 否则这一环永远停在「进行中」，后面的 400803/400901 全走不到。
                    if cmd == 1859 and maze_ready[0] and active_qid and \
                            QUEST_TASK.get(qid_of(active_qid)) == 35:
                        _qi = qid_of(active_qid)
                        conn.sendall(pack(210, 0, quest_210_ready(_qi)))
                        set_quest(_qi, True)
                        log("c%03d .. push cmd=210 len=9 [迷宫任务 %d 进副本即算完成]"
                            % (cid, _qi))
                    # 400403「继续修行」= task=11「去蓬莱选一个职业」，
                    # 走的就是 35 转职 → 36。转完必须转「可交」，否则链断在这里。
                    # 真服 15:39:02 顺序是 36 -> 29 -> 28 -> 210(9B 可交) -> 306，
                    # 所以 210 要排在 306 前面。
                    if cmd == 35 and active_qid and \
                            QUEST_TASK.get(qid_of(active_qid)) == 11:
                        _qi = qid_of(active_qid)
                        conn.sendall(pack(210, 0, quest_210_ready(_qi)))
                        set_quest(_qi, True)
                        log("c%03d .. push cmd=210 len=9 [选职业任务 %d 转职后即可交]"
                            % (cid, _qi))
                    # ★ 转职必须补 306：真服 15:39:02 样本为
                    #   36 -> 29 -> 28 -> 210 -> 306(10B) -> 1266
                    # 306 = 当前出手技能：01 + [CD+u16 技能ID] + 6×00。
                    # 技能号规则（Skill.xml 实证）：职业1..5 -> 10101/20101/
                    # 30101/40101/50101；未转职是 60101「仙人指路」。
                    # 少了这个包，客户端技能栏/战斗技能仍是初级的——这就是
                    # 「跑图是转职皮肤、一进战斗打回原形」的直接原因。
                    if cmd == 35:
                        _skb = build_306()
                        conn.sendall(pack(306, 0, _skb))
                        log("c%03d .. push cmd=306 len=%d [职业技能 %s]"
                            % (cid, len(_skb),
                               {1: "御剑10101", 2: "逍遥20101", 3: "幻羽30101",
                                4: "法尊40101", 5: "破军50101"}.get(cur().cls, "未转职")))
                    # 交任务后推下一环任务状态（真服 15:37:08：214 → 210(8B) 下一环进行中）
                    if cmd == 214 and len(payload) >= 5 and payload[0] == 0xCE:
                        qid_int = int.from_bytes(payload[1:5], "big")
                        nxt = QUEST_NEXT.get(qid_int)
                        if nxt:
                            conn.sendall(pack(210, 0, b"\x00\x01" + b"\xce"
                                              + nxt.to_bytes(4, "big") + b"\x01"))
                            log("c%03d .. push cmd=210 len=8 [下一环 %d 进行中]"
                                % (cid, nxt))
                    continue

                j = None
                for k in range(ptr, len(SEQ)):
                    # seq 会循环复用，必须同时校验 cmd，否则后期请求会撞上
                    # 抓包早期的同 seq 记录，回出类型不对的包。
                    # 必须同时接受 cmd 与 cmd+1：cmd=17 是双向命令（登录请求 17
                    # → 登录响应也是 17，152 字节），只认 cmd+1 会把登录响应丢掉，
                    # 客户端等 5 秒超时后报「网络连接失败」。
                    if SEQ[k][1] == seq and SEQ[k][0] in (cmd, cmd + 1):
                        j = k
                        break
                    if k - ptr > 12:          # 跳太远说明是误匹配，别硬凑
                        break
                if j is not None:
                    for c2, s2, p2 in SEQ[ptr:j + 1]:
                        # 1266 是 1378 字节的大推送。早前误以为它在场景加载后触发
                        # 背景音乐播放（createMediaplayer b6.mp3）导致 native 段错误，
                        # 后续实测它经 TPL_PUSH 正常下发也没有崩——崩溃真因是 cmd=1。
                        # 这里保留跳过只是保守起见，不是已证实的崩溃点。
                        if c2 in (1266, 113):
                            # 1266/113 由 1265、128 分支统一下发（真服样本），
                            # 回放里的旧版本再发一次会造成对象重复刷/场景错乱。
                            log("c%03d .. 跳过回放 cmd=%d（由场景分支统一下发）"
                                % (cid, c2))
                            continue
                        # 同一推送不能发两次：客户端日志里 got packet cmd=178 出现两遍，
                        # 重复推送会让它的 UI 状态机错位。
                        if s2 == 0 and c2 in sent_cmds:
                            log("c%03d .. 跳过重复推送 cmd=%d" % (cid, c2))
                            continue
                        # 回放里的 28 是抓包当时的旧等级快照（1~2 级），
                        # 直接回放会把角色打回低级；统一换成 build_28() 动态构造。
                        if c2 == 28 and s2 == 0:
                            p2 = build_28()
                        # 登录 17 是「装备/外观」的总入口：模板里装备位全 0，
                        # 客户端就认为身上没穿东西，点装备格才去发 819。
                        # 这里换成动态构造，装备栏才有数据（真服 197B 对齐）。
                        if c2 == 17:
                            p2 = build_17(cur_map)
                            log("c%03d .. cmd=17 动态化 len=%d 场景=%s(%d) 装备=%s 外观=%s"
                                % (cid, len(p2), scene_name(cur_map), cur_map,
                                   sorted(cur().equip.items()) or "空",
                                   cur().wing[0]))
                        # 登录回放里的 1922 是抓包当时的「空翅膀面板」（33B）。
                        # 玩家已经装过翅膀的话，必须换成 48B 那份，否则重登后
                        # 翅膀就没了（真服是把状态存在自己身上）。
                        if c2 == 1922 and cur().wing[0]:
                            p2 = wing_proto.body(1922, cur().wing[0])
                        # 回放里的 30 同样是抓包那个号的装备快照（11108~11114），
                        # 新角色会被要求渲染不存在的装备 → GLThread 段错误。
                        if c2 == 30 and not cur().wing[0]:
                            _b30 = build_30()
                            if _b30:
                                p2 = _b30
                        # 回放里的 306 是抓包当时未转职号的技能栏
                        # （60101 仙人指路 / 60201 无极气刃）。转职后重登
                        # 如果原样下发，技能栏会被刷回未转职 → 战斗中
                        # 只剩初始技能。统一换成按当前职业动态构造。
                        if c2 == 306:
                            p2 = build_306()
                        # 回放里的 29 是抓包当时的战力快照。强化过的号重登后
                        # 战力会被刷回旧值，所以统一换成本地按装备算出来的包。
                        if c2 == 29 and s2 == 0:
                            _b29 = build_29()
                            if _b29:
                                p2 = _b29
                                log("c%03d .. cmd=29 动态化 len=%d 战力=%d"
                                    % (cid, len(p2), power_now()))
                        conn.sendall(pack(c2, s2, p2))
                        if s2 == 0:
                            sent_cmds.add(c2)
                        log("c%03d -> cmd=%-6d seq=%-4d len=%d%s"
                            % (cid, c2, s2, len(p2), "  [推送]" if s2 == 0 else ""))
                        # ★ 真服 21:48:09 登录现场：17 -> 113(33B) -> 117(1B) 连推三条，
                        # 客户端根本没发请求要这两个包。本服以前把回放里的 113 跳过、
                        # 117 也不发，要等 1265 才补 113 —— 登录 loading 就卡住转圈。
                        # 这里在 17 发完的同一时刻补上（113 用当前场景动态生成）。
                        if c2 == 17 and not login_pushed[0]:
                            login_pushed[0] = True
                            _b113 = build_113(cur_map)
                            conn.sendall(pack(113, 0, _b113))
                            last_113 = cur_map
                            conn.sendall(pack(117, 0, b"\x00"))
                            log("c%03d .. push cmd=113 len=%d [登录即推 %s(%d)]"
                                " + cmd=117 len=1"
                                % (cid, len(_b113), scene_name(cur_map), cur_map))
                        # 登录时真服只推 1922；装了翅膀要补 1931/1932，
                        # 否则角色面板/外观那栏读不到翅膀对象。
                        if c2 == 1922 and cur().wing[0]:
                            for wc in (1931, 1932):
                                wb = wing_proto.body(wc, cur().wing[0])
                                conn.sendall(pack(wc, 0, wb))
                                sent_cmds.add(wc)
                                log("c%03d -> cmd=%-6d seq=0    len=%d  [翅膀补推]"
                                    % (cid, wc, len(wb)))
                    ptr = j + 1
                    # 回放全部发完后补推任务状态：放在 17 那一刻会被加载流程吃掉，
                    # 放在这里客户端已经进场景了，210 才会真正更新任务面板。
                    if ptr >= len(SEQ) and not quest_pushed:
                        quest_pushed = True
                        if cur().quest_active:
                            # 按任务类型智能判定「可交」，避免重启后卡死在
                            # 那些条件已经天然满足的任务上（用户会看到一直重复）：
                            #   task=4  对话任务，接了即满足
                            #   task=11 选职业：角色已经有职业（默认 1）即视为已选
                            _tt = QUEST_TASK.get(cur().quest_active, 0)
                            _auto = (cur().quest_ready or _tt == 4
                                     or (_tt == 11 and 1 <= cur().cls <= 5))
                            qb = (quest_210_ready(cur().quest_active) if _auto
                                  else quest_210_doing(cur().quest_active))
                            conn.sendall(pack(210, 0, qb))
                            if _auto and not cur().quest_ready:
                                set_quest(cur().quest_active, True)
                            log("c%03d .. push cmd=210 len=%d [登录补推 %s %d]"
                                % (cid, len(qb),
                                   "可交" if _auto else "进行中",
                                   cur().quest_active))
                        else:
                            log("c%03d .. 日志：存档里没有进行中的任务" % cid)
                        # 登录后补推技能栏。抓包回放里那个号当时是未转职，
                        # 序列里没有 306；转职号重登如果不补，技能栏会是客户端
                        # 缓存的旧技能（进战斗再被打回 60101 那套）。
                        _skb = build_306()
                        conn.sendall(pack(306, 0, _skb))
                        log("c%03d .. push cmd=306 len=%d [登录补推技能栏 职业%d]"
                            % (cid, len(_skb), cur().cls))
                    continue

                # 209 = 客户端主动拉任务状态。默认走 TPL[210] 那份抓包快照
                # （永远 8B「某任务进行中」），重登后会把任务面板刷成别人的任务，
                # 表现为「转职完卡住 / 任务反复出现」。这里换成本服真实状态。
                if cmd == 209:
                    if cur().quest_active:
                        qb = (quest_210_ready(cur().quest_active) if cur().quest_ready
                              else quest_210_doing(cur().quest_active))
                    else:
                        qb = TPL.get(210, b"")
                    if qb:
                        conn.sendall(pack(210, seq, qb))
                        log("c%03d -> cmd=210 seq=%-4d len=%d [动态任务状态 %s%s]"
                            % (cid, seq, len(qb), cur().quest_active or "无",
                               "(可交)" if cur().quest_ready else ""))
                    continue

                # 305 = 拉技能栏。默认走 push_306 模板（永远 60101/60201 未转职技能），
                # 重登后技能栏会变回初级技能。这里按当前职业动态生成。
                if cmd == 305:
                    _b306 = build_306()
                    conn.sendall(pack(306, 0, _b306))
                    log("c%03d -> cmd=306 seq=0    len=%d [动态技能栏 职业%d]"
                        % (cid, len(_b306), cur().cls))
                    continue

                # ---- 宠物 / 妖灵（真服 2026-11-23 21:03 实测序列）----
                # 961 拉宠物栏 -> 962；946 拉选中详情 -> 947；
                # 973 是宠物界面初始化确认（回 1 字节 01）；
                # 1411 上阵/卸下妖灵 -> 1412，然后补推 1410 与 947。
                if cmd == 1409:
                    # ★ 2026-11-24 16:5x：1409 是「拉妖灵列表」，应答 1410。
                    # 以前没有专门分支，落到通用 `cmd+1 in TPL_PUSH` 直接回
                    # TPL_PUSH[1410]（3B `cc8000` = 0 只），所以任务给了妖灵、
                    # 存档里也有，客户端界面红框里却还是空的。
                    # 必须用动态构造（未上阵列表），并且用请求的 seq 回。
                    _b1410 = spirit_1410()
                    conn.sendall(pack(1410, seq, _b1410))
                    log("c%03d -> cmd=1410 seq=%-4d len=%d [妖灵列表 未上阵%d只 上阵%s] hex=%s"
                        % (cid, seq, len(_b1410), len(spirit_free_list()),
                           spirit_strap() or "无", _b1410.hex()))
                    continue
                if cmd == 961:
                    # ★ 2026-11-24 15:0x：962 = 「已拥有的宠物形象（皮肤）」列表。
                    # n2 就是形象数，客户端会按这个列表给玩家挂跟随外观。
                    # 老代码给空号发 22B（n2=3，主号的三个形象）→ 新号地图上
                    # 多出一只没有名字的熊猫（显示「？？」）。
                    # 真服 15:41:16 交任务刚拿到功夫熊猫时是 962=4B + 947=83B，
                    # 证明「有没有宠物」由 947 决定，962 只管形象数量。
                    _b = pet_proto.pack_962(pets=cur().pets)
                    conn.sendall(pack(962, seq, _b))
                    log("c%03d -> cmd=962 seq=%-4d len=%d [宠物形象 %d个]"
                        % (cid, seq, len(_b), len(cur().pets)))
                    continue

                if cmd == 946:
                    # ★ 2026-11-24 14:05：947 的模板里第一条就是「功夫熊猫」
                    # （01 ac e58a9fe5a4abe7868ae78cab …），它会被客户端当成
                    # “当前跟随宠物”在地图上渲染出来。新号没有宠物，
                    # 真服新号（954259122）整场都没出现过跟随宠，所以这里跳过。
                    if not cur().pets:
                        log("c%03d .. 跳过 cmd=947（当前角色没有宠物，真服新号也不回）"
                            % cid)
                        continue
                    _b = pet_proto.pack_947(spirit_strap())
                    conn.sendall(pack(947, seq, _b))
                    log("c%03d -> cmd=947 seq=%-4d len=%d [宠物详情 妖灵%s%s]"
                        % (cid, seq, len(_b), spirit_strap() or "无",
                           "" if pet_proto.has_tpl(spirit_strap())
                           else "（该组合无真服原包，退回无妖灵版）"))
                    continue

                if cmd == 973:
                    conn.sendall(pack(973, seq, b"\x01"))
                    log("c%03d -> cmd=973 seq=%-4d len=1" % (cid, seq))
                    continue

                if cmd == 950:
                    # 950 payload=0100 -> 951 回 1 字节（宠物窗口初始化的一环）。
                    # 取值就是 947 里那个计数：98B 那份是 02、99/105B 那份是 03，
                    # 与真服 15:41:18(02) / 21:02:46(03) 两个现场完全对上。
                    _b947 = pet_proto.pack_947(spirit_strap())
                    _n = _b947[16] if (cur().pets and len(_b947) > 16) else 0
                    conn.sendall(pack(951, seq, bytes([_n])))
                    log("c%03d -> cmd=951 seq=%-4d len=1 [计数 %d]" % (cid, seq, _n))
                    continue

                if cmd == 963:
                    # 真服打开宠物界面后连发 3 次 963(01)，每次都回 964，
                    # 且是「逐只累加」：g1 前 1 只 -> 前 2 只 -> 三只全。
                    # 40B 稳定态下 964 三档是 34/40/46B：
                    #   pets = g1[:k]，extra = g2 恒为三只，尾字节 = 3-k。
                    _all = list(pet_proto.PET_UIDS)
                    if _all:
                        pet_sel[0] = (pet_sel[0] + 1) % len(_all)
                        _pets = _all[:pet_sel[0] + 1]
                        _sel = _pets[-1]
                    else:
                        pet_sel[0], _pets, _sel = 0, [], 0
                    _b = pet_proto.pack_964(_pets, _sel, pet_proto.PET_SKIN)
                    conn.sendall(pack(964, seq, _b))
                    log("c%03d -> cmd=964 seq=%-4d len=%d [选中第%d只，列表%d只+形象%d只]"
                        % (cid, seq, len(_b), pet_sel[0] + 1, len(_pets),
                           len(pet_proto.PET_SKIN)))
                    continue

                if cmd == 965:
                    # 965 的 payload 是「一只宠物的 varint uid」= 设为出战/选中。
                    # 真服回空包，然后补推 947（详情）和 178（可交互对象）。
                    _u = 0
                    if payload[:1] == b"\xce" and len(payload) >= 5:
                        _u = int.from_bytes(payload[1:5], "big")
                    elif payload[:1] == b"\xcd" and len(payload) >= 3:
                        _u = int.from_bytes(payload[1:3], "big")
                    elif payload[:1] == b"\xcc" and len(payload) >= 2:
                        _u = payload[1]
                    _known = list(pet_proto.PET_UIDS) + list(pet_proto.PET_SKIN)
                    if _u in _known:
                        pet_sel[0] = min(_known.index(_u), len(pet_proto.PET_UIDS) - 1)
                    conn.sendall(pack(965, seq, b""))
                    log("c%03d -> cmd=965 seq=%-4d len=0 [选中宠物 uid=%s]"
                        % (cid, seq, _u or "?"))
                    _b947 = pet_proto.pack_947(spirit_strap())
                    conn.sendall(pack(947, 0, _b947))
                    log("c%03d .. push cmd=947    len=%d [965 后刷新详情]"
                        % (cid, len(_b947)))
                    send_push(conn, cid, 178)
                    continue

                if cmd == 1411:
                    # 1411 只带 1 字节槽位。真服序列：1411(槽) 两次 -> 1412 [槽][该槽妖灵]；
                    # 顺手补发 1410（未上阵列表），客户端的选择面板就有内容了。
                    spirit_slot_sel = payload[0] if payload else 0
                    spirit_pick[0] = spirit_pick_for_slot(spirit_slot_sel)
                    _b = pet_proto.pack_1412(spirit_slot_sel, spirit_pick[0])
                    conn.sendall(pack(1412, seq, _b))
                    log("c%03d -> cmd=1412 seq=%-4d len=%d [槽%d → %s] hex=%s"
                        % (cid, seq, len(_b), spirit_slot_sel,
                           pet_proto.name_of(spirit_pick[0]) if spirit_pick[0] else "空",
                           _b.hex()))
                    push_spirit(conn, cid)
                    continue

                if cmd == 1413:
                    # 1413 的 payload 是「第几只妖灵」（真服 00/02/01 三次正好
                    # 指到 1101/2101/4101）。放到刚才 1411 点开的槽位，回空包。
                    _idx = payload[0] if payload else 0
                    free = spirit_free_list()
                    conn.sendall(pack(1413, seq, b""))
                    iid = spirit_pick[0]
                    if 0 <= _idx < len(free):
                        iid = free[_idx][0]
                    # ★ 2026-11-24 17:1x：槽位优先用**妖灵自身部位**推断。
                    # 客户端「卸下之后再装」的流程是直接发 1413，不发 1411，
                    # 此时 spirit_slot_sel 还是 0 → 部位校验永远不过，
                    # 客户端却已经弹了「装备成功」（用户实测）。
                    # 每只妖灵的合法槽位本来就唯一，直接从 iid 反推最稳。
                    _slot_use = pet_proto.nature_of(iid) or spirit_slot_sel
                    if iid and _slot_use and \
                            pet_proto.nature_of(iid) in (0, _slot_use):
                        _old, _put = spirit_equip(_slot_use, iid)
                        if _put:
                            log("c%03d -> cmd=1413 seq=%-4d [%s 上阵槽%d，原 %s]"
                                % (cid, seq, pet_proto.name_of(iid),
                                   _slot_use,
                                   pet_proto.name_of(_old) if _old else "空"))
                            save_state()
                            _b947 = pet_proto.pack_947(spirit_strap())
                            conn.sendall(pack(947, 0, _b947))
                            log("c%03d .. push cmd=947    len=%d [上阵 %s]"
                                % (cid, len(_b947), pet_proto.strap_key(spirit_strap())))
                            push_spirit(conn, cid)
                            send_push(conn, cid, 29)
                            continue
                    log("c%03d -> cmd=1413 seq=%-4d [第%d只/槽%d 无法上阵，只刷列表]"
                        % (cid, seq, _idx, _slot_use))
                    push_spirit(conn, cid)
                    continue

                if cmd == 1414:
                    # ★ 2026-11-24 17:1x：1414 = 卸下某个槽位的妖灵（payload = 槽位）。
                    # 用户点「卸下」时客户端发的是 1414，以前没有分支被静默丢弃，
                    # 所以 8 只装上以后一只都脱不下来。
                    # 真服 1414 的应答格式没有样本，先回同号空 ACK，再推 947 + 1410，
                    # 与上阵（1413）走同一套刷新。
                    _uslot = payload[0] if payload else 0
                    _uold = spirit_unequip(_uslot)
                    conn.sendall(pack(1414, seq, b""))
                    log("c%03d -> cmd=1414 seq=%-4d [卸下槽%d 原 %s]"
                        % (cid, seq, _uslot,
                           pet_proto.name_of(_uold) if _uold else "空"))
                    if _uold:
                        save_state()
                        _b947u = pet_proto.pack_947(spirit_strap())
                        conn.sendall(pack(947, 0, _b947u))
                        log("c%03d .. push cmd=947    len=%d [卸下后 %s]"
                            % (cid, len(_b947u),
                               pet_proto.strap_key(spirit_strap())))
                        push_spirit(conn, cid)
                    continue

                # 真服登录初始化里有 7 个应答，本服以前全部走「无模板静默丢弃」。
                # 全部逐字节抄自 21:48:09 登录现场（work/login_2148.json）：
                #   536 -> 538(00)              849 -> 849(17B 同号)  502 -> 502(00 同号)
                #   956 -> 957(82B 第二只宠物)  689 -> 690(7B)        658 -> 659(6B)
                # 注意 849/502 是**同号**应答（真服 seq 也照抄），不是 cmd+1。
                LOGIN_EXTRA = {
                    536: (538, "00"),
                    658: (659, "ce0003313f00"),
                    689: (690, "01100100000000"),
                    849: (849, "04cd03e901cd03ea01cd03ed00cd03f700"),
                    502: (502, "00"),
                    956: (957, "01ace58a9fe5a4abe7868ae78cab090002ccdeccfe33"
                               "cd080acd0324ccb833663300cd02b301010101011919"
                               "1919cd27100800000000000000000000000000000000"
                               "00000000000000000000000000000000"),
                }
                if cmd in LOGIN_EXTRA:
                    # ★ 956 -> 957 是「宠物详情」，包里写死了「功夫熊猫」。
                    # 真服 2026-11-24 12:39:34 新号建角后的登录现场：客户端发了 956，
                    # 服务端**什么都没回** —— 因为那个号一只宠物都没有。
                    # 以前无条件发 82B 的 957，结果 Lv1 新号身边也跟着熊猫跑图。
                    if cmd == 956 and not cur().pets:
                        log("c%03d .. 跳过 cmd=957（当前角色没有宠物，真服新号也不回这个包）"
                            % cid)
                        continue
                    _rc, _rh = LOGIN_EXTRA[cmd]
                    _rb = bytes.fromhex(_rh)
                    conn.sendall(pack(_rc, seq, _rb))
                    sent_cmds.add(_rc)
                    log("c%03d -> cmd=%-6d seq=%-4d len=%d [登录初始化补包]"
                        % (cid, _rc, seq, len(_rb)))
                    continue

                resp = None
                if (cmd + 1) in TPL_PUSH and (cmd + 1) not in sent_cmds:
                    # 真服用 seq=0 的推送形式回复这类请求。
                    # 若回放已经发过同号推送就不重复发，避免客户端收到两份。
                    _pb = build_88() if (cmd + 1) == 88 else TPL_PUSH[cmd + 1]
                    resp = pack(cmd + 1, 0, _pb)
                    sent_cmds.add(cmd + 1)
                    log("c%03d -> cmd=%-6d seq=0    len=%d  [推送式响应]"
                        % (cid, cmd + 1, len(_pb)))
                else:
                    # 只回 cmd+1；心跳 cmd=4 例外（同号响应）。
                    # 绝不能用「cmd 自己」的模板——那是把请求原样回显，
                    # 客户端会打 no handler for cmd X，m_depth 永远减不到 0，
                    # 加载卡死后崩在 nativeGetFileOffsetAndSize。
                    k = None
                    quiet = False
                    if (cmd + 1) in TPL:
                        k = cmd + 1
                    # 这几个客户端根本没注册响应处理器，回了只会在它日志里刷
                    # 「no handler for cmd X」：心跳 4、位置同步 115。
                    # 顺带不打日志：c005 心跳每 10 秒一次，一天能刷两万多行，
                    # 把真正的新命令（无模板那行）淹掉。
                    if cmd == 115:
                        # ★ 2026-11-24 18:5x 真服实证（relay.log 01:24:17~01:24:19）：
                        # 115 = 客户端上报移动坐标，服务端对**每一步**回两条 118：
                        #   S->C 118 len=3 hex=06<115 payload>   （自己的位置确认）
                        #   S->C 118 len=3 hex=10<115 payload>   （同屏广播）
                        # 以前这里跟心跳一起静默丢弃 → 客户端走两步就停（115 只发到
                        # seq=50），永远走不到怪身边，于是永远不发 145
                        # ——「点怪打不了怪」的真正断点就在这，不是 116。
                        _p = payload or b""
                        if _p:
                            # ★ 记下最后坐标：115 就是 [x][y]（一格 1 字节），
                            #   与 17 的 off28/29 同一坐标系。只改内存，不写盘 ——
                            #   115 每秒一条，逐条落盘会把磁盘打满；
                            #   断连时 finally 里那次 save_state() 自然会带上它。
                            if len(_p) >= 2:
                                cur().pos_x = _p[0]
                                cur().pos_y = _p[1]
                            conn.sendall(pack(118, 0, b"\x06" + _p))
                            conn.sendall(pack(118, 0, b"\x10" + _p))
                            log("c%03d .. 118 x2 [移动同步 %s]" % (cid, _p.hex()))
                        continue
                    if cmd in (4, 115):
                        k, quiet = None, True
                    if k is not None:
                        resp = pack(k, seq, TPL[k])
                        log("c%03d -> cmd=%-6d seq=%-4d len=%d (模板 %d)"
                            % (cid, k, seq, len(TPL[k]), k))
                    elif cmd == 17:
                        # 断线自动重连时 seq 会接着涨（实测 seq=334），
                        # 而回放里只有登录那一刻的 seq=2，匹配不到就掉进「无模板静默丢弃」，
                        # 客户端表现为一直转圈、连上了却进不去。
                        # 这里按当前角色动态回一份 17，并把 113/117 一起补上。
                        _b17 = build_17()
                        _cm17 = int(cur().scene or DEFAULT_SCENE)
                        conn.sendall(pack(17, seq, _b17))
                        conn.sendall(pack(113, 0, build_113(_cm17)))
                        conn.sendall(pack(117, 0, b"\x00"))
                        log("c%03d -> cmd=17 seq=%d 重连兜底 len=%d + 113/117"
                            % (cid, seq, len(_b17)))
                        continue
                    elif quiet:
                        continue
                    else:
                        # 不认识就不回，绝对不要回 cmd=1。
                        # 实测：客户端收到 cmd=1（模板 1211 的提示包）后会在
                        # 下一帧被掐死（无 Fatal、无 tombstone、无 System.exit 日志），
                        # 主城刚 init 完就挂。真服对未注册的 cmd 就是静默丢弃。
                        log("c%03d .. 无模板 cmd=%d seq=%d len=%d hex=%s，静默丢弃（不回 cmd=1）"
                            % (cid, cmd, seq, len(payload or b""),
                               (payload or b"")[:64].hex()))
                        continue
                if resp:
                    conn.sendall(resp)
    except socket.timeout:
        log("c%03d 超时" % cid)
    except Exception as e:
        log("c%03d 异常 %s %s" % (cid, type(e).__name__, e))
    finally:
        # 先落盘再摘连接：这之后 CONN_PLAYERS 里没有它，GM/后台不会再改到死连接。
        # 没走到 cmd=17 的连接（探活/半开）不落盘 —— 否则会用默认值覆盖主号存档。
        if pl.authed:
            try:
                save_state()
            except Exception:
                pass
        with GAME_LOCK:
            GAME_CONNS.pop(cid, None)
            CONN_PLAYERS.pop(cid, None)
        write_status()
        try:
            conn.close()
        except Exception:
            pass


def handle(conn, addr, cid):
    log("c%03d 连接 %s:%d" % (cid, addr[0], addr[1]))
    try:
        conn.settimeout(20)
        first = conn.recv(1, socket.MSG_PEEK)
    except Exception:
        first = b""
    if first and first[0] == 0x16:
        handle_tls(conn, cid)
    else:
        handle_game(conn, cid)


def main():
    log("回放序列 %d 包（推送 %d）" % (len(SEQ), sum(1 for _, s, _ in SEQ if s == 0)))
    _ep = _load_equip_str()
    pet_proto.load()
    log("宠物配置：妖灵 %d 种 / 宠物 %d 种 / 947 真服原包 %s"
        % (len(pet_proto.SPIRIT), len(pet_proto.PET),
           sorted(k for k in (pet_proto.TPL["947"] or {}))))
    log("强化属性表 %d 条 gift %d 条 强化大师 %d 档 套装 %d 套 配方 %d 条 商城 %d 条（%s）"
        % (len(equip_attr.EQUIP_STR), len(equip_attr.EQUIP_GIFT),
           len(equip_attr.ADDON), len(equip_attr.SUITE),
           len(equip_attr.FORMULA), len(equip_attr.MALL), _ep or "缺 Equip.xml"))
    log("等级属性表 %d 条（Levelup.xml）" % len(equip_attr.LEVELUP))
    log("当前面板 Lv%d：生命%d 法力%d 攻击%d-%d 防御%d（装备强化 %s）"
        % (cur().level, panel_attr("hp_max"), panel_attr("mp_max"),
           panel_attr("attack_min"), panel_attr("attack_max"),
           panel_attr("defense"), enh_breakdown()))
    write_status()
    threading.Thread(target=gm_watch, daemon=True).start()
    log("GM 通道已开：写 %s 即可改数值（网页后台 admin_server.py 走的就是这条）" % GM_CMD)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(16)
    log("服务端 v3 监听 :%d（TLS /auth + 明文游戏协议）" % PORT)
    log("无角色试验：账号=%s 变体=%d（空=关闭，登录该账号只回「账号在/角色空」的 17）"
        % (FRESH_ACCT or "关闭", FRESH_V))
    cid = 0
    while True:
        c, a = srv.accept()
        cid += 1
        threading.Thread(target=handle, args=(c, a, cid), daemon=True).start()


if __name__ == "__main__":
    main()
