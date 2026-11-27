#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1

fill () { "$ADB" $D shell input tap $1 $2; sleep 0.6
          "$ADB" $D shell input keyevent KEYCODE_MOVE_END; sleep 0.2
          n=0; while [ $n -lt 25 ]; do "$ADB" $D shell input keyevent 67; n=$((n+1)); done
          sleep 0.2; [ -n "$3" ] && "$ADB" $D shell input text "$3"; sleep 0.5; }

"$ADB" $D shell am force-stop com.kdxx.online
"$ADB" $D shell rm -f /sdcard/battle.log
"$ADB" $D logcat -c
sleep 1
"$ADB" $D shell "logcat -b all -v time -f /sdcard/battle.log" &
LOGPID=$!
"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
sleep 22
fill 800 405 "test001"
fill 800 484 "123456"
"$ADB" $D shell input tap 673 575
sleep 8
"$ADB" $D shell input tap 755 573
sleep 13
echo "[关福利面板]"; "$ADB" $D shell input tap 1310 84; sleep 1.5
"$ADB" $D shell input keyevent 4; sleep 1.5
"$ADB" $D shell input tap 684 635; sleep 2
echo "[开始旅程]"; "$ADB" $D shell input tap 895 740; sleep 9
B=$(wc -l < logs/server.log)
echo "[接受任务]"; "$ADB" $D shell input tap 1217 560
echo "[等战斗]"; sleep 26
echo "=== 服务端新增 ==="
tail -n +$((B+1)) logs/server.log
"$ADB" $D shell screencap -p /sdcard/bp.png >/dev/null 2>&1
"$ADB" $D pull /sdcard/bp.png "D:/66666/kd163/logs/bp.png" >/dev/null 2>&1
echo "=== 进程 ==="; "$ADB" $D shell pidof com.kdxx.online || echo 已退出
