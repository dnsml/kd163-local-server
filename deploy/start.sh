#!/bin/bash
# KD163 开服脚本（Linux）
#
#   ./start.sh start        启动 游戏服 9000 + 资源服 80 + 网页后台 8899
#   ./start.sh stop         停止（按 run/*.pid）
#   ./start.sh restart      重启
#   ./start.sh status       看端口
#
# 可选环境变量：
#   PYTHON=python3          指定解释器
#   KD163_HOST=1.2.3.4      资源服 server-list.xml 里广播的地址（默认 127.0.0.1）
#   KD_STATE_BACKEND=json   回退旧 JSON 存档（默认 sqlite）
#   KD_ALLOW_ACCOUNTS=...   白名单（也可以放 accounts.json）
#
# 注意：80 端口在 Linux 上需要 root 或 CAP_NET_BIND_SERVICE；
#      普通用户启动时资源服会起不来，脚本会提示。

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
# 在仓库里从 deploy/ 调用时，工作目录应该是它的上一级（代码所在处）
if [ ! -f "$DIR/local_server3.py" ] && [ -f "$DIR/../local_server3.py" ]; then
  DIR="$(cd "$DIR/.." && pwd)"
fi
cd "$DIR" || exit 1

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3 python; do
    command -v "$c" >/dev/null 2>&1 && PY="$c" && break
  done
fi
[ -z "$PY" ] && { echo "找不到 python3，用 PYTHON=/usr/bin/python3 ./start.sh start"; exit 1; }
HOSTIP="${KD163_HOST:-127.0.0.1}"

mkdir -p logs run backups
export PYTHONIOENCODING=utf-8

port_open() {
  "$PY" - "$1" <<'PYEOF' >/dev/null 2>&1
import socket, sys
s = socket.socket(); s.settimeout(0.4)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PYEOF
}

start_one() {
  local name=$1 port=$2; shift 2
  if port_open "$port"; then
    echo "[skip] $name :$port 已在监听"
    return 0
  fi
  nohup "$PY" "$@" >> "logs/${name}.out" 2>&1 &
  local pid=$!
  echo "$pid" > "run/${name}.pid"
  sleep 2
  if port_open "$port"; then
    echo "[ ok ] $name :$port pid=$pid"
  else
    echo "[fail] $name 没起来，看 logs/${name}.out"
    tail -5 "logs/${name}.out" 2>/dev/null | sed 's/^/       /'
    return 1
  fi
}

stop_one() {
  local name=$1
  local f="run/${name}.pid"
  if [ ! -f "$f" ]; then
    echo "[skip] $name 没有 pid 文件"
    return 0
  fi
  local pid
  pid=$(cat "$f")
  if kill "$pid" 2>/dev/null; then
    echo "[stop] $name pid=$pid"
  else
    echo "[skip] $name pid=$pid 已经不在了"
  fi
  rm -f "$f"
}

case "${1:-start}" in
start)
  echo "== KD163 开服（$DIR）"
  [ "$(id -u)" != "0" ] && echo "[提示] 当前不是 root：资源服 80 端口可能起不来"
  start_one game 9000 local_server3.py 9000 patch/exact.pem patch/exact.key
  start_one http 80   local_http.py 80 "$HOSTIP"
  start_one admin 8899 admin_server.py 8899
  echo
  echo "网页后台：http://127.0.0.1:8899  （对外请用 SSH 隧道或 Nginx + HTTPS）"
  echo "游戏端口：9000   资源端口：80   日志：logs/{game,http,admin}.out"
  ;;
stop)
  stop_one admin
  stop_one http
  stop_one game
  ;;
restart)
  "$0" stop
  sleep 1
  "$0" start
  ;;
status)
  for p in 9000 80 8899; do
    if port_open "$p"; then echo ":$p  LISTEN"; else echo ":$p  -"; fi
  done
  ;;
*)
  echo "用法：$0 {start|stop|restart|status}"
  exit 1
  ;;
esac
