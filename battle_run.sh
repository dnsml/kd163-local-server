#!/bin/bash
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
D="-s 127.0.0.1:5555"
cd /d/66666/kd163 || exit 1
TAG="${1:-run}"
fill () { "$ADB" $D shell input tap $1 $2; sleep 0.6
          "$ADB" $D shell input keyevent KEYCODE_MOVE_END; sleep 0.2
          n=0; while [ $n -lt 25 ]; do "$ADB" $D shell input keyevent 67; n=$((n+1)); done
          sleep 0.2; [ -n "$3" ] && "$ADB" $D shell input text "$3"; sleep 0.4; }
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
"$ADB" $D shell input tap 1310 84; sleep 1.5
"$ADB" $D shell input keyevent 4; sleep 1.5
"$ADB" $D shell input tap 684 635; sleep 2
"$ADB" $D shell input tap 890 725; sleep 10
"$ADB" $D shell input tap 1215 545; sleep 4
shot ${TAG}_ready

echo "[网格扫描守卫]"
HIT=0
for y in 560 640 720 800; do
  for x in 560 660 760; do
    [ "$HIT" = "1" ] && break
    "$ADB" $D shell input tap $x $y; sleep 2
    if grep -aq "cmd=145" logs/server.log && [ $(grep -ac "cmd=145" logs/server.log) -gt 0 ]; then
      # 用时间戳判断是否本轮新增
      if tail -3 logs/server.log | grep -aq "cmd=145"; then HIT=1; echo "  命中 $x,$y"; fi
    fi
  done
  [ "$HIT" = "1" ] && break
done
[ "$HIT" = "0" ] && echo "  未命中"
sleep 6
shot ${TAG}_result
echo "=== 服务端 ==="
tail -20 logs/server.log | grep -a "145\|146"
echo "=== 客户端 ==="
"$ADB" $D logcat -d -v time 2>/dev/null | grep -a "cocos2d-x debug info" | tail -14
echo "=== 进程 ==="; "$ADB" $D shell pidof com.kdxx.online || echo 已退出
