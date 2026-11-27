# -*- coding: utf-8 -*-
"""⚠️ 已停用（2026-11-27）——不要再用它推，改用 `git push` 或 `work/gh_push.sh`。

    原因：本脚本走 REST API 逐条 replay，每个 commit 都是**新对象**（作者变成 token 账号），
    哈希全变。远程与本地因此成为两条平行历史，之后只能强推：
    本地 2e59811 与远程 727666f 标题相同、树对象完全一致、哈希不同，就是这么来的。
    正确通道：git 走 http://127.0.0.1:7890 + http.sslBackend=openssl（已写进仓库配置），
    代理掉线时用 `bash work/gh_push.sh` 轮询重试。旧链备份在分支 backup-remote-20261127。

----- 以下为原始说明，仅作历史参考 -----

用 GitHub REST API 推送提交（github.com:443 被墙、api.github.com 能通时的备用通道）。

原理：不碰 git 传输协议，逐个本地提交replay 成远端提交：
    每个提交 → 该提交相对父提交的改动文件 → blob → tree(base_tree) → commit → 最后更新 ref
提交信息、作者时间、增删改都保留，只是哈希会变（远端提交的 committer 是 token 账号）。

用法：
    python work/gh_api_push.py --dry     # 只打印计划
    python work/gh_api_push.py           # 真推

前置：token 在 D:/66666/.gh_pat（scope=repo，不入库）。
推完请执行：git fetch origin && git reset --soft origin/main
（工作区内容一致，只是本地历史对齐到远端那几条）
"""
import base64
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "dnsml/kd163-local-server"
BRANCH = "master"
TOKEN = open("D:/66666/.gh_pat", encoding="utf-8").read().strip()
API = "https://api.github.com"
# 出口：优先用环境里的代理（KD163_GH_PROXY 可覆盖），没有就直接连。
# 以前这里写死 127.0.0.1:64435，代理一换端口就 WinError 10061，
# 还容易误判成「GitHub 不通」。
PROXY = (os.environ.get("KD163_GH_PROXY")
         or os.environ.get("https_proxy")
         or os.environ.get("HTTPS_PROXY")
         or "").strip()


def _opener(verify=True, proxy=None):
    """按指定出口建 opener；proxy 为空串 = 直连。

    代理会做 TLS 中间人，某些会话下给的是「签不出完整链」的证书，
    默认校验会 `CERTIFICATE_VERIFY_FAILED`。这时退一步不校验证书再试一次
    —— 只为把提交推上去，不是安全通道，别拿它传别的东西。
    """
    if proxy is None:
        proxy = PROXY
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if proxy:
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        # 显式空 ProxyHandler，避免 urllib 又去读环境变量
        handlers.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "kd163-push")
    if data:
        req.add_header("Content-Type", "application/json")
    if method == "GET":
        req.add_header("Cache-Control", "no-cache")

    exits = [PROXY, ""] if PROXY else [""]
    last = None
    for proxy in exits:
        for verify in (True, False):
            try:
                with _opener(verify, proxy).open(req, timeout=180) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError:
                raise
            except urllib.error.URLError as e:
                last = e
                if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                    if verify:
                        sys.stderr.write("[warn] %s 证书链校验失败，改不校验重试\n"
                                         % (proxy or "直连"))
                        continue
                if proxy:
                    sys.stderr.write("[warn] 出口 %s 不可用（%s），改直连\n"
                                     % (proxy, getattr(e, "reason", e)))
                break
    raise last


# core.quotepath=false：不然中文文件名会输出成 "docs/\344\272..." 的八进制转义，
# `git show <c>:<path>` 直接 128。踩过一次。
def git(*args, **kw):
    out = subprocess.check_output(["git", "-c", "core.quotepath=false"] + list(args),
                                  cwd=HERE, **kw)
    return out if kw.get("text") is False else out.decode("utf-8")


def gitbytes(*args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false"] + list(args),
                                   cwd=HERE)


def main():
    dry = "--dry" in sys.argv
    ref = call("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH))
    base = ref["object"]["sha"]
    head = git("rev-parse", "HEAD").strip()
    print("远端 %s = %s" % (BRANCH, base[:10]))
    print("本地 HEAD = %s" % head[:10])
    if base == head:
        print("已经同步，无需推送")
        return 0

    # 远端 HEAD 可能是上一轮 API 推出来的：哈希和本地不同、tree 相同，本地根本没有
    # 这个对象 → `base..head` 会直接报 128。这时按 tree 找到等价的本地提交当起点，
    # replay 出来的内容一样，只是少算几条「已经推过」的提交。
    try:
        todo = git("rev-list", "--reverse", "%s..%s" % (base, head)).split()
    except subprocess.CalledProcessError:
        rtree = call("GET", "/repos/%s/git/commits/%s" % (REPO, base))["tree"]["sha"]
        eq = None
        for c in git("rev-list", head).split():
            if git("rev-parse", c + "^{tree}").strip() == rtree:
                eq = c
                break
        if eq is None:
            print("远端提交不在本地历史里，也找不到同 tree 的本地提交；"
                  "先 git fetch / git pull 对齐再推")
            return 1
        print("远端提交本地没有，按 tree 对齐到本地 %s（tree=%s）" % (eq[:10], rtree[:10]))
        todo = git("rev-list", "--reverse", "%s..%s" % (eq, head)).split()
    if not todo:
        print("没有新提交（远端不在本地历史里？先 git fetch 对齐）")
        return 1
    print("待 replay %d 个提交：" % len(todo))
    for c in todo:
        stat = git("diff", "--name-status", c + "^", c).strip().splitlines()
        print("   %s %s  (%d 个文件)" % (c[:8], git("log", "-1", "--pretty=%s", c), len(stat)))
        for line in stat:
            print("        " + line.replace("\t", " "))
    if dry:
        return 0

    prev = base
    for c in todo:
        local_parent = git("rev-parse", c + "^").strip()
        stat = [l.split("\t") for l in
                git("diff", "--name-status", local_parent, c).strip().splitlines() if l.strip()]
        entries = []
        for parts in stat:
            st = parts[0]
            if st.startswith("R") and len(parts) >= 3:
                # 改名：老路径要从 tree 里删掉，新路径写 blob。
                # 之前这里直接 `for st, path in stat` 解包，遇到 R100（3 列）就
                # ValueError: too many values to unpack —— 一改名推送必挂。
                old, path = parts[1], parts[2]
                entries.append({"path": old, "mode": "100644",
                                "type": "blob", "sha": None})
            elif st == "D":
                entries.append({"path": parts[1], "mode": "100644",
                                "type": "blob", "sha": None})
                continue
            else:
                path = parts[1]
            raw = gitbytes("show", "%s:%s" % (c, path))
            b = call("POST", "/repos/%s/git/blobs" % REPO,
                     {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
            print("   blob %s %-40s %8dB" % (b["sha"][:8], path, len(raw)))
            entries.append({"path": path, "mode": "100644",
                            "type": "blob", "sha": b["sha"]})
        base_tree = call("GET", "/repos/%s/git/commits/%s" % (REPO, prev))["tree"]["sha"]
        tree = call("POST", "/repos/%s/git/trees" % REPO,
                    {"base_tree": base_tree, "tree": entries})
        msg = git("log", "-1", "--pretty=%B", c).strip()
        date = git("log", "-1", "--pretty=%aI", c).strip()
        commit = call("POST", "/repos/%s/git/commits" % REPO,
                      {"message": msg, "tree": tree["sha"], "parents": [prev],
                       "author": {"name": "kd163", "email": "kd163@local",
                                  "date": date}})
        print("   commit %s <- %s" % (commit["sha"][:8], c[:8]))
        prev = commit["sha"]

    call("PATCH", "/repos/%s/git/refs/heads/%s" % (REPO, BRANCH),
         {"sha": prev, "force": False})
    now = call("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH))
    print("远端已更新为 %s" % now["object"]["sha"][:10])
    print("\n下一步（对齐本地历史，工作区不会变）：")
    print("   git fetch origin && git reset --soft origin/%s" % BRANCH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
