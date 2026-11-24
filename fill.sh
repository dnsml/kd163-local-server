#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1

clear_and_type () {  # $1=x $2=y $3=text
  "$ADB" $D shell input tap $1 $2
  sleep 0.7
  "$ADB" $D shell input keyevent KEYCODE_MOVE_END
  sleep 0.25
  n=0
  while [ $n -lt 25 ]; do "$ADB" $D shell input keyevent 67; n=$((n+1)); done
  sleep 0.25
  [ -n "$3" ] && "$ADB" $D shell input text "$3"
  sleep 0.6
}

clear_and_type 800 405 "test001"
clear_and_type 800 484 "123456"

"$ADB" $D shell screencap -p /sdcard/i.png >/dev/null 2>&1
"$ADB" $D pull /sdcard/i.png "D:/66666/kd163/logs/i.png" >/dev/null 2>&1
echo filled
