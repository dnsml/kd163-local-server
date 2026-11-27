#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
OUT="D:/66666/kd163/logs"
cd /d/66666/kd163 || exit 1

shot () {  # $1=name
  "$ADB" $D shell screencap -p /sdcard/$1.png >/dev/null 2>&1
  "$ADB" $D pull /sdcard/$1.png "$OUT/$1.png" >/dev/null 2>&1
}

"$ADB" $D shell am force-stop com.kdxx.online
sleep 1
"$ADB" $D logcat -c 2>/dev/null
"$ADB" $D logcat -v time > logs/flow_$(date +%H%M%S).log 2>/dev/null &
LOGPID=$!

"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
echo "[1] 启动，等 20s"
sleep 20
shot g1_login
echo "[2] 点登录"
"$ADB" $D shell input tap 673 575
sleep 7
shot g2_select
echo "[3] 点开始游戏"
"$ADB" $D shell input tap 755 573

i=0
while [ $i -lt 50 ]; do
  ALIVE=$("$ADB" $D shell pidof com.kdxx.online 2>/dev/null | tr -d '\r')
  SZ=$(wc -c < logs/server.log 2>/dev/null)
  if [ -z "$ALIVE" ]; then echo "  !! t=${i}s 进程退出（server.log=${SZ}B）"; break; fi
  [ $((i % 10)) -eq 0 ] && echo "  t=${i}s 存活 server.log=${SZ}B"
  sleep 1
  i=$((i+1))
done
shot g3_ingame
kill $LOGPID 2>/dev/null
echo "=== 服务端尾部 ==="
tail -22 logs/server.log
echo done
