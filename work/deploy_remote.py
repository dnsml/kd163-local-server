# -*- coding: utf-8 -*-
"""把部署包推上 Linux 服务器（paramiko，密码走环境变量）。

用法：
    set KD_PASS=...            # Windows cmd；bash 用 export
    python work/deploy_remote.py probe
    python work/deploy_remote.py upload [zip路径]
    python work/deploy_remote.py install
    python work/deploy_remote.py start
    python work/deploy_remote.py status
    python work/deploy_remote.py verify

环境变量：
    KD_HOST  默认 129.204.52.72
    KD_USER  默认 root
    KD_PASS  必填
    KD_DIR   默认 /opt/kd163
"""
import os
import sys
import time

import paramiko

HOST = os.environ.get("KD_HOST", "129.204.52.72")
USER = os.environ.get("KD_USER", "root")
PASS = os.environ.get("KD_PASS", "")
APPDIR = os.environ.get("KD_DIR", "/opt/kd163")


def connect():
    if not PASS:
        sys.exit("缺 KD_PASS")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASS, timeout=15, banner_timeout=20,
              auth_timeout=20, look_for_keys=False, allow_agent=False)
    return c


def run(c, cmd, quiet=False, timeout=600):
    _in, out, err = c.exec_command(cmd, timeout=timeout, get_pty=False)
    o = out.read().decode("utf-8", "ignore")
    e = err.read().decode("utf-8", "ignore")
    rc = out.channel.recv_exit_status()
    if not quiet:
        if o.strip():
            print(o.rstrip())
        if e.strip():
            print("[stderr] " + e.rstrip())
    return rc, o, e


def probe(c):
    print("== 系统 ==")
    run(c, "cat /etc/os-release | head -3; uname -r; nproc; free -m | head -2; df -h / | tail -1")
    print("== python ==")
    run(c, "python3 -V 2>&1; which python3")
    print("== 端口占用 ==")
    run(c, "ss -lntp 2>/dev/null | grep -E ':(80|9000|8899)\\b' || echo '80/9000/8899 都空'")
    print("== 防火墙 ==")
    run(c, "(ufw status 2>/dev/null || echo 'no ufw'); "
           "(systemctl is-active firewalld 2>/dev/null || echo 'no firewalld'); "
           "iptables -S 2>/dev/null | head -8")
    print("== 已有目录 ==")
    run(c, "ls -ld %s 2>/dev/null || echo '%s 不存在'; id kd163 2>/dev/null || echo '无 kd163 用户'" % (APPDIR, APPDIR))


def upload(c, zpath):
    size = os.path.getsize(zpath)
    print("上传 %s（%.1f MB）..." % (zpath, size / 1048576.0))
    t0 = time.time()
    sftp = c.open_sftp()
    remote = "/tmp/" + os.path.basename(zpath)
    done = [0]

    def cb(sent, total):
        pct = sent * 100 // total if total else 0
        if pct >= done[0] + 10 or sent == total:
            done[0] = pct
            print("   %d%%  %.1f/%.1f MB  %.0fs"
                  % (pct, sent / 1048576.0, total / 1048576.0, time.time() - t0))

    sftp.put(zpath, remote, callback=cb)
    sftp.close()
    print("done in %.0fs -> %s" % (time.time() - t0, remote))
    return remote


def install(c, remote):
    print("== 建用户/目录 ==")
    run(c, "id kd163 >/dev/null 2>&1 || useradd -r -m -s /bin/bash kd163; "
           "mkdir -p %s" % APPDIR)
    print("== 解压 ==")
    run(c, "command -v unzip >/dev/null || (apt-get update -qq && apt-get install -y -qq unzip); "
           "cd %s && unzip -oq %s && cp -r kd163-server-*/* . && rm -rf kd163-server-* && ls" % (APPDIR, remote))
    print("== 权限 ==")
    run(c, "chown -R kd163:kd163 %s && chmod +x %s/start.sh 2>/dev/null; "
           "ls -l %s | head -20" % (APPDIR, APPDIR, APPDIR))


def start(c):
    print("== systemd ==")
    run(c, "cp %s/deploy/kd163-*.service /etc/systemd/system/ 2>/dev/null; "
           "systemctl daemon-reload; "
           "systemctl enable --now kd163-http kd163-game kd163-admin 2>&1 | tail -3" % APPDIR)
    time.sleep(3)
    run(c, "systemctl is-active kd163-http kd163-game kd163-admin")


def status(c):
    run(c, "systemctl status kd163-http kd163-game kd163-admin --no-pager 2>&1 | "
           "grep -E 'Active:|Main PID|error|Error' || true")
    run(c, "ss -lntp | grep -E ':(80|9000|8899)\\b' || echo '没有监听'")
    run(c, "tail -5 %s/logs/game.out 2>/dev/null; tail -5 %s/logs/admin.out 2>/dev/null" % (APPDIR, APPDIR))


def verify(c):
    print("== 本机端口 ==")
    run(c, "curl -s -o /dev/null -w 'http80=%{http_code}\\n' --max-time 5 http://127.0.0.1/ ; "
           "curl -s -o /dev/null -w 'admin=%{http_code}\\n' --max-time 5 http://127.0.0.1:8899/ ; "
           "curl -s -X POST http://127.0.0.1:8899/api/login -H 'Content-Type: application/json' "
           "-d '{\"user\":\"admin\",\"pass\":\"kd163admin\"}' --max-time 5")
    print("== 公网端口 ==")
    for p in (80, 9000, 8899):
        rc, o, _ = run(c, "timeout 3 bash -c '</dev/tcp/129.204.52.72/%d' 2>&1 && echo open || echo closed" % p, quiet=True)
        print("  %d: %s" % (p, o.strip()))


def main():
    step = sys.argv[1] if len(sys.argv) > 1 else "probe"
    c = connect()
    try:
        if step == "probe":
            probe(c)
        elif step == "upload":
            import glob
            z = sys.argv[2] if len(sys.argv) > 2 else sorted(glob.glob("dist/kd163-server-*.zip"))[-1]
            remote = upload(c, z)
            print("REMOTE=%s" % remote)
        elif step == "install":
            install(c, sys.argv[2] if len(sys.argv) > 2 else "/tmp/kd163-server.zip")
        elif step == "start":
            start(c)
        elif step == "status":
            status(c)
        elif step == "verify":
            verify(c)
        elif step == "exec":
            run(c, sys.argv[2])
        elif step == "put":
            local, remote = sys.argv[2], sys.argv[3]
            sftp = c.open_sftp()
            sftp.put(local, remote)
            sftp.close()
            print("已上传 %s -> %s" % (local, remote))
        else:
            print("未知步骤")
    finally:
        c.close()


if __name__ == "__main__":
    main()
