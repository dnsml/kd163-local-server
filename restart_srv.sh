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
PYTHONIOENCODING=utf-8 nohup "$PY" local_server3.py 9000 patch/exact.pem patch/exact.key > logs/server_stdout.log 2>&1 &
sleep 2
echo "--- 新占用 ---"
netstat -ano 2>/dev/null | grep LISTENING | grep ":9000"
echo "--- 启动输出 ---"
cat logs/server_stdout.log
