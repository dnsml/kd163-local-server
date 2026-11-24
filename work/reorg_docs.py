# -*- coding: utf-8 -*-
"""把 docs/ 平铺的 25 份文档按 AGENTS.md 的三分类落位，并修正所有 .md 相对引用。

用法：
    python work/reorg_docs.py --dry     # 只打印计划
    python work/reorg_docs.py --apply   # 执行 git mv + 改写引用
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REFERENCE = [
    "协议逆向报告.md",
    "任务协议_真服抓包_20261122.md",
    "战斗协议_真服抓包_20261122.md",
    "VIP协议_真服抓包_20261124.md",
    "抓包清单.md",
    "建角流程_真服与本地实测_20261124.md",
    "战斗伤害口径_面板真值_修复_20261124.md",
    "战斗闪退_151整数编码_修复_20261124.md",
    "宠物获得_400702_真服包序_20261124.md",
    "他服数据实测_20261124.md",
]

HISTORY = [
    "A路线_零改客户端接管_20261122.md",
    "B路线_改包成自己的App_20261122.md",
    "共存包说明_20261122.md",
    "脱机可行性分析.md",
    "自建服务端可行性评估_20261122.md",
    "接任务闪退_修复记录_20261122.md",
    "战斗名与迷宫任务_修复_20261124.md",
    "交任务后_金币背包称号_修复_20261124.md",
    "任务奖励物品与妖灵_修复_20261124.md",
    "地图熊猫与称号消失_修复_20261124.md",
    "私服现状与建角流程_复盘_20261124.md",
    "和他的服对齐_差距清单_20261124.md",
    "KD163_今日成果与下窗口接续_20261122.md",
]

DOCS = "docs"

# .md 引用 token：允许带目录的相对路径。
# 负向断言 (?<![\w./-]) 是关键：否则会匹配到 `../README.md` 里的 `README.md` 子串，
# 再拼上原有前缀，产出 `../../README.md` 这种坏路径（2026-11-24 踩过一次）。
TOKEN = re.compile(r"(?<![\w./-])([0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_\-./\u4e00-\u9fff]*\.md)")


def git(*args):
    return subprocess.run(["git", "-c", "core.quotepath=false"] + list(args),
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def main(apply=False):
    moves = []
    for name in REFERENCE:
        moves.append((os.path.join(DOCS, name), os.path.join(DOCS, "reference", name)))
    for name in HISTORY:
        moves.append((os.path.join(DOCS, name), os.path.join(DOCS, "history", name)))
    moves.append((os.path.join(DOCS, "给对接AI_交接说明.md"), os.path.join(DOCS, "交接说明.md")))

    print("== 移动计划 %d 项 ==" % len(moves))
    for a, b in moves:
        print("  %s -> %s" % (a, b))
    if not apply:
        return

    os.makedirs(os.path.join(ROOT, DOCS, "reference"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, DOCS, "history"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, DOCS, "notes"), exist_ok=True)
    for a, b in moves:
        r = git("mv", a, b)
        if r.returncode:
            print("git mv 失败: %s -> %s\n%s" % (a, b, r.stderr))
            sys.exit(1)

    # 建立 basename -> 新路径（basename 必须唯一）
    out = git("ls-files", "*.md").stdout.splitlines()
    by_name = {}
    dup = set()
    for p in out:
        n = os.path.basename(p)
        if n in by_name:
            dup.add(n)
        by_name[n] = p
    for n in dup:
        print("!! basename 冲突，跳过改写:", n)

    changed = 0
    for p in out:
        full = os.path.join(ROOT, p)
        try:
            s = open(full, encoding="utf-8", errors="replace").read()
        except FileNotFoundError:
            continue
        orig = s
        here = os.path.dirname(p)

        def repl(m):
            tok = m.group(1)
            if tok in dup:
                return tok
            target = by_name.get(tok)
            if not target:
                return tok
            rel = os.path.relpath(target, here or ".").replace("\\", "/")
            return rel

        # 只改写「相对引用已失效」的情况：路径（含裸文件名）按当前目录解析不存在时，
        # 用 basename 唯一匹配到新位置，并换成正确的相对路径。
        def guarded(m):
            tok = m.group(1)
            if tok.startswith("http"):
                return tok
            # 已存在的路径不动
            if os.path.exists(os.path.join(ROOT, here, tok)):
                return tok
            base = os.path.basename(tok)
            if base in dup:
                return tok
            target = by_name.get(base)
            if not target:
                return tok
            rel = os.path.relpath(target, here or ".").replace("\\", "/")
            if rel.startswith("./"):
                rel = rel[2:]
            return rel

        s = TOKEN.sub(guarded, s)
        if s != orig:
            open(full, "w", encoding="utf-8", newline="\n").write(s)
            changed += 1
            print("改写引用:", p)

    print("== 共改写 %d 个文档 ==" % changed)


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)