import sys
sys.path.insert(0, r"D:\66666\kd163")
import grant

BEFORE = ("0ca731373834333636a6e8908ce696b001012300cd04afce0008376a681209"
          "cd0a280000000500ce00424b4acd0fd2cd4e201f0000cd03fb0000000100"
          "0000cd08d30000000001000000ce000b663b")
AFTER = ("0ca731373834333636a6e8908ce696b001012300cd04afce0008376a681209"
         "cd0a280000000500ce00424b4acd0fd2cd4e201f04cd0730cd03fb00000001"
         "000000cd08d30000000001000000ce000b663b")

for name, hx in (("使用前", BEFORE), ("使用后", AFTER)):
    tk = grant.tokenize(bytes.fromhex(hx))
    print("== %s  共 %d 个 token ==" % (name, len(tk)))
    for i, t in enumerate(tk):
        print("  [%2d] %s" % (i, t))
    print()
