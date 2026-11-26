#!/bin/bash
# 一键起本地服全套（不开客户端）。
#
# 用法：
#   bash work/start_all.sh                    起 9000 游戏服 + 80 资源服
#   bash work/start_all.sh admin              另外起 8899 网页后台
#   bash work/start_all.sh login              起完顺便跑 relogin.sh（重启客户端并登录）
#   bash work/start_all.sh 192.168.0.50       指定资源服对外广播的 IP
#   KD163_HOST=10.0.0.5 bash work/start_all.sh
#
# 说明：
#   - 9000 / 80 已在监听就跳过，可以反复跑；
#   - Python 解释器按 PYTHON 环境变量 → python3 → python → Windows 常见路径 依次找，
#     Linux 服务器上直接 `PYTHON=python3 bash work/start_all.sh` 即可；
#   - adb / 客户端相关步骤只在装了 adb 的开发机上跑，部署机自动跳过。

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 1
mkdir -p logs
export PYTHONIOENCODING=utf-8

# ---- Python 解释器：优先环境变量，其次 PATH，最后 Windows 常见安装位置 ----
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
fi
if [ -z "$PY" ]; then
    for c in \
        "C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe" \
        "D:/WorkBuddyData/binaries/python/versions/3.13.12/python.exe"
    do
        [ -x "$c" ] && PY="$c" && break
    done
fi
if [ -z "$PY" ]; then
    echo "[fail] 找不到 python3；用 PYTHON=/usr/bin/python3 指定" >&2
    exit 1
fi
echo "[env] python = $PY"

# ---- 参数：admin / login 是模式，其余当成资源服广播 IP ----
MODE=""
HOSTIP="${KD163_HOST:-192.168.0.111}"
for a in "$@"; do
    case "$a" in
        admin|login) MODE="$MODE $a" ;;
        *) HOSTIP="$a" ;;
    esac
done

listening() {
    # 用 connect 探测，避免 netstat/ss 各平台列格式不同
    "$PY" - "$1" <<'PYEOF' >/dev/null 2>&1
import socket, sys
s = socket.socket()
s.settimeout(0.4)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PYEOF
}

start_srv() {
    local port=$1; shift
    local name=$1; shift
    if listening "$port"; then
        echo "[skip] $port 已在监听（$name）"
        return 0
    fi
    nohup "$PY" "$name" "$@" >> "logs/${name%.py}_run.out" 2>&1 &
    local pid=$!
    sleep 3
    if listening "$port"; then
        echo "[start] $port <- $name（pid $pid）"
    else
        echo "[fail] $port 没起来，看 logs/${name%.py}_run.out" >&2
        return 1
    fi
}

start_srv 9000 local_server3.py 9000 patch/exact.pem patch/exact.key
start_srv 80   local_http.py    80 "$HOSTIP"

case " $MODE " in
    *" admin "*) start_srv 8899 admin_server.py 8899 ;;
esac

# ---- 以下只在开发机（有 adb）执行 ----
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
if command -v adb >/dev/null 2>&1 || [ -x "$ADB" ]; then
    bash work/net_fix.sh || echo "[warn] net_fix.sh 失败（reverse/DNAT 没配上？）"
    case " $MODE " in
        *" login "*) bash work/relogin.sh ;;
    esac
else
    echo "[skip] 没装 adb：跳过 reverse/DNAT 与客户端登录（部署机正常）"
fi

echo "[start_all] 端口状态："
for p in 9000 80 8899; do
    if listening "$p"; then echo "  $p  LISTEN"; else echo "  $p  -"; fi
done
