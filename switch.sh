#!/bin/bash
# KD163 一键切换：真服抓包 / 本地服 / 共存包
# 用法：./switch.sh real     连他的服（经 relay 记录），装 orig.apk
#       ./switch.sh local    连本地服（v2，整机 DNAT）
#       ./switch.sh coexist  共存模式：orig 连他的服，kd163_coexist 连本地服，互不影响
#       ./switch.sh status   只看规则
set -u
ADB="/d/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
# 设备号自动探测：以前写死 127.0.0.1:5555，模拟器重启后会变成 emulator-5554，
# 脚本就整个失灵（规则没加上，客户端一直连真服，表现为「资源检测失败 / 进不去」）。
DEV="-s $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
HOST_IP="192.168.0.111"
REAL_IP="124.223.56.189"
DIR="/d/66666/kd163"
COEXIST_PKG="com.kdxx.local1"

MODE="${1:-status}"

su() { "$ADB" $DEV shell "su -c '$1'" 2>&1; }

# 反复删同一条规则，直到不存在（防止历史重复堆积）
del_rule() {
  for _ in 1 2 3 4 5; do
    su "iptables -t nat -C $1 2>/dev/null && echo HIT" | grep -q HIT || break
    su "iptables -t nat -D $1" >/dev/null 2>&1
  done
}

# 删掉所有 -m owner 的 DNAT 规则（共存包重装后 uid 会变，按特征清）
clean_owner_rules() {
  su "iptables -t nat -S OUTPUT" | grep -- '-m owner' | while read -r _ _ spec; do
    su "iptables -t nat -D OUTPUT $spec" >/dev/null 2>&1
  done
}

# 给指定 uid 加共存包专用 DNAT。
# 2026-11-22 起：模拟器 NAT 网段(172.16.1.x)到宿主机 LAN IP 的 TCP 入站被
# Windows 防火墙拦截(无管理员权限加不了放行规则)，唯一可用通道是 adb reverse。
# 所以 DNAT 目标改成设备回环：80 -> 127.0.0.1:8080(reverse 到宿主 80)，
# 9000 -> 127.0.0.1:9000(reverse)。8080 是因为 adb reverse 绑不了设备特权端口 80。
# 前置：route_localnet=1（DNAT 到 127.0.0.1 必需）+ adb reverse 三条。
add_coexist_rules() {
  local uid="$1"
  # 开 route_localnet（幂等）
  su "echo 1 > /proc/sys/net/ipv4/conf/all/route_localnet"
  # adb reverse：设备侧端口 -> 宿主机端口（幂等，先清后建）
  # 19000 = 真服抓包 relay，缺它时私服/DNAT 到 127.0.0.1:19000 会「网络连接失败」
  "$ADB" $DEV reverse --remove tcp:8080 >/dev/null 2>&1
  "$ADB" $DEV reverse --remove tcp:9000 >/dev/null 2>&1
  "$ADB" $DEV reverse --remove tcp:19000 >/dev/null 2>&1
  "$ADB" $DEV reverse tcp:8080 tcp:80 >/dev/null 2>&1
  "$ADB" $DEV reverse tcp:9000 tcp:9000 >/dev/null 2>&1
  "$ADB" $DEV reverse tcp:19000 tcp:19000 >/dev/null 2>&1
  del_rule "OUTPUT -m owner --uid-owner $uid -p tcp --dport 9000 -j DNAT --to-destination 127.0.0.1:9000"
  del_rule "OUTPUT -m owner --uid-owner $uid -p tcp --dport 80 -j DNAT --to-destination 127.0.0.1:8080"
  su "iptables -t nat -A OUTPUT -m owner --uid-owner $uid -p tcp --dport 9000 -j DNAT --to-destination 127.0.0.1:9000" >/dev/null
  su "iptables -t nat -A OUTPUT -m owner --uid-owner $uid -p tcp --dport 80 -j DNAT --to-destination 127.0.0.1:8080" >/dev/null
}

coexist_uid() {
  "$ADB" $DEV shell "dumpsys package $COEXIST_PKG" | grep -m1 userId | grep -o '[0-9]*'
}

clean_all() {
  del_rule "OUTPUT -d $REAL_IP -p tcp --dport 9000 -j DNAT --to-destination $HOST_IP:19000"
  del_rule "OUTPUT -d $REAL_IP -p tcp --dport 9000 -j DNAT --to-destination $HOST_IP:9000"
  del_rule "OUTPUT -d $REAL_IP -p tcp --dport 80 -j DNAT --to-destination $HOST_IP:80"
  clean_owner_rules
}

install_apk() {
  local apk="$1"
  "$ADB" $DEV shell am force-stop com.kdxx.online >/dev/null 2>&1
  sleep 1
  "$ADB" $DEV uninstall com.kdxx.online >/dev/null 2>&1
  "$ADB" $DEV install -r "$apk" 2>&1 | tail -2
}

case "$MODE" in
  real)
    clean_all
    su "iptables -t nat -A OUTPUT -d $REAL_IP -p tcp --dport 9000 -j DNAT --to-destination $HOST_IP:19000" >/dev/null
    echo "[真服模式] 9000 -> $HOST_IP:19000 (relay)，资源直连真服"
    install_apk "D:/66666/kd163/orig.apk"
    ;;
  local)
    clean_all
    su "iptables -t nat -A OUTPUT -d $REAL_IP -p tcp --dport 9000 -j DNAT --to-destination $HOST_IP:9000" >/dev/null
    su "iptables -t nat -A OUTPUT -d $REAL_IP -p tcp --dport 80 -j DNAT --to-destination $HOST_IP:80" >/dev/null
    echo "[本地模式] 9000 -> $HOST_IP:9000，80 -> $HOST_IP:80"
    install_apk "D:/66666/kd163/patch/kd163_v2.apk"
    ;;
  coexist)
    # 共存模式：不动 orig.apk，不装整机 DNAT。
    # 只有共存包 uid 的 9000/80 流量进本地服；他的服客户端照常直连真服。
    local_uid=$(coexist_uid)
    if [ -z "$local_uid" ]; then
      echo "[!] 未安装 $COEXIST_PKG，先装：adb install -r D:/66666/kd163/patch/kd163_coexist.apk"
      exit 1
    fi
    clean_all
    add_coexist_rules "$local_uid"
    echo "[共存模式] $COEXIST_PKG uid=$local_uid：9000/80 -> $HOST_IP（本地服）；com.kdxx.online 直连真服不受影响"
    echo "  确保宿主服务已启动：python local_server3.py 9000 && python local_http.py"
    ;;
  status)
    ;;
  *)
    echo "用法: $0 real|local|coexist|status"; exit 1;;
esac

echo "=== 当前 DNAT ==="
su "iptables -t nat -L OUTPUT -n" | grep -E "DNAT|target" | head -8
echo "=== 端口监听 ==="
netstat -ano | grep -E ":19000 .*LISTENING|:9000 .*LISTENING|:80 .*LISTENING" | awk '{print $2, $5}'
