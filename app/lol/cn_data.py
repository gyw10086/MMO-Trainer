# coding: utf-8
"""国服数据源（101 数据站）—— OP.GG 窗口的第二套数据

OP.GG 接口只有外服（global / kr）数据，国服的英雄登场率、禁用率、对位
胜率跟外服差得很远。101 数据站是腾讯自己的国服统计站，接口没有文档，
参数和字段都是从它的前端脚本（`strategy-*.js`）里扒出来的：

    榜单  /go/battle_info/odp_proxy/lol_101strategy
          itier=26&version_id=16.23&lane=MIDDLE&sort_metric=1&sort_order=2
          → datadetails = "排名_英雄id_T档_分路_胜率_登场率_禁用率_克制ids_排名变化_日增_热度#…"

    出装  /go/battle_info/odp_proxy/lol_101strategy_build
          → starting/shoes/core/forth/fifth/sixth_details = "装备ids_排名_登场率_胜率#…"

    符文  /go/battle_info/odp_proxy/lol_101strategy_runeinfo
          → rune_top_details = "序_基石_标签_符文ids_登场率_胜率_场次#…"
          → rune_single_details = "基石_副系代码_runeId_槽内占比_胜率_场次_槽位#…"

    加点  /go/battle_info/odp_proxy/lol_101strategy_skill_point
          → detaildetails = "主升:占比:胜率@加点序列_占比_胜率@…$…"

    时长  /go/battle_info/odp_proxy/lol_101strategy_duration
          → data_details = "段序_胜率_场次#…"（6 段）

    对位  lol_101strategy_confront（`cn_strategy` 已有）

这里只做「把国服数据翻译成 OP.GG 窗口认识的形状」，界面层不用关心数据
是哪家来的。注意 101 **没有**召唤师技能组合统计，也没有斗魂 / 大乱斗的
数据，对应模块在国服态下自然隐藏。
"""

import asyncio
import json

from async_lru import alru_cache

from app.common.logger import logger
from app.lol.cn_strategy import CONFRONT_PATH, cnStrategy, parseConfrontBoth
from app.lol.connector import connector

TAG = 'cnData'

RANK_PATH = '/go/battle_info/odp_proxy/lol_101strategy'
BUILD_PATH = '/go/battle_info/odp_proxy/lol_101strategy_build'
RUNE_PATH = '/go/battle_info/odp_proxy/lol_101strategy_runeinfo'
SKILL_PATH = '/go/battle_info/odp_proxy/lol_101strategy_skill_point'
DURATION_PATH = '/go/battle_info/odp_proxy/lol_101strategy_duration'

# 101 的副系代码 -> 符文系 id（它前端用两个字母缩写，别处一律用数字 id）
SUB_STYLE_CODES = {
    'jm': 8000,   # 精密
    'zz': 8100,   # 主宰
    'ws': 8200,   # 巫术
    'qd': 8300,   # 启迪
    'jj': 8400,   # 坚决
}

# 分段时长：段序 -> 文案（照抄 101 前端的 wy 表）
DURATION_LABELS = {
    1: '<20',
    2: '20-25',
    3: '25-30',
    4: '30-35',
    5: '35-40',
    6: '40+',
}

# 窗口里的分路（OP.GG 写法）-> 101 的 lane
POSITION_TO_LANE = {
    'TOP': 'TOP',
    'JUNGLE': 'JUNGLE',
    'MID': 'MIDDLE',
    'ADC': 'BOTTOM',
    'SUPPORT': 'SUPPORT',
}

# 101 的 lane -> 窗口里的分路
LANE_TO_POSITION = {
    'TOP': 'TOP',
    'JUNGLE': 'JUNGLE',
    'MIDDLE': 'MID',
    'BOTTOM': 'ADC',
    'SUPPORT': 'SUPPORT',
}

# 101 的梯度档 -> 窗口里的 icon-tier-N
TIER_TO_NUMBER = {'T0': 0, 'T1': 1, 'T2': 2, 'T3': 3, 'T4': 4}

# 榜单接口的 lane 传 ALL 时，会把五个分路各返回一遍（每段自带 lane）。
# 搜索框要「跨分路直达英雄」，靠的就是这一份一次拿全的索引
LANE_ALL = 'ALL'

# 101 的技能编号 -> 界面显示的字母
SKILL_LETTERS = {'1': 'Q', '2': 'W', '3': 'E', '4': 'R'}


def _load(raw):
    """`_fieldValues` 里的内容通常是一段 JSON 字符串"""
    if not raw:
        return None

    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None

    return payload if isinstance(payload, dict) else None


def _segments(value):
    return [x for x in str(value or '').split('#') if x and x != '-1']


def _floats(parts, indexes):
    """按位置取浮点数，缺字段的当 0"""
    out = []

    for i in indexes:
        try:
            out.append(float(parts[i]))
        except (IndexError, TypeError, ValueError):
            out.append(0.0)

    return out


# ------------------------------------------------------------------ 解析
def parseRank(raw) -> list:
    """榜单：`排名_英雄id_T档_分路_胜率_登场率_禁用率_克制ids_排名变化_日增_热度`

    排名变化是带符号的整数（`-2` 掉两名 / `3` 升三名），热度 `1` 表示当前热门。
    """
    payload = _load(raw)

    if not payload:
        return []

    items = []

    for chunk in _segments(payload.get('datadetails')):
        parts = chunk.split('_')

        if len(parts) < 7:
            continue

        try:
            rank = int(parts[0])
            championId = int(parts[1])
        except (TypeError, ValueError):
            continue

        if not championId:
            continue

        winRate, pickRate, banRate = _floats(parts, (4, 5, 6))

        counters = []

        if len(parts) > 7 and parts[7]:
            for x in parts[7].split(','):
                try:
                    x = int(x)
                except (TypeError, ValueError):
                    continue

                if x > 0:
                    counters.append(x)

        try:
            rankChange = int(parts[8]) if len(parts) > 8 else 0
        except (TypeError, ValueError):
            rankChange = 0

        items.append({
            'rank': rank,
            'championId': championId,
            'tier': parts[2] or 'T4',
            'tierNumber': TIER_TO_NUMBER.get(parts[2], 4),
            'lane': parts[3] or '',
            'winRate': winRate,
            'pickRate': pickRate,
            'banRate': banRate,
            'counters': counters,
            'rankChange': rankChange,
            'isHot': (parts[10] == '1') if len(parts) > 10 else False,
        })

    items.sort(key=lambda x: x['rank'])

    return items


def parseItemSets(raw) -> dict:
    """出装：六组 `装备ids_排名_登场率_胜率`

    `starting` 是出门装（可能带消耗品，重复 id 表示买多个），`forth`
    到 `sixth` 是第四到第六件。
    """
    payload = _load(raw) or {}

    result = {}

    for key, field in (
        ('starting', 'starting_details'),
        ('shoes', 'shoes_details'),
        ('core', 'core_details'),
        ('forth', 'forth_details'),
        ('fifth', 'fifth_details'),
        ('sixth', 'sixth_details'),
    ):
        rows = []

        for chunk in _segments(payload.get(field)):
            parts = chunk.split('_')

            if not parts or not parts[0]:
                continue

            ids = []

            for x in parts[0].split(','):
                try:
                    x = int(x)
                except (TypeError, ValueError):
                    continue

                if x > 0:
                    ids.append(x)

            if not ids:
                continue

            pickRate, winRate = _floats(parts, (2, 3))

            rows.append({
                'ids': ids,
                'pickRate': pickRate,
                'winRate': winRate,
            })

        rows.sort(key=lambda x: -x['pickRate'])
        result[key] = rows

    return result


def parseRunes(raw) -> list:
    """符文：`序_基石_标签_符文ids_登场率_胜率_场次`

    `符文ids` 是 9 个 id：主系 4 + 副系 2 + 属性碎片 3，和 OP.GG 的
    `primary_rune_ids + secondary_rune_ids + stat_mod_ids` 完全对得上。
    """
    payload = _load(raw)

    if not payload:
        return []

    items = []

    for chunk in _segments(payload.get('rune_top_details')):
        parts = chunk.split('_')

        if len(parts) < 6:
            continue

        runeIds = []

        for x in (parts[3] if len(parts) > 3 else '').split(','):
            try:
                x = int(x)
            except (TypeError, ValueError):
                continue

            if x > 0:
                runeIds.append(x)

        if not runeIds:
            continue

        pickRate, winRate, play = _floats(parts, (4, 5, 6))

        try:
            keystone = int(parts[1])
        except (IndexError, TypeError, ValueError):
            keystone = runeIds[0]

        items.append({
            'keystone': keystone,
            'runeIds': runeIds,
            'tag': parts[2] if len(parts) > 2 else '',
            'pickRate': pickRate,
            'winRate': winRate,
            'play': int(play),
        })

    # 展示顺序按登场率，第一条就是主页首推那套
    items.sort(key=lambda x: -x['pickRate'])

    return items


def parseRuneSingle(raw) -> dict:
    """符文单条：`基石_副系代码_runeId_槽内占比_胜率_场次_槽位`

    给的是「某个符文在这个基石 / 副系组合下的表现」，比整套符文页细一层。
    第 4 位才是单符文胜率，第 3 位是同一槽内的占比，两者不能混。

    返回 `{(基石, 副系 styleId): {(runeId, 槽位): {'winRate', 'pickRate', 'play'}}}`。
    槽位只有属性碎片会填（1/2/3），主副系符文一律 `-1` —— 因为 5001（生命值）
    和 5008（适应之力）这类碎片天然跨槽，只按 runeId 存会让后一个槽的样本
    顶掉前一个，界面上两行显示同一个数。同一 `(runeId, 槽位)` 重复出现时取
    场次最大的那条。
    """
    payload = _load(raw)

    if not payload:
        return {}

    result = {}

    for chunk in _segments(payload.get('rune_single_details')):
        parts = chunk.split('_')

        # 基石 / 副系 / runeId / 占比 / 胜率 / 场次，少一位就没法用
        if len(parts) < 6:
            continue

        styleId = SUB_STYLE_CODES.get(parts[1])

        # 副系代码认不出来（101 偶尔加新缩写）就整条丢掉，别猜
        if not styleId:
            continue

        try:
            keystone = int(parts[0])
            runeId = int(parts[2])
        except (TypeError, ValueError):
            continue

        if not keystone or not runeId:
            continue

        pickRate, winRate, play = _floats(parts, (3, 4, 5))

        row = {
            'winRate': winRate / 100.0,
            'pickRate': pickRate / 100.0,
            'play': int(play),
        }

        # 第 7 位是槽位，缺失或解析不出来一律当主副系（-1）
        slot = -1

        if len(parts) > 6:
            try:
                slot = int(parts[6])
            except (TypeError, ValueError):
                slot = -1

        group = result.setdefault((keystone, styleId), {})
        key = (runeId, slot)
        old = group.get(key)

        if old is None or row['play'] > old['play']:
            group[key] = row

    return result


def runeSingleStats(singleMap, keystone, styleId, runeIds) -> dict:
    """把 `(基石, 副系)` 那组单符文数据整理成界面用的 `{(runeId, 槽位): {...}}`

    101 前端对基石自己那条是「只按副系匹配」：基石的表现只跟副系挂钩，
    换基石不换副系时照样能显示。所以本组里缺基石自己那条时，退到同副系的
    其它组合里找；别的符文一律要求基石 + 副系都对上，避免串数据。
    """
    group = dict((singleMap or {}).get((keystone, styleId)) or {})
    key = (keystone, -1)

    if keystone and keystone in (runeIds or []) and key not in group:
        for (_, otherStyle), other in (singleMap or {}).items():
            if otherStyle != styleId or key not in other:
                continue

            group[key] = other[key]
            break

    return group


def parseDuration(raw) -> list:
    """分时段胜率：`段序_胜率_场次`，6 段

    返回 `[{'index', 'label', 'winRate', 'play'}]`，按段序升序。第 3 位是
    样本量，101 前端管它叫 rank，含义没确认，只丢给 tooltip、不当占比用。
    没有样本（场次 0）的段直接丢掉 —— 画出来会是一根 0% 的假柱子。
    """
    payload = _load(raw)

    if not payload:
        return []

    rows = []

    for chunk in _segments(payload.get('data_details')):
        parts = chunk.split('_')

        if len(parts) < 3:
            continue

        try:
            index = int(parts[0])
        except (TypeError, ValueError):
            continue

        label = DURATION_LABELS.get(index)

        if not label:
            continue

        winRate, play = _floats(parts, (1, 2))
        play = int(play)

        if play <= 0 or winRate <= 0:
            continue

        rows.append({
            'index': index,
            'label': label,
            'winRate': winRate / 100.0,
            'play': play,
        })

    rows.sort(key=lambda x: x['index'])

    return rows


def parseSkillOrder(raw) -> dict:
    """加点：`主升:占比:胜率@加点序列_占比_胜率@…`

    `主升` 是三个技能的先后（1/2/3 = Q/W/E），`加点序列` 是 1~15 级的
    逐级点法。101 只给占比和胜率、没给场次，`play` 留 None 让界面藏掉。
    """
    payload = _load(raw)

    if not payload:
        return {}

    groups = [x for x in str(payload.get('detaildetails') or '').split('$')
              if x and x != '-1']

    if not groups:
        return {}

    head, *rest = groups[0].split('@')

    try:
        masteries, pickRate, winRate = head.split(':')
    except ValueError:
        return {}

    order = []

    if rest:
        seq = rest[0].split('_')

        for x in (seq[0] if seq else '').split(','):
            letter = SKILL_LETTERS.get(x.strip())

            if letter:
                order.append(letter)

    masteries = [SKILL_LETTERS.get(x.strip(), '') for x in masteries.split(',')]
    masteries = [x for x in masteries if x]

    if not masteries and not order:
        return {}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    return {
        'masteries': masteries,
        'order': order,
        'pickRate': _f(pickRate) / 100.0,
        'winRate': _f(winRate) / 100.0,
        'play': None,
    }


def buildChampionLanes(rows) -> dict:
    """把 `lane=ALL` 的混分路榜单整理成「英雄 -> 分路」索引

    返回 `{championId: [{'position', 'rank', 'winRate', 'pickRate'}, ...]}`，
    每个英雄的分路按**登场率**降序 —— 第一个就是它的主分路。

    不能按分路内排名排：维克托下路排第 9（登场 1.48%）、中单排第 14
    （9.33%），按排名会把他判成下路。40 个多分路英雄里有 8 个会这样判错。
    """
    index = {}

    for row in rows or []:
        position = LANE_TO_POSITION.get(row.get('lane'))

        if not position:
            continue

        index.setdefault(row['championId'], []).append({
            'position': position,
            'rank': row['rank'],
            'winRate': row['winRate'] / 100.0,
            'pickRate': row['pickRate'] / 100.0,
        })

    for positions in index.values():
        positions.sort(key=lambda x: -x['pickRate'])

    return index


def styleOfRune(runeId, styleMap):
    """反查一个符文属于哪个系，属性碎片查不到返回 None"""
    for styleId, style in (styleMap or {}).items():
        for slot in style.get('slots') or []:
            for perk in slot:
                if perk.get('runeId') == runeId:
                    return styleId

    return None


def buildPerkRecord(item, styleMap):
    """把一条 101 符文记录翻译成窗口要的 perks 元素

    副系取「第一个不属于主系的符文」所在的系；属性碎片（5000 段）不参与
    判断，否则会算出个不存在的系。
    """
    runeIds = item['runeIds']
    primaryId = styleOfRune(runeIds[0], styleMap)

    secondaryId = None

    for runeId in runeIds[1:]:
        styleId = styleOfRune(runeId, styleMap)

        if styleId and styleId != primaryId:
            secondaryId = styleId
            break

    if not primaryId or not secondaryId:
        return None

    play = item['play'] or 0
    winRate = item['winRate'] / 100.0

    return {
        'primaryId': primaryId,
        'secondaryId': secondaryId,
        'perks': runeIds,
        'play': play,
        'win': winRate * play,
        'winRate': winRate,
        'pickRate': item['pickRate'] / 100.0,
    }


# ------------------------------------------------------------------ 客户端
class CnData:
    """101 数据站的数据客户端，输出结构和 `opgg` 模块一致"""

    def __init__(self):
        self.version = ''

    async def start(self):
        # 请求层复用 cn_strategy 的 session，避免再开一个连接池
        await cnStrategy.start()

    async def close(self):
        # 生命周期由主窗口统一管（cnStrategy.close），这里不重复关
        pass

    async def __version(self):
        if not self.version:
            names = await cnStrategy.getVersionNames()
            self.version = names[0] if names else ''

        return self.version

    @alru_cache(maxsize=64, ttl=1800)
    async def __rank(self, itier, lane, version) -> list:
        raw = await cnStrategy.requestRaw(RANK_PATH, {
            'itier': itier,
            'version_id': version,
            'lane': lane,
            'sort_metric': 1,
            'sort_order': 2,
        })

        try:
            return parseRank(raw)
        except Exception as e:
            logger.error(f'Parse cn rank failed: {e}', TAG)
            return []

    async def __rankRows(self, tier, position) -> tuple:
        """定位到「段位 + 分路」的榜单，返回 (rows, version)

        新版本刚上线时 101 常常还没统计，这时退一个版本 —— 和它自己页面
        的做法一致。
        """
        lane = POSITION_TO_LANE.get(position, 'MIDDLE')
        itier = cnStrategy.tierOf(tier)
        versions = await cnStrategy.getVersionNames()

        for version in versions[:2]:
            rows = await self.__rank(itier, lane, version)

            if rows:
                return rows, version

        return [], (versions[0] if versions else '')

    async def __rankAllRows(self, tier) -> tuple:
        """一次拿全五个分路的榜单，返回 (rows, version)

        `lane=ALL` 时 101 会把每个分路各返回一遍（每段自带 lane），
        全段位下合计 212 行、覆盖 167 个英雄。新版本无数据时同样退一版。
        """
        itier = cnStrategy.tierOf(tier)
        versions = await cnStrategy.getVersionNames()

        for version in versions[:2]:
            rows = await self.__rank(itier, LANE_ALL, version)

            if rows:
                return rows, version

        return [], (versions[0] if versions else '')

    @alru_cache(maxsize=16, ttl=1800)
    async def getChampionLanes(self, tier) -> dict:
        """英雄 -> 它在各个分路的排名，供搜索框跨分路定位

        没有这份索引时，搜索只能过滤「当前分路」那 40~50 条，对面选了
        别的位置的英雄就搜不到；有了它，输入名字就能直接跳到该英雄在
        正确分路的详情页。
        """
        rows, _ = await self.__rankAllRows(tier)

        return buildChampionLanes(rows)

    @alru_cache(maxsize=128, ttl=1800)
    async def getTierList(self, tier, position):
        """英雄梯队，形状同 `opgg.getTierList`：按位置分组的字典"""
        rows, version = await self.__rankRows(tier, position)

        res = []

        for row in rows:
            championId = row['championId']

            counters = [{
                'championId': cid,
                'icon': await connector.getChampionIcon(cid),
            } for cid in row['counters'][:3]]

            res.append({
                'championId': championId,
                'name': connector.manager.getChampionNameById(championId),
                'icon': await connector.getChampionIcon(championId),
                'winRate': row['winRate'] / 100.0,
                'pickRate': row['pickRate'] / 100.0,
                'banRate': row['banRate'] / 100.0,
                'kda': None,
                'tier': row['tierNumber'],
                'rank': row['rank'],
                'position': position,
                'counters': counters,
                'rankChange': row['rankChange'],
                'cnTier': row['tier'],
                # 101 独有的「热度飙升」标记，外服数据里恒为 False
                'isHot': row.get('isHot', False),
            })

        return {
            'data': {position: res},
            'version': version,
        }

    @alru_cache(maxsize=256, ttl=1800)
    async def getChampionBuild(self, championId, position, tier):
        """英雄详情（符文 / 加点 / 装备 / 对位）

        101 每个接口都要 `version_id` + `itier` + `lane`，所以先定位榜单拿
        到同一批参数，顺带把 summary 里的胜率、登场率也补齐。
        """
        rows, version = await self.__rankRows(tier, position)
        lane = POSITION_TO_LANE.get(position, 'MIDDLE')
        itier = cnStrategy.tierOf(tier)

        params = {
            'championid': int(championId),
            'lane': lane,
            'itier': itier,
            'version_id': version,
        }

        builds, runes, skills, confront, duration = await asyncio.gather(
            cnStrategy.requestRaw(BUILD_PATH, params),
            cnStrategy.requestRaw(RUNE_PATH, params),
            cnStrategy.requestRaw(SKILL_PATH, params),
            cnStrategy.requestRaw(CONFRONT_PATH, params),
            cnStrategy.requestRaw(DURATION_PATH, params),
        )

        items = parseItemSets(builds)
        perkItems = parseRunes(runes)
        skillData = parseSkillOrder(skills)
        low, high = parseConfrontBoth(confront)
        durationItems = parseDuration(duration)

        hasData = any((
            items.get('core'), items.get('starting'), items.get('shoes'),
            perkItems, skillData, low, high, durationItems,
        ))

        if not hasData:
            raise RuntimeError('国服暂无该英雄在这个分路的数据')

        styleMap = connector.manager.getPerkStyles() or {}
        singleMap = parseRuneSingle(runes)

        perks = []

        for item in perkItems[:3]:
            record = buildPerkRecord(item, styleMap)

            if not record:
                continue

            record['icons'] = [await connector.getRuneIcon(x)
                               for x in record['perks']]
            # 单符文胜率：副系 id 是 buildPerkRecord 推出来的，只能在这里查表
            record['singleStats'] = runeSingleStats(
                singleMap, item['keystone'], record['secondaryId'],
                record['perks'])

            perks.append(record)

        row = next((x for x in rows if x['championId'] == championId), None)

        summary = {
            'name': connector.manager.getChampionNameById(championId),
            'championId': championId,
            'icon': await connector.getChampionIcon(championId),
            'position': position,
            'winRate': (row['winRate'] / 100.0) if row else None,
            'pickRate': (row['pickRate'] / 100.0) if row else None,
            'banRate': (row['banRate'] / 100.0) if row else None,
            'kda': None,
            'tier': row['tierNumber'] if row else 4,
            'rank': row['rank'] if row else None,
            'source': 'cn',
        }

        return {
            'data': {
                'summary': summary,
                # 101 没有召唤师技能组合统计，界面会自动隐藏这一块
                'summonerSpells': [],
                'championSkills': skillData,
                'items': {
                    'boots': await _toItemRows(items.get('shoes'), 3),
                    'startItems': await _toItemRows(items.get('starting'), 3),
                    'coreItems': await _toItemRows(items.get('core'), 5),
                    'lastItems': await _toLastIcons(items, 16),
                },
                'counters': {
                    'strongAgainst': await _toCounterRows(high),
                    'weakAgainst': await _toCounterRows(low),
                },
                'perks': perks,
                # 分时段胜率（101 独有），外服没有这一块，界面按空表隐藏
                'duration': durationItems,
            },
            'version': version,
            'mode': 'ranked',
        }


async def _toItemRows(rows, limit) -> list:
    """`[{'ids','pickRate','winRate'}]` -> 窗口认识的 items 行

    101 没有场次，`play` 给 None，界面把它当「不显示场次」处理。
    """
    out = []

    for row in (rows or [])[:limit]:
        out.append({
            'icons': [await connector.getItemIcon(x) for x in row['ids']],
            'ids': row['ids'],
            'play': None,
            'win': None,
            'winRate': row['winRate'] / 100.0,
            'pickRate': row['pickRate'] / 100.0,
        })

    return out


async def _toLastIcons(items, limit) -> list:
    """「其余装备」那一排图标：101 拆成了第 4~6 件，合并去重"""
    seen = []

    for key in ('forth', 'fifth', 'sixth'):
        for row in items.get(key) or []:
            icon = await connector.getItemIcon(row['ids'][0])

            if icon in seen:
                continue

            seen.append(icon)

            if len(seen) >= limit:
                return seen

    return seen


async def _toCounterRows(items) -> list:
    out = []

    for item in items or []:
        championId = item['championId']

        out.append({
            'championId': championId,
            'name': connector.manager.getChampionNameById(championId),
            'icon': await connector.getChampionIcon(championId),
            'play': None,
            'win': None,
            'winRate': item['winRate'],
        })

    return out


cnData = CnData()
