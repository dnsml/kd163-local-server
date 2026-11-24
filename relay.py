#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""9000 端口透传记录器：客户端 -> 本机19000 -> 真服:9000
纯 TCP 透传，不解密、不改包。按帧解析游戏协议，把真实 145/146 抓下来。
"""
import socket, struct, threading, time, os, sys

UPSTREAM = ("124.223.56.189", 9000)
PORT = int(os.environ.get("RELAY_PORT", "19000"))
LISTEN = ("0.0.0.0", PORT)
HERE = os.path.dirname(os.path.abspath(__file__))
_SUF = "" if PORT == 19000 else "_%d" % PORT
LOG = os.path.join(HERE, "logs", "relay%s.log" % _SUF)
RAW = os.path.join(HERE, "logs", "relay_raw%s.bin" % _SUF)
WATCH = os.path.join(HERE, "logs", "watch%s.log" % _SUF)

# 建角相关的命令号：这些包不截断，单独全量落盘（watch.log）
WATCH_CMDS = {17, 18, 19, 20, 21, 22, 23}

lock = threading.Lock()

def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    with lock:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")

def dump(direction, data):
    with lock:
        with open(RAW, "ab") as f:
            f.write(direction.encode())
            f.write(struct.pack(">I", len(data)))
            f.write(data)

class FrameSplitter:
    """按 [u16 total][u16 cmd][u32 seq][payload] 切帧；TLS 段直接跳过。"""
    def __init__(self, tag):
        self.tag = tag
        self.buf = b""
        self.tls = False

    def feed(self, data):
        if not self.buf and data and data[0] == 0x16:
            self.tls = True
            log("%s TLS 握手开始（/auth），后续内容不解析" % self.tag)
        self.buf += data
        if self.tls:
            self.buf = b""
            return
        while len(self.buf) >= 8:
            total, cmd, seq = struct.unpack_from(">HHI", self.buf[:8])
            if total < 6 or total > 60000:
                log("%s 非法长度 %d，丢弃缓冲" % (self.tag, total))
                self.buf = b""
                return
            need = 2 + total
            if len(self.buf) < need:
                return
            payload = self.buf[8:need]
            self.buf = self.buf[need:]
            log("%s cmd=%-6d seq=%-5d len=%d hex=%s"
                % (self.tag, cmd, seq, len(payload), payload[:4096].hex()))
            if cmd in WATCH_CMDS:
                watch(self.tag, cmd, seq, payload)


def utf8_runs(b):
    """从二进制里挑出连续的 UTF-8 可读段，方便肉眼认昵称。"""
    out = []
    cur = bytearray()
    i = 0
    while i < len(b):
        c = b[i]
        n = 0
        if 0x20 <= c < 0x7F:
            n = 1
        elif 0xC2 <= c <= 0xDF:
            n = 2
        elif 0xE0 <= c <= 0xEF:
            n = 3
        elif 0xF0 <= c <= 0xF4:
            n = 4
        if n and i + n <= len(b):
            try:
                ch = b[i:i + n].decode("utf-8")
                cur += b[i:i + n]
                i += n
                continue
            except Exception:
                pass
        if len(cur) >= 2:
            out.append(cur.decode("utf-8", "replace"))
        cur = bytearray()
        i += 1
    if len(cur) >= 2:
        out.append(cur.decode("utf-8", "replace"))
    return out


def watch(tag, cmd, seq, payload):
    line = "[%s] %s cmd=%d seq=%d len=%d\n  hex=%s\n  utf8=%s\n" % (
        time.strftime("%H:%M:%S"), tag, cmd, seq, len(payload),
        payload.hex(), utf8_runs(payload))
    with lock:
        with open(WATCH, "a", encoding="utf-8") as f:
            f.write(line)
        with open(os.path.join(HERE, "logs", "cmd_%d.bin" % cmd), "ab") as f:
            f.write(struct.pack(">HHI", len(payload), seq, int(time.time()) & 0xFFFFFFFF))
            f.write(payload)

def pipe(src, dst, tag):
    sp = FrameSplitter(tag)
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dump(tag, data)
            sp.feed(data)
            dst.sendall(data)
    except Exception as e:
        log("%s 断开 %s" % (tag, e))
    finally:
        try: dst.shutdown(socket.SHUT_WR)
        except Exception: pass

def handle(c, addr):
    log("连接来自 %s:%d -> 上游 %s:%d" % (addr[0], addr[1], *UPSTREAM))
    try:
        u = socket.create_connection(UPSTREAM, timeout=10)
    except Exception as e:
        log("上游连接失败：%s" % e)
        c.close()
        return
    u.settimeout(None); c.settimeout(None)
    t1 = threading.Thread(target=pipe, args=(c, u, "C->S"), daemon=True)
    t2 = threading.Thread(target=pipe, args=(u, c, "S->C"), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()
    try: c.close()
    except Exception: pass
    try: u.close()
    except Exception: pass
    log("连接结束")

def main():
    log("relay 启动：监听 %s:%d -> %s:%d" % (LISTEN[0], LISTEN[1], *UPSTREAM))
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(LISTEN)
    s.listen(32)
    while True:
        c, addr = s.accept()
        threading.Thread(target=handle, args=(c, addr), daemon=True).start()

if __name__ == "__main__":
    main()
