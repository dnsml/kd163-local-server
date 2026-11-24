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
        "bag", "equip", "enhance", "wing", "wardrobe",
        "pets",
        "spirit_bag", "spirit_slot",
        "quest_active", "quest_ready", "done_quests",
        "scene",
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
        # 以前名字是写死在 17 包模板里的「萌新」，所有号都叫萌新；
        # has_char=False 表示「账号在、角色还没建」，这种号不许落盘，等建角流程初始化。
        self.name = "萌新"
        self.sex = 1             # 1=男 2=女（客户端 create_role 只有男女两项）
        self.has_char = True     # 默认值当「演示号」，真新号由 reset_newbie() 置 False
        # 职业 6 = 未转职（真服未转职号 151 的 b[2]=06，技能 60101/60201）
        self.cls = 6
        self.level = 10
        self.exp = 5765
        self.exp_need = 8549
        self.mp_add = [0]
        self.coin_bound = [10240]
        self.bind_yuanbao = [4030]
        self.tongqian = [0]
        self.yuanbao = [869]
        self.yueli = [0]
        self.vip_level = [0]
        self.vip_until = [0.0]
        self.bag = {0: [20190, 10], 1: [20432, 1], 2: [21001, 1]}
        self.equip = {}
        self.enhance = {}
        self.wing = [None]
        self.wardrobe = set()
        # 已拥有的宠物（Pet.xml 的 10000~10046 形象 id）。
        # 以前登录回放无条件给所有人塞一只 82B 的 957「功夫熊猫」，
        # 结果新建的 Lv1 号身边也跟着熊猫跑图 —— 真服新号没有宠物，这里必须是空表。
        self.pets = []
        self.spirit_bag = {}
        self.spirit_slot = {}
        self.quest_active = 0
        self.quest_ready = False
        self.done_quests = set()
        self.scene = 101

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
    p.wardrobe = set()
    p.pets = []               # ★ 新号一只宠物都没有（真服实测：新号身边不跟熊猫）
    p.spirit_bag = {}
    p.spirit_slot = {}
    p.quest_active = 0
    p.quest_ready = False
    p.done_quests = set()
    p.scene = 101
    p.name = ""
    p.sex = 1
    p.has_char = False
    p.loaded = False
    p.saved_sig = None
    return p
