#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
OUT=/d/66666/kd163/logs
cd /d/66666/kd163 || exit 1

"$ADB" $D shell am force-stop com.kdxx.online
"$ADB" $D shell "su -c 'rm -f /data/data/com.kdxx.online/shared_prefs/managed_account_login_v1.xml'"
sleep 1
"$ADB" $D logcat -c 2>/dev/null
"$ADB" $D logcat -v time > $OUT/login_$(date +%H%M%S).log 2>/dev/null &
LOGPID=$!

"$ADB" $D shell am start -n com.kdxx.online/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
sleep 18

# 账号框（1600x900 实际坐标）
"$ADB" $D shell input tap 733 405
sleep 1
"$ADB" $D shell input text "test001"
sleep 1
# 密码框
"$ADB" $D shell input tap 733 484
sleep 1
"$ADB" $D shell input text "123456"
sleep 1
"$ADB" $D shell screencap -p /sdcard/c.png
"$ADB" $D pull /sdcard/c.png $OUT/c.png >/dev/null 2>&1

echo "=== 点登录 前计数器 ==="
"$ADB" $D shell "su -c 'iptables -t nat -L OUTPUT -n -v'" 2>&1 | grep -E "dpt:80|dpt:9000"
"$ADB" $D shell input tap 673 575
echo "已点登录，监控 20s"
i=0
while [ $i -lt 20 ]; do
  ALIVE=$("$ADB" $D shell pidof com.kdxx.online 2>/dev/null | tr -d '\r')
  CONN=$(netstat -ano 2>/dev/null | grep ":9000" | grep -c ESTABLISHED)
  echo "  t=${i}s pid=[$ALIVE] est9000=$CONN"
  [ -z "$ALIVE" ] && echo "  !! 进程已退出" && break
  sleep 1
  i=$((i+1))
done
echo "=== 点登录 后计数器 ==="
"$ADB" $D shell "su -c 'iptables -t nat -L OUTPUT -n -v'" 2>&1 | grep -E "dpt:80|dpt:9000"
"$ADB" $D shell screencap -p /sdcard/d.png
"$ADB" $D pull /sdcard/d.png $OUT/d.png >/dev/null 2>&1
kill $LOGPID 2>/dev/null
echo done
