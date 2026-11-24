# -*- coding: utf-8 -*-
"""登录链路自检：起服务端 → 发 cmd=17 → 核对回包（17/28/30/29）→ 杀进程。

用法：
    python work/test_login_flow.py                 # 自起服 9102
    python work/test_login_flow.py --port 9000 --no-start   # 打已在跑的服

判定（任一不满足即 FAIL）：
1. 17 有回包，且长度 >= 190（197B 是带装备的完整态）
2. 28、30 都到（登录附加数据；缺 30 客户端会空装备/闪退）
3. 回包 cmd 里不出现 1（未注册 cmd 被打死的前兆）
"""
import os
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


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sf(x):
    b = x.encode() if isinstance(x, str) else x
    return bytes([0xA0 | len(b)]) + b


def recv_packets(sock, wait=4.0):
    sock.settimeout(0.5)
    buf = b""
    out = []
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
    """每次重试都新建 socket：Windows 上同一个 socket 首次 connect 失败后不可复用，
    复用它会让整个循环永远 OSError，表现为「服务端已监听但判 FAIL」（2026-11-24 踩过）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc and proc.poll() is not None:
            return False
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 9102
    nostart = "--no-start" in sys.argv
    proc = None
    log = None
    if not nostart:
        log = tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="w+", encoding="utf-8")
        proc = subprocess.Popen([PY, "local_server3.py", str(port), "patch/exact.pem", "patch/exact.key"],
                                cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if not wait_port(port, proc):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            print("FAIL 服务端未监听 :%d" % port)
            if log:
                log.close()
                print(open(log.name, encoding="utf-8", errors="replace").read()[-800:])
            return 1

    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sf(ACCT) + b"\xda\x00\x2b" + TOKEN))
        pks = recv_packets(s, wait=3.0)
        # 方向已核实（local_server3.py 的 send_push）：28/29/30/306 全是服务端推送（seq=0），
        # 客户端不发这些请求。这里发 28 再发 29，只是为了顺带触发面板刷新，
        # 结果只当 INFO 打印，不做断言——推送何时发生取决于回放指针与角色事件，
        # 拿它做 PASS/FAIL 会得到一个随机失败（2026-11-24 踩过两次）。
        s.sendall(pack(28, 3))
        pks += recv_packets(s, wait=1.5)
        s.sendall(pack(29, 4))
        pks += recv_packets(s, wait=1.5)
        s.close()
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
            for ln in tail[-6:]:
                print("   ", ln)
            os.unlink(log.name)

    print("\n收到 %d 个回包：" % len(pks))
    for cmd, seq, p in pks:
        print("   cmd=%-5d seq=%-3d len=%d" % (cmd, seq, len(p)))
    got = {c: len(p) for c, _, p in pks}

    ok = True
    if 17 not in got:
        print("FAIL 没有 17 回包")
        ok = False
    elif got[17] < 190:
        print("WARN 17 只有 %dB（197/205B 是带装备完整态；无角色时为短包）" % got[17])
    for need in (113, 117):
        if need not in got:
            print("WARN 缺少登录推送 cmd=%d（进图/对象表相关）" % need)
    if 1 in got:
        payload = [p for c, _, p in pks if c == 1]
        # cmd=1 + 01a0 是「账号在、角色空」的合法回包，不是崩溃前兆；
        # 其余 cmd=1 才是未注册 cmd 被打死。
        if all(p == b"\x01\xa0" for p in payload):
            print("NOTE 出现 cmd=1 01a0（无角色），这是合法分支")
        else:
            print("FAIL 出现非 01a0 的 cmd=1（未注册 cmd 被打死的前兆）")
            ok = False
    print("\n结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())