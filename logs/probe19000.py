import socket, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 19000)); s.listen(5)
s.settimeout(25)
print("listening 19000", flush=True)
try:
    c, a = s.accept()
    print("GOT CONN from", a, flush=True)
    c.settimeout(3)
    try:
        d = c.recv(300)
        print("first %dB: %s" % (len(d), d[:60].hex()), flush=True)
    except Exception as e:
        print("recv:", e, flush=True)
    c.close()
except socket.timeout:
    print("no conn in 25s", flush=True)
