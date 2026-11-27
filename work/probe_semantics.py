# -*- coding: utf-8 -*-
"""17 / 28 / 30 字段语义探针：把模板与真服样本都按 token 下标 + 字节偏移打印。

用法：
    python work/probe_semantics.py            # 模板
    python work/probe_semantics.py real       # 真服最新样本（relay.log）

输出用于 docs/reference/字段语义表_17_28_30.md，
不连客户端、不改任何文件。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]
import local_server3 as sv          # noqa: E402
import grant                        # noqa: E402

REC = re.compile(
    r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)")


def dump(name, payload, limit=300):
    tk = grant.tokenize(payload)
    print("== %s  len=%d  tokens=%d ==" % (name, len(payload), len(tk)))
    shown = 0
    for i, t in enumerate(tk):
        if i >= limit:
            break
        v = t["value"]
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        print("  idx%-2d off=%-3d len=%-2d %-4s %s"
              % (i, t["start"], t["end"] - t["start"],
                 t["kind"], v))
        shown += 1
    if shown < len(tk):
        print("  ... 还有 %d 个 token" % (len(tk) - shown))
    print()


def latest(cmd, direction="S->C"):
    hit = None
    for line in (ROOT / "logs" / "relay.log").open(encoding="utf-8", errors="replace"):
        m = REC.search(line)
        if not m:
            continue
        t, d, c, _seq, ln, hx = m.groups()
        if d == direction and int(c) == cmd and hx and int(ln) == len(hx) // 2:
            hit = (t, bytes.fromhex(hx))
    return hit


def dump30(payload):
    """30 是块结构，不按 token 拆，按 [id3B][槽][01][01] / 0000 走。"""
    print("== cmd30 len=%d ==" % len(payload))
    i, blk = 0, 0
    while i + 4 <= len(payload):
        if payload[i] == 0xCD and i + 6 <= len(payload):
            iid = int.from_bytes(payload[i + 1:i + 3], "big")
            slot = payload[i + 3]
            print("  blk%-2d off=%-3d id=%-6d slot=%02x  %s"
                  % (blk, i, iid, slot, payload[i:i + 6].hex()))
            i += 6
        else:
            print("  blk%-2d off=%-3d 空/填充 %s" % (blk, i, payload[i:i + 4].hex()))
            i += 4
        blk += 1
    if i < len(payload):
        print("  尾部余 %d B: %s" % (len(payload) - i, payload[i:].hex()))
    print()


def main():
    mode = _ARGV[0] if _ARGV else "tpl"
    if mode == "real":
        for cmd in (17, 28, 30):
            hit = latest(cmd)
            if not hit:
                print("relay.log 没有 cmd=%d" % cmd)
                continue
            t, p = hit
            print("### 真服样本 cmd=%d @%s" % (cmd, t))
            if cmd == 30:
                dump30(p)
            else:
                dump("cmd%d@%s" % (cmd, t), p)
        return 0

    dump("TPL17_LIVE(模板，主号 shift=0)", sv.TPL17_LIVE)
    dump("PLAYER_28_TPL(模板)", sv.PLAYER_28_TPL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
