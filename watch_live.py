#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时抓包器：跟读 logs/relay.log 的新增行，把「C->S 请求」和「S->C 应答」当场配对。

用法：
    python watch_live.py            # 抓 120 秒
    python watch_live.py 60         # 抓 60 秒
    python watch_live.py 0          # 一直抓，Ctrl+C 停

输出同时写到 logs/live_capture.txt，方便事后回看。
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "relay.log")
OUT = os.path.join(HERE, "logs", "live_capture.txt")

LINE = re.compile(
    r"\[(\d\d:\d\d:\d\d)\]\s+(C->S|S->C)\s+cmd=(\d+)\s+seq=(\d+)\s+len=(\d+)\s+hex=([0-9a-f]*)"
)

# 已知指令的语义（只写能确认的，其余留空让原始字节自己说话）
KNOWN = {
    4: "心跳",
    28: "玩家属性(货币)",
    29: "属性刷新(28前必带)",
    30: "装备栏",
    115: "移动/朝向",
    116: "? 线上服未实现",
    117: "?",
    118: "?",
    178: "背包全量推送",
    181: "背包打开/整理",
    182: "使用/穿戴物品",
    1154: "?",
    1282: "池子信息",
    1284: "池子剩余",
    2134: "? 线上服未实现",
}

try:
    import grant  # 货币解码，cmd=28
except Exception:
    grant = None

try:
    import bag_proto  # 复用背包解析，可选

    _items = bag_proto.items()
except Exception:
    bag_proto = None
    _items = {}


def fmt_text(raw: bytes) -> str:
    """cmd=1 的提示文本：[type 1B][flags 1B][utf8...]"""
    if len(raw) <= 2:
        return ""
    return raw[2:].decode("utf-8", "ignore")


def fmt_payload(cmd: int, raw: bytes) -> str:
    extra = []
    if cmd == 182 and len(raw) >= 2:
        pos, num = raw[0], raw[1]
        extra.append("格 %02x 数量 %d%s" % (pos, num, "（全部）" if num == 0 else ""))
        if _items and pos < 64:
            pass  # 需要背包状态才有物品名，这里只给格子
    elif cmd in (178, 181) and bag_proto and raw[:1] == b"\x40":
        try:
            entries = bag_proto.parse_bag(raw)
            extra.append("背包 %d 件" % len(entries))
        except Exception:
            extra.append("背包解析失败")
    elif cmd == 1:
        extra.append('"%s"' % fmt_text(raw))
    return ("  " + " ".join(extra)) if extra else ""


class Pending:
    def __init__(self, keep: float = 3.0):
        self.keep = keep
        self.items = {}

    def add(self, seq, t, cmd, raw):
        self.items[seq] = (t, cmd, raw)

    def pop(self, seq):
        return self.items.pop(seq, None)

    def sweep(self, now):
        for seq in [s for s, (t, _, _) in self.items.items() if now - t > self.keep]:
            self.items.pop(seq, None)


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    start = os.path.getsize(LOG)
    out = open(OUT, "w", encoding="utf-8")
    pend = Pending()
    last_cur = {}

    def emit(s):
        print(s, flush=True)
        out.write(s + "\n")
        out.flush()

    emit("# 开始监听 %s，偏移 %d，抓 %s 秒" % (LOG, start, duration or "无限"))
    emit("# 现在去游戏里点一次目标操作，这里会显示客户端发什么、服务端回什么")
    emit("")

    if grant:                                  # 用历史最后一包做基线，首次推送就能算出差值
        try:
            _t, _p = grant.latest_player_state()
            last_cur.update(grant.currencies(_p))
            emit("# 货币基线(取 %s)：%s" % (_t, "  ".join("%s=%d" % kv for kv in last_cur.items())))
            emit("")
        except Exception:
            pass

    t0 = time.time()
    f = open(LOG, "r", encoding="utf-8", errors="replace")
    f.seek(start)
    while True:
        if duration and time.time() - t0 > duration:
            break
        line = f.readline()
        if not line:
            time.sleep(0.2)
            pend.sweep(time.time())
            continue
        m = LINE.search(line)
        if not m:
            continue
        hhmmss, direction, cmd, seq, ln, hexs = m.groups()
        cmd, seq, ln = int(cmd), int(seq), int(ln)
        raw = bytes.fromhex(hexs) if hexs else b""
        tag = KNOWN.get(cmd) or ("" if direction == "S->C" else "未登记")
        name = ("  # " + tag) if tag else ""

        if direction == "C->S":
            prefix = "" if cmd in (4, 115) else "\n"      # 心跳/移动不空行，避免刷屏
            emit("%s%s %s->%s seq=%-4d len=%-4d %s%s"
                 % (prefix, hhmmss, "客户端", "服务端", seq, ln, raw.hex()[:160], name))
            pend.add(seq, time.time(), cmd, raw)
        else:
            p = pend.pop(seq)
            req = ""
            if p:
                req = "  [应答 %d]" % p[1]
            extra = fmt_payload(cmd, raw)
            if cmd == 28 and grant:
                try:
                    cur = grant.currencies(raw)
                    diff = ["%s %d→%d (%+d)" % (k, last_cur.get(k, 0), v, v - last_cur.get(k, 0))
                            for k, v in cur.items() if last_cur.get(k, 0) != v]
                    if diff:
                        extra += "   ★ " + " / ".join(diff)
                    last_cur.update(cur)
                except Exception as e:
                    extra += "  [28解码失败 %s]" % e
            emit("%s   ↳ 服务端->客户端 cmd=%-5d seq=%-4d len=%-4d %s%s%s"
                 % (hhmmss, cmd, seq, ln, raw.hex()[:160], extra, req))

    emit("\n# 结束")


if __name__ == "__main__":
    main()
