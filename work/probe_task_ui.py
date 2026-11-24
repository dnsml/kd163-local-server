#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交任务后「金币变 / 背包空 / 称号消失」的服务端数据自检。

不连服务器，直接调 local_server3 的构造函数，用 14:28:44 那份真实存档
（百里海熠 Lv7 exp187 绑铜1202 背包 20142x1）复算 28/17/88/178，
把货币 token 逐个打印出来，看是不是服务端本身发错了。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import grant
import bag_proto
import player as player_mod
import local_server3 as L


def build_state():
    p = player_mod.Player()
    p.acct = "17843661"
    p.name = "百里海熠"
    p.sex = 1
    p.cls = 6
    p.level = 7
    p.exp = 187
    p.exp_need = 634
    p.coin_bound = [1202]
    p.bind_yuanbao = [0]
    p.tongqian = [0]
    p.yuanbao = [0]
    p.bag = {0: [20142, 1]}
    p.pets = []
    p.has_char = True
    p.loaded = True
    player_mod.load(p)
    return p


def main():
    p = build_state()
    print("账号=%s 名字=%s(%dB) Lv%d 绑铜=%d 背包=%s"
          % (p.acct, p.name, len(p.name.encode()), p.level,
             p.coin_bound[0], p.bag))

    b28 = L.build_28()
    print("\n[28] len=%d hex=%s" % (len(b28), b28.hex()))
    tk = grant.tokenize(b28)
    print("[28] token 数=%d" % len(tk))
    for i in (0, 1, 2, 15, 16, 17, 18, 19, 20, 21, 22, 23):
        if i < len(tk):
            t = tk[i]
            v = t["value"]
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            print("   idx%-2d %-5s %s" % (i, t["kind"], v))
    print("   -> 货币应该 idx18=绑铜(%d) idx19=绑元(%d) idx20=铜钱(%d) idx21=元宝(%d)"
          % (p.coin_bound[0], p.bind_yuanbao[0], p.tongqian[0], p.yuanbao[0]))

    b17 = L.build_17()
    print("\n[17] len=%d 头部=%s" % (len(b17), b17[:24].hex()))
    # 昵称在 17 里的位置：找 UTF-8
    nb = p.name.encode()
    print("[17] 昵称出现位置=%s" % (b17.find(nb),))
    if b17.find(nb) >= 0:
        o = b17.find(nb) - 1
        print("[17] 昵称长度前缀=%02x（应为 a%x）" % (b17[o], len(nb)))

    b88 = L.build_88()
    print("\n[88] len=%d hex=%s" % (len(b88), b88.hex()))

    b178 = bag_proto.build_178(L.bag_view())
    print("\n[178] len=%d hex=%s" % (len(b178), b178.hex()))
    print("[178] 解析回读=%s" % (bag_proto.parse_178(b178) if hasattr(bag_proto, "parse_178") else "无"))

    b30 = L.build_30()
    print("\n[30] len=%d" % (len(b30) if b30 else 0))


if __name__ == "__main__":
    main()
