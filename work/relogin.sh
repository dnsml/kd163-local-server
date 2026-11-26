#!/bin/bash
# 重启客户端并登录本地服（账号框已记住，密码 123456）。
#   用法：bash work/relogin.sh
# 前置：9000 已监听（bash work/start_all.sh）。网络层（reverse + DNAT）这里会自己补。
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
PY="C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 1

# ★ 2026-11-26 11:06 设备名会变：有时 192.168.0.111:5555，有时 emulator-5554。
#   写死一个就 `device not found` + reverse 全空，表现同样是「进不去」。
if [ -n "$KD_DEV" ]; then
    DEV="-s $KD_DEV"
else
    DEV="-s $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
fi
if [ "$DEV" = "-s " ]; then
    echo "[relogin] 没有在线设备，先启动模拟器或 adb connect" >&2
    exit 1
fi
echo "[relogin] 设备 $DEV"

# 两条通路一起补：adb reverse（8080/9000/19000）+ iptables DNAT（124.223.56.189 -> 本机）
bash work/net_fix.sh || exit 1

# 按控件文字取中心坐标 —— 键盘弹出/收起时弹窗会上移，写死坐标会点空。
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

"$ADB" $DEV shell am force-stop com.kdxx.local1
sleep 2
"$ADB" $DEV shell monkey -p com.kdxx.local1 -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
sleep 34

"$ADB" $DEV shell input tap 800 480        # 密码框
sleep 1
"$ADB" $DEV shell input text 123456
sleep 1
"$ADB" $DEV shell input keyevent 4         # 收键盘，避免挡住按钮
sleep 2

read -r LX LY <<< "$(btn_center 登录)"
if [ -z "$LX" ]; then
    echo "[relogin] 没找到「登录」按钮，截图看下当前界面" >&2
    exit 3
fi
echo "[relogin] 点登录 ($LX,$LY)"
"$ADB" $DEV shell input tap "$LX" "$LY"
sleep 14

read -r SX SY <<< "$(btn_center 开始游戏)"
if [ -n "$SX" ]; then
    "$ADB" $DEV shell input tap "$SX" "$SY"
    sleep 10
fi
echo "[relogin] 已提交登录"
