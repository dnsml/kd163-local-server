#!/bin/bash
# 登录加载耗时测量：登录 -> 点开始游戏 -> 抓 12 秒 logcat，统计
#   「第一个 loading begin」到「m_depth 归零」的间隔 = 玩家看到的转圈时长，
#   并列出「发出去但没等到回包」的 seq/cmd。
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
PY="C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 1
DEV="-s $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
bash work/net_fix.sh >/dev/null 2>&1

"$ADB" $DEV shell am force-stop com.kdxx.local1
sleep 2
"$ADB" $DEV shell monkey -p com.kdxx.local1 -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
echo "[m] 等登录框…"
sleep 30
"$ADB" $DEV shell input tap 800 480
sleep 1
"$ADB" $DEV shell input text 123456
sleep 1
"$ADB" $DEV shell input keyevent 4
sleep 2
"$ADB" $DEV shell input tap 670 571
sleep 9
echo "[m] 点开始游戏…"
"$ADB" $DEV logcat -c
( "$ADB" $DEV logcat -v time > logs/timing/logcat4.txt 2>/dev/null ) &
LP=$!
sleep 1
"$ADB" $DEV shell input tap 804 600
sleep 14
kill $LP 2>/dev/null

"$PY" - <<'EOF'
import re
lines=[l.rstrip() for l in open('logs/timing/logcat4.txt',encoding='utf-8',errors='ignore') if 'cocos2d-x' in l]
def ts(l):
    m=re.match(r'\d\d-\d\d (\d\d):(\d\d):(\d\d\.\d+)',l)
    return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3)) if m else None
first=[l for l in lines if 'loading begin' in l]
zeros=[l for l in lines if 'm_depth = 0' in l]
cap=[l for l in lines if 'server capability' in l]
if first and zeros:
    print('第一个 loading begin :', first[0][19:27])
    print('m_depth 归零        :', zeros[0][19:27], ' / 最后一次归零', zeros[-1][19:27])
    print('转圈时长            : %.3fs' % (ts(zeros[0])-ts(first[0])))
if cap: print('能力查询            :', cap[0].split('): ')[-1])
calls, got = {}, set()
for l in lines:
    m=re.search(r'call cmd = (-?\d+), seq = (\d+)',l)
    if m: calls.setdefault(int(m.group(2)),[]).append(int(m.group(1))); continue
    m=re.search(r'got packet, seq = (\d+), cmd = (-?\d+)',l)
    if m: got.add(int(m.group(1)))
pend=[(s,c) for s,c in sorted(calls.items()) if s not in got]
print('未回包：', pend if pend else '无')
print('no handler：', sorted({l.split('no handler for cmd')[-1].strip() for l in lines if 'no handler' in l}))
EOF
