# -*- coding: utf-8 -*-
"""经验条回归：17/28 的「经验 ÷ 需求」必须一致，且需求不能被模板值盖住。

背景（2026-11-27 用户截图 766.49%）：
  客户端经验条 = 经验 ÷ 需求，两个数都由服务端下发：
    28 idx8 / idx40（token，可重编码）
    17 off22 / off72（定长 cd+u16）
  以前只写经验（还被 u16 截成 65535），需求永远留模板的 8550，
  于是 Lv39 号显示 65535/8550 = 766.49%。

真服实测需求：Lv1 760、Lv10 8550、Lv35 747067、Lv36 803144、Lv39 1290042。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.object(sys, "argv", [sys.argv[0]]):
    import local_server3 as sv
import grant
import player


def pct_of_17(b):
    exp = int.from_bytes(b[23:25], "big")
    need = int.from_bytes(b[73:75], "big")
    return exp, need


def pct_of_28(b):
    tk = grant.tokenize(b)
    return tk[8]["value"], tk[40]["value"]


class ExpBarTest(unittest.TestCase):
    def setUp(self):
        p = player.cur()
        p.acct = "1784366"
        p.name = "萌新"
        p.level = 39
        p.exp = 89402
        p.coin_bound[0] = 100
        p.bind_yuanbao[0] = 10
        p.tongqian[0] = 1
        p.yuanbao[0] = 20

    def test_curve_matches_capture(self):
        """拟合曲线要贴着真服实测点（±15%）。"""
        for lv, real in ((1, 760), (10, 8550), (35, 747067), (36, 803144), (39, 1290042)):
            got = sv.exp_need_of(lv)
            self.assertLess(abs(got - real) / real, 0.15,
                            "Lv%d 需求 %d 偏离真服 %d" % (lv, got, real))

    def test_bar_never_explodes(self):
        """Lv39 / 89402 经验必须是百分位以内的数字，不是 766%。"""
        e17, n17 = pct_of_17(sv.build_17())
        e28, n28 = pct_of_28(sv.build_28())
        self.assertLess(e17, n17, "17 包经验必须小于需求")
        self.assertLess(e28, n28, "28 包经验必须小于需求")
        p17 = e17 / n17 * 100
        p28 = e28 / n28 * 100
        self.assertGreater(p28, 1.0)
        self.assertLess(p28, 100.0)
        self.assertAlmostEqual(p17, p28, delta=0.5,
                               msg="两个包的经验条百分比必须一致（否则客户端来回跳）")

    def test_level1_bar(self):
        """新号 Lv1 exp=0：需求 760，条是空的。"""
        p = player.cur()
        p.level = 1
        p.exp = 0
        e28, n28 = pct_of_28(sv.build_28())
        self.assertEqual(n28, 760)
        self.assertEqual(e28, 0)

    def test_exp_over_need_does_not_overflow(self):
        """经验超过需求时只做取模显示，不能写出 >100% 的条。"""
        p = player.cur()
        p.level = 20
        p.exp = sv.exp_need_of(20) * 2 + 123
        e28, n28 = pct_of_28(sv.build_28())
        self.assertEqual(e28, 123)
        self.assertEqual(n28, sv.exp_need_of(20))


if __name__ == "__main__":
    unittest.main(verbosity=2)
