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

断言（★ 2026-11-25 23:25 真服抓包定性：首饰四格是**四件独立物品**）：
1. 182 穿第 1 枚戒指 → 只进槽 9；再穿第 2 枚 → 进槽 10（不是同一件写两格）
2. 182 穿护符同理：第 1 枚槽 11、第 2 枚槽 12
3. 30 包按「实槽 6B / 空槽 4B」变长，尾部 = 第12块 4B + 翅膀 20B + 坐骑 6B（两块长度恒定）：
   顺序 1..6 / 11,12 / 9,10 / 7（真服 96B 原包顺序）
4. 818 记录数 = 11，槽 9/10、11/12 各一条
5. 29 面板生命：每枚护符 +335，两枚 +670（一格一件，不再需要去重）
6. 817 回显带首饰槽（客户端重登后不会再被清空）
7. 819（点装备格）= 线上服只回 `04bb` +「本服尚未恢复此功能」，**一件都不脱**
   —— 回 819 空包会让客户端 SIGABRT 闪退（23:41:01 实测）；
   「武器点了没反应」的根因是 818 把同一件武器回了 7/8 两条，不是 819
8. 818 按内部槽去重：协议槽 7/8 是同一个武器槽，同一件武器只回一条，
   槽字节跟客户端 817 上报一致（12114 匕首 -> 07）；去重后真机点武器发 37 hex=0a
9. 37 点首饰格 → 只卸那一格；格 08→槽9、格 06→槽12、格 0a→槽8 武器

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


def parse_818(body):
    """818 体：[条数] + 每条 28B（[槽][cd][id hi][id lo][01] + 23×00）。"""
    if not body:
        return []
    n, out = body[0], []
    for k in range(n):
        r = body[1 + k * 28: 1 + (k + 1) * 28]
        if len(r) < 28:
            break
        out.append((r[0], int.from_bytes(r[2:4], "big")))
    return out


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
    # 两枚戒指 + 两枚护符：真服那个号就是 4 件独立首饰（178 里 4 个独立包格），
    # 这样才能验证「一格一件、四格都能穿满」。
    src["bag"]["21"] = [AMULET, 1]
    src["bag"]["22"] = [RING, 1]
    src["bag"]["23"] = [AMULET, 1]
    src["bag"]["24"] = [RING, 1]
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

        # ---- 1. 第 1 枚戒指（10305 不带生命属性 → 这一发 29 就是属性基线）----
        s.sendall(pack(182, 20, bytes([0x16, 0x01])))     # 格 22 = 0x16 = 飞仙戒指
        pks = recv_packets(s, wait=2.5)
        cmds = [c for c, _, _ in pks]
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        hp_ring1 = hp_of(d.get(29))
        b30_r1 = d.get(30) or b""
        check("182 穿戒指回 ACK", 182 in d, "cmd 顺序 %s" % cmds[:10])
        check("第 1 枚戒指只进槽9（30 = 84B，没有槽10）",
              len(b30_r1) == 84 and RING_HEX + b"\x09\x01\x01" in b30_r1
              and RING_HEX + b"\x0a\x01\x01" not in b30_r1,
              "%dB 记录=%s" % (len(b30_r1), parse_30(b30_r1)))

        # ---- 2. 第 2 枚戒指 → 同类另一个空槽 10（不是同一件写两格）----
        s.sendall(pack(182, 21, bytes([0x18, 0x01])))     # 格 24 = 0x18 = 第二枚戒指
        pks = recv_packets(s, wait=2.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30_r2 = d.get(30) or b""
        check("第 2 枚戒指进槽10（两格各一枚，30 = 86B）",
              len(b30_r2) == 86
              and RING_HEX + b"\x09\x01\x01" in b30_r2
              and RING_HEX + b"\x0a\x01\x01" in b30_r2,
              "%dB 记录=%s" % (len(b30_r2), parse_30(b30_r2)))

        # ---- 3/4. 两枚护符 → 槽 11、槽 12（每枚 +335 生命）----
        s.sendall(pack(182, 22, bytes([0x15, 0x01])))     # 格 21 = 0x15 = 飞仙护符
        recv_packets(s, wait=2.0)
        s.sendall(pack(182, 23, bytes([0x17, 0x01])))     # 格 23 = 0x17 = 第二枚护符
        pks = recv_packets(s, wait=2.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30 = d.get(30) or b""
        hp_both = hp_of(d.get(29))
        recs = parse_30(b30)
        print("      30=%dB 记录=%s" % (len(b30), recs))
        print("      29 生命 两枚戒指 %s -> 两枚护符 %s" % (hp_ring1, hp_both))

        check("30 = 90B（7 装备 + 4 首饰槽，实槽 6B / 空槽 4B + 翅膀 20B，没坐骑不补 6B）",
              len(b30) == 90, "%dB" % len(b30))
        check("30 含护符槽11 cd28420b0101", AMULET_HEX + b"\x0b\x01\x01" in b30)
        check("30 含护符槽12 cd28420c0101", AMULET_HEX + b"\x0c\x01\x01" in b30)
        check("30 含戒指槽9  cd2841090101", RING_HEX + b"\x09\x01\x01" in b30)
        check("30 含戒指槽10 cd28410a0101", RING_HEX + b"\x0a\x01\x01" in b30)
        slots = [s_ for s_, _ in recs]
        check("30 槽位顺序 = 1..6,护符12/11,戒指9/10,武器字节跟随 id（12114→07）",
              slots[:6] == [1, 2, 3, 4, 5, 6] and slots[-1] == 7
              and slots[6:-1] == [12, 11, 9, 10],
              str(slots))

        # 真服 182 穿戴（23:25:22~36 连穿 11 件）只推 29/28/30/178，
        # 从不主动推 818 —— 818 只是客户端 817 上报的回执。
        check("182 穿戴不主动推 818（与线上服一致）", 818 not in d, str(sorted(d)))

        if hp_ring1 and hp_both:
            check("29 生命 +670（两枚护符各 basic_effect 53,335）",
                  hp_both - hp_ring1 == 670,
                  "%d -> %d (+%d)" % (hp_ring1, hp_both, hp_both - hp_ring1))

        # ---- 6. 817 回显（重登/切面板时客户端会重新拉）----
        s.sendall(pack(817, 24, b"\x09" + AMULET_HEX + b"\x0b"
                       + RING_HEX + b"\x09"
                       + b"\xcd\x2b\x64\x01"))
        pks = recv_packets(s, wait=1.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b817r = d.get(818) or b""
        check("817 回显里带首饰槽", AMULET_HEX in b817r and RING_HEX in b817r,
              "len=%d" % len(b817r))

        # ---- 7. 819 = 客户端「点装备格」的无效分支：线上服只回文本、一件都不脱 ----
        # 线上服 23:52:30~37 连点 8 次，每次都是同一条 29B `04bb` +「本服尚未恢复此功能」。
        # ★ 2026-11-26 12:00 真机定性：「武器点了没反应」的根因不在 819，而是 818
        #   把同一件武器回了槽 7 和槽 8 两条，客户端装备栏状态错乱才落到这个分支。
        #   818 去重后真机点武器发的是 37 hex=0a（11:59:22 日志），面板正常重绘。
        #   所以 819 保持线上服原样，绝不能改成「卸下」（点一下就从身上掉，玩家投诉过）。
        s.sendall(pack(819, 25, RING_HEX + b"\x09"))
        pks = recv_packets(s, wait=2.0)
        cmds = [c for c, _, _ in pks]
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        btxt819 = d.get(1) or b""
        want819 = b"\x04\xbb" + "本服尚未恢复此功能".encode("utf-8")
        print("      [819 点戒指格] cmd 顺序 %s  text=%s"
              % (cmds[:8], btxt819.hex()))
        # ★ 23:41:01 实测：本地回 819 空包，客户端收到就 SIGABRT 闪退
        # （logcat: Zygote Process exited due to signal (6)）。线上服从不回 819 本身。
        check("819 只回 cmd=1 文本（绝不回 819 空包，否则客户端断言闪退）",
              1 in d and 819 not in d, str(cmds[:8]))
        check("819 文本与线上服同形（04bb + 本服尚未恢复此功能，29B）",
              btxt819 == want819, "%dB %s" % (len(btxt819), btxt819.hex()[:40]))
        check("819 不推 29/28/30/178（点一下不该改装备）",
              not (set(cmds) & {29, 28, 30, 178}), str(cmds[:8]))

        # ---- 8. 819 之后装备栏必须原样：用 817 让客户端上报，818 回显对账 ----
        s.sendall(pack(817, 27, b"\x09" + RING_HEX + b"\x09"
                       + AMULET_HEX + b"\x0b"))
        pks = recv_packets(s, wait=1.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b818_after = d.get(818) or b""
        check("819 之后戒指仍在槽 9（818 回显含 cd2841）",
              RING_HEX in b818_after, "818=%dB" % len(b818_after))
        check("819 之后护符仍在槽 11（818 回显含 cd2842）",
              AMULET_HEX in b818_after, "818=%dB" % len(b818_after))

        # ---- 8b. 818 按内部槽去重：协议槽 7/8 是同一个武器槽，只能回一条 ----
        # 这是 2026-11-26 11:37 的死结：817 上报武器 07，equip_slots() 又按
        # 12xxx 未收录回落 08，818 同时回 7 和 8 两条 → 客户端武器格状态错乱、
        # 点上去只发 819。现在按内部槽去重，武器字节统一用客户端上报值。
        s.sendall(pack(817, 28, b"\x01" + WEAPON_HEX + b"\x07"))
        pks = recv_packets(s, wait=1.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b818w = d.get(818) or b""
        _recs = parse_818(b818w)
        _wrecs = [(s, i) for s, i in _recs if i == WEAPON]
        print("      [817 只报武器 07] 818 条数=%d 武器记录=%s"
              % (len(_recs), _wrecs))
        check("818 武器只回一条（7/8 去重，不再同一件回两次）",
              len(_wrecs) == 1, str(_wrecs))
        check("818 武器槽字节跟客户端 817 上报一致（07）",
              bool(_wrecs) and _wrecs[0][0] == 0x07, str(_wrecs))
        check("818 里没有第二条 id=12114 的记录",
              sum(1 for _, i in _recs if i == WEAPON) == 1, str(_recs))

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
        check("37 卸下后 30 = 88B（11 件少一件 + 翅膀 20B，没坐骑）",
              len(b30_37) == 88, "30=%dB" % len(b30_37))
        check("37 卸下的装备回到背包（178 含 cd2f4c = 12108）",
              b"\xcd\x2f\x4c" in b178_37, "178=%dB" % len(b178_37))

        # ---- 10. 37 的字节是「客户端面板格号」，首饰 4 格是 护符11/12、戒指9/10 ----
        # ★ 819 只回文本不脱装，所以戒指/护符本来就还在身上。
        check("37 卸槽1 之后两枚戒指仍在（819 没有偷卸）",
              RING_HEX + b"\x09\x01\x01" in b30_37
              and RING_HEX + b"\x0a\x01\x01" in b30_37, str(parse_30(b30_37)))
        # ★ 线上服 23:24:38~51 逐格实测得到的映射（见 local_server3.CELL_SLOTS）：
        #   格 00..05 → 槽 1..6      格 06 → 槽 12   格 07 → 槽 11
        #   格 08 → 槽 9             格 09 → 槽 10   格 0a → 槽 8（武器在最后）
        # 2026-11-26 00:20~00:23 真服逐格点 37 再核对一遍，全部一致。
        s.sendall(pack(37, 29, b"\x08"))          # 第 8 格 = 槽 9 戒指
        pks = recv_packets(s, wait=2.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30_i = d.get(30) or b""
        b178_i = d.get(178) or b""
        print("      [37 第8格→槽9] 30=%dB 记录=%s"
              % (len(b30_i), parse_30(b30_i)))
        check("37 第8格 → 槽9 那枚戒指掉了（cd2841 槽09 不在）",
              RING_HEX + b"\x09\x01\x01" not in b30_i, str(parse_30(b30_i)))
        check("37 第8格 槽10 那枚戒指还在（一格一件，不连坐）",
              RING_HEX + b"\x0a\x01\x01" in b30_i, str(parse_30(b30_i)))
        check("37 第8格 护符还在（30 里仍有 cd2842）",
              AMULET_HEX in b30_i, str(parse_30(b30_i)))
        check("37 掉的戒指回背包", RING_HEX in b178_i, "178=%dB" % len(b178_i))

        # ---- 11. 第 6 格是护符 12 —— 不是武器（武器在第 0a 格）----
        s.sendall(pack(37, 30, b"\x06"))
        pks = recv_packets(s, wait=2.5)
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        b30_j = d.get(30) or b""
        b178_j = d.get(178) or b""
        print("      [37 第6格→槽12] 30=%dB 记录=%s"
              % (len(b30_j), parse_30(b30_j)))
        check("37 第6格 → 槽12 护符掉了（cd2842 槽0c 不在）",
              AMULET_HEX + b"\x0c\x01\x01" not in b30_j, str(parse_30(b30_j)))
        check("37 第6格 槽11 另一枚护符还在",
              AMULET_HEX + b"\x0b\x01\x01" in b30_j, str(parse_30(b30_j)))
        check("37 第6格 主装备不受影响（槽2~6 还在）",
              len(b30_j) >= 74, str(parse_30(b30_j)))
        check("37 掉的护符回背包", AMULET_HEX in b178_j, "178=%dB" % len(b178_j))

        # ---- 12. 武器：kind2=2 的匕首也必须进槽 8（五职业五种武器 1~5）----
        # 玩家实测「全穿上之后武器是空的，在背包里点使用提示该物品不能直接使用」：
        # 以前 SLOT_OF_KIND2 只有 kind2=1→槽7，匕首(2)/法杖(3)/弓(4)/重剑(5)
        # 全落到「槽位还没对上」。
        b178_k = b""
        s.sendall(pack(37, 31, b"\x0a"))          # 第 0a 格 = 槽 8 武器（面板最右下）
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
            check("182 穿匕首（kind2=2）进内部槽 8，协议字节 07",
                  WEAPON_HEX + b"\x07\x01\x01" in b30_l, str(parse_30(b30_l)))
            # ★ 2026-11-26 00:54 真服「全脱→全穿」整轮实测：30 包武器块第三字节
            #   跟着武器走，11235 天枢剑[二阶]=08、11328 玉衡剑[三阶]=07；
            #   11:37 本机客户端 817 对 12114 匕首上报的也是 07。
            #   这里做源码断言，防止有人图省事又写回固定 08。
            _src = open(os.path.join(HERE, "local_server3.py"),
                        encoding="utf-8").read()
            check("30 武器字节：12114→07 收录 + 其余回落 08 + 优先用 817 上报值",
                  "12114: 0x07" in _src
                  and "return WEAPON_SLOT_BYTE.get(cur().equip.get(8, 0) & 0xFFFF, 0x08)" in _src
                  and "hint = getattr(cur(), \"weapon_wire\", 0) or 0" in _src)
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
