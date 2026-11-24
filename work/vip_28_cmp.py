import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grant

S = {
 "PRE  00:23:32": "0ca731373834333636a6e8908ce696b001012300cd04afce0008376a681209cd0a280000000500ce00424b4acd0fd2cd4e201f0000cd03fb000000010000cd08d30000000001000000ce000b663b",
 "POST 00:23:39": "0ca731373834333636a6e8908ce696b001012300cd04afce0008376a681209cd0a280000000500ce00424b4acd0fd2cd4e201f04cd0730cd03fb000000010000cd08d30000000001000000ce000b663b",
 "LATE 00:33:24": "0ca731373834333636a6e8908ce696b0010123cd01f1cd0590ce0008e7326a0e02cd0a280000000500ce0043aadacd0fd2cd4e20cd027404cd0730cd03fb000000010000cd08d30000000001000000ce000b663b",
}
tks = {k: grant.tokenize(bytes.fromhex(v)) for k, v in S.items()}
n = max(len(t) for t in tks.values())
print("idx | PRE | POST | LATE")
for i in range(n):
    row = []
    for k in ("PRE  00:23:32", "POST 00:23:39", "LATE 00:33:24"):
        t = tks[k][i] if i < len(tks[k]) else None
        if t is None: row.append("-")
        elif t["kind"] == "str": row.append("s:%r" % t["value"])
        else: row.append(str(t["value"]))
    print("%3d | %-12s | %-12s | %s" % (i, row[0], row[1], row[2]))
