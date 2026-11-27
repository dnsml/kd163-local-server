#!/usr/bin/env python
"""
KD163 货币读写工具 —— 直接对 cmd=28（S->C 玩家属性）做定点修改。

★ 唯一权威的货币位置（实测定案，见文档第 19 节）

    idx18  绑定铜钱      idx19  绑定元宝
    idx20  铜钱          idx21  元宝

  证据：
    - 15:38 用绑定铜钱卡 x1  → idx18  2400 -> 12400   (+10000)
    - 15:38 用绑定铜钱卡 x9  → idx18  12400 -> 102400 (+90000)
    - 00:01 用 10绑定元宝 x2 → idx19  4030  -> 4050    (+20)
    - 00:04 充值福利领 880   → idx21  869   -> 1749    (+880)
    - 用户口述当前值 元宝1749 / 绑定元宝4050 / 铜钱0，与 idx21/idx19/idx20 完全一致

  注意 idx19 是"第几个值"，不是字节偏移。uid、名字长度一变，字节偏移就跟着变，
  所以本工具一律先解析成值列表再按下标改，最后整包重编码。

用法
    python grant.py                     # 显示当前四项货币（从 relay.log 取最新 28 包）
    python grant.py set 元宝=100000     # 生成改好的 28 包（只打印 hex）
    python grant.py set 元宝=100000 铜钱=50000 --frame   # 连帧头一起输出，可直接 sendall
    python grant.py set 元宝=100000 --out pkt.bin        # 写二进制帧到文件
    python grant.py fields              # 打印 28 包全部 41 个字段及当前值

集成到自建服
    from grant import build_player_state, frame
    sock.sendall(frame(build_player_state({"元宝": 100000})))
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "relay.log"

CMD_PLAYER = 28

# 值下标 -> 名称
CURRENCY_SLOT = {
    18: "绑定铜钱",
    19: "绑定元宝",
    20: "铜钱",
    21: "元宝",
}
SLOT_OF = {v: k for k, v in CURRENCY_SLOT.items()}

# 全部 41 个字段的语义（2026-11-26 定稿，见 docs/reference/字段语义表_17_28_30.md）
# ★ 以前这张表整体错位一格（写成 19=绑铜），照着它改包会改到 VIP 上。
FIELD_HINT = {
    0: "头标记", 1: "账号(字符串)", 2: "角色名(字符串)",
    3: "性别", 4: "职业", 5: "等级",
    6: "气血上限", 7: "法力上限", 8: "本级经验", 9: "场景ID",
    10: "坐标x", 11: "坐标y", 12: "阅历",
    13: "?", 14: "?", 15: "?", 16: "?", 17: "?",
    18: "绑定铜钱", 19: "绑定元宝", 20: "铜钱", 21: "元宝",
    22: "VIP等级", 23: "VIP剩余(0.1天)", 24: "?", 25: "?", 26: "?",
    27: "?", 28: "?", 29: "?", 30: "?",
    31: "战力", 32: "?", 33: "?", 34: "?", 35: "?",
    36: "?", 37: "?", 38: "?", 39: "?", 40: "累计经验",
}

_REC = re.compile(
    r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)"
)


# ---------------------------------------------------------------- 变长整数
def dec_val(b: bytes, i: int) -> tuple[int, int]:
    x = b[i]
    if x == 0xCC:                       # 0xCC + 1 字节
        return b[i + 1], i + 2
    if x == 0xCD:                       # 0xCD + 2 字节
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if x == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    raise ValueError("@%d 不是数值前缀: %02x" % (i, x))


def enc_val(v: int, prefix: int | None = None) -> bytes:
    """0xCC + 1 字节、0xCD + 2 字节。原包用哪个就回哪个，保证逐字节一致。"""
    if prefix == 0xCC and v <= 0xFF:
        return bytes([0xCC, v])
    if v <= 0xFF and prefix is None:
        return bytes([v])
    if v <= 0xFFFF:
        return bytes([0xCD]) + struct.pack(">H", v)
    if v <= 0xFFFFFFFF:
        return b"\xCE" + struct.pack(">I", v)
    raise ValueError("值过大: %d" % v)


# ---------------------------------------------------------------- 解析成 token
def tokenize(payload: bytes) -> list[dict]:
    """把 28 包拆成 [{kind,value,start,end}]，字符串也算一个值（占 1 个下标）。"""
    out, i = [], 0
    n = len(payload)
    while i < n:
        x = payload[i]
        if x in (0xCC, 0xCD, 0xCE):
            v, j = dec_val(payload, i)
            out.append({"kind": "int", "value": v, "start": i, "end": j, "prefix": x})
            i = j
        elif 0xA0 <= x <= 0xCB:            # 0xA0|len 短字符串
            ln = x & 0x0F
            out.append({"kind": "str", "value": payload[i + 1:i + 1 + ln],
                        "start": i, "end": i + 1 + ln})
            i += 1 + ln
        else:                               # 裸单字节 = 小整数
            out.append({"kind": "int", "value": x, "start": i, "end": i + 1})
            i += 1
    return out


def render(tokens: list[dict]) -> bytes:
    buf = bytearray()
    for t in tokens:
        if t["kind"] == "str":
            s = t["value"]
            buf.append(0xA0 | len(s))
            buf += s
        else:
            buf += enc_val(t["value"], t.get("prefix"))
    return bytes(buf)


# ---------------------------------------------------------------- 取模板
def latest_player_state(path: Path | None = None) -> tuple[str, bytes]:
    """从 relay.log 倒序找到最后一条完整的 cmd=28 S->C，返回 (时间, payload)。"""
    path = path or LOG
    if not path.exists():
        raise SystemExit("找不到日志: %s" % path)
    hit = None
    for line in path.open(encoding="utf-8", errors="replace"):
        m = _REC.search(line)
        if not m:
            continue
        t, d, c, _seq, ln, hx = m.groups()
        if d == "S->C" and int(c) == CMD_PLAYER and hx and int(ln) == len(hx) // 2:
            hit = (t, bytes.fromhex(hx))
    if hit is None:
        raise SystemExit("日志里没有完整的 cmd=28 包")
    return hit


def currencies(payload: bytes) -> dict[str, int]:
    tk = tokenize(payload)
    return {name: tk[idx]["value"] for idx, name in CURRENCY_SLOT.items()
            if idx < len(tk) and tk[idx]["kind"] == "int"}


# ---------------------------------------------------------------- 改包
def build_player_state(changes: dict[str, int], template: bytes | None = None) -> bytes:
    """在最新 28 包基础上改货币，返回新的 payload（未加帧头）。"""
    if template is None:
        _t, template = latest_player_state()
    tk = tokenize(template)
    if len(tk) != 41:
        raise SystemExit("模板字段数异常: %d（应为 41）" % len(tk))
    for name, val in changes.items():
        if name not in SLOT_OF:
            raise SystemExit("未知货币: %s（可选 %s）" % (name, "/".join(SLOT_OF)))
        idx = SLOT_OF[name]
        if tk[idx]["kind"] != "int":
            raise SystemExit("idx%d 不是数值字段" % idx)
        tk[idx]["value"] = int(val)
    return render(tk)


def frame(payload: bytes, seq: int = 0) -> bytes:
    """服务端 -> 客户端的完整帧：[u16 总长][u16 cmd][u32 seq][payload]"""
    return struct.pack(">HHI", 6 + len(payload), CMD_PLAYER, seq) + payload


# ---------------------------------------------------------------- CLI
def _parse_assigns(args: list[str]) -> dict[str, int]:
    out = {}
    for a in args:
        if "=" not in a:
            raise SystemExit("参数格式应为 名称=数值：%s" % a)
        k, v = a.split("=", 1)
        out[k.strip()] = int(v.replace(",", ""))
    return out


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "show"

    if cmd in ("show", "fields"):
        t, payload = latest_player_state()
        tk = tokenize(payload)
        print("模板包: %s  len=%d  字段数=%d\n" % (t, len(payload), len(tk)))
        if cmd == "fields":
            for i, x in enumerate(tk):
                v = x["value"]
                if isinstance(v, bytes):
                    v = v.decode("utf-8", "replace")
                print("  idx%-2d %-12s = %s" % (i, FIELD_HINT.get(i, "?"), v))
        else:
            for idx, name in CURRENCY_SLOT.items():
                print("  %-6s idx%-2d = %d" % (name, idx, tk[idx]["value"]))
        return 0

    if cmd == "set":
        rest = [a for a in argv[2:] if not a.startswith("--")]
        # --out 后面跟的是路径，不是赋值
        if "--out" in argv:
            out_i = argv.index("--out")
            if out_i + 1 < len(argv):
                rest = [a for a in rest if a != argv[out_i + 1]]
        changes = _parse_assigns(rest)
        t, payload = latest_player_state()
        new = build_player_state(changes, payload)
        old = currencies(payload)
        print("模板 %s" % t)
        for k, v in changes.items():
            print("  %-6s %d -> %d" % (k, old.get(k, -1), v))
        print("\nlen %d -> %d" % (len(payload), len(new)))
        print("payload:", new.hex())
        if "--frame" in argv or "--out" in argv:
            fr = frame(new)
            print("frame  :", fr.hex())
            if "--out" in argv:
                p = Path(argv[argv.index("--out") + 1])
                p.write_bytes(fr)
                print("已写入", p)
        return 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
