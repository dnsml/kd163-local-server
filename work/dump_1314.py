import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, grant

SAMPLES = {
 "1314 open2(3B)":        "0c0200",
 "1314 after1315(34B)":   "0c020101000c01a6e8908ce696b023cd08d301000000a0000000000000a000000000",
 "1314 vip(48B)":         "0c020101000c01a6e8908ce696b023cd08d301000600ace99a8fe4bebfe78ea9e78ea93ccd626903000000a000000000",
 "1314 vip2(62B)":        "0c020101000c01a6e8908ce696b023cd08d301000600ace99a8fe4bebfe78ea9e78ea93ccd626903001000ace6989fe8beb0e69cbe58c972fcd1ab60500",
 "1331(70B)":             "0305a9e58fb6e5909be88cb942030004cd250800cd936c0006ace99a8fe4bebfe78ea9e78ea93c030004cd1db000cd62690012a6e89b8be89b8b2a010004cd078001cd1a9c00",
 "1331(116B)":            "0505a9e58fb6e5909be88cb942030004cd250800cd94ab0006ace99a8fe4bebfe78ea9e78ea93c030004cd1db000cd6269000da6e79fa5e5bf8336010003cd0aa001cd33da0010ace6989fe8beb0e69cbe58c972f050003cd0ac800cd1ab60112a6e89b8be89b8b2b010004cd078001cd1ab900",
 "354(6B)":               "ffffff00ff00",
 "354(7B ccf20c)":        "ccf20cff00ff00",
 "354(7B vip)":           "ccf30c0601ff00",
 "354(7B vip2)":          "ccf30c06011001",
 "1378(38B vip)":         "0106ace99a8fe4bebfe78ea9e78ea90004cd1a4003002501a9e68b89e68891e590a700000000",
 "1378(94B)":             "010ca6e8908ce696b00100000100ce6b0468d901da0043e6b4b5bbe58aa8e7bb84e9989fe68b9be58b9fefbc9a31e58fb7e688bfefbc8c322f33e4babaefbc8ce8afb7e588b0e7bb84e9989fe5a4a7e58e85e58aa0e585a5e3808200000000",
}
for name, hx in SAMPLES.items():
    b = bytes.fromhex(hx)
    tk = grant.tokenize(b)
    print("== %s  len=%d  tokens=%d" % (name, len(b), len(tk)))
    line = []
    for i, t in enumerate(tk):
        v = t["value"]
        if isinstance(v, bytes):
            try: v = v.decode("utf-8")
            except Exception: v = v.hex()
            line.append("[%d]str<%s>" % (i, v))
        else:
            line.append("[%d]%d" % (i, v))
    print("   " + " ".join(line))
    print()
