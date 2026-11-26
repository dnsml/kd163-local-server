# -*- coding: utf-8 -*-
"""只读探针：用当前存档构造 28 包，反解 idx18~21，确认货币编码没有串位。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ARGS = sys.argv[1:]
sys.argv = [sys.argv[0]]

import local_server3 as sv          # noqa: E402
import grant                        # noqa: E402

pl = sv.cur()
sv.load_state(pl)
print("账号=%s 角色=%s Lv%d" % (pl.acct, pl.name, pl.level))
print("存档值 绑铜=%d 绑元=%d 铜钱=%d 元宝=%d"
      % (pl.coin_bound[0], pl.bind_yuanbao[0], pl.tongqian[0], pl.yuanbao[0]))
body = sv.build_28()
tk = grant.tokenize(body)
print("包长=%d 字段数=%d" % (len(body), len(tk)))
for i in range(16, 24):
    t = tk[i]
    print("  idx%-2d %-5s value=%s" % (i, t["kind"], t["value"]))
print("末尾 12B:", body[-12:].hex())
