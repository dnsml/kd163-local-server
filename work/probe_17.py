# -*- coding: utf-8 -*-
"""只读探针：反解 build_17 / build_88，确认货币与称号已动态化。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = [sys.argv[0]]

import local_server3 as sv          # noqa: E402
import grant                        # noqa: E402

pl = sv.cur()
sv.load_state(pl)
print("账号=%s 角色=%s Lv%d 绑铜=%d 绑元=%d 铜钱=%d 元宝=%d"
      % (pl.acct, pl.name, pl.level, pl.coin_bound[0], pl.bind_yuanbao[0],
         pl.tongqian[0], pl.yuanbao[0]))
b17 = sv.build_17()
tk = grant.tokenize(b17)
print("17 len=%d 字段=%d" % (len(b17), len(tk)))
for i in (16, 17, 18, 19, 20, 21, 22, 24):
    print("  idx%-2d = %s" % (i, tk[i].get("value")))
print("88 len=%d hex=%s" % (len(sv.build_88()), sv.build_88().hex()))
