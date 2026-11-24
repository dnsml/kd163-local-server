#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd163 抓包代理
监听本机 80 / 9000，模拟器通过 hosts 把 124.223.56.189 指到本机。
- 9000 首字节 0x16 -> TLS 透传（不解密，端到端保留）
- 9000 其他      -> 明文游戏协议，按 [u16BE len][u16BE cmd][u32BE seq][payload] 解析并 dump
- 80             -> HTTP 明文，原样记录
"""
import socket
import threading
import struct
import time
import os
import sys

TARGET_HOST = "124.223.56.189"
CAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture")
os.makedirs(CAP_DIR, exist_ok=True)

_lock = threading.Lock()
_seq_no = [0]


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _lock:
        try:
            print(line, flush=True)
        except Exception:
            pass


def new_conn_id():
    with _lock:
        _seq_no[0] += 1
        return _seq_no[0]


def dump_file(cid, tag, ext="bin"):
    return os.path.join(CAP_DIR, "c%03d_%s.%s" % (cid, tag, ext))


def hexdump(b, width=16):
    out = []
    for i in range(0, len(b), width):
        chunk = b[i:i + width]
        hx = " ".join("%02x" % x for x in chunk)
        asc = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        out.append("    %04x  %-*s  %s" % (i, width * 3, hx, asc))
    return "\n".join(out)


class GameParser:
    """解析游戏协议流，按包边界切分"""

    def __init__(self, cid, direction):
        self.cid = cid
        self.dir = direction
        self.buf = b""
        self.pkt_no = 0
        self.out = []

    def feed(self, data):
        self.buf += data
        while len(self.buf) >= 8:
            total_len, cmd, seq = struct.unpack(">HHI", self.buf[:8])
            if total_len < 6 or total_len > 65535:
                self.out.append("  [!] 非法长度 %d，原始头部: %s" % (total_len, self.buf[:16].hex()))
                self.buf = self.buf[8:]
                continue
            need = total_len + 2  # len 字段含 6 字节头，实际网络上 = len + 2
            if len(self.buf) < need:
                # 尝试另一种解释：len 是不含高亮的整个包长
                need2 = total_len
                if len(self.buf) < need2:
                    break
                need = need2
            payload = self.buf[8:need]
            self.pkt_no += 1
            self.out.append(
                "  #%03d %s  cmd=%-5d (0x%04x)  seq=%-6d  payload=%d 字节"
                % (self.pkt_no, self.dir, cmd, cmd, seq, len(payload))
            )
            if payload:
                body = payload[:256]
                self.out.append(hexdump(body) + ("\n    ... (共 %d 字节)" % len(payload) if len(payload) > 256 else ""))
                try:
                    txt = payload.decode("utf-8")
                    if txt.isprintable():
                        self.out.append("    text: %r" % txt)
                except Exception:
                    pass
            self.buf = self.buf[need:]
        if len(self.buf) >= 2 and len(self.out) == 0:
            pass

    def flush(self):
        if self.buf:
            self.out.append("  [残留 %d 字节] %s" % (len(self.buf), self.buf[:64].hex()))


def relay(src, dst, parser=None, raw_sink=None, name=""):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            if parser:
                parser.feed(data)
            if raw_sink is not None:
                raw_sink.write(data)
                raw_sink.flush()
            dst.sendall(data)
    except Exception as e:
        log("  %s relay 结束: %s" % (name, type(e).__name__))
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def handle(client, port, cid):
    try:
        client.settimeout(120)
        first = client.recv(1, socket.MSG_PEEK)
        if not first:
            client.close()
            return
        is_tls = (first[0] == 0x16) if port == 9000 else False
        remote = socket.create_connection((TARGET_HOST, port), timeout=15)
        remote.settimeout(120)

        if is_tls:
            log("c%03d :%d <- TLS 连接，透传中" % (cid, port))
            t = threading.Thread(target=relay, args=(client, remote, None, None, "TLS-up"), daemon=True)
            t.start()
            relay(remote, client, None, None, "TLS-down")
            t.join(timeout=1)
            client.close(); remote.close()
            log("c%03d :%d TLS 连接结束" % (cid, port))
            return

        # 明文
        tag = "http" if port == 80 else "game"
        fname = dump_file(cid, tag)
        sink = open(fname, "wb")
        log("c%03d :%d <- 明文 %s 连接，dump -> %s" % (cid, port, tag, os.path.basename(fname)))

        up_parser = GameParser(cid, "C->S") if port == 9000 else None
        down_parser = GameParser(cid, "S->C") if port == 9000 else None

        t = threading.Thread(target=relay, args=(client, remote, up_parser, sink, "up"), daemon=True)
        t.start()
        relay(remote, client, down_parser, sink, "down")
        t.join(timeout=2)

        sink.close()
        client.close(); remote.close()

        with _lock:
            lines = []
            for p in (up_parser, down_parser):
                if p:
                    p.flush()
                    if p.out:
                        lines.append("### %s" % p.dir)
                        lines.extend(p.out)
            if lines:
                logpath = dump_file(cid, tag, "log")
                open(logpath, "w", encoding="utf-8").write("\n".join(lines))
                print("\n".join(lines), flush=True)
                log("c%03d 解析结果 -> %s" % (cid, os.path.basename(logpath)))
    except Exception as e:
        log("c%03d 处理异常: %s %s" % (cid, type(e).__name__, e))
        try:
            client.close()
        except Exception:
            pass


def serve(port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(32)
    log("监听 :%d  ->  %s:%d" % (port, TARGET_HOST, port))
    while True:
        c, addr = srv.accept()
        cid = new_conn_id()
        log("c%03d 来自 %s:%d (本地端口 %d)" % (cid, addr[0], addr[1], port))
        threading.Thread(target=handle, args=(c, port, cid), daemon=True).start()


if __name__ == "__main__":
    ports = [80, 9000]
    if len(sys.argv) > 1:
        ports = [int(x) for x in sys.argv[1].split(",")]
    log("抓包代理启动，dump 目录: %s" % CAP_DIR)
    for p in ports[1:]:
        threading.Thread(target=serve, args=(p,), daemon=True).start()
    serve(ports[0])
