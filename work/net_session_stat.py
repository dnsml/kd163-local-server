# -*- coding: utf-8 -*-
"""按时间窗统计服务端日志里的上下行字节数（真实包长，含 6B 包头）。

用法：
  python work/net_session_stat.py                       # 找登录最密集的 20 秒
  python work/net_session_stat.py c001 23:41:40 23:42:10
"""
import re
import sys

PAT = re.compile(
    r"\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s*"
    r"(c\d+)\s+(->|<-)\s+cmd=(\d+)\s+.*?len=(\d+)"
)


def rows(path="logs/server.log"):
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = PAT.search(line)
            if m:
                out.append((m.group(1), m.group(2), m.group(3),
                            int(m.group(4)), int(m.group(5))))
    return out


def sec(t):
    h, m, s = [int(x) for x in t.split(":")]
    return h * 3600 + m * 60 + s


def summarize(seg, title):
    dn = [x for x in seg if x[2] == "->"]
    up = [x for x in seg if x[2] == "<-"]
    dn_b = sum(x[4] for x in dn)
    up_b = sum(x[4] for x in up)
    print("%s" % title)
    print("  时间      %s ~ %s" % (seg[0][0], seg[-1][0]))
    print("  上行  %3d 包  %7d B = %5.1f KB" % (len(up), up_b, up_b / 1024.0))
    print("  下行  %3d 包  %7d B = %5.1f KB" % (len(dn), dn_b, dn_b / 1024.0))
    big = sorted(dn, key=lambda x: -x[4])[:10]
    print("  下行最大 10 包：" + ", ".join("cmd=%d/%dB" % (x[3], x[4]) for x in big))
    print()


def main():
    rs = rows()
    print("解析到包级日志行：%d" % len(rs))
    if not rs:
        return
    if len(sys.argv) >= 4:
        c, t0, t1 = sys.argv[1], sec(sys.argv[2]), sec(sys.argv[3])
        seg = [x for x in rs if x[1] == c and t0 <= sec(x[0]) <= t1]
        if seg:
            summarize(seg, "连接 %s 指定窗口" % c)
        else:
            print("该窗口没有日志")
        return
    # 自动找：以每条 17 下发为锚点，前后 20 秒窗口内下行字节最多的那个
    best, best_b = None, -1
    for i, (t, c, d, cmd, ln) in enumerate(rs):
        if not (cmd == 17 and d == "->"):
            continue
        t0 = sec(t) - 6
        seg = [x for x in rs if x[1] == c and t0 <= sec(x[0]) <= t0 + 20]
        b = sum(x[4] for x in seg if x[2] == "->")
        if b > best_b:
            best, best_b = seg, b
    if best:
        summarize(best, "登录最密集窗口（连接 %s）" % best[0][1])
    # 心跳：cmd=4 的往返
    hb = [x for x in rs if x[3] == 4]
    if hb:
        print("心跳 cmd=4：%d 条，平均长度 %.1f B（含 6B 包头）"
              % (len(hb), sum(x[4] for x in hb) * 1.0 / len(hb)))


if __name__ == "__main__":
    main()
