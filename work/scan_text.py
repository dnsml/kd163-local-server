"""按时间窗列出有效帧，并把 payload 里的中文串抽出来。

用法：python work/scan_text.py 19:18:00 19:24:00
"""
import re
import sys

BS = chr(92)
PAT = re.compile(r"\[(?:20\d\d-\d\d-\d\d )?(" + BS + "d" + BS + "d:" + BS + "d" + BS + "d:" + BS + "d" + BS + "d" + r")\]"
                 r"\s+(C->S|S->C)\s+cmd=(" + BS + "d+)\s+seq=(" + BS + "d+)"
                 r"\s+len=(" + BS + "d+)\s+hex=([0-9a-fA-F]*)")

NOISE = {4, 115}


def texts(b):
    out = []
    i = 0
    while i < len(b):
        # 尝试从 i 开始解一段连续 UTF-8 中文
        j = i
        buf = bytearray()
        while j < len(b):
            c = b[j]
            if c < 0x80:
                break
            n = 3 if c >= 0xE0 else (2 if c >= 0xC0 else 0)
            if n == 0 or j + n > len(b):
                break
            try:
                ch = b[j:j + n].decode("utf-8")
            except UnicodeDecodeError:
                break
            buf += b[j:j + n]
            j += n
        if len(buf) >= 3:
            out.append(buf.decode("utf-8", "ignore"))
            i = j
        else:
            i += 1
    return out


t0 = sys.argv[1] if len(sys.argv) > 1 else "00:00:00"
t1 = sys.argv[2] if len(sys.argv) > 2 else "23:59:59"
for ln in open("logs/relay.log", encoding="utf-8", errors="ignore"):
    m = PAT.search(ln)
    if not m:
        continue
    t, d, cmd, seq, ln_, h = m.groups()
    if not (t0 <= t <= t1):
        continue
    cmd = int(cmd)
    if cmd in NOISE:
        continue
    b = bytes.fromhex(h) if h else b""
    tx = texts(b)
    show = h if len(h) <= 80 else h[:80] + "..."
    print("%s %-4s cmd=%-5d seq=%-5s len=%-4s %s%s"
          % (t, d, cmd, seq, ln_, show,
             ("  《" + " | ".join(tx) + "》") if tx else ""))
