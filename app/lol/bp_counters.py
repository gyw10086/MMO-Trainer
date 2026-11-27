# coding: utf-8
"""BP 浮窗用的国服对位数据（双向各 5 个）

和 OP.GG 出装页共用 `lol_101strategy_confront`，区别有两点：

1. 一次把 `low_op_details`（他打不过）和 `high_op_details`（他压制）都取出来；
2. 缓存按英雄单独失效 —— 浮窗上的 ⟳ 只刷当前这一个，不连累别人。

101 新版本刚上线常常还没统计，这里和它自己页面一样，退到上一版兜底。
"""
import time

from app.common.config import cfg
from app.common.logger import logger
from app.lol.cn_strategy import (CnStrategy, CONFRONT_PATH, cnStrategy,
                                 extractRaw, parseConfrontBoth)
from app.lol.opgg import opgg

TAG = 'BpCounters'

# 有数据缓存 30 分钟，空结果只缓存 3 分钟（接口抽风时别一直打）
CACHE_TTL = 30 * 60
EMPTY_TTL = 3 * 60

# 101 的分路写法 -> OP.GG 的写法
OPGG_POSITION = {
    'TOP': 'TOP',
    'JUNGLE': 'JUNGLE',
    'MIDDLE': 'MID',
    'BOTTOM': 'ADC',
    'SUPPORT': 'SUPPORT',
}


class BpCounters:
    def __init__(self):
        self.__cache = {}

    # ---------------------------------------------------------------- 缓存
    def invalidate(self, championId=None):
        """清缓存；给了英雄就只清这个英雄的所有分路/段位"""
        if championId is None:
            self.__cache.clear()
            return

        for key in list(self.__cache):
            if key[1] == int(championId):
                self.__cache.pop(key, None)

    def cachedAt(self, championId, position, tier):
        """返回该英雄这条缓存的写入时间戳，没有返回 0"""
        lane = CnStrategy.laneOf(position)

        if not lane or not championId:
            return 0

        region = cfg.get(cfg.opggRegion) or 'cn'
        key = (region, int(championId), lane, tier)
        item = self.__cache.get(key)

        return item[1] if item else 0

    # ---------------------------------------------------------------- 查询
    async def getPair(self, championId, position, tier, limit=5) -> dict:
        """双向对位，数据源跟着 OP.GG 弹窗的地区下拉走

        @return: {'weak': [...], 'strong': [...], 'version': '',
                  'lane': 'MIDDLE', 'at': 时间戳, 'source': '国服 101'}
        """
        lane = CnStrategy.laneOf(position)

        if not lane or not championId:
            return self.empty()

        # 国服走 101，韩服/全部地区走 OP.GG；默认（新装）是国服
        region = cfg.get(cfg.opggRegion) or 'cn'
        key = (region, int(championId), lane, tier)
        now = time.time()

        hit = self.__cache.get(key)

        if hit and hit[0] > now:
            return hit[2]

        if region == 'cn':
            payload = await self.__fetch(championId, lane, tier, limit)
        else:
            # OP.GG 的 session 平时靠「客户端启动」或「打开 OP.GG 窗口」拉起。
            # BP 期间从国服直接切到韩服时没人调 start()，session 还是 None，
            # 下面 __fetchOpgg 会抛 AttributeError 并被吞成空结果 ——
            # 用户看到的就是「切了数据源，浮窗全变暂无」。start() 是幂等的，放心调。
            await opgg.start()
            payload = await self.__fetchOpgg(region, championId, lane, tier, limit)

        ttl = CACHE_TTL if (payload['weak'] or payload['strong']) else EMPTY_TTL

        self.__cache[key] = (now + ttl, now, payload)

        return payload

    @staticmethod
    def empty() -> dict:
        return {'weak': [], 'strong': [], 'version': '', 'lane': '',
                'at': 0, 'source': ''}

    # ------------------------------------------------------------ 国服 101
    async def __fetch(self, championId, lane, tier, limit) -> dict:
        itier = CnStrategy.tierOf(tier)

        try:
            versions = await cnStrategy.getVersionNames()
        except Exception:
            versions = ()

        for version in versions[:2]:
            params = {
                'championid': championId,
                'lane': lane,
                'itier': itier,
                'version_id': version,
            }

            try:
                raw = extractRaw(await cnStrategy.request(CONFRONT_PATH, params))
            except Exception as e:
                logger.warning(
                    f'confront {championId} {lane} v{version} failed: {e}', TAG)
                continue

            weak, strong = parseConfrontBoth(raw)

            if weak or strong:
                return {
                    'weak': self.__rows(weak, limit),
                    'strong': self.__rows(strong, limit),
                    'version': version,
                    'lane': lane,
                    'at': time.time(),
                    'source': '国服 101',
                }

        return {'weak': [], 'strong': [], 'version': '', 'lane': lane,
                'at': time.time(), 'source': '国服 101'}

    @staticmethod
    def __rows(items, limit):
        rows = []

        for item in items[:limit]:
            championId = item.get('championId') or 0

            if championId:
                rows.append({
                    'championId': int(championId),
                    'winRate': float(item.get('winRate') or 0),
                })

        return rows

    # ------------------------------------------------------------ 外服 OP.GG
    async def __fetchOpgg(self, region, championId, lane, tier, limit) -> dict:
        position = OPGG_POSITION.get(lane, 'MID')

        try:
            raw = await opgg.getChampionBuild(
                region, 'ranked', championId, position, tier)
        except Exception as e:
            logger.warning(
                f'opgg counters {championId} {position} failed: {e}', TAG)
            return {'weak': [], 'strong': [], 'version': '', 'lane': lane,
                    'at': time.time(), 'source': 'OP.GG 外服'}

        data = (raw or {}).get('data') or {}
        counters = data.get('counters') or {}

        return {
            'weak': self.__opggRows(counters.get('weakAgainst'), limit),
            'strong': self.__opggRows(counters.get('strongAgainst'), limit),
            'version': (raw or {}).get('version') or '',
            'lane': lane,
            'at': time.time(),
            'source': 'OP.GG 外服',
        }

    @staticmethod
    def __opggRows(items, limit):
        rows = []

        for item in (items or [])[:limit]:
            championId = item.get('championId') or 0

            if championId:
                rows.append({
                    'championId': int(championId),
                    'winRate': float(item.get('winRate') or 0),
                })

        return rows


bpCounters = BpCounters()
