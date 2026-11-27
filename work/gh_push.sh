#!/usr/bin/env bash
# 推 master 到 GitHub（KD163）——不依赖梯子
#
# 背景（2026-11-27 实测，记在 docs/notes/2026-11-27.md）
#   · 开梯子时：github.com:443 走 127.0.0.1:7890 能推，但 schannel 后端握手会崩，
#     必须 -c http.sslBackend=openssl。
#   · 关梯子后：github.com 域名直连 TCP 超时（连接都建不起来），
#     而 GitHub 真实 IP（140.82.113.4 / 20.205.243.166 …）443 全部 OPEN，
#     curl --resolve 访问 git-receive-pack 返回 200，api.github.com 直连也正常。
#     → 故障只在"域名这一步"，TLS 与鉴权都是好的。
#   所以本脚本起一个本机 CONNECT 隧道（work/gh_tunnel.py），把 github.com
#   固定到 IP 池里的可用 IP，git 照常走 https，SNI/证书不受影响。
#
# ★ 绝对不要用 work/gh_api_push.py 替代它：那个走 REST API 逐条 replay，
#   会生成新 commit 哈希（committer 变成 token 账号），本地与远程就变成两条
#   平行历史，只能强推。2026-11-27 的 non-fast-forward 就是这么来的，
#   远程旧链已备份为 backup-remote-20261127。
#
# 用法：
#   bash work/gh_push.sh                     # 隧道 + 直推，几秒完事
#   KD_TUNNEL_PORT=7899 bash work/gh_push.sh # 换隧道端口
#   KD_GH_PROXY=http://127.0.0.1:7890 bash work/gh_push.sh   # 有梯子时直接用梯子
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1

BRANCH=${KD_PUSH_BRANCH:-master}
PORT=${KD_TUNNEL_PORT:-7899}
PAT=${KD_GH_PAT:-/d/66666/.gh_pat}
TRIES=${KD_PUSH_TRIES:-3}
GAP=${KD_PUSH_GAP:-5}

pick_python() {
    if [ -n "${KD_PYTHON:-}" ]; then echo "$KD_PYTHON"; return; fi
    for c in \
        "D:/WorkBuddyData/binaries/python/versions/3.13.12/python.exe" \
        python3 python; do
        if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then echo "$c"; return; fi
    done
}

port_open() { (echo > /dev/tcp/127.0.0.1/"$1") >/dev/null 2>&1; }

TUNNEL_PID=""
cleanup() {
    if [ -n "$TUNNEL_PID" ] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill "$TUNNEL_PID" 2>/dev/null
    fi
}
trap cleanup EXIT

# 选通道：KD_GH_PROXY 优先（有梯子时），否则起本地隧道
if [ -n "${KD_GH_PROXY:-}" ]; then
    PROXY=$KD_GH_PROXY
    echo "[gh_push] 用现成代理 $PROXY"
else
    PY=$(pick_python)
    if [ -z "$PY" ]; then
        echo "[gh_push] 找不到 python，退化为直连（大概率不通）" >&2
        PROXY=""
    else
        if port_open "$PORT"; then
            echo "[gh_push] 端口 $PORT 已在监听，复用现有隧道"
        else
            "$PY" work/gh_tunnel.py "$PORT" >logs/gh_tunnel.log 2>&1 &
            TUNNEL_PID=$!
            for _ in $(seq 1 30); do
                port_open "$PORT" && break
                sleep 0.3
            done
            if ! port_open "$PORT"; then
                echo "[gh_push] 隧道起不来，见 logs/gh_tunnel.log" >&2
                cat logs/gh_tunnel.log >&2 2>/dev/null
                exit 1
            fi
            echo "[gh_push] 隧道就绪 127.0.0.1:$PORT"
        fi
        PROXY="http://127.0.0.1:$PORT"
    fi
fi

GIT_OPTS=(-c http.sslBackend=openssl -c core.quotepath=false)
[ -n "$PROXY" ] && GIT_OPTS+=(-c "http.proxy=$PROXY")

# 有 PAT 文件就用它，避免 GCM 弹窗（关梯子时弹窗也弹不出来）
if [ -r "$PAT" ]; then
    GIT_OPTS+=(-c "credential.helper=")
    GIT_OPTS+=(-c "credential.helper=!f(){ echo username=x-access-token; echo password=\$(tr -d ' \\r\\n' < \"$PAT\"); };f")
fi

local_sha() { git rev-parse "$BRANCH"; }
remote_sha() {
    git "${GIT_OPTS[@]}" ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | awk '{print $1}'
}

echo "[gh_push] 仓库 $ROOT / 分支 $BRANCH / 通道 ${PROXY:-直连}"
echo "[gh_push] 待推提交："
git log --oneline "origin/$BRANCH..$BRANCH" 2>/dev/null | sed 's/^/    /' || git log --oneline -3 | sed 's/^/    /'

i=0
while [ "$i" -lt "$TRIES" ]; do
    i=$((i + 1))
    echo "[gh_push] 第 $i/$TRIES 次推送…"
    if git "${GIT_OPTS[@]}" push origin "$BRANCH"; then
        l=$(local_sha); r=$(remote_sha)
        if [ "$l" = "$r" ]; then
            # 这台机器上 git 自己写的 refs/remotes/origin/* 会被清掉（status 一直 [gone]），
            # 手工补一次：内容就是远端 sha + 换行。
            mkdir -p ".git/refs/remotes/origin"
            printf '%s\n' "$l" > ".git/refs/remotes/origin/$BRANCH"
            echo "[gh_push] 成功：remote $BRANCH = $l"
            exit 0
        fi
        echo "[gh_push] 推送返回成功但远端是 ${r:-空}，本地是 $l —— 人工核对" >&2
        exit 2
    fi
    sleep "$GAP"
done

echo "[gh_push] 失败：$TRIES 次都没推上去。" >&2
echo "[gh_push] 排查：python work/gh_tunnel.py $PORT （另开窗口），再 git -c http.proxy=http://127.0.0.1:$PORT push origin $BRANCH" >&2
exit 1
