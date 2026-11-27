#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1

shot () { "$ADB" $D shell screencap -p /sdcard/$1.png >/dev/null 2>&1
          "$ADB" $D pull /sdcard/$1.png "D:/66666/kd163/logs/$1.png" >/dev/null 2>&1; }

fill () { "$ADB" $D shell input tap $1 $2; sleep 0.6
          "$ADB" $D shell input keyevent KEYCODE_MOVE_END; sleep 0.2
          n=0; while [ $n -lt 25 ]; do "$ADB" $D shell input keyevent 67; n=$((n+1)); done
          sleep 0.2; [ -n "$3" ] && "$ADB" $D shell input text "$3"; sleep 0.5; }

"$ADB" $D shell am force-stop com.kdxx.online
sleep 1
"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
echo "[1] 启动等 22s"; sleep 22
fill 800 405 "test001"
fill 800 484 "123456"
echo "[2] 登录"; "$ADB" $D shell input tap 673 575
sleep 8
echo "[3] 开始游戏"; "$ADB" $D shell input tap 755 573
sleep 10
shot r1_city
echo "=== 进程 ==="; "$ADB" $D shell pidof com.kdxx.online
echo "=== 服务端尾部 ==="; tail -8 logs/server.log
