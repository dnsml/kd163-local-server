#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
"$ADB" $D shell input tap 800 407
sleep 0.5
"$ADB" $D shell input keyevent KEYCODE_MOVE_END
sleep 0.3
i=0
while [ $i -lt 30 ]; do "$ADB" $D shell input keyevent 67; i=$((i+1)); done
sleep 0.3
"$ADB" $D shell input text "test001"
sleep 0.5
"$ADB" $D shell input tap 800 484
sleep 0.5
"$ADB" $D shell input text "123456"
sleep 0.5
"$ADB" $D shell input keyevent 111
sleep 0.5
"$ADB" $D shell input tap 673 575
sleep 1
echo login_tapped
