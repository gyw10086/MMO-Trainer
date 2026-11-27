"""观战 / 对局中的实时数据采集

LCU 的 gameflow 在观战、回放、以及部分"已经进游戏但客户端没同步"的场景下是空的,
但游戏进程自己的 2999 端口一直有数据。这个 watcher 就负责轮询它, 并把 10 名玩家
组装成和选人阶段一致的卡片结构, 让 UI 可以无差别复用。

设计约束:

- 2999 只给 "名字#编号", 想要段位/标签就得再问 LCU, 所以额外挂了一个
  低优先级的 `SummonerEnricher`: 每个玩家只查一次, 走静默接口, 失败不弹提示
- 单人重试三次失败才判定"接口没了", 避免游戏加载瞬间的抖动直接收窗
"""

import asyncio
import traceback

from app.common.logger import logger
from app.lol.connector import connector
from app.lol.game_data import (averageWinRate, buildBadges, cardFromLive,
                               enrichWithPlayerMeta, normalizeAlias)
from app.lol.live_client import liveClient
from app.lol.tools import parseRankInfo

TAG = 'LiveGameWatcher'


class SummonerEnricher:
    """名字#编号 -> puuid + 段位

    观战时 20 个请求 (10 人 × 名字+段位) 是必然要发的, 但必须:

    - 每人只查一次, 结果缓存到本次对局结束
    - 用静默接口, 不重试、不弹 InfoBar
    - 串行查, 别和主面板抢 LCU 并发额度
    """

    def __init__(self):
        self.cache = {}
        self.failed = set()

    @staticmethod
    def key(name, tagLine):
        return f"{normalizeAlias(name)}|{tagLine or ''}"

    def get(self, name, tagLine):
        return self.cache.get(self.key(name, tagLine))

    def unresolved(self, players):
        result = []

        for player in players:
            riotId = player.get('riotId') or ''
            name, _, tagLine = riotId.partition('#')
            name = name.strip()

            if not name:
                continue

            key = self.key(name, tagLine)

            if key not in self.cache and key not in self.failed:
                result.append(player)

        return result

    async def resolve(self, player):
        riotId = player.get('riotId') or ''
        name, _, tagLine = riotId.partition('#')

        if not name:
            return

        key = self.key(name, tagLine)

        try:
            summoner = await connector.getSummonerByNameQuiet(
                f"{name}#{tagLine}" if tagLine else name)

            if not summoner:
                self.failed.add(key)
                return

            puuid = summoner.get('puuid')
            raw = await connector.getRankedStatsByPuuidQuiet(puuid)
            rank = parseRankInfo(raw)['solo'] if raw else {}

            self.cache[key] = {'puuid': puuid, 'rank': rank}
        except Exception as e:
            logger.info(f"Enrich {name} failed: {e}", TAG)
            self.failed.add(key)


class LiveGameWatcher:
    """轮询 2999 并产出双方卡片

    @param onSnapshot: 回调, 参数是 {"blue": [...], "red": [...], ...}
    @param onOffline:  连续失败达到阈值后回调一次, 用于收起悬浮窗
    @param spectating: 是否观战 (True 时快照里带上标记, UI 显示"观战")
    """

    MAX_FAILURES = 3
    # 进游戏/观战加载阶段 2999 还没起来, 这时候不能按 3 次就放弃
    STARTUP_FAILURES = 40

    def __init__(self, onSnapshot, onOffline=None, interval=3.0,
                 spectating=False):
        self.onSnapshot = onSnapshot
        self.onOffline = onOffline
        self.interval = interval
        self.spectating = spectating

        self.task = None
        self.failures = 0

        self.cards = {}
        self.itemIcons = {}
        self.lastPlayers = []
        self.enricher = SummonerEnricher()
        self.enrichTask = None

    # ------------------------------------------------------------------ 生命周期

    def start(self):
        if self.task and not self.task.done():
            return

        self.failures = 0
        self.task = asyncio.create_task(self.__run())

    def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()

        if self.enrichTask and not self.enrichTask.done():
            self.enrichTask.cancel()

        self.task = None
        self.enrichTask = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def __run(self):
        try:
            while True:
                snapshot = await self.poll()

                if snapshot is None:
                    self.failures += 1

                    # 拿到过数据之后, 连续 3 次失败基本就是退出对局了;
                    # 还没拿到过数据时给足加载时间 (3s * 40 = 2 分钟)
                    limit = self.MAX_FAILURES if self.cards else self.STARTUP_FAILURES

                    if self.failures >= limit:
                        logger.info("Live client data offline", TAG)

                        if self.onOffline:
                            self.onOffline()

                        break
                else:
                    self.failures = 0

                    if self.onSnapshot:
                        self.onSnapshot(snapshot)

                    self.__startEnrich()

                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Live watcher crashed: {e}\n{traceback.format_exc()}",
                         TAG)

    # ------------------------------------------------------------------ 采集

    async def poll(self):
        players = await liveClient.getPlayerList()

        if not players:
            return None

        stats = await liveClient.getGameStats() or {}

        blue = []
        red = []

        for player in players:
            side = 'blue' if player.get('team') == 'ORDER' else 'red'

            try:
                card = await self.__buildCard(player, side)
            except Exception as e:
                logger.error(f"Build live card failed: {e}", TAG)
                continue

            (blue if side == 'blue' else red).append(card)

        self.lastPlayers = players

        # 场上英雄变了 (换人 / 新一局) 时把旧缓存清掉
        alive = {c['key'] for c in blue + red}
        self.cards = {k: v for k, v in self.cards.items() if k in alive}

        return {
            'blue': blue,
            'red': red,
            'blueWinRate': averageWinRate(blue),
            'redWinRate': averageWinRate(red),
            'gameTime': stats.get('gameTime') or 0,
            'gameMode': stats.get('gameMode') or '',
            'mapName': stats.get('mapName') or '',
            'spectating': self.spectating,
        }

    # ------------------------------------------------------------------ 身份补全

    def __startEnrich(self):
        """后台补齐 puuid / 段位

        自家对局时 LCU 早就把段位存进卡里了, 只有观战 (或软件中途启动) 才需要补。
        """
        if not self.spectating and any(c.get('puuid')
                                       for c in self.cards.values()):
            return

        if self.enrichTask and not self.enrichTask.done():
            return

        pending = self.enricher.unresolved(self.lastPlayers)

        if not pending:
            return

        self.enrichTask = asyncio.create_task(self.__enrich(pending))

    async def __enrich(self, players):
        try:
            for player in players:
                await self.enricher.resolve(player)

                # 查到一个就先刷一个, 不用等 10 个人全查完
                self.__applyEnriched()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Enrich failed: {e}", TAG)

    def __applyEnriched(self):
        for card in self.cards.values():
            info = self.enricher.get(card.get('name'), card.get('tagLine'))

            if not info:
                continue

            card['puuid'] = info.get('puuid') or ''

            if card.get('name'):
                card['isAnonymous'] = False

            if info.get('rank'):
                card['rank'] = info['rank']

            enrichWithPlayerMeta(card)
            card['badges'] = buildBadges(card)

    async def __buildCard(self, player, side):
        key = (f"{player.get('riotId') or player.get('summonerName')}|"
               f"{player.get('championName')}|{side}")

        cached = self.cards.get(key)

        if cached is None:
            cached = await cardFromLive(player, side, 'game', self.itemIcons)
            cached['key'] = key
            cached['itemIds'] = self.__itemIds(player)

            info = self.enricher.get(cached.get('name'), cached.get('tagLine'))
            if info:
                cached['puuid'] = info.get('puuid') or ''
                cached['rank'] = info.get('rank') or {}

                enrichWithPlayerMeta(cached)
                cached['badges'] = buildBadges(cached)

            self.cards[key] = cached
        else:
            # 只更新本局数据, 不重建卡片
            scores = player.get('scores') or {}
            cached['live'].update({
                'level': player.get('level') or 0,
                'kills': scores.get('kills', 0),
                'deaths': scores.get('deaths', 0),
                'assists': scores.get('assists', 0),
                'cs': scores.get('creepScore', 0),
                'isDead': bool(player.get('isDead')),
                'respawn': player.get('respawnTimer') or 0,
            })

            await self.__refreshItems(cached, player)

        return cached

    @staticmethod
    def __itemIds(player):
        return [(item.get('itemID') or 0)
                for item in (player.get('items') or [])[:6]]

    async def __refreshItems(self, card, player):
        """装备会中途买, 所以每次都要比对; 图标本身有缓存, 只有 id 变了才查"""
        ids = self.__itemIds(player)

        if card.get('itemIds') == ids:
            return

        icons = []

        for itemId in ids:
            icon = self.itemIcons.get(itemId)

            if icon is None:
                try:
                    icon = await connector.getItemIcon(itemId)
                except Exception:
                    icon = 'app/resource/images/item-0.png'

                self.itemIcons[itemId] = icon

            icons.append(icon)

        card['itemIds'] = ids
        card['live']['items'] = icons
