# -*- coding: utf-8 -*-
"""生成 docs/reference/任务经验与升级规则.md（一次性脚本，数据源 res_cfg/*.xml）。"""
import re
from collections import defaultdict

HERE = __import__("pathlib").Path(__file__).resolve().parents[1]


def items(path):
    s = (HERE / path).read_text(encoding="utf-8")
    return [dict(re.findall(r'(\w+)="([^"]*)"', it))
            for it in re.findall(r"<item ([^>]+)/>", s)]


ms = items("res_cfg/Mission.xml")
ms.sort(key=lambda d: int(d.get("id", 0)))

qs = items("res_cfg/Quest.xml")
for q in qs:
    ex = 0
    for part in (q.get("reward") or "").split(";"):
        if part.startswith("add_exp,"):
            try:
                ex += int(part.split(",")[1])
            except (IndexError, ValueError):
                pass
    q["exp"] = ex
qs = [q for q in qs if q["exp"] > 0]


def need(lv):
    return int(round(760 * (1.31 ** (lv - 1)))) if lv <= 10 else int(round(2.1 * (lv ** 3.6)))


L = []
A = L.append
A("# 任务经验与升级规则\n")
A("> 2026-11-27 整理。数据源：`res_cfg/Mission.xml`（86 条章节任务）、")
A("> `res_cfg/Quest.xml`（590 条，其中 586 条带 `add_exp`）、线上服 17/28 抓包。\n")

A("## 1. 升级规则：客户端没有「升级经验表」\n")
A("| 项 | 在哪 | 说明 |")
A("|---|---|---|")
A("| 每级属性成长 | `res_cfg/Levelup.xml` | `class=1..6` × `level=1..80`，只有 hp_max / attack_min / defense / agile 等，**没有经验需求** |")
A("| 升级需求 | 服务器下发 | `17` off72（cd+u16）、`28` idx40；客户端经验条就是 `经验 ÷ 需求` |")
A("| 当前经验 | 服务器下发 | `17` off22、`28` idx8 |")
A("| 升级判定 | 服务器 | 本地 `add_reward()`：`exp >= exp_need` 就升级并扣掉需求 |\n")
A("线上服实测（同一号 1784366 的抓包）：\n")
A("| 等级 | 需求 exp | 来源 |")
A("|---:|---:|---|")
for lv, nd, src in [(1, 760, "17 idx40"),
                    (10, 8550, "17 idx40（模板号）"),
                    (35, 747067, "17 idx40（Lv35 号 222B 包）"),
                    (36, 803144, "28 idx40"),
                    (39, 1290042, "28 idx40（本号 Lv39 现场）")]:
    A("| %d | %d | %s |" % (lv, nd, src))
A("")
A("本地拟合（`local_server3.exp_need_of`）：`lv<=10: 760×1.31^(lv-1)`，`lv>10: 2.1×lv^3.6`。")
A("对实测点的误差：Lv10 +1%、Lv35 +1.7%、Lv36 +4.7%、Lv39 −13%。")
A("`KD_EXP_CURVE=fast` 回退到旧的 `200×1.18^(lv-1)`（升级快十倍以上，仅调试用）。\n")
A("| 等级 | 本地需求 | 等级 | 本地需求 | 等级 | 本地需求 |")
A("|---:|---:|---:|---:|---:|---:|")
rows = [(lv, need(lv)) for lv in (1, 5, 10, 15, 20, 25, 30, 35, 39, 40, 45, 50, 55, 60, 70, 80)]
for i in range(0, len(rows), 3):
    cells = []
    for lv, nd in rows[i:i + 3]:
        cells += ["%d" % lv, "{:,}".format(nd)]
    while len(cells) < 6:
        cells.append("")
    A("| " + " | ".join(cells) + " |")

A("\n## 2. 章节任务经验（Mission.xml，86 条）\n")
total_m = sum(int(m.get("reward_exp") or 0) for m in ms)
A("小额引导奖励，**全部加起来只有 {:,} 经验**，指望它升级不现实。\n".format(total_m))
A("| 任务id | 名称 | 开放等级 | 经验 | 绑定铜钱 |")
A("|---|---|---:|---:|---:|")
for m in ms:
    A("| %s | %s | %s | %s | %s |" % (
        m.get("id"), m.get("name"), m.get("open_lv"),
        m.get("reward_exp") or 0, m.get("reward_coin_bound") or 0))

band = defaultdict(lambda: [0, 0])
for q in qs:
    b = (int(q.get("show_level") or 1) // 10) * 10
    band[b][0] += 1
    band[b][1] += q["exp"]

A("\n## 3. 支线/活动任务经验（Quest.xml，%d 条带经验）\n" % len(qs))
A('主要经验来源（`reward="add_exp,N"`）。按 `show_level` 分段：\n')
A("| 等级段 | 条数 | 经验合计 | 单条平均 |")
A("|---|---:|---:|---:|")
for b in sorted(band):
    n, t = band[b]
    A("| %d-%d | %d | {:,} | {:,} |".format(t, t // max(1, n)) % (b, b + 9, n)
      if False else "| %d-%d | %d | %s | %s |" % (b, b + 9, n, "{:,}".format(t), "{:,}".format(t // max(1, n))))

A("\n经验最高的 15 条：\n")
A("| 任务id | 名称 | 类型 | 开放等级 | 经验 |")
A("|---|---|---:|---:|---:|")
for q in sorted(qs, key=lambda x: -x["exp"])[:15]:
    A("| %s | %s | %s | %s | %s |" % (
        q.get("id"), q.get("name"), q.get("type"), q.get("show_level"), "{:,}".format(q["exp"])))

A("\n## 4. 与升级需求对照（Lv30~45 抽样）\n")
A("| 等级 | 本地需求 | 该段单条任务均值 | 约几条任务升一级 |")
A("|---:|---:|---:|---:|")
for lv in range(30, 46):
    n, t = band.get((lv // 10) * 10, [0, 0])
    avg = t // max(1, n)
    A("| %d | %s | %s | %.1f |" % (lv, "{:,}".format(need(lv)), "{:,}".format(avg), need(lv) / max(1, avg)))

A("\n## 5. 本地服经验来源\n")
A("| 来源 | 经验 | 代码 |")
A("|---|---|---|")
A("| 打怪（150 胜利） | 每场 10 起 | `battle.py` |")
A("| 交任务 580/210 | `Quest.xml` 的 `add_exp,N` | `add_reward()` |")
A("| 经验丹 20458 | `max(500, need//10) × 数量` | 物品使用分支 |")
A("| GM `经验 N` | 直接加 | GM 700 分支 |\n")
A("> 经验条写法：`28` 的 idx8/idx40 写真值；`17` 两槽是定长 u16，"
  "需求超过 65535 时按比例缩到 65535（百分比与 28 一致）。")

out = HERE / "docs/reference/任务经验与升级规则.md"
out.write_text("\n".join(L) + "\n", encoding="utf-8")
print("written", out, len(L), "blocks")
