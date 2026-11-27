#!/usr/bin/env bash
# 等代理恢复后推送 master（KD163）
#
# 为什么需要它：这台机器上 github.com:443 只有走代理才通，而代理（Clash 之类）
# 会间歇性掉监听 —— 掉了 push 就报 "Failed to connect to github.com:443 over proxy"。
# 本脚本只做一件事：轮询代理，通了就 git push，不通就退避重试，不碰 GitHub REST API。
#
# ★ 绝对不要用 work/gh_api_push.py 替代它：那个走 API 逐条 replay，会生成新 commit
#   哈希（作者变成 token 账号），本地与远程因此变成两条平行历史，只能强推。
#   2026-11-27 就是这么撞上 non-fast-forward 的，远程旧链已备份在 backup-remote-20261127。
#
# 用法：
#   bash work/gh_push.sh              # 默认 30 次 * 20 秒 = 最多等 10 分钟
#   KD_PUSH_TRIES=120 KD_PUSH_GAP=30 bash work/gh_push.sh    # 等 1 小时
#   KD_GH_PROXY=http://127.0.0.1:7897 bash work/gh_push.sh   # 换代理端口
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1

PROXY=${KD_GH_PROXY:-http://127.0.0.1:7890}
TRIES=${KD_PUSH_TRIES:-30}
GAP=${KD_PUSH_GAP:-20}
BRANCH=${KD_PUSH_BRANCH:-master}

GIT_OPTS=(-c "http.proxy=$PROXY" -c http.sslBackend=openssl -c core.quotepath=false)

probe() {
    curl -sS -m 6 -o /dev/null -w '%{http_code}' -x "$PROXY" https://api.github.com/ 2>/dev/null
}

local_sha() { git rev-parse "$BRANCH"; }

remote_sha() {
    git "${GIT_OPTS[@]}" ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | awk '{print $1}'
}

echo "[gh_push] 仓库 $ROOT，分支 $BRANCH，代理 $PROXY"
echo "[gh_push] 本地待推提交："
if git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    git log --oneline "@{u}..$BRANCH" | sed 's/^/    /'
else
    echo "    （无上游跟踪，列最近 3 条）"
    git log --oneline -3 | sed 's/^/    /'
fi

i=0
while [ "$i" -lt "$TRIES" ]; do
    i=$((i + 1))
    code=$(probe)
    if [ "$code" = "200" ]; then
        echo "[gh_push] 第 $i 次：代理可用，开始推送…"
        if git "${GIT_OPTS[@]}" push origin "$BRANCH"; then
            l=$(local_sha); r=$(remote_sha)
            if [ "$l" = "$r" ]; then
                echo "[gh_push] 成功：remote $BRANCH = $l"
                exit 0
            fi
            echo "[gh_push] 推送返回成功但远端是 ${r:-空}，本地是 $l —— 请人工核对"
            exit 2
        fi
        echo "[gh_push] 推送失败（可能是认证窗口未完成），${GAP}s 后重试"
    else
        echo "[gh_push] 第 $i/$TRIES 次：代理不可用（probe=$code），${GAP}s 后重试"
    fi
    sleep "$GAP"
done

echo "[gh_push] 放弃：等了 $((TRIES * GAP)) 秒代理仍不通。"
echo "[gh_push] 手动重试： cd $ROOT && git push origin $BRANCH"
exit 1
