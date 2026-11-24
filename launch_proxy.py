#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以分离进程启动 capture_proxy.py，避免被父进程清理"""
import subprocess, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY = os.path.join(HERE, "capture_proxy.py")
LOG = os.path.join(HERE, "proxy.log")
PY = sys.executable

DETACHED = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW

logf = open(LOG, "ab", buffering=0)
p = subprocess.Popen(
    [PY, PROXY],
    cwd=HERE,
    stdout=logf,
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    creationflags=DETACHED,
    close_fds=True,
)
print("已启动代理 PID=%d  日志=%s" % (p.pid, LOG))
time.sleep(3)
r = subprocess.run(["tasklist", "/FI", "PID eq %d" % p.pid], capture_output=True)
print(r.stdout.decode("gbk", "replace").strip()[-200:])
