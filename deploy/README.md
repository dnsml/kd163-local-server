# Linux 部署（systemd）

前提：`python3 --version` ≥ 3.8，**不需要 pip 安装任何东西**（纯标准库）。

## 1. 建用户与目录

```bash
sudo useradd -r -s /usr/sbin/nologin -d /opt/kd163 kd163
sudo mkdir -p /opt/kd163
sudo chown kd163:kd163 /opt/kd163
```

把仓库内容拷到 `/opt/kd163`（至少要有）：

```
*.py  account_policy.py  state_store.py  admin_auth.py
res_cfg/  mirror/  templates.json  replay_sc.json  tpl_extra.json
admin.html  admin_login.html
patch/exact.pem  patch/exact.key      # 或你自己生成的那一对
```

`server_state.db` / `logs/` / `backups/` 会自动创建，不用带。

## 2. 先手工跑一次

```bash
cd /opt/kd163
python3 local_server3.py 9000 patch/exact.pem patch/exact.key
# 另一个终端
python3 local_http.py 80 127.0.0.1
```

两个都正常监听再往下。80 端口非 root 需要 `CAP_NET_BIND_SERVICE`（unit 里已配）。

## 3. 装 systemd

```bash
sudo cp deploy/kd163-game.service deploy/kd163-http.service deploy/kd163-admin.service /etc/systemd/system/
# 资源服广播地址改成你的公网 IP / 域名
sudo sed -i 's/^Environment=KD163_HOST=.*/Environment=KD163_HOST=1.2.3.4/' /etc/systemd/system/kd163-http.service
sudo systemctl daemon-reload
sudo systemctl enable --now kd163-game kd163-http kd163-admin
sudo systemctl status kd163-game kd163-http kd163-admin --no-pager
```

日志：`journalctl -u kd163-game -f`（服务端自己的 `logs/server.log` 也照旧写）。

## 4. 后台登录（公网必做）

后台默认只绑 `127.0.0.1`，本机访问不要求登录。**只要对外（0.0.0.0）就会强制登录**：
没有 `admin.json` 时自动生成 `admin` + 12 位随机密码，并打印在启动日志里。

```bash
journalctl -u kd163-admin -n 40 --no-pager | grep -A6 已生成
sudo -u kd163 python3 work/admin_password.py set admin 你的密码
```

远程访问两种方式：

1. SSH 隧道（最简单）：`ssh -L 8899:127.0.0.1:8899 user@server`，本地开 `http://127.0.0.1:8899`；
2. Nginx 反代 + HTTPS：反代到 `127.0.0.1:8899`，并给 unit 加
   `Environment=KD_ADMIN_REQUIRE=1`，否则后台会以为来源是本机而跳过登录。

> 后台密码走的是明文 HTTP（除 Nginx 那层），公网务必套 HTTPS。

## 5. 游戏账号（随机账号 + 随机密码）

后台「账号管理 → 新建游戏账号」会生成 **8 位随机账号 + 10 位随机密码**（无规律，
去掉了 0/O/1/I/l 这类易混字符），文件里只存 pbkdf2 哈希。玩家拿这组账号密码登录，
客户端会自己弹建角，男女和昵称由玩家自己选。

```bash
# 命令行也可以建（打印一次账号密码）
sudo -u kd163 python3 -c "import account_policy as A;a,p=A.new_account();print(a,p)"
```

删账号只删登录资格，角色存档还在；要一起清就再点「删角色」。

## 6. 账号白名单（公网必做）

账号可以带密码（后台生成），也可以只放行不设密码（老账号/主号）：

- 有密码的账号：`/auth` 校验密码，错了 401；`/auth` 过后 6 小时内才能进游戏；
- 只有白名单的账号：`/auth` 不校验密码，行为和以前一致；
- 名单外账号：`/auth` 403，登录 17/20 也被拒，且不建存档；
- `allow_accounts.py off` = 放开白名单（不删文件，账号密码还在）。

```bash
cd /opt/kd163
sudo -u kd163 python3 work/allow_accounts.py add 1784366 900000102
sudo -u kd163 python3 work/allow_accounts.py list
```

新玩家流程：后台点「新建游戏账号」→ 把账号密码发给玩家 → 玩家登录 → 自己建角。

## 7. 备份

```bash
sqlite3 /opt/kd163/server_state.db ".backup '/var/backups/kd163-$(date +%F-%H%M).db'"
```

建议 cron 每 30 分钟一次，保留 7 天。`backups/` 里还有每次写盘前的 JSON 快照。

## 8. 防火墙

只开 `80/tcp`、`9000/tcp`。**不要**开 8899（后台只绑 127.0.0.1），
需要远程看后台就用 SSH 隧道：`ssh -L 8899:127.0.0.1:8899 user@server`。
