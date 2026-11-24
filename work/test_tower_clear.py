# -*- coding: utf-8 -*-
"""锁妖塔「清怪 → 怪消失 → 下一层引导点亮」自检。

用法：
    python work/test_tower_clear.py                 # 自起服 9111
    python work/test_tower_clear.py --port 9000 --no-start

四条断言（对应 2026-11-25 用户反馈「打死当前的应该让怪物消失 / 下一层要有个
引导传送点」）：
1. 130 进塔后 113 与真服逐字节一致 `cd52d101ce002059a513000000`
   （relay.log 23:43:27。塔层 113 只有 1 只怪、坐标 (19,0)）
2. 145 打 2120101 → 151 里怪物 ID 与请求同号
3. 打到「致死那一回合」→ 148 末字节 00（真服结束模板）
4. 149 结算 → 150 之后紧跟 `121 ce002059a5`（怪物消失通知）

⚠️ 会改到账号存档（战斗奖励），跑前自动备份、跑完自动还原。
"""
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
ACCT = "900000102"
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"
STATE = os.path.join(HERE, "player_state_%s.json" % ACCT)

MOB = 2120101
MOB_HEX = bytes.fromhex("ce002059a5")
SCENE_1F_113 = bytes.fromhex("cd52d101ce002059a513000000")   # 真服原文
HIT_146 = bytes.fromhex("0107cd283d")                         # 真服 146 样本


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


def wait_port(port, proc, timeout=20):
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


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 9111
    nostart = "--no-start" in sys.argv
    proc = log = bak = None
    if os.path.exists(STATE):
        bak = STATE + ".bak_cleartest"
        shutil.copy2(STATE, bak)

    if not nostart:
        log = tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="w+", encoding="utf-8")
        proc = subprocess.Popen([PY, "local_server3.py", str(port), "patch/exact.pem", "patch/exact.key"],
                                cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if not wait_port(port, proc):
            proc.terminate()
            print("FAIL 服务端未监听 :%d" % port)
            if bak:
                shutil.copy2(bak, STATE)
            return 1

    fails = []

    def check(name, cond, detail=""):
        print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + detail) if detail else ""))
        if not cond:
            fails.append(name)

    try:
        s, pks = login(port)
        print("[1] 登录回包 %d 个" % len(pks))

        # ---- 1. 进塔：113 必须是真服原文（怪只有 1 只、坐标 19,0）----
        s.sendall(pack(130, 10, b"\xcc\xdd"))
        d = {c: p for c, _, p in recv_packets(s, wait=2.0)}
        check("进塔 113 与真服逐字节一致", d.get(113) == SCENE_1F_113,
              d.get(113, b"").hex())

        # 客户端加载完场景会回 32513，服务端据此把落点写进存档
        s.sendall(pack(32513, 11, bytes.fromhex("cd52d1ccdd00")))
        recv_packets(s, wait=0.5)

        # ---- 2. 开战：151 里怪物 ID 与 145 同号 ----
        s.sendall(pack(145, 12, MOB_HEX))
        d = {c: p for c, _, p in recv_packets(s, wait=2.0)}
        check("145 回 ACK", 145 in d, "len=%d" % len(d.get(145, b"")))
        check("151 带怪物 ID %d" % MOB, MOB_HEX in d.get(151, b""),
              "151=%dB" % len(d.get(151, b"")))

        # ---- 3. 一直打到结束模板（148 末字节 00）----
        ended, rounds, last148 = False, 0, b""
        for _ in range(60):
            s.sendall(pack(146, 100 + rounds, HIT_146))
            ads = {c: p for c, _, p in recv_packets(s, wait=0.6)}
            if 148 not in ads:
                break
            rounds += 1
            last148 = ads[148]
            if last148 and last148[-1] == 0x00:      # 结束模板（真服同样以 00 收尾）
                ended = True
                break
        check("打到致死回合出结束包", ended, "第%d回合 148=%dB 末字节%02x"
              % (rounds, len(last148), last148[-1] if last148 else 0xFF))

        # ---- 4. 结算：150 之后紧跟 121 怪物消失 ----
        s.sendall(pack(149, 200, b""))
        pks = recv_packets(s, wait=2.5)
        cmds = [c for c, _, _ in pks]
        d = {}
        for c, _, p in pks:
            d.setdefault(c, p)
        check("149 回 150", 150 in d, "150=%s" % (d.get(150, b"").hex()[:32]))
        check("150 之后推 121 怪物消失", 121 in d, "cmd 顺序 %s" % cmds[:12])
        check("121 载荷 = ce + %d" % MOB, d.get(121) == MOB_HEX,
              (d.get(121) or b"").hex())
        if 150 in cmds and 121 in cmds:
            check("121 在 150 之后（真服塔内顺序）", cmds.index(150) < cmds.index(121),
                  "%d < %d" % (cmds.index(150), cmds.index(121)))

        s.close()
        time.sleep(0.8)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if log:
            log.close()
            tail = open(log.name, encoding="utf-8", errors="replace").read().splitlines()
            print("--- 服务端日志末尾 ---")
            for ln in tail[-10:]:
                print("   ", ln)
            os.unlink(log.name)
        if bak:
            shutil.copy2(bak, STATE)
            os.unlink(bak)
            print("(存档已还原)")

    print("\n结论:", "PASS" if not fails else "FAIL(%d)" % len(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
