#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
OUT=/d/66666/kd163/logs

cd /d/66666/kd163 || exit 1
mkdir -p $OUT

echo "=== 前计数器 ==="
"$ADB" $D shell "su -c 'iptables -t nat -L OUTPUT -n -v'" 2>&1 | grep -E "dpt:80|dpt:9000"

"$ADB" $D logcat -c 2>/dev/null
"$ADB" $D shell am force-stop com.kdxx.online
sleep 1

# 后台抓全量日志
"$ADB" $D logcat -v time > $OUT/full_$(date +%H%M%S).log 2>/dev/null &
LOGPID=$!
echo "logcat pid=$LOGPID"

"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
echo "已启动，等 25s"
sleep 25

"$ADB" $D shell screencap -p /sdcard/a.png >/dev/null 2>&1
"$ADB" $D pull /sdcard/a.png $OUT/a.png >/dev/null 2>&1
echo "截图 a.png 完成"

echo "=== 后计数器 ==="
"$ADB" $D shell "su -c 'iptables -t nat -L OUTPUT -n -v'" 2>&1 | grep -E "dpt:80|dpt:9000"

sleep 2
kill $LOGPID 2>/dev/null
echo "日志: $OUT/full_*.log"
ls -l $OUT
