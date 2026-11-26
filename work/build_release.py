# -*- coding: utf-8 -*-
"""打部署包：只挑运行时要的文件，生成 dist/kd163-server-<时间>/ 和同名 zip。

用法：
    python work/build_release.py                 # 含资源包（res_update/mhsj_res.zip）
    python work/build_release.py --no-res        # 不含 110MB 资源包（客户端已装好资源时）
    python work/build_release.py --no-zip        # 只生成目录，不压缩
    python work/build_release.py --out D:/dist   # 换输出目录

包里有什么、为什么，见生成的《部署说明.md》和 manifest.txt。
"""
import argparse
import hashlib
import os
import shutil
import time
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行必需的模块（没有这些起不来）
RUNTIME_PY = [
    "local_server3.py",     # 游戏服：登录/场景/任务/背包/战斗/GM
    "local_http.py",        # 资源服：server-list.xml、assets、资源包
    "admin_server.py",      # 网页后台
    "state_store.py",       # 存档（SQLite）
    "account_policy.py",    # 账号/密码/白名单
    "admin_auth.py",        # 后台管理员登录
    "player.py",
    "battle.py",
    "bag_proto.py",
    "grant.py",
    "item_rules.py",
    "equip_attr.py",
    "pet_proto.py",
    "wing_proto.py",
]

ROOT_FILES = [
    "templates.json",
    "replay_sc.json",
    "tpl_extra.json",
    "admin.html",
    "admin_login.html",
]

DIRS = ["res_cfg", "mirror"]
CERT_FILES = ["patch/exact.pem", "patch/exact.key"]
OPTIONAL_BIG = ["res_update/mhsj_res.zip"]

QUICKSTART = """# KD163 服务端部署包

## 一、这是什么

《口袋修仙》自建服务端的**运行包**：游戏服（9000）、资源服（80）、网页后台（8899）。
纯 Python 标准库，**不需要 pip 安装任何东西**，Python 3.8+ 即可。

## 二、包里哪些文件是必需的

| 路径 | 作用 | 必需 |
|---|---|---|
| `local_server3.py` | 游戏服主程序（登录/场景/任务/背包/战斗/GM） | ✅ |
| `local_http.py` | 资源服（`/server-list.xml`、`/assets/**`、`/mhsj_res.zip`） | ✅ |
| `admin_server.py` | 网页后台（账号管理/GM/在线/日志） | ✅ |
| `state_store.py` | 角色存档（SQLite，`server_state.db` 自动生成） | ✅ |
| `account_policy.py` | 账号、密码、白名单（`accounts.json`） | ✅ |
| `admin_auth.py` | 后台管理员登录（`admin.json`） | ✅ |
| `player.py` `battle.py` `bag_proto.py` `grant.py` | 被主程序 import，缺一不可 | ✅ |
| `item_rules.py` `equip_attr.py` `pet_proto.py` `wing_proto.py` | 配置驱动：道具/属性/宠物/翅膀 | ✅ |
| `templates.json` `replay_sc.json` `tpl_extra.json` | 协议模板与回放数据 | ✅ |
| `res_cfg/` | Item/Quest/Scene/Levelup/Pet 等配置表 | ✅ |
| `mirror/` | 资源服要发的静态资源（assets、meta.xml） | ✅ |
| `admin.html` `admin_login.html` | 后台页面与登录页 | ✅ |
| `patch/exact.pem` `patch/exact.key` | 服务端证书，必须与改包 APK 内嵌的同一对 | ✅ |
| `res_update/mhsj_res.zip` | 110MB 完整资源包，客户端首次启动会下载 | ⭕ 建议 |
| `start.sh` / `start.bat` | 开服脚本 | ✅ |
| `deploy/*.service` | systemd 单元（Linux） | ⭕ 可选 |

**不包含**（运行时生成/本机数据，不要从旧环境拷）：
`server_state.db`、`accounts.json`、`admin.json`、`logs/`、`backups/`、
`player_state*.json`、`work/`、`tools/`、`docs/`。

## 三、开服

### Linux

```bash
chmod +x start.sh
sudo ./start.sh start        # 80 端口需要 root（或 CAP_NET_BIND_SERVICE）
./start.sh status
./start.sh stop
```

### Windows

```bat
start.bat
start.bat stop
start.bat status
```

启动后：

- 游戏服 `:9000`（TLS `/auth` + 游戏协议）
- 资源服 `:80`（`KD163_HOST` 可指定 server-list.xml 广播的地址）
- 网页后台 `http://127.0.0.1:8899`

## 四、账号

1. 打开后台 → 「新建游戏账号」→ 生成 8 位随机账号，**密码统一 123**；
2. 把账号发给玩家，玩家在客户端输入「账号 + 123」；
3. 玩家进游戏后自己弹建角，男女、昵称由玩家自己选；
4. 后台可以重置密码、删账号、删角色、踢下线。

第一次生成账号时，服务端会自动把**库里已有角色**写进 `allow`，不会把老玩家锁在门外。

## 五、后台登录（对外时）

后台默认只绑 `127.0.0.1`，本机访问免登录。绑 `0.0.0.0` 对外时会强制登录，
没有 `admin.json` 会自动生成 `admin` + 12 位随机密码，打印在启动日志里：

```bash
tail -n 30 logs/admin.out
python3 work/admin_password.py set admin 你的密码   # 改密码
```

走 Nginx 反代要加环境变量 `KD_ADMIN_REQUIRE=1`，否则后台会以为来源是本机而跳过登录。

## 六、客户端

服务端地址和证书是**写进 APK** 的：

1. 用 `gen_cert_exact.py --cn <公网IP> --out patch/srv` 生成证书（只支持 IP）；
2. 服务端用这对证书启动；
3. 用 `patch_apk.py --host <公网IP> --cert patch/srv.pem --key patch/srv.key` 改包；
4. host 长度不能超过 14 字符（原地改写 dex）。

## 七、备份

```bash
sqlite3 server_state.db ".backup '/var/backups/kd163-$(date +%F-%H%M).db'"
```

`backups/` 里另有每次写盘前的 JSON 快照（每角色保留 20 份）。

## 八、安全提醒（务必看）

- **账号密码统一是 123**，等于没有密码；只适合小圈子/朋友服，公网公开服请改强密码；
- 游戏帧在 `/auth` 之后是**明文**，能抓包也能伪造；
- 后台密码走 HTTP 明文，公网请套 Nginx + HTTPS，或只用 SSH 隧道；
- 防火墙只开 `80/tcp`、`9000/tcp`，不要暴露 8899。

## 九、升级

覆盖 `*.py`、`admin*.html`、`res_cfg/`、`mirror/*.xml` 后执行 `./start.sh restart`。
**不要覆盖** `server_state.db`、`accounts.json`、`admin.json`、`logs/`、`backups/`。
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_file(src, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    shutil.copy2(src, dst)
    return dst


def copy_tree(src, dst):
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "dist"))
    ap.add_argument("--no-res", action="store_true", help="不打包 110MB 资源包")
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M")
    name = "kd163-server-%s" % stamp
    dest = os.path.join(a.out, name)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    missing = []

    for fn in RUNTIME_PY + ROOT_FILES:
        src = os.path.join(HERE, fn)
        if not os.path.exists(src):
            missing.append(fn)
            continue
        copy_file(src, dest)
    for d in DIRS:
        src = os.path.join(HERE, d)
        if not os.path.isdir(src):
            missing.append(d + "/")
            continue
        copy_tree(src, os.path.join(dest, d))
    for fn in CERT_FILES:
        src = os.path.join(HERE, fn)
        if not os.path.exists(src):
            missing.append(fn)
            continue
        copy_file(src, os.path.join(dest, "patch"))

    # 开服脚本与 systemd 单元
    for fn in ("start.sh", "start.bat"):
        src = os.path.join(HERE, "deploy", fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, fn))
    if os.path.isdir(os.path.join(HERE, "deploy")):
        os.makedirs(os.path.join(dest, "deploy"), exist_ok=True)
        for fn in os.listdir(os.path.join(HERE, "deploy")):
            if fn.endswith(".service"):
                shutil.copy2(os.path.join(HERE, "deploy", fn),
                             os.path.join(dest, "deploy", fn))

    # 资源包（可选）
    res_note = "未包含（--no-res）"
    for fn in OPTIONAL_BIG:
        src = os.path.join(HERE, fn)
        if a.no_res:
            continue
        if os.path.exists(src):
            os.makedirs(os.path.join(dest, "res_update"), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, fn))
            res_note = "已包含 %.0f MB" % (os.path.getsize(src) / 1048576.0)
        else:
            res_note = "源目录没有 res_update/mhsj_res.zip"

    with open(os.path.join(dest, "部署说明.md"), "w", encoding="utf-8") as f:
        f.write(QUICKSTART)
        f.write("\n---\n\n## 本包信息\n\n")
        f.write("- 打包时间：%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("- 资源包：%s\n" % res_note)
        f.write("- 缺文件：%s\n" % ("、".join(missing) if missing else "无"))

    # manifest
    rows = []
    for root, _dirs, files in os.walk(dest):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, dest).replace("\\", "/")
            rows.append((rel, os.path.getsize(p), sha256(p)))
    rows.sort()
    with open(os.path.join(dest, "manifest.txt"), "w", encoding="utf-8") as f:
        f.write("# 路径\t字节\tsha256\n")
        for rel, size, h in rows:
            f.write("%s\t%d\t%s\n" % (rel, size, h))

    total = sum(r[1] for r in rows)
    print("输出目录：%s" % dest)
    print("文件 %d 个，共 %.1f MB（资源包：%s）"
          % (len(rows), total / 1048576.0, res_note))
    if missing:
        print("！！缺少：%s" % "、".join(missing))

    if not a.no_zip:
        zpath = dest + ".zip"
        print("压缩中…")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for root, _dirs, files in os.walk(dest):
                for fn in files:
                    p = os.path.join(root, fn)
                    z.write(p, os.path.join(name, os.path.relpath(p, dest)))
        print("zip：%s（%.1f MB）" % (zpath, os.path.getsize(zpath) / 1048576.0))
        return zpath
    return dest


if __name__ == "__main__":
    main()
