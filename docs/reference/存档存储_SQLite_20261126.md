# 角色存档：SQLite 后端（2026-11-26）

## 一句话

角色存档从「一人一个 `player_state*.json` 整份覆盖」改成「一个账号一行，存在
`server_state.db`」。业务代码只改了两个函数（`load_state` / `save_state`），
背包/装备/任务的读写逻辑一行没动。

## 为什么换

| 问题 | 旧 JSON | 现在 |
|---|---|---|
| 同一账号双开/后台代改 | 后写的整份盖掉先写的，丢改动 | `rev` 乐观锁，冲突有日志；写永不半截 |
| 账号 → 文件名 | 字符串拼路径，主号还特殊化成 `player_state.json` | 主键就是账号，没有文件名规则 |
| 统计「几个角色/谁在线」 | 把目录所有 JSON 读一遍 | `SELECT` 一下 |
| 单条改动 | 整份 10~30KB 重写 + fsync | 一行 UPDATE（仍是最多 30KB，但只有一行） |
| 后台展示 | `player_state.json` 常是主号/旧快照 | 直接查列 |

## 表结构

```sql
CREATE TABLE player (
    acct TEXT PRIMARY KEY,       -- 账号（主号 1784366；空账号按主号处理）
    name TEXT, level INT, cls INT, sex INT, exp INT, exp_need INT,
    scene INT, x INT, y INT,     -- 最后下线地点
    bind_copper INT,             -- 绑定铜钱（旧字段名 coin）
    bind_gold INT,               -- 绑定元宝（旧字段名 bind_yuanbao）
    copper INT,                  -- 铜钱（旧字段名 tongqian）
    gold INT,                    -- 元宝（旧字段名 yuanbao）
    vip INT, title INT, bag_count INT,
    rev INT NOT NULL DEFAULT 0,  -- 乐观锁版本
    data TEXT NOT NULL,          -- 完整存档 JSON（背包/装备/妖灵/任务…）
    created_at TEXT, updated_at TEXT
);
CREATE TABLE online (acct TEXT PRIMARY KEY, cid INT, since TEXT, seen TEXT);
CREATE TABLE meta   (key TEXT PRIMARY KEY, value TEXT);
```

设计取舍：**高频/要查询的字段拆列，复杂结构继续放 `data`**。
`data` 里的键与旧 `player_state*.json` 完全一致（`state_dict()` 没改），
所以「从库里导出一份 JSON」就是旧格式存档，可以直接回滚。

`PRAGMA journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=5000`。
写是单行事务，掉电最多丢最后一条未提交事务，不会出现半截 JSON。

## 代码入口

| 位置 | 作用 |
|---|---|
| `state_store.py` | 全部 SQL：`load/save/exists/delete/count/accounts/updated`、`snapshot`、`online_*` |
| `local_server3.py: read_state_payload()` | 读档统一入口（库 → 懒迁移老 JSON → JSON 后端） |
| `local_server3.py: save_state()` | 落盘统一入口（备份 → 写库，失败重试 3 次 → JSON 兜底） |
| `local_server3.py: backup_current()` | 写前快照，后端无关，规则仍是「10 分钟一份、每人 20 份」 |
| `local_server3.py: state_exists()` | `switch_account` 判断「这号有没有角色」 |
| `admin_server.py: db_state()` | 后台数值源：`status.json` → 库 → 老 JSON |

`Player.rev`（`player.py`）保存读出来的版本号，落盘时带回。
`Player.__slots__` 已经加上 `rev`，**别再往 `Player` 上加运行时字段而不加 slots**。

## 三个开关

```bash
KD_STATE_BACKEND=sqlite   # 默认；=json 回退旧文件存档（救急/对照用）
KD_STATE_DB=<path>        # 换库文件（测试、备份演练用）
KD_STATE_STRICT=1         # rev 冲突时抛 Stale 而不是「最后写入者胜」
```

回退方法：停服 → `set KD_STATE_BACKEND=json` → 起服。
老 JSON 文件一直没删，回退后读到的就是迁移前最后一次内容。

## 迁移

```bash
python work/migrate_state_to_sqlite.py --dry     # 先看会导哪些账号、等级/背包/货币
python work/migrate_state_to_sqlite.py           # 导入（库里已有的跳过）
python work/migrate_state_to_sqlite.py --force   # 以 JSON 为准覆盖（推荐在停服后跑）
```

规则：

- `player_state.json` → 账号 `1784366`
- `player_state_<账号>.json` → 账号 `<账号>`
- 导入后逐账号核对「等级/经验/职业/四项货币/背包件数/装备槽」，不一致记失败
- 覆盖前自动给库里旧行留快照（`backups/`）

**懒迁移**：新服第一次读到某账号时，如果库里没有、磁盘上有老 JSON，会当场导入并打日志
`[state] 老存档 player_state_xxx.json 已迁入 SQLite`。所以就算不跑脚本，老号登录也会自动搬过来。

**JSON 比库新**：写库连续失败时会回退写 JSON；下次读档发现 JSON 的 mtime 比
`updated_at` 新，就以 JSON 为准回灌。这是兜底，不是常态（日志里会写明）。

## 并发与冲突

```
读档  rev=7 ──> 玩家操作 ──> 落盘 WHERE rev=7 → 成功，rev=8
                                  └─ 若库里已是 8：默认记日志后按最后写入者落盘（rev=9）
                                     KD_STATE_STRICT=1 时抛 Stale，save_state 重读后重写
```

本地服同一账号双开本质是同一个玩家，所以默认「最后写入者胜」而不是拒绝写入——
拒绝会让玩家的一次操作静默丢失。冲突一定会在 `server.log` 留一行 `rev 冲突`。

## 运维

- GM `存档`：看当前角色后端/rev/等级/背包/更新时间
- GM `备份`：立刻强制留一份快照（不吃 10 分钟节流）
- 后台 `GET /api/accounts`：账号列表（等级/货币/背包件数/rev/更新时间）+ 当前在线表
- 手工查库：

```bash
python -c "import state_store as S;[print(r) for r in S.accounts()]"
```

## 回归

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `work/test_state_store.py` | 建行/读回/rev/冲突/隔离/重启/懒迁移/闸门/快照 | 19/19 |
| `work/test_state_guard.py` | 真机跑着的服：未登录不写、无角色不建行、登录不改内容 | 8/8 |
| `work/test_account_flow.py phase1/phase2` | 建角→保存、老号加载、双号在线、重启后一致 | 全过 |

`work/test_*.py` / `probe_*.py` 默认会被 `state_store` 识别为自检脚本，
自动改用系统临时目录的 `kd_test_state_<脚本名>.db`，**不会污染正式库**。
需要看正式库的脚本（`test_state_guard` / `test_account_flow`）显式设了
`KD_STATE_DB=<项目>/server_state.db`。

## 注意

- `server_state.db` 是玩家数据，已在 `.gitignore`，**不要提交**。
- `player_state*.json` 暂时保留：回滚用 + 懒迁移的源。确认稳定一个版本后再谈清理。
- 备份仍在 `backups/`，SQLite 模式导出的是 JSON（可以直接 `state_store.save(acct, d)` 塞回去）。
