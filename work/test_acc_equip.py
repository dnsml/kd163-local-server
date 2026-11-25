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
    src["equip"] = {k: v for k, v in (src.get("equip") or {}).items()
                    if int(k) <= 7}
    src["bag"] = src.get("bag") or {}
    src["bag"]["21"] = [AMULET, 1]
    src["bag"]["22"] = [RING, 1]
    if os.path.exists(STATE):
        shutil.copy2(STATE, STATE + ".bak_acctest")
    json.dump(src, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

    if not nostart:
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
        check("30 槽位顺序 = 1..6,首饰,7（真服顺序）",
              slots[:6] == [1, 2, 3, 4, 5, 6] and slots[-1] == 7
              and set(slots[6:-1]) == {9, 10, 11, 12},
              str(slots))
        check("30 首饰 id 正确（成对同 id）",
              dict(recs).get(9) == RING and dict(recs).get(10) == RING
              and dict(recs).get(11) == AMULET and dict(recs).get(12) == AMULET,
              "槽9=%s 槽10=%s 槽11=%s 槽12=%s"
              % (dict(recs).get(9), dict(recs).get(10),
                 dict(recs).get(11), dict(recs).get(12)))

        b818 = d.get(818) or b""
        n818 = b818[0] if b818 else 0
        check("818 记录数 = 11（7 装备 + 4 首饰槽）", n818 == 11, "n=%d len=%d" % (n818, len(b818)))
        check("818 含槽9/10 戒指", b"\x09" + RING_HEX in b818 and b"\x0a" + RING_HEX in b818)
        check("818 含槽11/12 护符",
              b"\x0b" + AMULET_HEX in b818 and b"\x0c" + AMULET_HEX in b818)

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
        b818_no_ring = d.get(818) or b""
        b30_no_ring = d.get(30) or b""
        b178_ring = d.get(178) or b""
        n_no_ring = b818_no_ring[0] if b818_no_ring else 0
        recs_no_ring = parse_30(b30_no_ring)
        print("      [819 卸戒指] cmd 顺序 %s  818 n=%d  30=%dB  178=%dB"
              % (cmds[:10], n_no_ring, len(b30_no_ring), len(b178_ring)))
        check("819 卸戒指回 818", 818 in d)
        check("819 戒指成对清空（槽9/10 都不在 30 里）",
              RING_HEX not in b30_no_ring, b30_no_ring.hex()[:48])
        check("819 后 818 n=9（掉了成对两格）", n_no_ring == 9,
              "n=%d len=%d" % (n_no_ring, len(b818_no_ring)))
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
        b818_none = d.get(818) or b""
        b30_none = d.get(30) or b""
        b178_am = d.get(178) or b""
        n_none = b818_none[0] if b818_none else 0
        hp_none = hp_of(d.get(29))
        print("      [819 卸护符] 818 n=%d  30=%dB  29 生命 %s"
              % (n_none, len(b30_none), hp_none))
        check("819 卸护符后 818 n=7（只剩 7 件主装备）", n_none == 7,
              "n=%d len=%d" % (n_none, len(b818_none)))
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
        b818_37 = d.get(818) or b""
        b178_37 = d.get(178) or b""
        b30_37 = d.get(30) or b""
        n37 = b818_37[0] if b818_37 else 0
        print("      [37 卸下槽1] cmd 顺序 %s  818 n=%d  30=%dB  178=%dB"
              % (cmds[:10], n37, len(b30_37), len(b178_37)))
        check("37 有应答（不再静默丢弃）", 37 in d or 818 in d, str(cmds[:6]))
        # 1~7 是固定槽位，空槽也占一条（id 写 0），所以条数不变、槽1 记录变空
        check("37 卸下后 818 槽1 变空（01 cd 0000）",
              b"\x01\xcd\x00\x00" in b818_37, "len=%d" % len(b818_37))
        check("37 卸下后 30 里没有该装备", b"\xcd\x2f\x4c" not in b30_37,
              "30=%dB" % len(b30_37))
        check("37 卸下的装备回到背包（178 含 cd2f4c = 12108）",
              b"\xcd\x2f\x4c" in b178_37, "178=%dB" % len(b178_37))

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
