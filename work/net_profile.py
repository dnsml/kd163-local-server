# -*- coding: utf-8 -*-
"""按 cmd 统计服务端日志的包长分布，用于估算带宽。

用法： python work/net_profile.py [日志路径]
"""
import collections
import re
import sys

PAT = re.compile(
    r"\[(?:20\d\d-\d\d-\d\d )?(\d\d:\d\d:\d\d)\]\s*"
    r"(c\d+)\s+(->|<-)\s+cmd=(\d+)\s+.*?len=(\d+)"
)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/server.log"
    st = collections.defaultdict(lambda: [0, 0])   # (dir, cmd) -> [count, bytes]
    total = [0, 0]
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = PAT.search(line)
            if not m:
                continue
            d = 0 if m.group(3) == "<-" else 1
            cmd, ln = int(m.group(4)), int(m.group(5))
            k = (d, cmd)
            st[k][0] += 1
            st[k][1] += ln
            total[d] += ln
    print("日志：%s" % path)
    print("上行合计 %.2f MB，下行合计 %.2f MB"
          % (total[0] / 1048576.0, total[1] / 1048576.0))
    print()
    print("%-4s %-6s %8s %10s %10s" % ("方向", "cmd", "包数", "合计B", "平均B"))
    for (d, cmd), (cnt, by) in sorted(st.items(), key=lambda x: -x[1][1])[:20]:
        print("%-4s %-6d %8d %10d %10.1f"
              % ("上行" if d == 0 else "下行", cmd, cnt, by, by * 1.0 / cnt))


if __name__ == "__main__":
    main()
