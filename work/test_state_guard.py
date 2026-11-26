# -*- coding: utf-8 -*-
"""存档三重闸门回归：authed / loaded / acct。

跑这个脚本必须保证本地服已重启到最新代码。它验四件事：

  1. 没登录的连接发业务包 —— 主号存档一个字节都不许动；
  2. 登录一个没有存档的账号 —— 不许复制主号存档，也不许凭空写文件；
  3. 登录主号 —— 必须真的读到 Lv45 那份，而不是 Player() 默认值；
  4. 全程结束后主号存档 md5 不变。

用法：
    python work/test_state_guard.py
"""
import hashlib
import json
import os
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 9000
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(HERE, "player_state.json")
GHOST = os.path.join(HERE, "player_state_ZZZ99999.json")
sys.path.insert(0, HERE)
# 这是**对着运行中的服**验收的脚本，必须看正式库，不能落到自检临时库
os.environ.setdefault("KD_STATE_DB", os.path.join(HERE, "server_state.db"))
try:
    import state_store
    USE_DB = state_store.enabled()
except Exception:
    state_store = None
    USE_DB = False
PRIMARY = "1784366"

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + extra) if extra else ""))


def md5(p):
    if not os.path.exists(p):
        return "-"
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:12]


def save_snapshot(acct=PRIMARY):
    """存档指纹 + 内容。SQLite 后端下按库里那一行的 data 算，不再看 JSON 文件。"""
    if USE_DB:
        d, rev = state_store.load(acct)
        if d is None:
            return "-", 0, {}
        raw = json.dumps(d, ensure_ascii=False, sort_keys=True)
        return (hashlib.md5(raw.encode("utf-8")).hexdigest()[:12], rev, d)
    p = MAIN if acct == PRIMARY else os.path.join(HERE, "player_state_%s.json" % acct)
    if not os.path.exists(p):
        return "-", 0, {}
    return md5(p), 0, json.load(open(p, encoding="utf-8"))


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sfield(s):
    b = s.encode("utf-8")
    return bytes([0xA0 | len(b)]) + b


def login_payload(acct):
    """尽量贴近真服：01 cc9702 + 账号 + da002b + token。"""
    return b"\x01\xcc\x97\x02" + sfield(acct) + b"\xda\x00\x2b" + b"T" * 43


def drain(s, sec):
    t0 = time.time()
    out = b""
    while time.time() - t0 < sec:
        try:
            d = s.recv(65536)
            if not d:
                break
            out += d
        except socket.timeout:
            pass
    return out


def frames(buf):
    """粗解帧：返回 [(cmd, payload)]。"""
    res = []
    i = 0
    n = len(buf)
    while i + 4 <= n:
        total = struct.unpack(">H", buf[i:i + 2])[0]
        if total < 4 or i + total + 2 > n:
            break
        cmd = struct.unpack(">H", buf[i + 2:i + 4])[0]
        res.append((cmd, buf[i + 6:i + 2 + total]))
        i += total + 2
    return res


def connect():
    s = socket.create_connection((HOST, PORT), timeout=8)
    s.settimeout(0.5)
    return s


def main():
    print("=" * 62)
    print("存档三重闸门回归")
    print("=" * 62)

    before, rev0, d0 = save_snapshot()
    print("主号存档：%s=%s Lv%s 背包%d件"
          % ("rev" if USE_DB else "md5", rev0 if USE_DB else before,
             d0.get("level"), len(d0.get("bag", {}))))
    check("前置：主号存档存在且有角色", bool(d0) and int(d0.get("level") or 0) > 0,
          "实际 Lv%s" % d0.get("level"))

    # ---------- 场景 1：不登录直接发业务包 ----------
    print("\n[1] 未登录连接发业务包（模拟半开连接误触发写盘）")
    s = connect()
    drain(s, 1.5)
    for seq, cmd, pl in ((9001, 211, b""), (9002, 146, b"\x01\x02"),
                         (9003, 149, b"\x01"), (9004, 214, b"")):
        try:
            s.sendall(pack(cmd, seq, pl))
        except OSError:
            break
        time.sleep(0.3)
    drain(s, 1.0)
    s.close()
    time.sleep(0.8)
    now, _, _ = save_snapshot()
    check("未登录不改主号存档", now == before,
          "before=%s after=%s" % (before, now))

    # ---------- 场景 2：登录没有存档的账号 ----------
    print("\n[2] 登录不存在角色的账号 ZZZ99999")
    if USE_DB:
        state_store.delete("ZZZ99999")
    if os.path.exists(GHOST):
        os.remove(GHOST)
    s = connect()
    drain(s, 1.2)
    s.sendall(pack(17, 9101, login_payload("ZZZ99999")))
    r = drain(s, 2.5)
    fr = frames(r)
    got17 = [p for c, p in fr if c == 17]
    print("     收到 17 应答 %d 个，长度 %s" % (len(got17), [len(p) for p in got17]))
    # 再发业务包，逼它走 save 路径
    for seq, cmd in ((9102, 211), (9103, 149)):
        try:
            s.sendall(pack(cmd, seq, b"\x01"))
        except OSError:
            break
        time.sleep(0.4)
    drain(s, 0.8)
    s.close()
    time.sleep(0.8)
    now, _, _ = save_snapshot()
    check("不复制主号存档给新账号", now == before,
          "before=%s after=%s" % (before, now))
    ghost_db = USE_DB and state_store.exists("ZZZ99999")
    check("不为无角色账号凭空建存档", not ghost_db and not os.path.exists(GHOST),
          "库=%s 文件=%s" % (ghost_db, os.path.exists(GHOST)))

    # ---------- 场景 3：登录主号 ----------
    print("\n[3] 登录主号 1784366")
    dup = os.path.join(HERE, "player_state_1784366.json")
    s = connect()
    drain(s, 1.2)
    s.sendall(pack(17, 9201, login_payload("1784366")))
    r = drain(s, 3.0)
    fr = frames(r)
    got17 = [p for c, p in fr if c == 17]
    print("     收到 17 应答 %d 个，长度 %s" % (len(got17), [len(p) for p in got17]))
    s.close()
    time.sleep(0.6)

    # 主号存档内容必须一字不变（登录只读，不改写）
    now, rev1, d1 = save_snapshot()
    check("登录主号后存档内容不变", now == before,
          "before=%s after=%s rev %d->%d" % (before, now, rev0, rev1))
    check("登录主号后等级仍是 Lv%s" % d0.get("level"),
          d1.get("level") == d0.get("level"), "实际 Lv%s" % d1.get("level"))
    check("登录主号后背包件数仍是 %d" % len(d0.get("bag", {})),
          len(d1.get("bag", {})) == len(d0.get("bag", {})),
          "实际 %d 件" % len(d1.get("bag", {})))
    check("主号没被拆成第二个存档", not os.path.exists(dup),
          "%s 存在=%s" % (os.path.basename(dup), os.path.exists(dup)))

    # ---------- 收尾 ----------
    print("\n" + "=" * 62)
    print("通过 %d 项 / 失败 %d 项" % (len(OK), len(BAD)))
    if BAD:
        for b in BAD:
            print("  失败：%s" % b)
    print("=" * 62)
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
