#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1
TAG="${1:-run}"
fill () { "$ADB" $D shell input tap $1 $2; sleep 0.6
          "$ADB" $D shell input keyevent KEYCODE_MOVE_END; sleep 0.2
          n=0; while [ $n -lt 25 ]; do "$ADB" $D shell input keyevent 67; n=$((n+1)); done
          sleep 0.2; [ -n "$3" ] && "$ADB" $D shell input text "$3"; sleep 0.5; }
shot () { "$ADB" $D shell screencap -p /sdcard/$1.png >/dev/null 2>&1
          "$ADB" $D pull /sdcard/$1.png "D:/66666/kd163/logs/$1.png" >/dev/null 2>&1; }

"$ADB" $D shell am force-stop com.kdxx.online
sleep 1
"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
sleep 21
fill 800 405 "test001"
fill 800 484 "123456"
"$ADB" $D shell input tap 673 575
sleep 8
"$ADB" $D shell input tap 755 573
sleep 13
shot ${TAG}_city0
# 关福利面板
"$ADB" $D shell input tap 1310 84; sleep 1.5
"$ADB" $D shell input keyevent 4; sleep 1.5
"$ADB" $D shell input tap 684 635; sleep 2
shot ${TAG}_city1
echo "[开始旅程]"
"$ADB" $D shell input tap 890 725; sleep 10
shot ${TAG}_city2
B=$(wc -l < logs/server.log)
"$ADB" $D logcat -c 2>/dev/null
echo "[点守卫]"
"$ADB" $D shell input tap 659 762
sleep 8
shot ${TAG}_after
echo "=== 服务端 ==="
tail -n +$((B+1)) logs/server.log | grep -a "145\|146\|115"
echo "=== 客户端 ==="
"$ADB" $D logcat -d -v time 2>/dev/null | grep -a "cocos2d-x debug info" | tail -16
echo "=== 进程 ==="; "$ADB" $D shell pidof com.kdxx.online || echo 已退出
