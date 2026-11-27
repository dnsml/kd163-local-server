#!/bin/bash
# 抓包模式：把客户端的 9000 流量接到本机 19000（relay.py → 真线上服 124.223.56.189:9000），
# 80 仍走本机资源服（资源内容与线上一致，不影响登录）。
# 用法：KD_DEV=emulator-5554 bash work/net_capture.sh
# 用完恢复开发环境：KD_DEV=emulator-5554 bash work/net_fix.sh
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"

if [ -n "$KD_DEV" ]; then
    DEV="-s $KD_DEV"
else
    DEV="-s $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
fi
if [ "$DEV" = "-s " ]; then
    echo "[cap] 没有在线设备" >&2
    exit 1
fi

# 资源 80 -> 宿主 8080；抓包 9000 -> 宿主 19000
"$ADB" $DEV reverse tcp:8080 tcp:80   >/dev/null
"$ADB" $DEV reverse tcp:19000 tcp:19000 >/dev/null
echo "[cap] reverse: $("$ADB" $DEV reverse --list | tr '\n' ' ')"

"$ADB" $DEV shell "su 0 iptables -t nat -F OUTPUT" 2>/dev/null
for R in 124.223.56.189 192.168.0.111; do
    "$ADB" $DEV shell "su 0 iptables -t nat -A OUTPUT -d $R -p tcp --dport 80 -j DNAT --to-destination 127.0.0.1:8080" 2>/dev/null
    "$ADB" $DEV shell "su 0 iptables -t nat -A OUTPUT -d $R -p tcp --dport 9000 -j DNAT --to-destination 127.0.0.1:19000" 2>/dev/null
done

# 清掉 net_fix 留下的 KD_GUARD，否则 9000 会被 RST
"$ADB" $DEV shell "su 0 iptables -F KD_GUARD" 2>/dev/null

echo "[cap] 9000 -> 19000(relay) -> 124.223.56.189:9000"
"$ADB" $DEV shell "su 0 iptables -t nat -L OUTPUT -n" | grep -E "124.223.56.189|192.168.0.111"
