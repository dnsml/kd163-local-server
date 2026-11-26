# -*- coding: utf-8 -*-
"""把老的 player_state*.json 全部导入 SQLite（server_state.db）。

用法：
    python work/migrate_state_to_sqlite.py --dry     # 只看会导哪些、字段对不对
    python work/migrate_state_to_sqlite.py           # 真导（库里已有的默认跳过）
    python work/migrate_state_to_sqlite.py --force   # 库里有也覆盖（JSON 为准）

规则：
  * player_state.json            -> 账号 1784366（主号）
  * player_state_<账号>.json     -> 账号 <账号>
  * backups/ 里的快照、*.tmp 一律不导
  * 默认「库里已经有这个账号且 rev>0」就跳过，避免把玩家最新进度盖回旧文件
  * 导入前会核对：等级 / 货币 / 背包件数 / 装备槽 与原 JSON 完全一致，不一致报错
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import state_store as S

PRIMARY = "1784366"
SKIP = {"player_state_backup", "player_state.tmp"}


def acct_of(path):
    b = os.path.basename(path)
    if b == "player_state.json":
        return PRIMARY
    if not b.startswith("player_state_") or not b.endswith(".json"):
        return None
    a = b[len("player_state_"):-len(".json")]
    return a or None


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        return None, str(e)
    if not isinstance(d, dict):
        return None, "不是 JSON 对象"
    return d, None


def sig(d):
    return (d.get("level"), d.get("exp"), d.get("class"),
            d.get("coin"), d.get("bind_yuanbao"), d.get("tongqian"), d.get("yuanbao"),
            len(d.get("bag") or {}), sorted((d.get("equip") or {}).keys()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只检查，不写库")
    ap.add_argument("--force", action="store_true", help="库里已有也覆盖")
    ap.add_argument("--dir", default=HERE, help="存档目录（默认项目根）")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "player_state*.json")))
    files = [f for f in files
             if os.path.basename(f) not in SKIP
             and not os.path.basename(f).endswith(".tmp")]
    if not files:
        print("没有找到 player_state*.json")
        return 1

    S.init()
    print("库：%s（现有 %d 个角色）" % (S.DB_FILE, S.count()))
    ok = skip = bad = 0
    for f in files:
        acct = acct_of(f)
        if not acct:
            print("  跳过（文件名不认识）: %s" % os.path.basename(f))
            continue
        d, err = load_json(f)
        if err:
            print("  ！读不了 %s: %s" % (os.path.basename(f), err))
            bad += 1
            continue
        exists = S.exists(acct)
        if exists and not args.force:
            cur, rev = S.load(acct)
            same = sig(cur) == sig(d)
            print("  跳过（库里已有）%-12s Lv%-3s %s  JSON Lv%-3s 一致=%s"
                  % (acct, cur.get("level"), cur.get("name") or "?", d.get("level"), same))
            skip += 1
            continue
        if args.dry:
            print("  将导入 %-12s Lv%-3s %-8s 背包%2d 件 绑铜%-8s 元宝%s"
                  % (acct, d.get("level") or 0, d.get("name") or "?",
                     len(d.get("bag") or {}), d.get("coin") or 0, d.get("yuanbao") or 0))
            ok += 1
            continue
        # 导入前先给库里的旧行留快照（覆盖场景）
        if exists:
            old, _ = S.load(acct)
            if old:
                S.snapshot(acct, old, force=True)
        rev = S.save(acct, d, 0)
        back, rev2 = S.load(acct)
        if sig(back) != sig(d):
            print("  ！校验失败 %s：写入后字段不一致" % acct)
            bad += 1
            continue
        print("  ✔ 导入 %-12s Lv%-3s %-8s 背包%2d 件 rev=%d"
              % (acct, d.get("level") or 0, d.get("name") or "?",
                 len(d.get("bag") or {}), rev2))
        ok += 1

    print("\n结果：导入/待导入 %d，跳过 %d，失败 %d，库内共 %d 个角色"
          % (ok, skip, bad, S.count()))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
