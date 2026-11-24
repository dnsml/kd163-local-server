#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1

shot () { "$ADB" $D shell screencap -p /sdcard/$1.png >/dev/null 2>&1
          "$ADB" $D pull /sdcard/$1.png "D:/66666/kd163/logs/$1.png" >/dev/null 2>&1; }

fill () { # $1=x $2=y $3=text
  "$ADB" $D shell input tap $1 $2; sleep 0.6
  "$ADB" $D shell input keyevent KEYCODE_MOVE_END; sleep 0.2
  n=0; while [ $n -lt 25 ]; do "$ADB" $D shell input keyevent 67; n=$((n+1)); done
  sleep 0.2
  [ -n "$3" ] && "$ADB" $D shell input text "$3"
  sleep 0.5
}

"$ADB" $D shell am force-stop com.kdxx.online
sleep 1
"$ADB" $D logcat -c 2>/dev/null
"$ADB" $D logcat -v time > logs/f2_$(date +%H%M%S).log 2>/dev/null &
LOGPID=$!

"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
echo "[1] 启动 等待 22s"
sleep 22

fill 800 405 "test001"
fill 800 484 "123456"
shot p1_filled
echo "[2] 点登录"
"$ADB" $D shell input tap 673 575
sleep 8
shot p2_after_login

echo "[3] 点开始游戏"
"$ADB" $D shell input tap 755 573

i=0
while [ $i -lt 40 ]; do
  ALIVE=$("$ADB" $D shell pidof com.kdxx.online 2>/dev/null | tr -d '\r')
  SZ=$(wc -c < logs/server.log 2>/dev/null)
  if [ -z "$ALIVE" ]; then echo "!! t=${i}s 进程退出 log=${SZ}"; break; fi
  [ $((i % 8)) -eq 0 ] && echo "  t=${i}s 存活 log=${SZ}B"
  sleep 1; i=$((i+1))
done
shot p3_ingame
kill $LOGPID 2>/dev/null
echo "=== 服务端尾部 ==="
tail -26 logs/server.log
