# -*- coding: utf-8 -*-
"""离线统计：一次登录服务端要下发多少字节（不连网、不改存档）。

用法： python work/probe_login_bytes.py [账号]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = [sys.argv[0]]

import local_server3 as sv   # noqa: E402

ACCT = sys.argv[1] if len(sys.argv) > 1 else None

pl = sv.cur()
if ACCT:
    pl.acct = ACCT
sv.load_state(pl)

items = []
for name in ("build_17", "build_28", "build_29", "build_30",
             "build_818", "build_178", "build_88"):
    fn = getattr(sv, name, None)
    if fn is None:
        continue
    try:
        b = fn()
    except Exception as e:                      # noqa: BLE001
        print("  %-10s 调用失败：%s" % (name, e))
        continue
    if isinstance(b, (list, tuple)):
        n = sum(len(x) for x in b)
        items.append((name, n, len(b)))
    else:
        items.append((name, len(b), 1))

print("账号=%s 角色=%s Lv%d" % (pl.acct, getattr(pl, "name", "?"), pl.level))
tot = 0
for name, n, cnt in items:
    tot += n
    print("  %-10s %4d B  (%d 条)" % (name, n, cnt))
print("合计（不含 113/117 场景包）：%d B ≈ %.1f KB" % (tot, tot / 1024.0))
