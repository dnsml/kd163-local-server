# -*- coding: utf-8 -*-
"""账号四条流程验收（ChatGPT 提的那四条）。

  phase1  跑前三项，并把「重启前」的状态快照写进 logs/accept_snapshot.json
  phase2  服务端重启后跑第四项，和快照逐字段比对

用法：
  python work/test_account_flow.py phase1
  （重启 9000）
  python work/test_account_flow.py phase2

设计要点：
- 全程用直连明文协议（和 probe_create_role.py 同一套帧），不依赖模拟器。
- 「已有账号」用 17843661（Lv7，独立存档），不碰正在玩的主号。
- 只读校验主流程，唯一一次写操作是给测试号发 115 改坐标，
  用来证明「两个号同时在线时，A 写盘不会冲到 B 身上」。
"""
import json
import os
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

HOST, PORT = "127.0.0.1", 9000
TOKEN = b"+jRY5oMSUYC44DPcEVBfdWlHuF0FzRC_t5eHoE3iZPsM"
SNAP = os.path.join(ROOT, "logs", "accept_snapshot.json")
# 这是对着运行中的服验收，必须看正式库（不落自检临时库）
os.environ.setdefault("KD_STATE_DB", os.path.join(ROOT, "server_state.db"))

OLD_ACCT = "17843661"          # 已有账号（百里海熠 Lv7）
NEW_ACCT = "91" + time.strftime("%H%M%S")
NEW_NAME = "验收小号"

fails = []


def pack(cmd, seq, payload=b""):
    return struct.pack(">HHI", 6 + len(payload), cmd, seq) + payload


def sfield(s):
    b = s.encode("utf-8") if isinstance(s, str) else s
    return bytes([0xA0 | len(b)]) + b


def recv_all(sock, wait=1.2):
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


def conn():
    return socket.create_connection((HOST, PORT), timeout=5)


def login(sock, acct, seq=2):
    pl = b"\x01\xcc\x97\x02" + sfield(acct) + b"\xda\x00\x2b" + TOKEN
    sock.sendall(pack(17, seq, pl))
    return recv_all(sock, 1.5)


def create(sock, acct, name, sex=1, seq=7):
    body = (b"\x01\x01\x02" + sfield(acct) + b"\xda\x00\x2b" + TOKEN
            + sfield(name) + bytes([sex]))
    sock.sendall(pack(20, seq, body))
    return recv_all(sock, 2.0)


def check(tag, cond, extra=""):
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", tag,
                           ("  " + extra) if extra else ""))
    if not cond:
        fails.append(tag)


def state(acct):
    """读某个账号的存档：默认走后端（SQLite），KD_STATE_BACKEND=json 时读文件。"""
    try:
        sys.path.insert(0, ROOT)
        import state_store
        if state_store.enabled():
            d, _ = state_store.load(acct)
            return d
    except Exception as e:
        print("     (读库失败，回退文件：%s)" % e)
    p = os.path.join(ROOT, "player_state_%s.json" % acct)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def pick(d):
    """快照用的稳定字段（不含时间戳/指纹这类易变项）。"""
    if not d:
        return {}
    return {
        "name": d.get("name"), "sex": d.get("sex"), "level": d.get("level"),
        "class": d.get("class"), "exp": d.get("exp"),
        "has_char": d.get("has_char"), "ver": d.get("ver"),
        "coin": d.get("coin"), "scene": d.get("scene"),
        "pos": [d.get("pos_x"), d.get("pos_y")],
        "equip": {k: v for k, v in (d.get("equip") or {}).items()},
        "bag": {k: v for k, v in (d.get("bag") or {}).items()},
    }


def has_name(pk, name):
    key = name.encode("utf-8")
    return any(c == 17 and key in p for c, _q, p in pk)


def phase1():
    print("=" * 66)
    print("① 新账号：登录(无角色) → 建角 → 保存 → 重登加载")
    print("=" * 66)
    print("  测试账号 %s / %s" % (NEW_ACCT, NEW_NAME))

    s = conn()
    pk = login(s, NEW_ACCT)
    check("新账号登录回 cmd=1 01a0（无角色）",
          any(c == 1 and p == b"\x01\xa0" for c, _q, p in pk),
          str([(c, p.hex()) for c, _q, p in pk]))
    check("新账号不应直接回 17", not any(c == 17 for c, _q, _p in pk))
    s.close()

    s = conn()
    login(s, NEW_ACCT)
    pk = create(s, NEW_ACCT, NEW_NAME)
    ack = [p for c, _q, p in pk if c == 20]
    check("建角有 cmd=20 应答", bool(ack))
    if ack:
        a = ack[0]
        want = 1 + (1 + len(NEW_ACCT)) + (1 + len(NEW_NAME.encode())) + 2 + 134
        check("20 应答长度按真服公式", len(a) == want,
              "实际 %d 期望 %d" % (len(a), want))
        check("20 应答 0x14 头", a[0] == 0x14, "0x%02x" % a[0])
    check("建角后推 113", any(c == 113 for c, _q, _p in pk))
    check("建角后推 117", any(c == 117 for c, _q, _p in pk))
    s.close()

    time.sleep(0.6)
    d = state(NEW_ACCT)
    check("存档已落盘", d is not None, "player_state_%s.json" % NEW_ACCT)
    if d:
        check("昵称正确", d.get("name") == NEW_NAME, repr(d.get("name")))
        check("等级 Lv1", d.get("level") == 1, repr(d.get("level")))
        check("已建角 has_char", bool(d.get("has_char")))
        check("存档版本 ver=2", d.get("ver") == 2, repr(d.get("ver")))
        # 2026-11-26：建角现在真发玄女新人三件套（20190×10 / 20432×1 / 21001×1），
        # 这里不能再用「背包必须为空」——要断言「只有三件套，没有别人的东西」。
        _bag = d.get("bag") or {}
        _want = {0: [20190, 10], 1: [20432, 1], 2: [21001, 1]}
        _got = {int(k): [v[0], v[1]] for k, v in _bag.items()}
        check("背包只有新人三件套（没继承别人的东西）", _got == _want, repr(_bag))
        check("装备为空", not d.get("equip"), repr(d.get("equip")))
        check("货币未继承主号", (d.get("coin") or 0) == 0, repr(d.get("coin")))

    s = conn()
    pk = login(s, NEW_ACCT)
    check("重登回 17（角色已存在）", any(c == 17 for c, _q, _p in pk))
    check("重登 17 里带自己的昵称", has_name(pk, NEW_NAME))
    s.close()

    print()
    print("=" * 66)
    print("② 已有账号：登录 → 加载原角色")
    print("=" * 66)
    before = state(OLD_ACCT)
    check("已有账号存档存在", before is not None)
    s = conn()
    pk = login(s, OLD_ACCT)
    check("回 17（不是 01a0）", any(c == 17 for c, _q, _p in pk),
          str([(c, len(p)) for c, _q, p in pk][:8]))
    check("17 里带原角色昵称", has_name(pk, (before or {}).get("name") or "?"),
          repr((before or {}).get("name")))
    check("加载后等级与存档一致（服务端没重置）",
          (before or {}).get("level", -1) > 1,
          "Lv%s" % (before or {}).get("level"))
    s.close()

    print()
    print("=" * 66)
    print("③ 两个账号同时在线 → 数据互不影响")
    print("=" * 66)
    sa, sb = conn(), conn()
    pa = login(sa, NEW_ACCT)
    pb = login(sb, OLD_ACCT)
    check("A 线拿到的是 A 的数据", has_name(pa, NEW_NAME))
    check("A 线不含 B 的昵称",
          not has_name(pa, (before or {}).get("name") or "?"))
    check("B 线拿到的是 B 的数据", has_name(pb, (before or {}).get("name") or "?"))
    check("B 线不含 A 的昵称", not has_name(pb, NEW_NAME))

    # 交替再登一次，确认没有串线
    pa2 = login(sa, NEW_ACCT)
    pb2 = login(sb, OLD_ACCT)
    check("交替登录后 A 仍是 A", has_name(pa2, NEW_NAME) and not has_name(pa2, (before or {}).get("name") or "?"))
    check("交替登录后 B 仍是 B", has_name(pb2, (before or {}).get("name") or "?") and not has_name(pb2, NEW_NAME))

    # A 做一次写操作：115 把坐标改到 (11,9)，B 完全不动
    sa.sendall(pack(115, 20, b"\x0b\x09"))
    recv_all(sa, 0.8)
    pa3 = login(sa, NEW_ACCT)
    check("A 写坐标后自己仍然正常", has_name(pa3, NEW_NAME))
    pb3 = login(sb, OLD_ACCT)
    check("B 没被 A 的写入影响", has_name(pb3, (before or {}).get("name") or "?"))
    sa.close()
    sb.close()
    time.sleep(0.8)

    da, db = state(NEW_ACCT), state(OLD_ACCT)
    check("A 的坐标已落盘 (11,9)",
          [da.get("pos_x"), da.get("pos_y")] == [11, 9] if da else False,
          "%s" % ([da.get("pos_x"), da.get("pos_y")] if da else None))
    if db and before:
        check("B 的等级/装备未被 A 覆盖",
              db.get("level") == before.get("level")
              and (db.get("equip") or {}) == (before.get("equip") or {}),
              "Lv%s/%d件" % (db.get("level"), len(db.get("equip") or {})))

    snap = {"new_acct": NEW_ACCT, "new_name": NEW_NAME,
            "new_state": pick(state(NEW_ACCT)),
            "old_acct": OLD_ACCT, "old_state": pick(state(OLD_ACCT))}
    with open(SNAP, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print("  快照已写：%s" % SNAP)

    print()
    print("phase1 结论：%s（失败 %d 项）" % ("全部通过" if not fails else "有失败", len(fails)))
    return 0 if not fails else 1


def phase2():
    print("=" * 66)
    print("④ 角色退出 → 服务端重启 → 再登录，数据完全一致")
    print("=" * 66)
    if not os.path.exists(SNAP):
        print("  缺少快照 %s，先跑 phase1" % SNAP)
        return 2
    snap = json.load(open(SNAP, encoding="utf-8"))

    for acct, name, key in ((snap["new_acct"], snap["new_name"], "new_state"),
                            (snap["old_acct"], snap["old_state"].get("name"), "old_state")):
        s = conn()
        pk = login(s, acct)
        check("[%s] 重启后回 17" % acct, any(c == 17 for c, _q, _p in pk))
        check("[%s] 17 里昵称正确" % acct, has_name(pk, name or "?"), repr(name))
        s.close()
        time.sleep(0.5)
        now = pick(state(acct))
        want = snap[key]
        diff = {k: (want.get(k), now.get(k)) for k in want if want.get(k) != now.get(k)}
        check("[%s] 重启后存档逐字段一致" % acct, not diff, str(diff))

    print()
    print("phase2 结论：%s（失败 %d 项）" % ("全部通过" if not fails else "有失败", len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "phase1"
    sys.exit(phase2() if mode == "phase2" else phase1())
