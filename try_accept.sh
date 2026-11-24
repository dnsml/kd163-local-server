#!/bin/bash
# 用法: try_accept.sh <payload_hex>
# 把 211 的人工响应写成指定 payload，重新登录进服，点接受任务，报告结果
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1

HEX="$1"
printf '{"211": {"cmd": 212, "payload": "%s"}}\n' "$HEX" > tpl_extra.json
echo "payload = $HEX ($((${#HEX}/2)) 字节)"

shot () { "$ADB" $D shell screencap -p /sdcard/$1.png >/dev/null 2>&1
          "$ADB" $D pull /sdcard/$1.png "D:/66666/kd163/logs/$1.png" >/dev/null 2>&1; }

fill () { "$ADB" $D shell input tap $1 $2; sleep 0.6
          "$ADB" $D shell input keyevent KEYCODE_MOVE_END; sleep 0.2
          n=0; while [ $n -lt 25 ]; do "$ADB" $D shell input keyevent 67; n=$((n+1)); done
          sleep 0.2; [ -n "$3" ] && "$ADB" $D shell input text "$3"; sleep 0.5; }

"$ADB" $D shell am force-stop com.kdxx.online
sleep 1
"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
sleep 22
fill 800 405 "test001"
fill 800 484 "123456"
"$ADB" $D shell input tap 673 575
sleep 8
"$ADB" $D shell input tap 755 573
sleep 12
shot u_city
echo "已进服，点接受任务"
B=$(wc -l < logs/server.log)
"$ADB" $D logcat -c 2>/dev/null
"$ADB" $D shell input tap 1217 560
sleep 3
echo "=== 服务端新增 ==="
tail -n +$((B+1)) logs/server.log
echo "=== 客户端 ==="
"$ADB" $D logcat -d -v time 2>/dev/null | grep -a "cocos2d-x debug info\|signal" | tail -12
echo "=== 进程 ==="
"$ADB" $D shell pidof com.kdxx.online || echo "已退出"
