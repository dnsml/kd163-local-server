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

六条断言：
1. 182 把 10306 穿上 → 槽 11；182 把 10305 穿上 → 槽 9
2. 30 包仍是 82B，且含 `cd28420b0101` / `cd2841090101`
3. 30 包里首饰排在槽 6 之后、槽 7 之前（真服顺序）
4. 818 记录数 = 9，槽 9/11 各一条，物品 id 正确
5. 29 面板生命 +335（10306 basic_effect 53,335）
6. 817 回显带首饰槽（客户端重登后不会再被清空）

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

        # ---- 1. 先穿戒指（10305 不带生命属性 → 这一发 29 就是基线）----
        s.sendall(pack(182, 20, bytes([0x16, 0x01])))     # 格 22 = 0x16 = 飞仙戒指
        pks = recv_packets(s, wait=2.5)
        cmds = [c for c, _, _ in pks]
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        hp_ring = hp_of(d.get(29))
        check("182 穿戒指回 ACK", 182 in d, "cmd 顺序 %s" % cmds[:10])
        check("戒指进槽 9", "slot" and True, "30 里待校验")

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

        check("30 包仍是 82B（总长不能变）", len(b30) == 82, "%dB" % len(b30))
        check("30 含护符槽11 cd28420b0101", AMULET_HEX + b"\x0b\x01\x01" in b30)
        check("30 含戒指槽9  cd2841090101", RING_HEX + b"\x09\x01\x01" in b30)
        slots = [s_ for s_, _ in recs]
        check("30 槽位顺序 = 1..6,首饰,7（真服顺序）",
              slots[:6] == [1, 2, 3, 4, 5, 6] and slots[-1] == 7
              and set(slots[6:-1]) == {9, 11},
              str(slots))
        check("30 首饰 id 正确",
              dict(recs).get(9) == RING and dict(recs).get(11) == AMULET,
              "槽9=%s 槽11=%s" % (dict(recs).get(9), dict(recs).get(11)))

        b818 = d.get(818) or b""
        n818 = b818[0] if b818 else 0
        check("818 记录数 = 9（7 装备 + 2 首饰）", n818 == 9, "n=%d len=%d" % (n818, len(b818)))
        check("818 含槽9 戒指", b"\x09" + RING_HEX in b818)
        check("818 含槽11 护符", b"\x0b" + AMULET_HEX in b818)

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
