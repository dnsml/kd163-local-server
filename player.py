"""一个在线角色 = 一个 Player 对象。

以前 local_server3.py 把等级/背包/装备/货币全写成模块级全局变量，只要开第二个
客户端，两个号就会互相覆盖（A 捡的东西出现在 B 身上、B 的元宝把 A 刷掉）。
现在改成 threading.local 保存「当前线程正在处理的角色」，所有读写都走 cur()：

    from player import cur, new, ctx
    new()                 # 连接建立时造一份空的
    cur().level           # 任何地方都能拿到当前角色
    with ctx(other):      # 临时切到另一个角色（GM 后台 / 多连接推送用）
        ...

列表包装（coin_bound / vip_level ...）保留原来的 [x] 形态，就是为了让
local_server3.py 里所有 `XXX[0] += n` 这类写法原样可用，改引用不改表达式。
"""
import threading


class Player(object):
    __slots__ = (
        "acct", "cid", "authed", "loaded", "saved_sig",
        "name", "sex", "has_char",
        "cls", "level", "exp", "exp_need", "mp_add",
        "coin_bound", "bind_yuanbao", "tongqian", "yuanbao", "yueli",
        "vip_level", "vip_until",
        "bag", "equip", "enhance", "wing", "wardrobe", "fashion",
        "pets",
        # 当前展示宠物的形象 id（Pet.xml 的 item id，默认 10000 功夫熊猫）。
        # 用「宠物形象卡」会改这里，947 的 off48 直接写这个值。
        "pet_skin",
        # 已拥有/可切换的宠物形象 id 列表（默认 [10000]）。
        # 958 面板要回 959 = [n][n×varint skin]，960 提交后按这张表校验；
        # 用掉一张形象卡就往里追加，缺这条 958 只能回 1 个形象，客户端会转圈。
        "pet_skin_own",
        # 当前坐骑形象 id（Horse.xml 的 item id）。0 = 没坐骑（30 尾部的坐骑块整块不写）。
        "mount",
        # 坐骑穿的形象卡（item id），0 = 没换过形象。
        "mount_skin",
        "spirit_bag", "spirit_slot",
        # ---- 宠物「仙府探秘」技能系统（2026-11-28 线上服抓包定案）----
        # 秘籍库：[[技能id, 经验], ...]，顺序＝962 第一组的顺序（客户端按这个画网格）
        "pet_skill_bag",
        # 已装技能栏：最多 4 个技能 id，顺序＝962 第二组＝947 尾部 4 个 id
        "pet_skill_slots",
        # 每日免费探秘剩余次数（默认 3），配 pet_skill_day 按自然日重置
        "pet_skill_free", "pet_skill_day",
        # 洗髓四条比例（体/力/防/巧，合计 100），写 947 的 k20~23
        "pet_qual",
        # 954 返回的预览比例；None = 没有待采用的洗髓结果
        "pet_qual_preview",
        # ---- 藏宝图（268/271）----
        # {背包格位: [场景, x, y]}：同一格重复查 268 必须回同一个点
        "treasure",
        # 秘籍库（1506）：[[秘籍id, 序号], ...]，挖宝掉到 k1=16 的秘籍时追加
        "miji",
        "quest_active", "quest_ready", "done_quests",
        "title",                 # 当前佩戴的称号 id（0=没有）；只有用掉称号卡才会有
        "title_own",             # 已拥有的称号 id 集合（真服会回「已拥有该称号」）
        "scene", "pos_x", "pos_y",
        "bag_sent",              # 上次推给客户端的背包快照，只用来算差异日志
        # 客户端 817 上报的「当前武器用的协议槽字节」（7 或 8）。
        # 武器在内存里永远存槽 8，但协议上 7/8 两个字节客户端都认，
        # 具体用哪个只有客户端自己知道 —— 817 一上报就记下来，
        # 之后 818/30 全按这个字节回，避免同一件武器回两个槽。
        "weapon_wire",
        # 存档版本号（SQLite player.rev）。读档时记下，落盘时带回去做乐观锁：
        # 同一个账号两个连接同时写，后写的能发现「我拿到的是旧版本」。
        "rev",
    )

    def __init__(self):
        self.acct = ""
        self.cid = 0
        self.authed = False        # 收到过 cmd=17 才算这个角色上线过（没上线的不许落盘）
        # loaded = 真的从磁盘读到了这个角色的存档。
        # 只有 authed 不够：登录了但角色还是 Player() 默认值的时候，
        # 一写盘就把真存档盖成 Lv10/空背包（2026-11-24 02:08 就是这么丢的）。
        self.loaded = False
        # 上次写盘内容的指纹，用来避免「值没变也写一遍」（顺带当 dirty 用）
        self.saved_sig = None
        # 角色名 / 性别 / 是否已建角。
        # ★ 2026-11-26：这里以前是一套「Lv10 演示号」默认值（有角色、10 级、
        #   4030 绑元、3 件演示道具）。只要有一条代码路径忘了读档/建角就直接 cur()，
        #   就会凭空冒出一个能进游戏的假角色，还会把真存档写坏（11-24 那份 Lv45 就是这么没的）。
        #   现在 Player() 只代表「一个还没初始化的连接」：
        #     has_char=False / Lv0 / 空背包 / 全 0 货币
        #   真正的角色只有两条路来：load_state()（读已有存档）或 create_character()
        #   （建角，内部走 reset_newbie()）。忘了初始化就只会是空号，不会变成演示号。
        self.name = ""
        self.sex = 1             # 1=男 2=女（客户端 create_role 只有男女两项）
        self.has_char = False    # 默认没有角色；建角/读档才会置 True
        # 职业 6 = 未转职（真服未转职号 151 的 b[2]=06，技能 60101/60201）
        self.cls = 6
        self.level = 0
        self.exp = 0
        self.exp_need = 0        # 0 表示「还没有角色」，升级循环里会判 exp_need > 0
        self.mp_add = [0]
        self.coin_bound = [0]
        self.bind_yuanbao = [0]
        self.tongqian = [0]
        self.yuanbao = [0]
        self.yueli = [0]
        self.vip_level = [0]
        self.vip_until = [0.0]
        self.bag = {}
        self.equip = {}
        self.enhance = {}
        self.wing = [None]
        # 当前穿着的时装（Item.xml 时装 item_id，kind1=1/kind2=14）。
        # 838 每条的第二字节就是「是否在穿」：真服样本 9 件全是 00（没穿）。
        self.fashion = [None]
        self.wardrobe = set()
        # 已拥有的宠物（Pet.xml 的 10000~10046 形象 id）。
        # 以前登录回放无条件给所有人塞一只 82B 的 957「功夫熊猫」，
        # 结果新建的 Lv1 号身边也跟着熊猫跑图 —— 真服新号没有宠物，这里必须是空表。
        self.pets = []
        # 当前宠物形象（Pet.xml id）。10000 = 功夫熊猫，形象卡会改成别的 id。
        self.pet_skin = [10000]
        # 已拥有的宠物形象（958/959/960 换形象面板的数据源）。新号只有默认熊猫。
        self.pet_skin_own = [10000]
        # 当前坐骑形象（Horse.xml id）与它穿的形象卡。0 = 没坐骑 / 没换过。
        self.mount = [0]
        self.mount_skin = [0]
        self.spirit_bag = {}
        self.spirit_slot = {}
        # 仙府探秘：秘籍库里默认给 3 条（与 947 模板尾部的 3 个技能同源，
        # 这样老号不点探秘也能看到技能栏有东西，不会误以为"数据丢了"）。
        self.pet_skill_bag = [[804201, 0], [810101, 0], [812101, 0]]
        self.pet_skill_slots = [[804201, 0], [810101, 0], [812101, 0]]
        self.pet_skill_free = 3
        self.pet_skill_day = ""
        self.pet_qual = [25, 25, 25, 25]
        self.pet_qual_preview = None
        self.treasure = {}
        self.miji = []
        self.quest_active = 0
        self.quest_ready = False
        # 0 = 没有任何称号。称号不是「等级到了就有」，必须用称号卡激活。
        # 17 的 idx24 与 88 的末尾 varint 都读这一个值。
        self.title = 0
        self.title_own = set()
        self.done_quests = set()
        self.scene = 101
        # 最后所在坐标（115 上报的 x/y，一格 1 字节）。
        # 老号登录要回到「最后一次下线的地点」，所以坐标必须和场景一起落盘；
        # 默认值 21,7 是原 17 模板里那对坐标，保持既有手感不变。
        self.pos_x = 21
        self.pos_y = 7
        # 只用于「背包变动」日志：None 表示这次连接还没推过背包
        self.bag_sent = None
        # 0 = 客户端还没通过 817 告诉我们武器用哪个协议槽字节
        self.weapon_wire = 0
        # 存档版本（见 state_store）；0 = 还没从库里读过这一行
        self.rev = 0

    def purses(self):
        """货币键 -> 可变列表（GM 与商城共用同一份引用）。"""
        return {"bind_coin": self.coin_bound,
                "bind_yuanbao": self.bind_yuanbao,
                "tongqian": self.tongqian,
                "yuanbao": self.yuanbao,
                "yueli": self.yueli}


_TLS = threading.local()


def new():
    """给当前线程造一份新角色并设为当前。连接建立时调用。"""
    p = Player()
    _TLS.p = p
    return p


def cur():
    """当前线程正在处理的角色；没设过就惰性建一份（GM/后台等线程）。"""
    p = getattr(_TLS, "p", None)
    if p is None:
        p = Player()
        _TLS.p = p
    return p


def load(p):
    """把 p 设为当前（等价于 ctx 的常驻版本）。"""
    _TLS.p = p
    return p


class ctx(object):
    """临时切到某个角色，退出时恢复。用于 GM 线程和多连接推送。"""

    def __init__(self, p):
        self.p = p
        self.prev = None

    def __enter__(self):
        self.prev = getattr(_TLS, "p", None)
        _TLS.p = self.p
        return self.p

    def __exit__(self, *exc):
        _TLS.p = self.prev
        return False


def reset_newbie(p=None):
    """把 p 重置成「Lv1 未转职空号」。

    和 Player() 默认值的区别：默认那套是 Lv10 演示号（带 4030 绑元、3 格背包），
    拿去当新角色会让新号一出生就 10 级、带一堆东西。真新号只能是这样：
        Lv1 / 未转职(cls=6) / 经验 0 / 钱 0 / 空背包 / 空装备 / 没翅膀 / 没任务。

    has_char 置 False —— 表示「这个账号还没有角色」，此时 save_state 的三重闸门
    会拦住写盘，避免把空号写成正式存档。
    """
    p = p or cur()
    p.cls = 6                 # 6 = 未转职（真服未转职号 151 的 b[2]=06，技能 60101/60201）
    p.level = 1
    p.exp = 0
    p.exp_need = 200          # 1 级升 2 级；之后每级按服务端的 1.18 倍一路推
    p.mp_add = [0]
    p.coin_bound = [0]
    p.bind_yuanbao = [0]
    p.tongqian = [0]
    p.yuanbao = [0]
    p.yueli = [0]
    p.vip_level = [0]
    p.vip_until = [0.0]
    p.bag = {}
    p.equip = {}
    p.enhance = {}
    p.wing = [None]
    p.fashion = [None]
    p.wardrobe = set()
    p.pets = []               # ★ 新号一只宠物都没有（真服实测：新号身边不跟熊猫）
    p.pet_skin = [10000]
    p.pet_skin_own = [10000]
    p.spirit_bag = {}
    p.spirit_slot = {}
    p.pet_skill_bag = [[804201, 0], [810101, 0], [812101, 0]]
    p.pet_skill_slots = [[804201, 0], [810101, 0], [812101, 0]]
    p.pet_skill_free = 3
    p.pet_skill_day = ""
    p.pet_qual = [25, 25, 25, 25]
    p.pet_qual_preview = None
    p.treasure = {}
    p.miji = []
    p.quest_active = 0
    p.quest_ready = False
    p.title = 0
    p.title_own = set()
    p.done_quests = set()
    p.scene = 101              # ★ 新号只出生在蓬莱仙境，不看老号的存档场景
    p.pos_x, p.pos_y = 21, 7   # 与 17 模板默认落点一致
    p.name = ""
    p.sex = 1
    p.has_char = False
    p.loaded = False
    p.saved_sig = None
    p.rev = 0
    return p
