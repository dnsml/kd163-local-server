#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
OUT=/d/66666/kd163/logs
cd /d/66666/kd163 || exit 1

"$ADB" $D logcat -c 2>/dev/null
"$ADB" $D logcat -v time > $OUT/enter_$(date +%H%M%S).log 2>/dev/null &
LOGPID=$!

echo "=== 点开始游戏 ==="
"$ADB" $D shell input tap 755 573
sleep 3

i=0
while [ $i -lt 45 ]; do
  ALIVE=$("$ADB" $D shell pidof com.kdxx.online 2>/dev/null | tr -d '\r')
  echo "  t=${i}s pid=[$ALIVE] serverlog=$(wc -c < logs/server.log)"
  [ -z "$ALIVE" ] && echo "  !! 进程退出" && break
  sleep 1
  i=$((i+1))
done

"$ADB" $D shell screencap -p /sdcard/f.png >/dev/null 2>&1
"$ADB" $D pull /sdcard/f.png "D:/66666/kd163/f.png" >/dev/null 2>&1
kill $LOGPID 2>/dev/null
echo "=== server.log 末尾 ==="
tail -30 logs/server.log
echo done
