# coding: utf-8
"""国服对位数据（腾讯 101 数据站）

BP 阶段敌方卡上的「克制」头像原来只有 OP.GG 的韩服 / 全局统计。国服的英雄
登场率和外服差得很远，对位胜率自然也不是一套数。101 数据站的英雄详情页
其实就有国服排位对位数据，接口参数是从页面脚本里扒出来的（没有文档）：

    GET https://mlol.qt.qq.com/go/battle_info/odp_proxy/lol_101strategy_confront
        championid  英雄 id
        lane        TOP / JUNGLE / MIDDLE / BOTTOM / SUPPORT
        itier       段位档位 id，26 = 翡翠+（255 所有段位、24 黄金+…）
        version_id  版本名，形如 "16.23"（不是数字 id，也不是 versionlist 的 key）

结果被塞在 `data._fieldValues.<随机字段>` 里的 JSON 字符串：

    {"dtstatdate": "20261125",
     "low_op_details":  "1_246_43.68_-9.05#2_126_45.26_-7.79",
     "high_op_details": "1_86_71.43_16.65"}

`low_op_details` 每段是 `排名_英雄id_该英雄对位胜率_差值`，按胜率升序，也就是
「最能打他的几个英雄」。这个语义和 OP.GG 的 counters 一致，直接替换即可。

接口没有鉴权也没有版本号，随时可能变，所以这里所有异常都当成「没数据」，
由调用方决定要不要退回 OP.GG —— BP 期间宁可显示外服数据，也不能空着。
"""

import json

import aiohttp
from async_lru import alru_cache

TAG = "cn"

BASE_URL = 'https://mlol.qt.qq.com'
CONFRONT_PATH = '/go/battle_info/odp_proxy/lol_101strategy_confront'
VERSION_PATH = '/go/database/versionlist'

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Referer': 'https://101.qq.com/',
    'Accept': 'application/json, text/plain, */*',
}

# LCU 的分路写法 -> 101 的分路写法
LANE_ALIAS = {
    'TOP': 'TOP',
    'JUNGLE': 'JUNGLE',
    'JNG': 'JUNGLE',
    'MIDDLE': 'MIDDLE',
    'MID': 'MIDDLE',
    'BOTTOM': 'BOTTOM',
    'BOT': 'BOTTOM',
    'ADC': 'BOTTOM',
    'UTILITY': 'SUPPORT',
    'SUPPORT': 'SUPPORT',
    'SUP': 'SUPPORT',
}

# 101 的段位档位 id（从页面脚本里的段位下拉扒出来的）
TIER_IDS = {
    'ibsg': '255',
    'all': '255',
    'gold_plus': '24',
    'platinum_plus': '25',
    'emerald_plus': '26',
    'diamond_plus': '27',
    'master': '8',
    'master_plus': '28',
    'grandmaster': '9',
    'challenger': '10',
}

TIER_NAMES = {
    '255': '所有段位',
    '88': '峡谷之巅',
    '10': '王者',
    '9': '宗师',
    '28': '大师+',
    '8': '大师',
    '27': '钻石+',
    '7': '钻石',
    '26': '翡翠+',
    '6': '翡翠',
    '25': '铂金+',
    '5': '铂金',
    '24': '黄金+',
    '4': '黄金',
}

LANE_NAMES = {
    'TOP': '上路',
    'JUNGLE': '打野',
    'MIDDLE': '中路',
    'BOTTOM': '下路',
    'SUPPORT': '辅助',
}


def extractRaw(payload) -> str:
    """从 odp_proxy 的返回里取出真正的内容

    腾讯这层代理会把上游结果包成 `{"data": {"_fieldValues": {"R17968": "..."}}}`，
    字段名是随机编号，只能取第一个非空值。
    """
    if not isinstance(payload, (dict, list)):
        return ''

    data = payload.get('data') if isinstance(payload, dict) else None

    if not isinstance(data, dict):
        return ''

    fields = data.get('_fieldValues')

    if not isinstance(fields, dict):
        return ''

    for value in fields.values():
        if value:
            return value

    return ''


def _parseConfrontField(raw, field, reverse=False) -> list:
    """拆 `low_op_details` / `high_op_details` 这类 `排名_id_胜率_差值#…` 的串"""
    if not raw:
        return []

    try:
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return []

    if not isinstance(payload, dict):
        return []

    items = []

    for chunk in (payload.get(field) or '').split('#'):
        parts = chunk.split('_')

        if len(parts) < 3:
            continue

        try:
            championId = int(parts[1])
            winRate = float(parts[2])
            delta = float(parts[3]) if len(parts) > 3 else 0.0
        except (TypeError, ValueError):
            continue

        if not championId:
            continue

        items.append({
            'championId': championId,
            'winRate': winRate / 100.0,
            'delta': delta,
            'date': payload.get('dtstatdate') or '',
        })

    items.sort(key=lambda x: x['winRate'], reverse=reverse)

    return items


def parseConfront(raw) -> list:
    """把 `low_op_details` 拆成克制名单

    返回 [{'championId': int, 'winRate': 0~1, 'delta': float}]，按胜率升序。
    胜率是**被克制那个英雄**在对位里的胜率，所以越低说明越打不过。
    """
    return _parseConfrontField(raw, 'low_op_details')


def parseConfrontBoth(raw) -> tuple:
    """同时解析「打不过谁」和「打得过谁」

    返 `(low, high)`：`low` 是该英雄胜率最低的几个对位（被克制），
    `high` 是胜率最高的几个（克制对面）。两份都按胜率排序，可直接
    喂给 OP.GG 出装页的 weakAgainst / strongAgainst。
    """
    return _parseConfrontField(raw, 'low_op_details'), \
        _parseConfrontField(raw, 'high_op_details', reverse=True)


class CnStrategy:
    def __init__(self):
        self.session = None

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(BASE_URL, headers=HEADERS)

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def __get(self, path, params):
        if self.session is None:
            await self.start()

        async with self.session.get(path, params=params,
                                    ssl=False,
                                    timeout=aiohttp.ClientTimeout(total=8)) as res:
            return await res.json(content_type=None)

    async def request(self, path, params):
        """给同站点的其它模块（`cn_data` 的榜单 / 出装）复用同一个 session"""
        return await self.__get(path, params)

    async def requestRaw(self, path, params):
        """请求并直接取出 `_fieldValues` 里的内容，失败返回空串"""
        try:
            return extractRaw(await self.__get(path, params))
        except Exception:
            return ''

    # ---------------------------------------------------------------- 映射
    @staticmethod
    def laneOf(position) -> str:
        return LANE_ALIAS.get(str(position or '').strip().upper(), '')

    @classmethod
    def tierOf(cls, tier) -> str:
        return TIER_IDS.get(str(tier or '').strip().lower(), '26')

    @classmethod
    def tierName(cls, tier) -> str:
        return TIER_NAMES.get(cls.tierOf(tier), '翡翠+')

    @classmethod
    def label(cls, tier, lane='') -> str:
        """给 tooltip 用的一句来源说明，例如「国服 翡翠+ · 中路」"""
        head = f'国服 {cls.tierName(tier)}'
        laneName = LANE_NAMES.get(cls.laneOf(lane) or str(lane or ''), '')

        return f'{head} · {laneName}' if laneName else head

    # ---------------------------------------------------------------- 请求
    @alru_cache(maxsize=8, ttl=6 * 3600)
    async def getVersionNames(self) -> tuple:
        """当前可用版本名（最新在前），形如 ('16.23', '16.22', …)"""
        try:
            data = await self.__get(VERSION_PATH, {'zone': 'lol', 'from': 'h5'})
        except Exception:
            return ()

        names = []

        for item in (data.get('data') or []) if isinstance(data, dict) else []:
            name = (item or {}).get('name')

            if name and name not in names:
                names.append(name)

        return tuple(names)

    @alru_cache(maxsize=512, ttl=1800)
    async def __confront(self, championId, lane, itier, version) -> list:
        params = {
            'championid': int(championId),
            'lane': lane,
            'itier': itier,
            'version_id': version,
        }

        try:
            return parseConfront(extractRaw(
                await self.__get(CONFRONT_PATH, params)))
        except Exception:
            return []

    @alru_cache(maxsize=512, ttl=1800)
    async def getChampionCounters(self, championId, position,
                                  tier='emerald_plus', limit=3) -> list:
        """克制该英雄的对手（对位胜率最低的几个）

        新版本刚上线时 101 常常还没出统计，这时用上一个版本的数据兜底 ——
        和它自己页面的处理一致。
        """
        lane = self.laneOf(position)

        if not lane or not championId:
            return []

        itier = self.tierOf(tier)
        versions = await self.getVersionNames()

        for version in versions[:2]:
            items = await self.__confront(int(championId), lane, itier, version)

            if items:
                return items[:limit]

        return []


cnStrategy = CnStrategy()
