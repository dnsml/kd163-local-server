我把你上传的 `battle.py` 认真看了一遍，也做了 Python 语法检查。

**结论：这套确实有问题，但不是整个战斗内核都废了。** 文件可以正常编译，问题主要集中在战斗数值、出手顺序和协议演出之间没有完全统一。

你现在这版已经比单纯复制固定战斗包复杂很多了，但有几处会直接造成“伤害不对、技能没效果、怪死了还攻击、明明打不过却显示可胜”等问题。

## 一、最严重的几个问题

### 1\\. 面板攻击力可能被重复放大

代码里有：

```python
self.panel_based = bool(at)

_gain = ATK_SCALE if self.panel_based else (
    1.0 + DMG_LEVEL_GAIN * self.lv
)

self.base_deal = atk_avg * _gain * self.crit_mult
```

而 `ATK_SCALE` 默认是 `3.0`。

问题是，文件注释一方面说 `attrs` 是从 29 包取得的真实面板属性，另一方面又说真实面板攻击力比本地高约 3.7 倍，所以要再乘 3。

这两种口径不能混用。

如果 `attrs[\"attack_min\"]` 和 `attrs[\"attack_max\"]` 已经是角色真实面板攻击力，再乘 3 就可能把伤害放大过头。

另外，`self.panel_based = bool(at)` 也不严谨：只要传入一个属性，比如只有 `hp_max`，就会触发 `ATK_SCALE=3`，即使攻击力实际上回退到了 `Levelup.xml`。

相关代码：fileciteturn1file0L746-L775

**建议：先明确 `attrs` 到底是裸属性、真实面板属性，还是本地面板属性。攻击倍率只能在一种明确口径下使用。**

---

### 2\\. 技能目前很可能只是“换动画”，没有真正改变伤害

你已经读取了技能表：

```python
self.skill_id = skill_id_of(payload)
_sk = self.tables.skill.get(str(self.skill_id)) or {}
self.aoe = (_sk.get(\"target_type\") == 5)
```

但实际玩家伤害仍然是：

```python
d = min(max(1, self.deal), left)
```

这里没有使用：

```python
damage_percent
consume_mp
```

也就是说，当前代码会识别技能是单体还是群体，但伤害仍然使用同一个 `self.deal`。

举个例子：

- 普通攻击：伤害 300
- 技能 A：伤害倍率 150%
- 技能 B：伤害倍率 250%

按照当前这段逻辑，三个技能的服务端伤害都可能是 300。

而且 `self.player_mp` 初始化了，却没有在战斗过程中扣除技能消耗。

相关代码：fileciteturn1file0L974-L1001

这会让技能系统看起来有功能，实际上数值没有真正接入。

---

### 3\\. 出手顺序计算了，但实际结算没有完全按顺序执行

这是我认为最容易导致战斗表现异常的地方。

你在 `on_action()` 开头计算了：

```python
order = self.turn_order()
```

但随后先把玩家和宠物的伤害全部结算：

```python
self.mob_hp[_u] = left - d
```

然后才开始处理怪物攻击。

这意味着：

**服务器实际上先扣了玩家和宠物的伤害，再根据敏捷决定怪物是否还能攻击。**

你用这段逻辑补救：

```python
if self.mob_hp.get(uid, 0) <= 0 and \\
        self.unit_agile.get(uid, self.mob_agile) <= self.agile:
    continue
```

但它只能近似处理部分情况。

例如：

1. 怪物敏捷比玩家高。
2. 怪物应该先攻击。
3. 怪物先把玩家打死。
4. 玩家本回合的伤害却已经提前扣到怪物身上。

最后甚至可能出现玩家已经死亡，但怪物也被提前打死，服务器优先判定胜利的情况。

判定代码是：

```python
if all(v <= 0 for v in self.mob_hp.values()):
    self.over, self.result = True, \"win\"
elif self.player_hp_left <= 0:
    self.over, self.result = True, \"lose\"
```

这就可能出现**死后反杀**。

相关代码：fileciteturn1file0L963-L1020 fileciteturn1file0L1031-L1038

正确方向应该是：

```text
按照敏捷排序
    ↓
执行第一个行动者
    ↓
扣除对应血量
    ↓
判断目标是否死亡
    ↓
执行下一个行动者
    ↓
生成与实际结算一致的 148 条目
```

不能先把伤害全部算完，再用出手顺序补救。

---

### 4\\. `can_win` 的判断不够准确

代码：

```python
self.fight_rounds = max(
    1, min(self.rounds_to_kill, _cap)
)

self.can_win = self.rounds_to_die >= self.fight_rounds
```

这里存在一个逻辑漏洞。

假设：

- 击杀怪物需要 100 回合
- 玩家可以扛 70 回合
- 战斗上限是 60 回合

那么：

```text
fight_rounds = 60
rounds_to_die = 70
can_win = True
```

但实际上玩家 60 回合还没打死怪，最终只能力竭。

所以 `can_win` 可能显示“可胜”，实际却只能平局或失败。

相关代码：fileciteturn1file0L811-L821

---

### 5\\. 宠物伤害固定为 338，和真实宠物属性没有关联

代码里：

```python
PET_DMG = 338
```

然后：

```python
self.pet_dmg = PET_DMG if self.pet else 0
```

也就是说，只要带宠物，服务端就默认宠物每次造成 338 点伤害。

但不同宠物、等级、成长、技能、装备可能都会影响宠物伤害。

你文件注释里也记录了真服宠物伤害出现过 `-338` 和 `-642` 等不同值。固定 338 只能作为当前测试样本的拟合值，不能作为通用宠物战斗模型。

相关代码：fileciteturn1file0L59-L65 fileciteturn1file0L805-L815

---

## 二、还有一个容易被忽略的问题：代码注释和实际逻辑已经不一致

例如文件开头写的是：

```text
等级压制：
gap = clamp(1 + 0.020 * (mob_lv - lv), 0.60, 1.60)
```

但实际代码已经改成：

```python
_ratio = float(self.mob_lv) / max(1.0, float(self.lv))

self.gap = max(
    0.05,
    min(1.50, _ratio * _ratio)
)
```

也就是怪物等级与玩家等级的平方比例。

此外，`_split_take()` 的注释说主怪占 64%，但默认 `MOB_HIT_MAIN` 是 `0.766`，实际主怪占 76.6%。

这些不一定都是功能错误，但说明你已经经历了很多轮修改，**旧设计说明、当前代码和实测结论没有统一维护**。

相关代码：fileciteturn1file0L24-L29 fileciteturn1file0L777-L787 fileciteturn1file0L834-L844

---

## 三、我建议你现在先不要继续往里面加功能

你现在的战斗内核已经有：

- 多怪战斗
- 单体和群体目标
- 宠物出手
- 敏捷排序
- 真实血量模式
- 战斗结束判定
- 掉落和经验结算

这些功能方向没错。

但现在最大的问题是：**每个功能单独看似乎成立，组合起来却不一定成立。**

我建议先按这个顺序修：

| 优先级 | 先修什么 | 原因 |
|--------|--------|--------|
| P0 | 统一攻击属性和倍率口径 | 防止伤害过高或过低 |
| P0 | 改成真正逐行动者结算 | 防止死后反击、出手顺序错乱 |
| P1 | 接入技能倍率和 MP 消耗 | 让技能不只是动画 |
| P1 | 修正 `can_win` 和回合上限判断 | 避免错误预测胜负 |
| P2 | 宠物伤害改为属性计算 | 避免所有宠物固定伤害 |
| P2 | 清理旧注释和废弃模型 | 避免以后改错方向 |

我的判断是：**这份 `battle.py` 不是完全错误，而是已经到了需要“整理模型、统一结算”的阶段。**

你之前一直在解决“客户端到底需要什么包”的问题。现在这份代码其实已经有了不少协议层成果，但战斗规则还混着抓包拟合值、客户端演出值和服务端真实数值。

下一步最值得做的是先把 `on_action()` 重构成真正按敏捷逐个结算的流程，再用你已有的真服 148 抓包逐条对照。这样比继续增加更多环境变量和固定伤害值更有价值。