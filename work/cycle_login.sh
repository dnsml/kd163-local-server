#!/bin/bash
# 一轮真机验收：重启服务端 -> 重启客户端 -> 登录 -> 进服 -> 关活动面板 -> 截图
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
cd /d/66666/kd163
PID=$(netstat -ano | grep ":9000.*LISTENING" | awk '{print $5}' | head -1)
[ -n "$PID" ] && echo "stop $PID" && python -c "
import subprocess,sys
subprocess.run(['taskkill','/PID','$PID','/F'],capture_output=True)
"
sleep 1
CERT_DIR="${KD_CERT_DIR:-}"
if [ -z "$CERT_DIR" ]; then
    if [ -f patch/local/exact.pem ] && [ -f patch/local/exact.key ]; then CERT_DIR=patch/local; else CERT_DIR=patch; fi
fi
PYTHONIOENCODING=utf-8 "C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe" local_server3.py 9000 "$CERT_DIR/exact.pem" "$CERT_DIR/exact.key" >> logs/local_server3_run.out 2>&1 &
sleep 3
$ADB -s emulator-5554 shell am force-stop com.kdxx.local1
sleep 1
$ADB -s emulator-5554 shell am start -n com.kdxx.local1/com.aiyou.mhsj.MainActivity >/dev/null 2>&1
sleep 14
$ADB -s emulator-5554 shell input tap 800 484
sleep 1
$ADB -s emulator-5554 shell input text "1"
sleep 1
$ADB -s emulator-5554 shell input tap 667 583
sleep 14
$ADB -s emulator-5554 shell input tap 807 587
sleep 16
$ADB -s emulator-5554 shell input keyevent 4
sleep 2
$ADB -s emulator-5554 shell screencap -p /sdcard/cy.png
$ADB -s emulator-5554 pull /sdcard/cy.png logs/shots/cy.png >/dev/null 2>&1
echo shot_ok
