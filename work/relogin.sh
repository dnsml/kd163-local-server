#!/bin/bash
# 重启客户端并登录本地服（账号框已记住）。
#   用法：bash work/relogin.sh
#   ★ 密码是账号表里的那条（1784366 = 123，accounts.json DEFAULT_PASSWORD），
#     不是 123456；KD_PASS / KD_ACCT 可覆盖。
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

# ★ 2026-11-27 16:4x：包名已变。com.kdxx.local1 早就不在设备上了，
#   现役开发版是 com.kd163.dev（main activity com.aiyou.mhsj.MainActivity）。
#   老包名结果：monkey 静默失败 → 界面还停在别的包上 → 看起来「进不去」。
PKG="${KD_PKG:-com.kd163.dev}"

"$ADB" $DEV shell am force-stop "$PKG"
sleep 2
"$ADB" $DEV shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
sleep 34

# ★ 2026-11-27 19:5x：登录框的坐标**不能写死**（不同分辨率/软键盘弹出都会移）。
#   之前写 `input tap 800 480` 经常点到账号框，把账号改成「17123456…」，
#   再报「登录失败，请重试」。现在统一走 uiautomator 取真实 bounds，并做重试。
ui_dump() {
    for _i in 1 2 3 4 5 6; do
        "$ADB" $DEV shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
        "$ADB" $DEV pull /sdcard/ui.xml logs/ui.xml >/dev/null 2>&1 || true
        if grep -q 'text="登录"' logs/ui.xml 2>/dev/null \
           && grep -q 'EditText' logs/ui.xml 2>/dev/null; then
            return 0
        fi
        sleep 3
    done
    return 1
}

# 账号密码都在账号表里：1784366 的密码是 **123**（accounts.json 的 DEFAULT_PASSWORD），
# 不是 123456 —— 用错密码 /auth 会回「密码错误」，界面只显示「登录失败，请重试」。
PW="${KD_PASS:-123}"
ACCT="${KD_ACCT:-1784366}"

if ! ui_dump; then
    echo "[relogin] 抓不到登录对话框控件，截图看下当前界面" >&2
    exit 3
fi

# 账号框当前文字（EditText 第 1 个），同样从 dump 读，不再 grep 不存在的 acct= 行
AC=$(D:/WorkBuddyData/binaries/python/envs/default/Scripts/python.exe - <<'PY'
import re
s = open('logs/ui.xml', encoding='utf-8').read()
for t in re.findall(r'<node[^>]*>', s):
    if 'EditText' in t:
        m = re.search(r'text="([^"]*)"', t)
        print(m.group(1) if m else "")
        break
PY
)
if [ "$AC" != "$ACCT" ]; then
    echo "[relogin] 账号框是 '$AC'，改成 $ACCT"
    # 账号框在密码框上方：用 probe 给的 acct 行同时拿不到坐标，这里按控件纵向顺序推算
    AXY=$(D:/WorkBuddyData/binaries/python/envs/default/Scripts/python.exe - "$ACCT" <<'PY'
import re, sys
s = open('logs/ui.xml', encoding='utf-8').read()
for t in re.findall(r'<node[^>]*>', s):
    if 'EditText' in t:
        bd = re.search(r'bounds="([^"]*)"', t)
        x1, y1, x2, y2 = map(int, re.findall(r'\d+', bd.group(1)))
        print((x1 + x2) // 2, (y1 + y2) // 2)
        break
PY
)
    read -r AX AY <<< "$AXY"
    "$ADB" $DEV shell input tap "$AX" "$AY"
    sleep 1
    "$ADB" $DEV shell input keyevent 123          # 光标移到末尾
    for _i in $(seq 1 25); do "$ADB" $DEV shell input keyevent 67 >/dev/null; done
    "$ADB" $DEV shell input text "$ACCT"
    sleep 1
    ui_dump || true
fi

PXY=$(D:/WorkBuddyData/binaries/python/envs/default/Scripts/python.exe - <<'PY'
import re
s = open('logs/ui.xml', encoding='utf-8').read()
ed = []
for t in re.findall(r'<node[^>]*>', s):
    if 'EditText' in t:
        bd = re.search(r'bounds="([^"]*)"', t)
        if bd:
            x1, y1, x2, y2 = map(int, re.findall(r'\d+', bd.group(1)))
            ed.append(((x1 + x2) // 2, (y1 + y2) // 2))
if len(ed) > 1:
    print(ed[1][0], ed[1][1])
PY
)
read -r PX PY2 <<< "$PXY"
if [ -n "$PX" ]; then
    "$ADB" $DEV shell input tap "$PX" "$PY2"
    sleep 1
    "$ADB" $DEV shell input keyevent 123
    for _i in $(seq 1 12); do "$ADB" $DEV shell input keyevent 67 >/dev/null; done
    "$ADB" $DEV shell input text "$PW"
    sleep 1
fi

# ★ 2026-11-27 20:4x：修 bug —— 以前这里 `grep '^login=' logs/ui.xml`，
#   但 logs/ui.xml 是 uiautomator 的控件树 dump，里面根本没有 `login=` 这种行
#   （那是 work/ui_login_probe.py 的 stdout，从来没落盘）。于是永远「找不到
#   登录按钮」、exit 3，账号密码填好了却不点登录。
#   现在直接从 dump 里按时点文字取坐标。
ui_node_xy() {   # $1 = text 正则
    D:/WorkBuddyData/binaries/python/envs/default/Scripts/python.exe - "$1" <<'PY'
import re, sys
pat = sys.argv[1]
s = open('logs/ui.xml', encoding='utf-8').read()
for t in re.findall(r'<node[^>]*>', s):
    m = re.search(r'text="([^"]*)"', t)
    if not m or not re.search(pat, m.group(1)):
        continue
    bd = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', t)
    if bd:
        x1, y1, x2, y2 = map(int, bd.groups())
        print((x1 + x2) // 2, (y1 + y2) // 2)
        break
PY
}

read -r LX LY <<< "$(ui_node_xy '^登录$')"
if [ -z "$LX" ]; then
    echo "[relogin] 没找到「登录」按钮，截图看下当前界面" >&2
    exit 3
fi
echo "[relogin] 点登录 ($LX,$LY)"
"$ADB" $DEV shell input tap "$LX" "$LY"
sleep 16

read -r SX SY <<< "$(ui_node_xy '开始旅程|开始游戏|进入游戏|点击开始')"
if [ -n "$SX" ]; then
    echo "[relogin] 点开始旅程 ($SX,$SY)"
    "$ADB" $DEV shell input tap "$SX" "$SY"
    sleep 10
fi
echo "[relogin] 已提交登录"
