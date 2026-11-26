# -*- coding: utf-8 -*-
"""建角 / 新人礼包 / 剧情神装槽位 自检（全部写临时档，不碰真实存档、不起服）。

用法：
    python work/test_newbie_char.py

背景（2026-11-26）：
1. 13:41:01 事故：账号 1784366 已有 Lv45 角色（御剑战甲 / 背包 32 件 / 绑铜 298120 /
   元宝 924），客户端又弹了一次建角界面，服务端直接 reset_newbie + force 落盘，
   把 Lv45 冲成 Lv1。→ 建角必须拒绝覆盖已有角色。
2. 新号背包以前是空的，「玄女给的三件套」只是 TPL_PUSH[178] 录制的假快照。
   真服新号 178 原包（23B）= 槽0 20190×10 / 槽1 20432×1 / 槽2 21001×1。
3. 400501「物归原主」的剧情神装写成槽 1~7，武器 11114 落在槽 7（协议层字节），
   build_30 不认 → 面板武器格空着 =「太虚星君给的装备不显示」。

断言：
A. 已有角色时 create_character 返回 False，存档文件逐字节不变
B. 空号 create_character → 背包 0/1/2 = 20190×10 / 20432×1 / 21001×1
C. equip_story_set → 装备键 = {1,2,3,4,5,6,8}，槽 8 是 11114，槽 7 不存在
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 本用例的 fixture 是临时 JSON 文件（patch 了 state_path），固定跑 JSON 后端；
# SQLite 后端的读档/落盘由 work/test_state_store.py 覆盖。
os.environ["KD_STATE_BACKEND"] = "json"

import local_server3 as sv                     # noqa: E402
from player import ctx, new as new_player      # noqa: E402

# 备份目录也指向临时目录：建角路径会 backup_state(force=True)，
# 不隔离的话每次跑测试都往 backups/ 里丢一份 tmp*.json
sv.BACKUP_DIR = tempfile.mkdtemp(prefix="kd_bak_")

fails = []


def _tmpfile(data):
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    if data is not None:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
    return tmp


def main():
    # ---- A：已有角色不许被建角覆盖 ----
    old = {"name": "老角色", "has_char": True, "level": 45, "exp": 116313,
           "coin": 298120, "yuanbao": 924, "ver": 2,
           "bag": {str(i): [20139, 1] for i in range(32)},
           "equip": {"1": 12108, "8": 12114}}
    raw = json.dumps(old, ensure_ascii=False, sort_keys=True)
    tmp = _tmpfile(raw)
    before = open(tmp, encoding="utf-8").read()
    sv_old = sv.state_path
    sv.state_path = lambda _pl=None: tmp
    try:
        pl = new_player()
        pl.acct = "99990001"
        with ctx(pl):
            sv.load_state(pl)
            ok, msg = sv.create_character("新名字", 1)
            if ok:
                fails.append("A 已有角色仍被建角成功：%s" % msg)
            if "已有角色" not in msg:
                fails.append("A 提示文案没说明已有角色：%r" % msg)
            if pl.level != 45 or pl.name != "老角色":
                fails.append("A 内存里的角色被改：Lv%s %r" % (pl.level, pl.name))
        after = open(tmp, encoding="utf-8").read()
        if after != before:
            fails.append("A 存档文件被改写（%dB -> %dB）" % (len(before), len(after)))
    finally:
        sv.state_path = sv_old
        os.unlink(tmp)

    # ---- B：空号建角要真的带三件套 ----
    tmp = _tmpfile(None)
    sv.state_path = lambda _pl=None: tmp
    try:
        pl = new_player()
        pl.acct = "99990002"
        with ctx(pl):
            ok, msg = sv.create_character("测试新丁", 1)
            if not ok:
                fails.append("B 空号建角失败：%s" % msg)
            want = {0: (20190, 10), 1: (20432, 1), 2: (21001, 1)}
            for pos, (iid, num) in want.items():
                got = pl.bag.get(pos)
                if not got or got[0] != iid or got[1] != num:
                    fails.append("B 槽%d 应为 %d×%d，实际 %r"
                                 % (pos, iid, num, got))
            if len(pl.bag) != 3:
                fails.append("B 背包件数 %d（应 3）" % len(pl.bag))
        d = json.load(open(tmp, encoding="utf-8"))
        if len(d.get("bag") or {}) != 3:
            fails.append("B 落盘背包只有 %d 件" % len(d.get("bag") or {}))
    finally:
        sv.state_path = sv_old
        os.unlink(tmp)

    # ---- C：剧情神装的武器必须在槽 8 ----
    for cls in (1, 3):
        pl = new_player()
        pl.cls = cls
        with ctx(pl):
            got, base = sv.equip_story_set(pl)
        keys = set(pl.equip)
        if 7 in keys:
            fails.append("C 职业%d 武器落在槽 7（%r）" % (cls, pl.equip))
        if keys != {1, 2, 3, 4, 5, 6, 8}:
            fails.append("C 职业%d 装备槽 %s（应 1~6 + 8）" % (cls, sorted(keys)))
        if pl.equip.get(8) != base + 6:
            fails.append("C 职业%d 槽8 是 %r（应 %d）"
                         % (cls, pl.equip.get(8), base + 6))
        if got != 7:
            fails.append("C 职业%d 只穿了 %d 件" % (cls, got))
        # 30 包必须和真服 15:39:25 那串原包逐字节一致（82B）：
        # 6 块防具 + 首饰 4 空槽 + 武器 cd2b6a**07**0101 + 4B + 翅膀 20B，
        # 没坐骑就**不补**那 6B。多 6B 时客户端整包丢弃，面板 11 格全空。
        if cls == 1:
            with ctx(pl):
                b30 = sv.build_30() or b""
            real = ("cd2b64010101cd2b65020101cd2b66030101cd2b67040101"
                    "cd2b68050101cd2b69060101"
                    + "00" * 16
                    + "cd2b6a070101"
                    + "00" * 24)
            if len(b30) != 82:
                fails.append("C 30 包长 %dB（应 82）%s" % (len(b30), b30.hex()))
            elif b30.hex() != real:
                fails.append("C 30 包与真服原包不一致：\n      %s\n      %s"
                             % (b30.hex(), real))

    print("建角防覆盖 / 新人三件套 / 神装槽位：%s"
          % ("全部通过" if not fails else "FAIL"))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
