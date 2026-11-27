#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1

"$ADB" $D logcat -c 2>/dev/null
"$ADB" $D logcat -v time > logs/e2_$(date +%H%M%S).log 2>/dev/null &
LOGPID=$!

"$ADB" $D shell input tap 755 573
echo "已点开始游戏"

i=0
while [ $i -lt 40 ]; do
  ALIVE=$("$ADB" $D shell pidof com.kdxx.online 2>/dev/null | tr -d '\r')
  SZ=$(wc -c < logs/server.log 2>/dev/null)
  if [ -z "$ALIVE" ]; then echo "!! t=${i}s 进程退出 server.log=${SZ}"; break; fi
  [ $((i % 8)) -eq 0 ] && echo "  t=${i}s 存活 log=${SZ}B"
  sleep 1
  i=$((i+1))
done

"$ADB" $D shell screencap -p /sdcard/k.png >/dev/null 2>&1
"$ADB" $D pull /sdcard/k.png "D:/66666/kd163/logs/k.png" >/dev/null 2>&1
kill $LOGPID 2>/dev/null
echo "=== 服务端尾部 ==="
tail -20 logs/server.log
