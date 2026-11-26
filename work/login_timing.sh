#!/bin/bash
# 实测「点开始游戏 -> 进场景」的耗时，并在过程中抓客户端 logcat。
#   用法：bash work/login_timing.sh
# 产出：logs/timing/*.jpg（每秒一帧）+ logs/timing/logcat.txt
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
PY="C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 1
DEV="-s $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
mkdir -p logs/timing
rm -f logs/timing/*.jpg logs/timing/logcat.txt

btn_center() {
    "$ADB" $DEV shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
    "$ADB" $DEV pull /sdcard/ui.xml logs/ui.xml >/dev/null 2>&1
    "$PY" -c "
import re, sys
s = open('logs/ui.xml', encoding='utf-8').read()
for m in re.finditer(r'<node[^>]*>', s):
    t = m.group(0)
    tx = re.search(r'text=\"([^\"]*)\"', t)
    bd = re.search(r'bounds=\"([^\"]*)\"', t)
    if tx and bd and tx.group(1) == sys.argv[1]:
        x1, y1, x2, y2 = map(int, re.findall(r'\d+', bd.group(1)))
        print((x1 + x2) // 2, (y1 + y2) // 2)
        break
" "$1"
}

shot() { "$ADB" $DEV exec-out screencap -p > "logs/timing/$1.png" 2>/dev/null; }

"$ADB" $DEV shell am force-stop com.kdxx.local1
sleep 2
"$ADB" $DEV shell monkey -p com.kdxx.local1 -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
echo "[t] 启动，等登录框出现…"
sleep 30
shot 00_login

# 密码框：点手机号下面那一行
"$ADB" $DEV shell input tap 800 505
sleep 1
"$ADB" $DEV shell input text 123456
sleep 1
"$ADB" $DEV shell input keyevent 4
sleep 2

read -r LX LY <<< "$(btn_center 登录)"
echo "[t] 登录按钮 $LX,$LY"
[ -z "$LX" ] && shot 01_no_login_btn
"$ADB" $DEV shell input tap "$LX" "$LY"
sleep 8
shot 02_serverlist

read -r SX SY <<< "$(btn_center 开始游戏)"
echo "[t] 开始游戏 $SX,$SY"
if [ -z "$SX" ]; then
    shot 03_no_start_btn
    echo "[t] 没找到开始游戏按钮"
    exit 3
fi

# 从这里开始计秒：点开始游戏 + 同时开 logcat
"$ADB" $DEV logcat -c
( "$ADB" $DEV logcat -v time > logs/timing/logcat.txt 2>/dev/null ) &
LOGPID=$!
T0=$(date +%s)
"$ADB" $DEV shell input tap "$SX" "$SY"
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 1
    shot "f$(printf %02d $i)"
    echo "[t] +${i}s 已截图"
done
kill $LOGPID 2>/dev/null
echo "[t] 点击时刻 $(date -d @$T0 '+%H:%M:%S')"
for f in logs/timing/*.png; do
    "$PY" -c "
from PIL import Image
import sys
im=Image.open(sys.argv[1]).convert('RGB'); im.thumbnail((760,760))
im.save(sys.argv[1].replace('.png','.jpg'), quality=75)
" "$f"
done
rm -f logs/timing/*.png
echo "[t] 完成，见 logs/timing/"
