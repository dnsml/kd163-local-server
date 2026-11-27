#!/bin/bash
cd /d/66666/kd163 || exit 1
PY="C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe"
for P in $(netstat -ano 2>/dev/null | grep LISTENING | grep ":9000" | awk '{print $NF}' | sort -u); do
  echo "kill $P"
  MSYS_NO_PATHCONV=1 taskkill /F /PID $P >/dev/null 2>&1
done
sleep 2
echo "--- 剩余占用 ---"
netstat -ano 2>/dev/null | grep LISTENING | grep ":9000" || echo "已清空"
rm -f logs/server.log logs/server_stdout.log
# 证书：本地开发版客户端只认 patch/local/，公网/部署版才是 patch/（KD_CERT_DIR 可强制指定）
CERT_DIR="${KD_CERT_DIR:-}"
if [ -z "$CERT_DIR" ]; then
  if [ -f patch/local/exact.pem ] && [ -f patch/local/exact.key ]; then CERT_DIR=patch/local; else CERT_DIR=patch; fi
fi
echo "--- 证书 $CERT_DIR ---"
# nohup & 会随本 shell 退出被回收（端口假成功、几秒后无监听）→ 用 DETACHED_PROCESS 真脱离
PYTHONIOENCODING=utf-8 "$PY" - "$CERT_DIR" <<'PYEOF'
import os, subprocess, sys
cert = sys.argv[1]
os.makedirs("logs", exist_ok=True)
log = open(os.path.join("logs", "server_stdout.log"), "ab", buffering=0)
env = dict(os.environ, PYTHONIOENCODING="utf-8")
p = subprocess.Popen([sys.executable, "local_server3.py", "9000",
                      cert + "/exact.pem", cert + "/exact.key"],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     creationflags=0x00000008,  # DETACHED_PROCESS
                     close_fds=True, cwd=os.getcwd(), env=env)
print("pid=%d cert=%s" % (p.pid, cert))
PYEOF
sleep 3
echo "--- 新占用 ---"
netstat -ano 2>/dev/null | grep LISTENING | grep ":9000"
echo "--- 启动输出 ---"
cat logs/server_stdout.log
