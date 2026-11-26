#!/bin/bash
# 只修「模拟器 -> 本机」的两层通路，不碰客户端。
#   用法：bash work/net_fix.sh
# 1) adb reverse 三条（端口映射，adb 重连就丢，8080 常先没）
# 2) iptables DNAT 两条（模拟器重启就丢，客户端只认 IP 字面量 124.223.56.189）
# 两层都幂等，重复跑没事。

ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"

if [ -n "$KD_DEV" ]; then
    DEV="-s $KD_DEV"
else
    DEV="-s $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
fi
if [ "$DEV" = "-s " ]; then
    echo "[net] 没有在线设备，先启动模拟器或 adb connect" >&2
    exit 1
fi
echo "[net] 设备 $DEV"

for p in "tcp:8080 tcp:80" "tcp:9000 tcp:9000" "tcp:19000 tcp:19000"; do
    "$ADB" $DEV reverse $p
done
echo "[net] reverse: $("$ADB" $DEV reverse --list | tr '\n' ' ')"

# ★ 客户端读的是 server-list.xml / assets.txt 里的 IP 字面量，不走 DNS（hosts 无效）。
#   必须用 iptables OUTPUT DNAT 兜住。
#
# ★ 2026-11-26 12:33 改目标：以前 DNAT 到宿主 LAN IP（192.168.0.111），
#   但 Windows 防火墙会挡「模拟器 -> 宿主 80」，实测 SYN 能匹配到 DNAT 规则、
#   连接却建不起来（规则计数在涨、本机 80 一条 GET 都没有），
#   表现就是客户端一启动弹「检测资源版本信息失败，是否重试？」
#   （assets.txt 里 assets_url 指向 124.223.56.189:80，客户端会去校验它）。
#   改成转到 127.0.0.1 的 adb reverse 端口：reverse 是 adb 自己拉的通路，
#   与宿主防火墙、LAN 可达性全部无关，实测立刻能取到 assets.txt。
RIP="124.223.56.189"
HOSTIP="${KD_HOST:-192.168.0.111}"
"$ADB" $DEV shell "su 0 iptables -t nat -F OUTPUT" 2>/dev/null
"$ADB" $DEV shell "su 0 iptables -t nat -A OUTPUT -d $RIP -p tcp --dport 80 -j DNAT --to-destination 127.0.0.1:8080" 2>/dev/null
"$ADB" $DEV shell "su 0 iptables -t nat -A OUTPUT -d $RIP -p tcp --dport 9000 -j DNAT --to-destination 127.0.0.1:9000" 2>/dev/null

# ★ 2026-11-26 12:36：光改 124.223.56.189 还不够 —— server-list.xml 里游戏服地址
#   被 rewrite_host 改成了宿主 IP，客户端拿着 192.168.0.111:9000 直连，
#   模拟器网段到宿主 9000 同样被挡，socket 永远 SYN_SENT，
#   界面就停在「正在连接服务器。。。」（点开始游戏后一直转圈，进不去）。
#   所以宿主 IP 的 80/9000 也一起转到 reverse 端口。
"$ADB" $DEV shell "su 0 iptables -t nat -A OUTPUT -d $HOSTIP -p tcp --dport 80 -j DNAT --to-destination 127.0.0.1:8080" 2>/dev/null
"$ADB" $DEV shell "su 0 iptables -t nat -A OUTPUT -d $HOSTIP -p tcp --dport 9000 -j DNAT --to-destination 127.0.0.1:9000" 2>/dev/null

# 兜底：万一 reverse 没起来（老设备/无 reverse 权限），再挂一条直连宿主的。
"$ADB" $DEV reverse --list 2>/dev/null | grep -q "tcp:8080" || {
    "$ADB" $DEV shell "su 0 iptables -t nat -I OUTPUT 1 -d $RIP -p tcp --dport 80 -j DNAT --to-destination $HOSTIP:80" 2>/dev/null
}

_rule=$("$ADB" $DEV shell "su 0 iptables -t nat -S OUTPUT" 2>/dev/null | tr -d '\r')
if echo "$_rule" | grep -q "124.223.56.189"; then
    echo "[net] DNAT -> 127.0.0.1（adb reverse）已生效"
else
    echo "[net] ！！DNAT 没加上（模拟器没 root？）—— 客户端会去连线上服" >&2
    exit 2
fi

# ★ 2026-11-26 13:0x：最后一道保险 —— 「宁可连不上，也绝不打到线上服」。
#   过滤器表（filter OUTPUT）是在 nat DNAT 之后评估的，DNAT 命中的包目的地址
#   已经变成 127.0.0.1，不会撞到下面这些规则；只有 DNAT 丢了（iptables 被清、
#   模拟器重启后没跑本脚本）时，包才会带着 124.223.56.189 到达这里，被直接 RST。
#   以前 DNAT 一丢，客户端就静默登进线上服，表现成「安全连接验证失败」或
#   「账号在别人服上」—— 加这条后立刻变成连接失败，一眼能看出是本机服没起来。
"$ADB" $DEV shell "su 0 iptables -N KD_GUARD 2>/dev/null; su 0 iptables -F KD_GUARD" 2>/dev/null
"$ADB" $DEV shell "su 0 iptables -A KD_GUARD -d $RIP -p tcp --dport 9000 -j REJECT --reject-with tcp-reset" 2>/dev/null
"$ADB" $DEV shell "su 0 iptables -A KD_GUARD -d $RIP -p tcp --dport 19000 -j REJECT --reject-with tcp-reset" 2>/dev/null
"$ADB" $DEV shell "su 0 iptables -A KD_GUARD -j RETURN" 2>/dev/null
"$ADB" $DEV shell "su 0 iptables -C OUTPUT -j KD_GUARD 2>/dev/null || su 0 iptables -I OUTPUT 1 -j KD_GUARD" 2>/dev/null
if "$ADB" $DEV shell "su 0 iptables -S OUTPUT" 2>/dev/null | tr -d '\r' | grep -q "KD_GUARD"; then
    echo "[net] 防误连线上服：124.223.56.189(:9000/:19000) 未走 DNAT 时直接 RST"
fi
