# -*- coding: utf-8 -*-
"""交任务（214）推送序列回归探针。

验证 2026-11-24 的修复：
  1) 214 后**不再推 178**（真服 15:37:08 / 23:55:41 两个样本都没有）
  2) 身上没装备/翅膀时不推 30（避免用模板快照刷掉外观）
  3) 升级跨过 Lv5 时补推 88（称号「神界新丁」）
  4) 全程不允许出现 cmd=1（除 116 这类明确要求的）

用法：
  python work/probe_quest_push.py [账号] [任务ID]
默认用 17843661 交 400401（交完推到 400402）。
"""
import os
import socket
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

HOST, PORT = "127.0.0.1", 9000
ACCT = sys.argv[1] if len(sys.argv) > 1 else "17843661"
QID = int(sys.argv[2]) if len(sys.argv) > 2 else 400401

TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"
ok_all = True


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sfield(s):
    b = s.encode("utf-8") if isinstance(s, str) else s
    return bytes([0xA0 | len(b)]) + b


def recv_all(sock, wait=1.0):
    sock.settimeout(wait)
    buf, out = b"", []
    while True:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            break
        while len(buf) >= 8:
            total = struct.unpack(">H", buf[:2])[0]
            need = total + 2
            if len(buf) < need:
                break
            cmd, seq = struct.unpack(">HI", buf[2:8])
            out.append((cmd, seq, buf[8:need]))
            buf = buf[need:]
    return out


def check(tag, cond, extra=""):
    global ok_all
    print("  [%s] %s %s" % ("OK" if cond else "FAIL", tag, extra))
    if not cond:
        ok_all = False


def main():
    print("账号=%s 交任务=%d" % (ACCT, QID))
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.sendall(pack(17, 2, b"\x01\xcc\x97\x02" + sfield(ACCT)
                   + b"\xda\x00\x2b" + TOKEN))
    login = recv_all(s, 1.8)
    print("登录包 %d 个: %s" % (len(login), [(c, l) for c, _q, p in login for l in [len(p)]][:20]))
    lv = None
    for c, _q, p in login:
        if c == 28 and len(p) > 26:
            lv = p[25]
            print("  登录 28: len=%d 头(sex,cls,lv)=%s exp16=%d 包=%s"
                  % (len(p), list(p[22:26]), int.from_bytes(p[32:34], "big") if len(p) > 34 else -1,
                     p[:40].hex()))
    print("  登录等级=%s" % lv)

    # 交任务
    qp = b"\xce" + QID.to_bytes(4, "big")
    s.sendall(pack(214, 200, qp))
    resp = recv_all(s, 1.8)
    print("交任务后 %d 个包:" % len(resp))
    for c, q, p in resp:
        print("   cmd=%-6d seq=%-4d len=%-4d %s" % (c, q, len(p), p[:24].hex()))

    cmds = [c for c, _q, _p in resp]
    check("214 有自己的空应答", 214 in cmds)
    check("不再推 178（背包全量）", 178 not in cmds, str(cmds))
    check("有 29 面板", 29 in cmds)
    check("有 28 属性", 28 in cmds)
    check("有 210 下一环", 210 in cmds)
    check("无 cmd=1 提示包", 1 not in cmds, str(cmds))

    # 88 只在跨 5 级时出现：这里打印结果供人工判断
    print("  含 88(称号)=%s  含 30(外观)=%s" % (88 in cmds, 30 in cmds))

    for c, _q, p in resp:
        if c == 28 and len(p) > 26:
            print("  交完 28: len=%d 头(sex,cls,lv)=%s" % (len(p), list(p[22:26])))
    s.close()
    print("\n结果：%s" % ("全部通过" if ok_all else "有失败项"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
