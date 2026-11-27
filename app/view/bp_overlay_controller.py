# coding: utf-8
"""BP 对位浮窗的调度器

把选人会话的变化翻译成浮窗的更新。

跨局清理是这里的重点：`gameId` 一变就把目标、对位、阵容全清，并且把
正在飞的请求作废（token 自增）—— 否则上一局慢半拍回来的响应会盖到
新一局的面板上，看起来就是「下一把还显示上一把」。
"""
import asyncio

from PyQt5.QtCore import QObject

from app.common.config import cfg
from app.common.logger import logger
from app.common.signals import signalBus
from app.lol.bp_counters import bpCounters
from app.lol.connector import connector
from app.view.bp_overlay import BpOverlay, lane101, laneName

TAG = 'BpOverlay'


# ---------------------------------------------------------------- 纯逻辑
# 抽成模块级函数，测试不用起窗口

# LCU 认的五个分路写法（它用 UTILITY 而不是 SUPPORT）
LCU_LANES = ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')


def laneOfPlayer(player):
    """玩家的实际分路，优先 `selectedPosition`

    LCU 给两个字段，语义不一样：`assignedPosition` 是系统分配位，玩家跟
    队友换位之后不一定会跟着变；`selectedPosition` 才是当前实际位置。
    补位到辅助、再跟队友换去中路时，只读 assignedPosition 会一直显示「辅助」。
    """
    for key in ('selectedPosition', 'assignedPosition'):
        value = str(player.get(key) or '').strip().upper()

        if value in LCU_LANES:
            return value

    return ''


def sessionKeyOf(data):
    """一局的身份：优先 gameId，没有就用我方 cellId 拼"""
    gameId = data.get('gameId') or 0

    if gameId:
        return str(gameId)

    ids = sorted((p.get('cellId') or 0) for p in data.get('myTeam') or [])

    return 'cells:' + ','.join(str(i) for i in ids) if any(ids) else ''


def localLaneOf(data):
    """我的分路，LCU 写法（TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY）"""
    cellId = data.get('localPlayerCellId')

    for player in data.get('myTeam') or []:
        if player.get('cellId') == cellId:
            return laneOfPlayer(player)

    return ''


def buildRoster(team, lane):
    """敌方的格子（只放已锁的），顺带算出我的对位是哪个

    @return: (roster, mineChampionId)
    """
    myLane = lane101(lane)
    roster = []
    mineId = 0

    for player in team or []:
        championId = player.get('championId') or 0

        if not championId:
            continue

        position = laneOfPlayer(player)
        mine = bool(myLane) and lane101(position) == myLane

        if mine and not mineId:
            mineId = championId

        roster.append({'id': championId, 'lane': position, 'mine': mine})

    return roster[:5], mineId


def pickTarget(current, previous, currentTarget, mineId):
    """这轮该看谁：新锁的优先，其中我的对位最优先

    @param current:       这一帧敌方已锁的 championId 集合
    @param previous:      上一帧的集合
    @param currentTarget: 面板当前显示的目标
    @param mineId:        我对位的敌方英雄 id（没有传 0）
    """
    if not current:
        return 0

    newIds = set(current) - set(previous)

    if newIds:
        return mineId if mineId in newIds else max(newIds)

    if currentTarget in current:
        return currentTarget

    if mineId in current:
        return mineId

    return next(iter(current))


class BpOverlayController(QObject):
    def __init__(self, window=None):
        super().__init__()
        self.window = window
        self.overlay = BpOverlay()

        self.sessionKey = ''
        self.dismissedKey = ''
        self.seenIds = set()
        self.targetId = 0
        self.lane = ''
        # 请求趟号。每发起一次（整批 / 单卡 / 换局）就 +1，回来时对不上就丢掉。
        # 不用「loading 布尔量」：趟号被更新的那趟取代时布尔量会永远卡在 True，
        # 而唯一读它的地方是「切数据源要不要重拉」—— 一卡死就是切了没反应。
        self.token = 0
        self.phase = ''
        # 已经拉过数据的阵容（id 元组 + 当时的分路）。只有它变了才重拉，
        # 否则每一帧选人事件都要打 5 个请求
        self.loadedIds = ()
        self.loadedLane = ''

        # PyQt5 用 connect 挂 async def 只会生成一个没人 await 的协程,
        # 方法体一行都不跑 —— 所以这里一律经 __spawn 转成 task
        self.overlay.refreshAllRequested.connect(
            lambda: self.__spawn(self.__onRefreshAll()))
        self.overlay.refreshOneRequested.connect(
            lambda championId: self.__spawn(self.__onRefreshOne(championId)))
        self.overlay.dismissRequested.connect(self.__onDismiss)

        signalBus.champSelectChanged.connect(
            lambda event: self.__spawn(self.__onSession(event)))
        signalBus.gameStatusChanged.connect(self.__onPhase)

        # 设置里的总开关: BP 过程中改也要立刻跟随
        cfg.bpOverlayEnabled.valueChanged.connect(self.__onEnabledChanged)

        # 在 OP.GG 弹窗里切了地区，克制数据源跟着变；当前这局的浮窗立刻重拉
        cfg.opggRegion.valueChanged.connect(self.__onRegionChanged)

    @staticmethod
    def __spawn(coro):
        """把协程交给事件循环

        事件循环没起来或已经停了（退出瞬间）就丢掉这次更新，
        不能让它变成一个「never awaited」的警告。
        """
        try:
            asyncio.create_task(coro)
        except RuntimeError:
            coro.close()

    # ---------------------------------------------------------------- 生命周期
    def close(self):
        self.token += 1
        self.overlay.hideOverlay()
        self.overlay.close()

    def show(self):
        self.overlay.showOverlay()

    # ---------------------------------------------------------------- 会话
    async def __onSession(self, event):
        if not cfg.get(cfg.bpOverlayEnabled):
            return

        data = (event or {}).get('data') or {}
        key = sessionKeyOf(data)

        if not key:
            return

        if key != self.sessionKey:
            # 新的一局：先把上一局的东西全清掉，再让旧请求作废
            self.sessionKey = key
            self.token += 1
            self.seenIds = set()
            self.targetId = 0
            self.lane = ''
            self.loadedIds = ()
            self.loadedLane = ''
            self.dismissedKey = ''
            self.overlay.clearAll()
            self.overlay.showOverlay()

        if self.dismissedKey == self.sessionKey:
            return

        # 1) 我的分路（换位置也跟着变）
        lane = localLaneOf(data)

        if lane != self.lane:
            self.lane = lane
            self.overlay.setLane(lane)
            self.__syncOpggLane(lane)

        # 2) 敌方阵容：5 行都要有，不管锁没锁
        team = data.get('theirTeam') or []
        current = {p.get('championId') or 0 for p in team} - {0}

        roster, mineId = buildRoster(team, lane)

        for slot in roster:
            slot['name'] = self.__championName(slot['id'])

        self.overlay.setRoster(roster)

        if not current:
            if self.targetId or self.loadedIds:
                self.targetId = 0
                self.loadedIds = ()
                self.token += 1
                self.overlay.setWaiting()
            return

        # 3) 记下「这轮最该看的人」：新锁的优先，其中我的对位最优先。
        #    5 张卡是平铺的，这个值不再驱动显示，但要留着给日志和后续排序用
        focusId = pickTarget(current, self.seenIds, self.targetId, mineId)
        self.seenIds = current

        if focusId != self.targetId:
            self.targetId = focusId

        # 4) 阵容变了（又锁了一个 / 有人换分路）就整批重拉；
        #    没变就用缓存，别每帧都打 5 个请求
        ids = tuple(s['id'] for s in roster)

        if ids != self.loadedIds or lane != self.loadedLane:
            await self.__loadTeam(roster)

    # ---------------------------------------------------------------- 数据
    async def __loadTeam(self, roster, force=False):
        """把敌方已锁的人一次拉齐（最多 5 个）

        每个人用**他自己的分路**去查：101 的 lane 参数决定统计的是哪个位置的
        对位数据，拿我的分路去查对面打野会得到一份不相干的中路统计。
        """
        roster = [s for s in roster or [] if s.get('id')]

        if not roster:
            return

        self.token += 1
        token = self.token
        self.loadedIds = tuple(s['id'] for s in roster)
        self.loadedLane = self.lane

        ids = [s['id'] for s in roster]
        self.overlay.markEnemiesLoading(ids)

        if force:
            for championId in ids:
                bpCounters.invalidate(championId)

        tier = cfg.get(cfg.opggTier)
        results = await asyncio.gather(
            *[bpCounters.getPair(s['id'], s.get('lane') or self.lane, tier, 5)
              for s in roster],
            return_exceptions=True)

        # 这一趟已经过期（换局 / 又点了一次刷新），结果直接丢掉
        if token != self.token:
            return

        rows = []
        version = ''
        source = ''
        at = 0

        for slot, result in zip(roster, results):
            if isinstance(result, Exception):
                logger.warning(f'load counters {slot["id"]} failed: {result}', TAG)
                result = bpCounters.empty()
            elif not isinstance(result, dict):
                result = bpCounters.empty()

            rows.append({'id': slot['id'],
                         'weak': result.get('weak') or [],
                         'strong': result.get('strong') or []})
            version = version or result.get('version') or ''
            source = source or result.get('source') or ''
            at = at or result.get('at') or 0

        self.overlay.setEnemies(rows, version, source)
        self.overlay.setCachedAt(at)

    def __onRegionChanged(self, value):
        """切了数据源（国服 101 / 韩服 / 全部地区），当前这局的对位立刻换源重拉

        这里**不能**加「上一批还在拉就跳过」的门禁：切过来正好撞上上一批没回来
        时，这次切换会被整个吞掉，之后也没有任何补拉时机，表现就是「切了没反应」。
        并发安全由趟号保证 —— 只有最后发起的那一趟会把结果写进界面。
        """
        logger.info(f'数据源切到 {value}，浮窗对位重拉', TAG)
        bpCounters.invalidate()

        if self.overlay.roster:
            self.__spawn(self.__loadTeam(self.overlay.roster, force=True))

    @staticmethod
    def __championName(championId):
        try:
            name = connector.manager.getChampionNameById(championId)

            if name:
                return name
        except Exception:
            pass

        return f'英雄 {championId}'

    # ---------------------------------------------------------------- 事件
    async def __onRefreshAll(self):
        """右键菜单：清掉全部缓存，把对面 5 个人重新拉一遍"""
        bpCounters.invalidate()

        if self.overlay.roster:
            await self.__loadTeam(self.overlay.roster, force=True)
        else:
            await self.__resync()

    async def __onRefreshOne(self, championId):
        """单张卡右上角的 ⟳：只重拉这一个人

        5 张卡一起重拉要等最慢的那个，某个人数据不对劲时没必要拖着别人。
        """
        championId = int(championId or 0)
        slot = next((s for s in self.overlay.roster if s['id'] == championId),
                    None)

        if not slot:
            return

        self.token += 1
        token = self.token

        bpCounters.invalidate(championId)
        self.overlay.markEnemiesLoading([championId])

        try:
            payload = await bpCounters.getPair(
                championId, slot.get('lane') or self.lane,
                cfg.get(cfg.opggTier), 5)
        except Exception as e:
            logger.warning(f'refresh counters {championId} failed: {e}', TAG)
            payload = bpCounters.empty()

        # 这中间换局了（或者整批又拉了一次），单刷的结果就作废
        if token != self.token:
            return

        self.overlay.setEnemies(
            [{'id': championId,
              'weak': payload.get('weak') or [],
              'strong': payload.get('strong') or []}],
            payload.get('version') or '', payload.get('source') or '')
        self.overlay.setCachedAt(payload.get('at') or 0)

    def __onDismiss(self):
        self.dismissedKey = self.sessionKey
        self.overlay.hideOverlay()

    def __onEnabledChanged(self, enabled):
        """设置里的总开关，选人过程中改也要立刻跟着变

        关着的时候 __onSession 第一行就 return，整局的事件全被丢掉了，
        所以重新打开不能只把窗口 show 出来 —— 得自己补一次会话，否则面板是空的。
        """
        if not enabled:
            self.overlay.hideOverlay()
            return

        if self.phase != 'ChampSelect':
            return

        # 用户主动在设置里打开，说明这局还想要：之前手动「关闭本局」不作数了
        self.dismissedKey = ''
        self.overlay.showOverlay()
        self.__spawn(self.__resync())

    async def __resync(self):
        """按当前会话把面板补回来（开关从关变开时用）"""
        try:
            data = await connector.getChampSelectSession()
        except Exception as e:
            logger.warning(f'resync champ select failed: {e}', TAG)
            return

        if data:
            await self.__onSession({'data': data})

    def __onPhase(self, phase):
        self.phase = phase

        if phase == 'ChampSelect':
            if not cfg.get(cfg.bpOverlayEnabled):
                return

            if not self.sessionKey:
                # 程序在 BP 中途启动时，可能只收到阶段、收不到会话事件：
                # 主动补一次，别让它一直空白（拿到会话后 __onSession 会开面板）
                self.__spawn(self.__resync())
                return

            if self.dismissedKey != self.sessionKey:
                self.overlay.showOverlay()
            return

        # 离开选人（进游戏 / 回大厅 / 关客户端）立刻收起来并清空
        self.overlay.hideOverlay()
        self.token += 1
        self.sessionKey = ''
        self.dismissedKey = ''
        self.seenIds = set()
        self.targetId = 0
        self.lane = ''
        self.loadedIds = ()
        self.loadedLane = ''
        self.overlay.clearAll()

    def __syncOpggLane(self, lane):
        """把自动识别到的分路同步给 OP.GG 窗口的筛选条"""
        opggWindow = getattr(self.window, 'opggWindow', None)

        if not opggWindow:
            return

        try:
            opggWindow.setPositionByLane(lane)
        except Exception:
            pass
