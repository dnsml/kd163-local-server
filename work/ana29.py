import re

_D = chr(92) + "d"
PAT = re.compile(r"\[(" + _D + _D + ":" + _D + _D + ":" + _D + _D + r")\]"
                 + chr(92) + "s+(C->S|S->C)" + chr(92) + "s+cmd=(" + _D + "+)"
                 + chr(92) + "s+seq=(" + _D + "+)" + chr(92) + "s+len=(" + _D + "+)"
                 + chr(92) + "s+hex=([0-9a-fA-F]*)")


def toks(b):
    out = []
    i = 0
    while i < len(b):
        c = b[i]
        if c == 0xCE and i + 4 < len(b):
            out.append(int.from_bytes(b[i + 1:i + 5], "big")); i += 5
        elif c == 0xCD and i + 2 < len(b):
            out.append(int.from_bytes(b[i + 1:i + 3], "big")); i += 3
        elif c == 0xCC and i + 1 < len(b):
            out.append(b[i + 1]); i += 2
        else:
            out.append(c); i += 1
    return out


def vread(b, i):
    if b[i] == 0xCE:
        return int.from_bytes(b[i + 1:i + 5], "big"), i + 5
    if b[i] == 0xCD:
        return int.from_bytes(b[i + 1:i + 3], "big"), i + 3
    if b[i] == 0xCC:
        return b[i + 1], i + 2
    return b[i], i + 1


rows = []
cur = None
for ln in open("logs/relay.log", encoding="utf-8", errors="ignore"):
    m = PAT.search(ln)
    if not m:
        continue
    t, d, cmd, seq, ln_, h = m.groups()
    cmd = int(cmd)
    b = bytes.fromhex(h)
    if d == "C->S" and cmd == 822:
        if cur:
            rows.append(cur)
        cur = {"t": t, "req": h, "slot": b[0]}
        continue
    if cur is None:
        continue
    if d == "S->C" and cmd == 818:
        i = 1
        while i + 8 < len(b):
            slot = b[i]
            j = i + 1
            iid, j = vread(b, j)
            cur["lv"] = b[j + 2] if j + 2 < len(b) else None
            cur["iid"] = iid
            i = j + 22
    elif d == "S->C" and cmd == 29 and "p29" not in cur:
        cur["p29"] = toks(b)
        rows.append(cur)
        cur = None
if cur:
    rows.append(cur)

print("时间      槽 物品  +N  29包字段")
for r in rows:
    print("%s %-2s %-6s %-3s %s" % (r["t"], r.get("slot"), r.get("iid"),
                                    r.get("lv"), r.get("p29")))
