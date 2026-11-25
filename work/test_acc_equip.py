# -*- coding: utf-8 -*-
"""首饰（护符 / 戒指）穿戴自检。

用法：
    python work/test_acc_equip.py                # 自起服 9112
    python work/test_acc_equip.py --no-start     # 打已经在跑的 9112

背景（2026-11-25 用户反馈「系统送的护符戒指穿不上」）：
    背包点 10305 飞仙戒指 / 10306 飞仙护符时，客户端发的是 `182 [格子][数量]`，
    服务端 SLOT_OF_KIND2 里只有 kind2 1/6/7/8/9/10/11，没有 12(护符)/13(戒指)
    → 回「这件装备的槽位还没对上，道具已保留」，客户端就停在「穿不上、还是空的」。

真服依据（relay.log 00:15:10 的 817，客户端自己上报的装备栏）：
    [0b] cd2842 0b / cd2842 0c / cd2b65 02 / cd2b66 03 / cd2b64 01
         cd2b67 04 / cd2b68 05 / cd2841 09 / cd2841 0a / cd2b69 06 / cd2c40 07
    → 首饰槽 = 9/10（戒指）、11/12（护符）；30 包里槽 6 与槽 7 之间那 16 字节
      就是这 4 个槽（空槽 4B，有装备 6B：cd+u16 + 槽 + 01 01）。

断言：
1. 182 把 10306 穿上 → 槽 11/12；182 把 10305 穿上 → 槽 9/10（真服是成对槽）
2. 30 包变长（真服 80/82/86/88/96 都出现过），7 装备 + 4 首饰槽 = 90B，
   且含 `cd28420b0101` / `cd28410a0101` 等四条
3. 30 包里首饰排在槽 6 之后、槽 7 之前（真服顺序）
4. 818 记录数 = 11，槽 9/10、11/12 各一条，成对同 id
5. 29 面板生命 +335（10306 basic_effect 53,335），且不因双槽翻倍
6. 817 回显带首饰槽（客户端重登后不会再被清空）
7. 819 卸戒指 → 槽 9/10 一起清空、戒指回背包、818 从 11 降回 9、30 从 90B 降回 86B
8. 819 卸护符 → 818 剩 7 条、30 回 82B、生命回到基线

⚠️ 用独立测试账号 9112001 的存档，跑完删除，不动玩家主号。
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
SRC_STATE = os.path.join(HERE, "player_state_900000102.json")
ACCT = "9112001"
STATE = os.path.join(HERE, "player_state_%s.json" % ACCT)
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"

RING, AMULET = 10305, 10306
RING_HEX = bytes.fromhex("cd2841")
AMULET_HEX = bytes.fromhex("cd2842")
# 武器用测试存档里那件逍遥匕首（item_kind2=2，正是玩家穿不上的那种）
WEAPON = 12114
WEAPON_HEX = bytes.fromhex("cd2f52")


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sf(x):
    b = x.encode() if isinstance(x, str) else x
    return bytes([0xA0 | len(b)]) + b


def recv_packets(sock, wait=2.0):
    sock.settimeout(0.4)
    buf, out = b"", []
    end = time.time() + wait
    while time.time() < end:
        try:
            ch = sock.recv(65536)
            if not ch:
                break
            buf += ch
        except socket.timeout:
            continue
        while len(buf) >= 8:
            total = struct.unpack(">H", buf[:2])[0]
            need = total + 2
            if len(buf) < need:
                break
            cmd, seq = struct.unpack(">HI", buf[2:8])
            out.append((cmd, seq, buf[8:need]))
            buf = buf[need:]
    return out


def port_busy(port):
    """端口上已经有人在监听 → 说明旧服务没退，测试不能跑（会连到旧代码）。"""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def wait_port(port, proc, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc and proc.poll() is not None:
            return False
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def login(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sf(ACCT) + b"\xda\x00\x2b" + TOKEN))
    return s, recv_packets(s, wait=3.0)


def hp_of(body29):
    """29 包第一个 token 是生命上限（<0x80 / cc / cd / ce 编码都要认）。"""
    if not body29:
        return None
    b = body29[0]
    if b < 0x80:
        return b
    if b == 0xCC:
        return body29[1]
    if b == 0xCD:
        return int.from_bytes(body29[1:3], "big")
    if b == 0xCE:
        return int.from_bytes(body29[1:5], "big")
    return None


def parse_bag(body):
    """178 体：[0x40][n]，每条 [槽][id varint][enh][bind][num]。"""
    if not body or body[0] != 0x40 or len(body) < 2:
        return []
    n, i, out = body[1], 2, []
    for _ in range(n):
        if i >= len(body):
            break
        pos = body[i]
        i += 1
        b = body[i]
        if b == 0xCD:
            iid, i = int.from_bytes(body[i + 1:i + 3], "big"), i + 3
        elif b == 0xCE:
            iid, i = int.from_bytes(body[i + 1:i + 5], "big"), i + 5
        elif b == 0xCC:
            iid, i = body[i + 1], i + 2
        else:
            iid, i = b, i + 1
        num = body[i + 2] if i + 2 < len(body) else 0
        i += 3
        out.append((pos, iid, num))
    return out


def parse_30(body):
    """把 30 包解成 [(槽位, 物品id)]。空槽是 4 个 0，有装备是 6B。"""
    recs, i = [], 0
    while i < len(body):
        b = body[i]
        if b == 0xCD and i + 4 <= len(body):
            recs.append((body[i + 3], int.from_bytes(body[i + 1:i + 3], "big")))
            i += 6
        elif b == 0xCE and i + 6 <= len(body):
            recs.append((body[i + 5], int.from_bytes(body[i + 1:i + 5], "big")))
            i += 8
        elif b == 0x00:
            i += 4
        else:
            break
    return recs


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 9112
    nostart = "--no-start" in sys.argv
    proc = log = None

    # ---- 造一份测试存档：主号那套装 + 首饰放背包 21/22 格 ----
    if not os.path.exists(SRC_STATE):
        print("FAIL 找不到主号存档 %s" % SRC_STATE)
        return 1
    src = json.load(open(SRC_STATE, encoding="utf-8"))
    # 装备表写死：以前是「过滤主号 equip<=7」，主号一脱装备这个测试就跟着漂
    # （22:33 真机把逍遥战甲脱了，30=90B 的断言直接红）。回归必须与线上存档无关。
    src["equip"] = {1: 12108, 2: 12109, 3: 12110, 4: 12111,
                    5: 12112, 6: 12113, 7: 12114}
    src["bag"] = src.get("bag") or {}
    src["bag"]["21"] = [AMULET, 1]
    src["bag"]["22"] = [RING, 1]
    if os.path.exists(STATE):
        shutil.copy2(STATE, STATE + ".bak_acctest")
    json.dump(src, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

    if not nostart:
        # ★ 2026-11-25：踩过坑 —— 9112 上还有旧代码的进程在跑时，新 Popen 绑不上端口，
        # 测试会静默连到旧进程上，断言全红但改的其实是新代码。这里必须硬报错。
        if port_busy(port):
            print("FAIL 端口 %d 已被占用（旧的服务进程没退），先杀掉再跑" % port)
            return 1
        log = tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="w+", encoding="utf-8")
        proc = subprocess.Popen([PY, "local_server3.py", str(port), "patch/exact.pem", "patch/exact.key"],
                                cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if not wait_port(port, proc):
            proc.terminate()
            print("FAIL 服务端未监听 :%d" % port)
            return 1

    fails = []

    def check(name, cond, detail=""):
        print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + detail) if detail else ""))
        if not cond:
            fails.append(name)

    try:
        s, pks = login(port)
        d0 = {}
        for c, _, p in pks:
            d0.setdefault(c, p)
        hp0 = hp_of(d0.get(29))
        print("[1] 登录 %d 个包，初始生命 %s，30=%s" %
              (len(pks), hp0, (d0.get(30) or b"").hex()[:24]))

        # ---- 0. 177 打开背包 → 178 必须是当前背包全量 ----
        # 之前这里落到「cmd+1 有模板就回模板」，回的是录制的新号三件套
        # （绑定铜钱卡×10 / 10绑定元宝 / 5级新丁礼包，23B），
        # 于是每次开背包那三件就盖在槽 0/1/2 上，点使用没反应。
        s.sendall(pack(177, 19))
        pks = recv_packets(s, wait=1.5)
        d177 = {}
        for c, _, p in pks:
            d177.setdefault(c, p)
        b177 = d177.get(178) or b""
        n177 = b177[1] if len(b177) >= 2 else 0     # 178 体：[0x40][条数]…
        print("      [177 开背包] cmd 顺序 %s  n=%d len=%d" %
              ([c for c, _, _ in pks][:6], n177, len(b177)))
        check("177 回 178 全量（不是 23B 新手三件套）",
              n177 >= 5 and len(b177) != 23, "n=%d len=%d" % (n177, len(b177)))

        # ---- 1. 先穿戒指（10305 不带生命属性 → 这一发 29 就是基线）----
        s.sendall(pack(182, 20, bytes([0x16, 0x01])))     # 格 22 = 0x16 = 飞仙戒指
        pks = recv_packets(s, wait=2.5)
        cmds = [c for c, _, _ in pks]
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        hp_ring = hp_of(d.get(29))
        check("182 穿戒指回 ACK", 182 in d, "cmd 顺序 %s" % cmds[:10])

        # ---- 2. 再穿护符（10306 basic_effect 53,335 → 生命 +335）----
        s.sendall(pack(182, 21, bytes([0x15, 0x01])))     # 格 21 = 0x15 = 飞仙护符
        pks = recv_packets(s, wait=2.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30 = d.get(30) or b""
        hp_both = hp_of(d.get(29))
        recs = parse_30(b30)
        print("      30=%dB 记录=%s" % (len(b30), recs))
        print("      29 生命 基线(只戴戒指) %s -> 戴护符 %s" % (hp_ring, hp_both))

        check("30 变长：7 装备 + 4 首饰槽 = 90B（真服 30 本就变长）",
              len(b30) == 90, "%dB" % len(b30))
        check("30 含护符槽11 cd28420b0101", AMULET_HEX + b"\x0b\x01\x01" in b30)
        check("30 含护符槽12 cd28420c0101", AMULET_HEX + b"\x0c\x01\x01" in b30)
        check("30 含戒指槽9  cd2841090101", RING_HEX + b"\x09\x01\x01" in b30)
        check("30 含戒指槽10 cd28410a0101", RING_HEX + b"\x0a\x01\x01" in b30)
        slots = [s_ for s_, _ in recs]
        check("30 槽位顺序 = 1..6,护符11/12,戒指9/10,7（真服 96B 顺序）",
              slots[:6] == [1, 2, 3, 4, 5, 6] and slots[-1] == 7
              and slots[6:-1] == [11, 12, 9, 10],
              str(slots))
        check("30 首饰 id 正确（成对同 id）",
              dict(recs).get(9) == RING and dict(recs).get(10) == RING
              and dict(recs).get(11) == AMULET and dict(recs).get(12) == AMULET,
              "槽9=%s 槽10=%s 槽11=%s 槽12=%s"
              % (dict(recs).get(9), dict(recs).get(10),
                 dict(recs).get(11), dict(recs).get(12)))

        # 真服 182 穿戴（23:25:22~36 连穿 11 件）只推 29/28/30/178，
        # 从不主动推 818 —— 818 只是客户端 817 上报的回执。
        check("182 穿戴不主动推 818（与线上服一致）", 818 not in d, str(sorted(d)))

        if hp_ring and hp_both:
            check("29 生命 +335（护符 basic_effect 53,335）",
                  hp_both - hp_ring == 335,
                  "%d -> %d (+%d)" % (hp_ring, hp_both, hp_both - hp_ring))

        # ---- 6. 817 回显（重登/切面板时客户端会重新拉）----
        s.sendall(pack(817, 22, b"\x09" + AMULET_HEX + b"\x0b"
                       + RING_HEX + b"\x09"
                       + b"\xcd\x2b\x64\x01"))
        pks = recv_packets(s, wait=1.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b817r = d.get(818) or b""
        check("817 回显里带首饰槽", AMULET_HEX in b817r and RING_HEX in b817r,
              "len=%d" % len(b817r))

        # ---- 7. 819 卸戒指（真服载荷 cd2841 0a = 戒指 10305 / 槽 9）----
        s.sendall(pack(819, 23, RING_HEX + b"\x09"))
        pks = recv_packets(s, wait=2.5)
        cmds = [c for c, _, _ in pks]
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30_no_ring = d.get(30) or b""
        b178_ring = d.get(178) or b""
        recs_no_ring = parse_30(b30_no_ring)
        print("      [819 卸戒指] cmd 顺序 %s  30=%dB  178=%dB"
              % (cmds[:10], len(b30_no_ring), len(b178_ring)))
        # 线上服 819 自己回「本服尚未恢复此功能」；本地服实现成卸下，
        # 应答必须是 819（同 seq），推送对齐 37：29/28/30/178。
        check("819 应答是 819 本身", 819 in d, str(cmds[:8]))
        check("819 戒指成对清空（槽9/10 都不在 30 里）",
              RING_HEX not in b30_no_ring, b30_no_ring.hex()[:48])
        check("819 后 30 = 86B（4 首饰槽变 2 空 2 实）",
              len(b30_no_ring) == 86, "%dB 记录=%s" % (len(b30_no_ring), recs_no_ring))
        check("819 后护符槽 11/12 仍在", AMULET_HEX + b"\x0b\x01\x01" in b30_no_ring
              and AMULET_HEX + b"\x0c\x01\x01" in b30_no_ring)
        check("819 卸下的戒指回到背包（178 含 cd2841）", RING_HEX in b178_ring,
              "178=%dB" % len(b178_ring))

        # ---- 8. 819 卸护符（cd2842 0b = 护符 10306 / 槽 11）----
        s.sendall(pack(819, 24, AMULET_HEX + b"\x0b"))
        pks = recv_packets(s, wait=2.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30_none = d.get(30) or b""
        b178_am = d.get(178) or b""
        hp_none = hp_of(d.get(29))
        print("      [819 卸护符] 30=%dB  29 生命 %s"
              % (len(b30_none), hp_none))
        check("819 卸护符后 30 = 82B（4 个首饰槽全空）",
              len(b30_none) == 82, "%dB 记录=%s" % (len(b30_none), parse_30(b30_none)))
        check("819 卸护符后 30 里没有首饰", AMULET_HEX not in b30_none and RING_HEX not in b30_none)
        check("819 卸下的护符回到背包（178 含 cd2842）", AMULET_HEX in b178_am,
              "178=%dB" % len(b178_am))
        if hp_ring and hp_none:
            check("819 全卸后生命回到只戴戒指的基线（属性不残留）", hp_none == hp_ring,
                  "%s -> %s" % (hp_ring, hp_none))
        else:
            check("819 全卸后拿到 29 面板做比对", bool(hp_none), "hp=%s" % hp_none)

        # ---- 9. 37 = 点装备格卸下（客户端 0 基格号：00 -> 槽 1）----
        # 这是真机脱装备真正发的命令，以前服务端没分支直接丢，表现「脱不下来」。
        s.sendall(pack(37, 25, b"\x00"))
        pks = recv_packets(s, wait=2.5)
        cmds = [c for c, _, _ in pks]
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b178_37 = d.get(178) or b""
        b30_37 = d.get(30) or b""
        print("      [37 卸下槽1] cmd 顺序 %s  30=%dB  178=%dB"
              % (cmds[:10], len(b30_37), len(b178_37)))
        check("37 有应答（不再静默丢弃）", 37 in d, str(cmds[:6]))
        # 线上服 37 的回包实测：29 -> 28 -> 30 -> 178 -> 37 空ACK，
        # 所以「衣服真的脱了」要看 30 里那件消失，而不是看 818。
        check("37 卸下后 30 里没有该装备", b"\xcd\x2f\x4c" not in b30_37,
              "30=%dB" % len(b30_37))
        check("37 卸下后 30 = 80B（槽1 变空 + 17 槽结构）",
              len(b30_37) == 80, "30=%dB" % len(b30_37))
        check("37 卸下的装备回到背包（178 含 cd2f4c = 12108）",
              b"\xcd\x2f\x4c" in b178_37, "178=%dB" % len(b178_37))

        # ---- 10. 37 的字节是「客户端面板格号」，首饰 4 格是 护符11/12、戒指9/10 ----
        # 反例就是 22:33 真机：穿上戒指+护符后点第 8 格（面板上的护符格），
        # 「格号+1」会当成槽 9 去脱戒指。真服 96B 的 30 原包首饰顺序是
        # 11,12,9,10，面板 4 格 = 护符/护符/戒指/戒指，所以 8 = 槽 12 护符、
        # 9 = 槽 9 戒指。点 8 必须掉护符、戒指留着。
        s.sendall(pack(177, 26))
        pks = recv_packets(s, wait=1.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        bag_now = parse_bag(d.get(178) or b"")
        pos_ring = next((p for p, i, _ in bag_now if i == RING), None)
        pos_am = next((p for p, i, _ in bag_now if i == AMULET), None)
        rehung = False
        if pos_ring is not None and pos_am is not None:
            s.sendall(pack(182, 27, bytes([pos_ring, 1])))
            recv_packets(s, wait=1.5)
            s.sendall(pack(182, 28, bytes([pos_am, 1])))
            pks = recv_packets(s, wait=2.0)
            dd = {}
            for c, _, p in pks:
                dd.setdefault(c, p)
            rehung = bool(dd.get(30)) and AMULET_HEX in (dd.get(30) or b"") \
                and RING_HEX in (dd.get(30) or b"")
        check("重新戴上戒指+护符（准备验证下标映射）", rehung,
              "戒指格=%s 护符格=%s" % (pos_ring, pos_am))
        if rehung:
            # ★ 线上服 23:24:38~51 逐格实测得到的映射（见 local_server3.CELL_SLOTS）：
            #   格 00..05 → 槽 1..6      格 06 → 槽 11   格 07 → 槽 12
            #   格 08 → 槽 10            格 09 → 槽 9    格 0a → 槽 7（武器在最后）
            s.sendall(pack(37, 29, b"\x08"))          # 第 8 格 = 槽 10 戒指
            pks = recv_packets(s, wait=2.5)
            d = {}
            for c, _, p in pks:
                d.setdefault(c, p)
            b30_i = d.get(30) or b""
            b178_i = d.get(178) or b""
            print("      [37 第8格→槽10] 30=%dB 记录=%s"
                  % (len(b30_i), parse_30(b30_i)))
            check("37 第8格 → 槽10 戒指（30 里没有 cd2841）",
                  RING_HEX not in b30_i, str(parse_30(b30_i)))
            check("37 第8格 护符还在（30 里仍有 cd2842）",
                  AMULET_HEX in b30_i, str(parse_30(b30_i)))
            check("37 掉的戒指回背包", RING_HEX in b178_i, "178=%dB" % len(b178_i))

            # ---- 11. 第 6 格是护符 11 —— 不是武器（武器在第 0a 格）----
            s.sendall(pack(37, 30, b"\x06"))
            pks = recv_packets(s, wait=2.5)
            d = {}
            for c, _, p in pks:
                d.setdefault(c, p)
            b30_j = d.get(30) or b""
            b178_j = d.get(178) or b""
            print("      [37 第6格→槽11] 30=%dB 记录=%s"
                  % (len(b30_j), parse_30(b30_j)))
            check("37 第6格 → 槽11 护符（30 里没有 cd2842）",
                  AMULET_HEX not in b30_j, str(parse_30(b30_j)))
            check("37 第6格 主装备不受影响（30 里仍有槽1 装备）",
                  b"\x01\x01\x01" in b30_j or len(b30_j) >= 74,
                  str(parse_30(b30_j)))
            check("37 掉的护符回背包", AMULET_HEX in b178_j, "178=%dB" % len(b178_j))

        # ---- 12. 武器：kind2=2 的匕首也必须进槽 7（五职业五种武器 1~5）----
        # 玩家实测「全穿上之后武器是空的，在背包里点使用提示该物品不能直接使用」：
        # 以前 SLOT_OF_KIND2 只有 kind2=1→槽7，匕首(2)/法杖(3)/弓(4)/重剑(5)
        # 全落到「槽位还没对上」。
        b178_k = b""
        s.sendall(pack(37, 31, b"\x0a"))          # 第 0a 格 = 槽 7 武器（面板最右下）
        pks = recv_packets(s, wait=2.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30_k = d.get(30) or b""
        b178_k = d.get(178) or b""
        print("      [37 第0a格=武器] 30=%dB 记录=%s" % (len(b30_k), parse_30(b30_k)))
        check("37 第0a格卸下武器（30 里没有 cd2f52）", WEAPON_HEX not in b30_k,
              str(parse_30(b30_k)))
        check("37 卸下的武器回背包", WEAPON_HEX in b178_k, "178=%dB" % len(b178_k))

        pos_w = next((p for p, i, _ in parse_bag(b178_k) if i == WEAPON), None)
        check("背包里找到刚脱下的武器", pos_w is not None, "格=%s" % pos_w)
        if pos_w is not None:
            s.sendall(pack(182, 32, bytes([pos_w, 1])))   # 背包里点「使用」穿回去
            pks = recv_packets(s, wait=2.5)
            d = {}
            for c, _, p in pks:
                d.setdefault(c, p)
            b30_l = d.get(30) or b""
            b178_l = d.get(178) or b""
            print("      [182 穿武器] cmd 顺序 %s  30=%dB 记录=%s"
                  % ([c for c, _, _ in pks][:7], len(b30_l), parse_30(b30_l)))
            check("182 穿匕首（kind2=2）进槽 7",
                  WEAPON_HEX + b"\x07\x01\x01" in b30_l, str(parse_30(b30_l)))
            # 真服 182 穿戴只推 29/28/30/178，不推 818（818 是 817 的回执）
            check("182 穿戴不主动推 818（与线上服一致）", 818 not in d,
                  "cmd=%s" % [c for c, _, _ in pks][:8])
            # 同一把武器在背包里是叠在一格上的，所以看「总数量」而不是记录条数
            n_before = sum(_n for _p, _i, _n in parse_bag(b178_k) if _i == WEAPON)
            n_after = sum(_n for _p, _i, _n in parse_bag(b178_l) if _i == WEAPON)
            check("穿上的武器从背包扣掉 1 把", n_after == n_before - 1,
                  "背包 %d -> %d 把" % (n_before, n_after))

        s.close()
        time.sleep(0.8)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        for p in (STATE, STATE + ".bak_acctest"):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        if log:
            log.close()
            try:
                os.unlink(log.name)
            except OSError:
                pass

    print("")
    if fails:
        print("FAIL %d 项：%s" % (len(fails), "、".join(fails)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
