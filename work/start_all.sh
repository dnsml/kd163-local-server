#!/bin/bash
# 一键起本地服全套（不开客户端）。
# 用法：
#   bash work/start_all.sh           起 9000 + 80 + 补 reverse/DNAT
#   bash work/start_all.sh admin     另外起 8899 网页后台
#   bash work/start_all.sh login     起完顺便跑 relogin.sh（重启客户端并登录）
# 说明：9000 / 80 已在监听就跳过，可以反复跑。

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 1
PY="C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe"
export PYTHONIOENCODING=utf-8

listening() {
    netstat -ano 2>/dev/null | grep LISTENING | grep -q ":$1 "
}

start_srv() {
    local port=$1; shift
    local name=$1; shift
    if listening "$port"; then
        echo "[skip] $port 已在监听（$name）"
        return 0
    fi
    nohup "$PY" "$name" "$@" >> "logs/${name%.py}_run.out" 2>&1 &
    sleep 3
    if listening "$port"; then
        echo "[start] $port <- $name（pid $!）"
    else
        echo "[fail] $port 没起来，看 logs/${name%.py}_run.out" >&2
        return 1
    fi
}

start_srv 9000 local_server3.py 9000 patch/exact.pem patch/exact.key
start_srv 80   local_http.py    80 192.168.0.111

if [ "$1" = "admin" ] || [ "$2" = "admin" ]; then
    start_srv 8899 admin_server.py 8899
fi

bash work/net_fix.sh

if [ "$1" = "login" ] || [ "$2" = "login" ]; then
    bash work/relogin.sh
fi

echo "[start_all] 监听端口：$(netstat -ano 2>/dev/null | grep LISTENING | grep -oE ':(9000|80|8899) ' | tr -d ' ' | sort -u | tr '\n' ' ')"
