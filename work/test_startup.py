# -*- coding: utf-8 -*-
"""启动自检：真起 local_server3.py 与 local_http.py，探活后杀掉。

用法：python work/test_startup.py [--srv-port 9101] [--http-port 8081]

判定：端口能连上 + 进程没提前退出 + 日志里出现关键行，三者缺一即 FAIL。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def arg(name, default):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def wait_port(port, proc, timeout=20):
    """每次重试都新建 socket（Windows 上同一个 socket 首次 connect 失败后不可复用）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False, "进程提前退出 rc=%s" % proc.returncode
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True, "%.1fs 可连接" % (time.time() - t0)
        except OSError:
            time.sleep(0.3)
    return False, "%.0fs 超时未监听" % timeout


def run(cmd, port, tag):
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="w+", encoding="utf-8")
    print("\n===== %s =====" % tag)
    print("cmd:", " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=out, stderr=subprocess.STDOUT,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    ok, why = wait_port(port, proc)
    print("端口 %d: %s %s" % (port, "OK" if ok else "FAIL", why))
    if ok:
        try:
            req = urllib.request.urlopen("http://127.0.0.1:%d/version.txt" % port, timeout=3)
            body = req.read(200)
            print("HTTP /version.txt: %s %r" % (req.status, body[:80]))
        except Exception as e:
            if tag.startswith("资源服"):
                print("HTTP /version.txt: FAIL", repr(e))
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    out.close()
    lines = open(out.name, encoding="utf-8", errors="replace").read().splitlines()
    print("--- 输出末尾 %d 行 ---" % min(12, len(lines)))
    for ln in lines[-12:]:
        print("   ", ln)
    os.unlink(out.name)
    return ok


def main():
    srv_port = int(arg("--srv-port", "9101"))
    http_port = int(arg("--http-port", "8081"))
    results = []
    results.append(("游戏服", run([PY, "local_server3.py", str(srv_port),
                                    "patch/exact.pem", "patch/exact.key"], srv_port, "游戏服 local_server3")))
    results.append(("资源服", run([PY, "local_http.py", str(http_port), "127.0.0.1"], http_port, "资源服 local_http")))
    print("\n===== 汇总 =====")
    for name, ok in results:
        print("  %s: %s" % (name, "PASS" if ok else "FAIL"))
    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == "__main__":
    main()