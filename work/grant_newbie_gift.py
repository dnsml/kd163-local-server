# -*- coding: utf-8 -*-
"""给指定存档补发「新人三件套」（玄女礼包），不重复发。

用法：
    python work/grant_newbie_gift.py player_state.json
    python work/grant_newbie_gift.py player_state_<acct>.json --dry

为什么用脚本而不是 GM 命令：GM 命令要客户端在线才能走 logs/gm.cmd，
这里直接在离线存档上补，改完再起服，不依赖客户端。

2026-11-26：新号三件套以前只是 TPL_PUSH[178] 录制的假快照，服务端根本没有；
现在 create_character 会对**新建**角色真发，已经建好的老角色用这个脚本补。
"""
import json
import os
import sys

# local_server3 在 import 时就把 sys.argv[1] 当端口解析，这里必须先摘掉自己的参数
_ARGS = sys.argv[1:]
sys.argv = [sys.argv[0]]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import local_server3 as sv     # noqa: E402  只为拿 NEWBIE_GIFT / BAG_CAPACITY / 物品名


def main():
    if not _ARGS:
        print(__doc__)
        return 2
    path = _ARGS[0]
    dry = "--dry" in _ARGS
    if not os.path.exists(path):
        print("没有这个存档：%s" % path)
        return 2
    d = json.load(open(path, encoding="utf-8"))
    bag = {int(k): list(v) for k, v in (d.get("bag") or {}).items()}
    have = {v[0] for v in bag.values()}
    added = []
    for iid, num in sv.NEWBIE_GIFT:
        if iid in have:
            print("已有 %s（%d），跳过" % (sv.bag_proto.item(iid)["name"], iid))
            continue
        pos = 0
        while pos in bag:
            pos += 1
        if pos >= sv.BAG_CAPACITY:
            print("背包已满，%d 放不下" % iid)
            continue
        bag[pos] = [iid, num]
        added.append((pos, iid, num))
        print("槽%02d <- %s（%d）×%d"
              % (pos, sv.bag_proto.item(iid)["name"], iid, num))
    if dry:
        print("[dry] 不写盘")
        return 0
    if not added:
        print("无需改动")
        return 0
    d["bag"] = {str(k): v for k, v in sorted(bag.items())}
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print("已写入 %s（%d 件）" % (path, len(d["bag"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
