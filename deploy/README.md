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
*.py  account_policy.py  state_store.py
res_cfg/  mirror/  templates.json  replay_sc.json  tpl_extra.json  admin.html
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
sudo cp deploy/kd163-game.service deploy/kd163-http.service /etc/systemd/system/
# 资源服广播地址改成你的公网 IP / 域名
sudo sed -i 's/^Environment=KD163_HOST=.*/Environment=KD163_HOST=1.2.3.4/' /etc/systemd/system/kd163-http.service
sudo systemctl daemon-reload
sudo systemctl enable --now kd163-game kd163-http
sudo systemctl status kd163-game kd163-http --no-pager
```

日志：`journalctl -u kd163-game -f`（服务端自己的 `logs/server.log` 也照旧写）。

## 4. 账号白名单（公网必做）

本服没有密码：`/auth` 默认对任何用户名发令牌。上线前至少开白名单：

```bash
cd /opt/kd163
sudo -u kd163 python3 work/allow_accounts.py add 1784366 900000102
sudo -u kd163 python3 work/allow_accounts.py list
```

- 名单外账号：`/auth` 直接 403，游戏登录（17/20）也会被拒；
- 改名单不用重启，下一次登录就生效；
- 想临时全开：`allow_accounts.py off`（删掉 `accounts.json`）；
- 也可以用环境变量：`KD_ALLOW_ACCOUNTS=1784366,900000102`（`*` = 全开）。

新玩家流程：管理员先 `add <账号>` → 玩家登录 → 弹建角 → 建完就能玩。

也可以在网页后台（本机 `http://127.0.0.1:8899`）的「账号管理」里做同样的事：
建角、删角色、踢下线、逐账号放行/关闭白名单。后台只绑 127.0.0.1，远程用 SSH 隧道。

## 5. 备份

```bash
sqlite3 /opt/kd163/server_state.db ".backup '/var/backups/kd163-$(date +%F-%H%M).db'"
```

建议 cron 每 30 分钟一次，保留 7 天。`backups/` 里还有每次写盘前的 JSON 快照。

## 6. 防火墙

只开 `80/tcp`、`9000/tcp`。**不要**开 8899（后台只绑 127.0.0.1），
需要远程看后台就用 SSH 隧道：`ssh -L 8899:127.0.0.1:8899 user@server`。
