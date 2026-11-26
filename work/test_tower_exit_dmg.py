# -*- coding: utf-8 -*-
"""锁妖塔「退出副本」+ GM「伤害」倍率 自检。

用法：
    python work/test_tower_exit_dmg.py              # 自起服 9112
    python work/test_tower_exit_dmg.py --no-start

断言（对应 2026-11-25 用户反馈「后台要给伤害调高」「塔里没法退出去」）：
1. 130 进塔后 132 → 133 = [场景][x][y]，紧跟 113/117（真服 23:46:17 `133 67 37 05`）
2. 133 里的场景 == 随后 113 的场景（客户端就是拿这个重建场景）
3. GM `伤害 5` 被服务端确认执行
4. 同样一只怪：×1 时要 N 回合，×5 后 ≤2 回合

⚠️ 会改到账号存档（经验/掉落/场景），跑前自动备份、跑完还原。
⚠️ 起服时把 GM 指令文件指到临时文件（KD163_GM_CMD），不跟正在跑的 9000 抢指令。
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

MOB_HEX = bytes.fromhex("ce002059a5")     # 2120101 紫精魂塔一层
HIT_146 = bytes.fromhex("0107cd283d")


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


def round_count(s, port_hint=""):
    """一直发 146，数到结束模板（148 末字节 00）为止。"""
    n = 0
    for _ in range(60):
        s.sendall(pack(146, 300 + n, HIT_146))
        d = {c: p for c, _, p in recv_packets(s, wait=0.6)}
        if 148 not in d:
            break
        n += 1
        if d[148] and d[148][-1] == 0x00:
            break
    return n


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 9112
    nostart = "--no-start" in sys.argv
    proc = log = bak = None
    gmfile = os.path.join(tempfile.gettempdir(), "kd163_gm_test_%d.cmd" % port)
    if os.path.exists(STATE):
        bak = STATE + ".bak_exitdmg"
        shutil.copy2(STATE, bak)

    if not nostart:
        log = tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="w+", encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", KD163_GM_CMD=gmfile)
        env.pop("KD163_DMG_MULT", None)
        proc = subprocess.Popen([PY, "local_server3.py", str(port), "patch/exact.pem", "patch/exact.key"],
                                cwd=HERE, stdout=log, stderr=subprocess.STDOUT, env=env)
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

        # ---- 1. 进塔 → 打一场（×1，记回合数）----
        s.sendall(pack(130, 10, b"\xcc\xdd"))
        recv_packets(s, wait=1.5)
        s.sendall(pack(32513, 11, bytes.fromhex("cd52d1ccdd00")))
        recv_packets(s, wait=0.5)
        s.sendall(pack(145, 12, MOB_HEX))
        recv_packets(s, wait=1.5)
        r1 = round_count(s)
        s.sendall(pack(149, 200, b""))
        recv_packets(s, wait=1.5)
        check("×1 倍率打出正常回合数", r1 >= 1, "%d 回合" % r1)

        # ---- 2. 132 退出副本 ----
        s.sendall(pack(132, 13, b""))
        d = {c: p for c, _, p in recv_packets(s, wait=2.0)}
        p133, p113 = d.get(133, b""), d.get(113, b"")
        check("132 → 133 有回包", p133 is not None and len(p133) in (3, 5),
              p133.hex())
        check("132 后补推 113/117", 113 in d and 117 in d, "113=%dB" % len(p113))
        scene_133 = p133[:-2] if len(p133) == 5 else p133[:1]
        check("133 的场景与 113 一致", bool(scene_133) and p113.startswith(scene_133),
              "133=%s 113=%s" % (p133.hex(), p113[:6].hex()))
        # 132 的语义是「从哪进塔回哪去」（TOWER_BACK，130 时记录）。
        # 本测试账号进塔前在南天门 1101（Scene.xml scene_type=1），所以必须回 1101；
        # 只有记录点本身在塔里（scene_type=3）时才兜底洛水城 103（下面单独断言）。
        check("退出回进塔前的南天门 1101", scene_133 == bytes.fromhex("cd044d"),
              p133.hex())
        # 兜底规则：把 TOWER_BACK 里的记录点改成塔，再退一次必须回 103
        # （直接读服务端行为不好造，这里用源码常量 + 配置交叉验证）
        try:
            import re as _re
            _src = open(os.path.join(HERE, "local_server3.py"), encoding="utf-8").read()
            _has_fb = ("SCENES[bsc].get(\"scene_type\") == \"3\"" in _src
                       and "(103, 55, 5)" in _src)
            check("塔内退出兜底洛水城 103（源码规则在位）", _has_fb)
        except Exception as _e:
            check("塔内退出兜底洛水城 103（源码规则在位）", False, str(_e))

        # ---- 3. GM 伤害 5 ----
        with open(gmfile, "a", encoding="utf-8") as f:
            f.write("伤害 5\n")
        ok_gm = False
        deadline = time.time() + 6
        while time.time() < deadline:
            time.sleep(0.4)
            with open(log.name, encoding="utf-8", errors="replace") as f:
                if "[gm] OK 伤害 5 -> 伤害倍率已设为" in f.read():
                    ok_gm = True
                    break
        check("GM `伤害 5` 被执行", ok_gm)

        # ---- 4. 再打同一只怪：回合数应显著变小 ----
        s.sendall(pack(130, 14, b"\xcc\xdd"))
        recv_packets(s, wait=1.5)
        s.sendall(pack(32513, 15, bytes.fromhex("cd52d1ccdd00")))
        recv_packets(s, wait=0.5)
        s.sendall(pack(145, 16, MOB_HEX))
        d = {c: p for c, _, p in recv_packets(s, wait=1.5)}
        r2 = round_count(s)
        s.sendall(pack(149, 201, b""))
        recv_packets(s, wait=1.5)
        check("×5 后 1~2 回合解决", 1 <= r2 <= 2, "%d 回合（原 %d 回合）" % (r2, r1))

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
            try:
                os.unlink(log.name)
            except OSError:
                pass
        for p in (gmfile,):
            try:
                os.unlink(p)
            except OSError:
                pass
        if bak:
            shutil.copy2(bak, STATE)
            try:
                os.unlink(bak)
            except OSError:
                pass
            print("(存档已还原)")

    print("\n结论:", "PASS" if not fails else "FAIL(%d)" % len(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
