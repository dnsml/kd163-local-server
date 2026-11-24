# -*- coding: utf-8 -*-
"""锁妖塔「进塔 / 上下层」+ 下线点存档 自检。

用法：
    python work/test_tower_jump.py                 # 自起服 9110，用账号存档全流程
    python work/test_tower_jump.py --port 9000 --no-start

覆盖四条断言（对应 2026-11-24 两次用户反馈）：
1. 130 进塔：回 131 = `ccdd cd52d1 0408`（逐字节照 relay.log 23:43:27）
2. 128 cd52d1（一层跳点）→ 129 = `cd52d2 03 05`  ← 「进不了下一层」的修复点
3. 128 cd52d2（二层回门）→ 129 = `cd52d1 21 05`  ← 落点由 Jumppoint 反推，不再写死 (3,5)
4. 断线重登：17 里带 `cd52d1 21 05`         ← 「老号回到最后一次下线地点」

⚠️ 这个脚本会改到账号存档里的场景/坐标。跑前自动备份，跑完自动还原。
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
ACCT = "900000102"
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"
STATE = os.path.join(HERE, "player_state_%s.json" % ACCT)


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
    """每次重试新建 socket（Windows 上同一 socket 首次失败后不可复用）。"""
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
    pks = recv_packets(s, wait=3.0)
    return s, pks


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 9110
    nostart = "--no-start" in sys.argv
    proc = log = bak = None
    if os.path.exists(STATE):
        bak = STATE + ".bak_towertest"
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
        # ---- 1. 登录 + 进塔 ----
        s, pks = login(port)
        print("[1] 登录回包 %d 个" % len(pks))
        s.sendall(pack(130, 10, b"\xcc\xdd"))
        pks = recv_packets(s, wait=2.0)
        d = {c: p for c, _, p in pks}
        check("130 → 131 进塔包逐字节", d.get(131) == bytes.fromhex("ccddcd52d10408"),
              d.get(131, b"").hex())
        check("130 后补推 113/117/659", 113 in d and 117 in d and 659 in d)

        # ---- 2. 一层 → 二层（原先被 scene 覆盖，原地不动）----
        s.sendall(pack(128, 11, bytes.fromhex("cd52d1")))
        d = {c: p for c, _, p in recv_packets(s, wait=2.0)}
        check("128 cd52d1 → 129 cd52d2 0305（下一层）",
              d.get(129) == bytes.fromhex("cd52d20305"), d.get(129, b"").hex())
        s.sendall(pack(115, 12, bytes.fromhex("0305")))
        recv_packets(s, wait=0.6)

        # ---- 3. 二层 → 一层（回门落点由 Jumppoint 反推）----
        s.sendall(pack(128, 13, bytes.fromhex("cd52d2")))
        d = {c: p for c, _, p in recv_packets(s, wait=2.0)}
        check("128 cd52d2 → 129 cd52d1 2105（回一层）",
              d.get(129) == bytes.fromhex("cd52d12105"), d.get(129, b"").hex())
        s.sendall(pack(32513, 14, bytes.fromhex("cd52d1ccdd00")))
        recv_packets(s, wait=0.8)
        s.close()                     # 触发 finally 落盘：scene=21201, pos=(33,5)
        time.sleep(1.0)

        # ---- 4. 重登：17 必须带上塔里最后的位置 ----
        s2, pks2 = login(port)
        b17 = next((p for c, _, p in pks2 if c == 17), b"")
        s2.close()
        check("重登 17 带下线场景/坐标 cd52d1 2105",
              bytes.fromhex("cd52d12105") in b17,
              "17=%dB" % len(b17))

        # 存档本身也要有这两个字段（就算 17 拼错，存档层也得对）
        try:
            with open(STATE, encoding="utf-8") as f:
                sd = json.load(f)
        except Exception:
            sd = {}
        check("存档写入 scene/pos_x/pos_y",
              sd.get("scene") == 21201 and sd.get("pos_x") == 33 and sd.get("pos_y") == 5,
              "scene=%s pos=%s,%s" % (sd.get("scene"), sd.get("pos_x"), sd.get("pos_y")))
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
            for ln in tail[-8:]:
                print("   ", ln)
            os.unlink(log.name)
        if bak:
            shutil.copy2(bak, STATE)     # 还原测试改动
            os.unlink(bak)
            print("(存档已还原)")

    print("\n结论:", "PASS" if not fails else "FAIL(%d)" % len(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
