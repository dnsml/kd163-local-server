# -*- coding: utf-8 -*-
"""建角协议回归：性别映射 + 28 包昵称动态化（2026-11-24 12:55 实测结论）

实测事实：
  - 真服 12:38 选「男」→ cmd=20 末字节 01
  - 本地客户端 12:55 选「女」→ cmd=20 末字节 **00**（不是 02）
  - 建角后客户端名牌仍显示「萌新」→ 因为 cmd=28 模板里账号/昵称是写死的，
    28 才是客户端显示角色名的地方。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import local_server3 as L  # noqa: E402
import player as P  # noqa: E402

ok = fail = 0


def check(tag, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK] %s %s" % (tag, extra))
    else:
        fail += 1
        print("  [FAIL] %s %s" % (tag, extra))


def sfield(s):
    b = s.encode("utf-8")
    return bytes([0xA0 | len(b)]) + b


TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"


def req(acct, name, sex_byte):
    return (b"\x01\x01\x02" + sfield(acct) + b"\xda\x00\x2b" + TOKEN
            + sfield(name) + bytes([sex_byte]))


print("[1] 性别编码：01=男 / 00=女")
for sex_byte, want in ((1, 1), (0, 2), (2, 2)):
    n, s = L.parse_create_role(req("900000005", "呼延若梦", sex_byte))
    check("末字节 %02x -> sex=%d" % (sex_byte, want), s == want, "实际 %s 昵称 %r" % (s, n))

print("\n[2] 昵称只认最后一个 sfield（不能把账号/TOKEN 当昵称）")
n, s = L.parse_create_role(req("900000005", "呼延若梦", 0))
check("昵称", n == "呼延若梦", repr(n))
n2, _ = L.parse_create_role(req("abcdefgh", "LingEr", 1))
check("字母账号 + 英文昵称", n2 == "LingEr", repr(n2))

print("\n[3] cmd=28 昵称/账号动态化")
p = P.load(P.Player())
p.acct = "1784366"
p.name = "萌新"
p.level = 10
p.cls = 6
b_main = L.build_28()
# 注意：build_28 会 tokenize -> render 重编码，ce+4B 会被压成 cd+2B，
# 所以包长比模板短 2 字节（71 vs 73）是正常的，客户端照样接受。
check("主号 28 长度合理（69~75B）", 69 <= len(b_main) <= 75, "%dB" % len(b_main))
check("主号 28 头部仍是 0c + 账号 + 昵称",
      b_main[:16] == b"\x0c" + sfield("1784366") + sfield("萌新"))
check("主号昵称在包里", "萌新".encode("utf-8") in b_main)
check("主号账号在包里", b"1784366" in b_main)

p.acct = "900000005"
p.name = "呼延若梦"
p.level = 1
p.cls = 6
b_new = L.build_28()
check("新号 28 带自己的名字", "呼延若梦".encode("utf-8") in b_new)
check("新号 28 不再残留「萌新」", "萌新".encode("utf-8") not in b_new)
check("新号 28 带自己的账号", b"900000005" in b_new)
_shift = (len(sfield("900000005")) - 8) + (len(sfield("呼延若梦")) - 7)
check("新号 28 等级=1", b_new[18 + _shift] == 1, "off=%d 值=%d" % (18 + _shift, b_new[18 + _shift]))
check("新号 28 职业=6", b_new[16 + _shift] == 6, "值=%d" % b_new[16 + _shift])

print("\n[4] 17 包同样不残留「萌新」")
b17 = L.build_17()
check("17 带新昵称", "呼延若梦".encode("utf-8") in b17)
check("17 无「萌新」", "萌新".encode("utf-8") not in b17)

print("\n通过 %d 项 / 失败 %d 项" % (ok, fail))
sys.exit(0 if fail == 0 else 1)
