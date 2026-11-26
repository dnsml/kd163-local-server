# -*- coding: utf-8 -*-
"""登录快照回归：17 / 88 里不能再出现抓包那个号的旧数据。

背景（2026-11-26 14:2x 用户反馈）：
  每次上线，做完第一个任务或打一只小怪，屏幕就弹
    「绑定铜币-2202471 / 元宝-1749 / 绑定元宝-4050」，
  同时头顶的「神界新丁」称号消失。

根因不在 28，在 **17**：
  TPL17_LIVE 是真服抓包那一个号的快照，头部 idx18~21 写死
    绑铜 2,304,000 / 绑元 4,050 / 铜钱 0 / 元宝 1,749，
  idx24 写死称号 1019（神界新丁）。
  build_17 以前只换装备/等级/经验，货币和称号原样带出去 →
  客户端登录先按 17 记账，28 再推真值，差额就成了负飘字；
  而 build_88 回的是另一个号的称号 2940（cd0b7c），
  所以第一次交任务收到 88，「神界新丁」就被换掉了。

本测试锁三件事：
  1. 17 的 idx18~21 必须等于存档货币，idx24 必须等于当前称号；
  2. 17 的包长必须和修复前一致（200B，定宽写入，不许缩短导致装备段前移）；
  3. 88 必须回 1019（cd03fb），Lv<5 回 19B 空表。
"""
import copy
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.argv = [sys.argv[0]]

import local_server3 as sv          # noqa: E402
import grant                        # noqa: E402

sv.BACKUP_DIR = tempfile.mkdtemp(prefix="kd17_")
pl = sv.cur()
pl.acct = "1784366"
pl.name = "御剑战甲"
pl.has_char = True
pl.authed = True
pl.loaded = True
pl.level = 39
pl.cls = 1
pl.sex = 1
pl.coin_bound[0] = 212932
pl.bind_yuanbao[0] = 10
pl.tongqian[0] = 0
pl.yuanbao[0] = 40
pl.vip_level[0] = 0

fails = []


def check(name, got, want):
    if got == want:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  got=%r want=%r" % (name, got, want))
        fails.append(name)


print("[17] 货币/称号必须来自存档")
_af = sv.sfield(pl.acct)
_nf = sv.sfield(pl.name)
# 17 的基准长度 = 真服 197B 原包 + 账号/昵称长度变化带来的位移
WANT_LEN = 197 + (len(_af) - 8) + (len(_nf) - 7)
b17 = sv.build_17(scene=102)
tk = grant.tokenize(b17)
check("包长 = 197B + 位移（定宽写入不变）", len(b17), WANT_LEN)
check("idx18 绑定铜钱", tk[18]["value"], 212932)
check("idx19 绑定元宝", tk[19]["value"], 10)
check("idx20 铜钱", tk[20]["value"], 0)
check("idx21 元宝", tk[21]["value"], 40)
check("idx24 没戴称号=0", tk[24]["value"], 0)

print("[17/88] 称号只能靠称号卡激活")
pl.title = 1019
b17 = sv.build_17(scene=102)
check("用过卡后 idx24=1019", grant.tokenize(b17)[24]["value"], 1019)
check("用过卡后 88=21B cd03fb", sv.build_88().hex(),
      "d08800070000000000000000000000000000cd03fb")
pl.title = 0

print("[17] 换个数值也要跟着变")
pl.coin_bound[0] = 7
pl.yuanbao[0] = 8888
b17 = sv.build_17(scene=102)
tk = grant.tokenize(b17)
check("idx18 跟随", tk[18]["value"], 7)
check("idx21 跟随", tk[21]["value"], 8888)
check("包长不变", len(b17), WANT_LEN)

print("[88] 没戴称号 = 19B 空表（与等级无关）")
pl.level = 39
check("Lv39 没戴 -> 19B", sv.build_88().hex(),
      "d0880007000000000000000000000000000000")
pl.level = 1
check("Lv1 没戴 -> 19B", sv.build_88().hex(),
      "d0880007000000000000000000000000000000")

print()
if fails:
    print("失败 %d 项：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("17 货币快照 / 17 称号 / 88 称号：全部通过")
