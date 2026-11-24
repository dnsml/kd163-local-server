#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd163 充值福利：每日 88 元额度（880 元宝）查询 / 领取。

机制（2026-11-23 抓包定案）：
    领取走 HTTP 80  POST /api/recharge/daily
    到账走 TCP 9000  服务端随后推 29/28/30
    鉴权只认 uid + token，token 在登录包 C->S cmd=17 里明文提交：
        01 cc9702 a7"1784366" da 00 2b <43 字节 token>
    a7 = 0xA0|7 短串编码；da 00 2b = 标记 + u16 长度 43

用法：
    python recharge_claim.py            # 只查状态（安全）
    python recharge_claim.py --claim    # 真领一次
    python recharge_claim.py --log other.log --host 124.223.56.189
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.path.join(HERE, "logs", "relay.log")
DEFAULT_HOST = "124.223.56.189"

REC = re.compile(
    r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)"
)


def find_credentials(log_path):
    """从 relay.log 里倒序找最后一个带 token 的登录包 cmd=17。"""
    if not os.path.exists(log_path):
        sys.exit("找不到日志：%s" % log_path)

    last = None
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = REC.search(line)
            if not m:
                continue
            t, d, c, seq, ln, hexs = m.groups()
            if d != "C->S" or int(c) != 17 or not hexs:
                continue
            raw = bytes.fromhex(hexs)

            mu = re.search(rb"\xa7(\d{5,12})", raw)
            pos = raw.find(b"\xda")
            if not mu or pos < 0 or pos + 3 > len(raw):
                continue
            n = int.from_bytes(raw[pos + 1:pos + 3], "big")
            tok_b = raw[pos + 3:pos + 3 + n]
            if n < 16 or len(tok_b) != n:
                continue
            try:
                tok = tok_b.decode("ascii")
            except UnicodeDecodeError:
                continue

            last = {
                "time": t,
                "uid": mu.group(1).decode(),
                "token": tok,
            }
    return last


def call(host, path, uid, token, timeout=10):
    url = "http://%s/api/recharge/%s" % (host, path)
    body = json.dumps({"uid": uid, "token": token}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "kd163-recharge/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"raw": txt}


def show(tag, status, data):
    print("== %s  [HTTP %s]" % (tag, status))
    if not isinstance(data, dict):
        print("  ", data)
        return
    if not data.get("ok", True):
        print("   失败:", data.get("error") or data)
        return

    gold = data.get("gold")
    per = data.get("gold_per_yuan", 10)
    amt = data.get("daily_amount", data.get("daily_amount_yuan"))
    print("   角色      %s (id=%s)" % (data.get("role_name", "?"), data.get("role_id", "?")))
    print("   元宝      %s" % gold)
    print("   每日额度  %s 元 = %s 元宝" % (amt, (amt or 0) * per))
    print("   今日已领  %s   (day=%s)" % (data.get("daily_claimed"), data.get("day")))
    print("   下次重置  %s" % data.get("next_reset_at"))
    print("   累计额度  %s 元 / %s 元宝" % (data.get("total_yuan"), data.get("total_gold")))
    if data.get("clock_rollback"):
        print("   !! 服务端检测到时钟回拨")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", action="store_true", help="真正领取（默认只查询）")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--host", default=DEFAULT_HOST)
    args = ap.parse_args()

    cred = find_credentials(args.log)
    if not cred:
        sys.exit("日志里没找到登录包 cmd=17（没带 token），先启动游戏登录一次再跑。")

    print("凭据来源: %s 的 cmd=17" % cred["time"])
    print("uid=%s  token=%s…（%d 位）\n" % (cred["uid"], cred["token"][:12], len(cred["token"])))

    st, data = call(args.host, "status", cred["uid"], cred["token"])
    show("status", st, data)
    if st != 200 or not data.get("ok"):
        return

    if not args.claim:
        print("\n（只查询。要领取加 --claim）")
        return
    if data.get("daily_claimed"):
        print("\n今日已领取，服务端不会再发。")
        return

    print()
    st2, data2 = call(args.host, "daily", cred["uid"], cred["token"])
    show("daily", st2, data2)
    if st2 == 200 and data2.get("ok"):
        print("\n领取成功。元宝到账会在几秒内由游戏服推 29/28/30，"
              "游戏里没变化就重进一次背包或切场景。")


if __name__ == "__main__":
    main()
