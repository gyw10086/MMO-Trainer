"""对局信息的数据归一化

三个数据源的结构完全不同:

1. `parseAllyGameInfo()` / `parseGameInfoByGameflowSession()` —— 选人与对局加载阶段的 LCU 数据
2. `live_client` —— 对局中 / 观战时的 2999 Live Client Data
3. `spectator` —— 把 2 组装成双方 10 人

UI 只认识本模块输出的 "卡片数据", 这样 `PlayerCard` 不必关心数据从哪来。
本模块保持纯函数 (除 `cardFromLive` 需要取图标), 方便单测。
"""

import re
from collections import Counter

from app.lol.connector import connector
from app.lol.player_db import TAG_TONES, playerDB

POSITION_ORDER = ['TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY']

# 数据源里分路字段的别名
POSITION_ALIAS = {
    'MID': 'MIDDLE',
    'ADC': 'BOTTOM',
    'SUPPORT': 'UTILITY',
    'JUG': 'JUNGLE',
}


def normalizePosition(value) -> str:
    if not value:
        return ''

    value = str(value).upper().strip()
    value = POSITION_ALIAS.get(value, value)

    return value if value in POSITION_ORDER else ''


def normalizeAlias(name: str) -> str:
    """英雄别名归一化

    Live Client Data 给的 `championName` 在不同版本里出现过 `K'Sante` / `KSante` /
    `Nunu & Willump` 等写法, 去掉非字母数字后比较最稳。
    """
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def resolveChampionId(championName: str) -> int:
    """英雄名 -> id, 同时兼容英文别名与国服中文名

    英文: 2999 在外服给 "Malzahar" / "K'Sante"
    中文: 国服 2999 直接给 "虚空先知"
    """
    if not championName:
        return 0

    manager = connector.manager

    if manager is None:
        return 0

    name = str(championName).strip()

    championId = getattr(manager, 'championNames', {}).get(name)

    if championId:
        return championId

    return getattr(manager, 'championAlias', {}).get(
        normalizeAlias(name), 0)


def _ratio(kda) -> float:
    if not kda:
        return 0.0

    kills, deaths, assists = (list(kda) + [0, 0, 0])[:3]
    return (kills + assists) / max(deaths, 1)


def topChampions(games, count=3):
    """从最近的对局里统计常用英雄, 返回图标路径列表

    比额外查 10 次英雄精通接口便宜得多, 语义上也够用 ("近期常用")。
    """
    counter = Counter()
    icons = {}

    for game in games or []:
        championId = game.get('championId') or 0
        icon = game.get('championIcon')

        if not championId or not icon:
            continue

        counter[championId] += 1
        icons[championId] = icon

    return [icons[cid] for cid, _ in counter.most_common(count)]


def enrichWithPlayerMeta(card: dict) -> dict:
    """挂上本地库里的标签 / 备注 / 遇见次数

    没有 puuid 的卡 (例如还没补全身份的观战卡) 原样返回。
    """
    puuid = card.get('puuid')

    if not puuid:
        return card

    meta = playerDB.summary(puuid)
    card['meta'] = meta
    card['userTags'] = meta.get('tags') or []

    return card


def buildBadges(card) -> list:
    """按设计稿的标签体系生成胶囊, 最多两个

    只有两个位置, 所以按"信息价值"排优先级:
    自己打的标签 > 开黑 > 大腿 > 连胜/连败 > 上局对手 > 上局队友 > 高胜率
    —— 开黑是本局信息, 比"历史战绩好不好看"更值得占位置: 对面两人同排
    意味着他们的节奏是配合过的, 这直接影响要不要针对。
    「开黑 N」原来排在大腿后面, 常常被挤掉, 现在提前。
    """
    badges = []

    if card.get('isAnonymous'):
        return [('匿名', 'muted')]

    # 用户自己标的优先, 因为那是他专门记下来的
    for tag in (card.get('userTags') or [])[:2]:
        badges.append((tag, TAG_TONES.get(tag, 'violet')))

    if len(badges) >= 2:
        return badges[:2]

    premade = card.get('premade') or 0

    if premade >= 2:
        badges.append((card.get('premadeLabel') or f"开黑 {premade}", 'premade'))

    winRate = card.get('winRate')
    kda = card.get('kdaValue') or 0

    if winRate is not None and winRate >= 60 and kda >= 4.5:
        badges.append(('大腿', 'gold'))

    streakType, streakCount = card.get('streak') or ('', 0)
    if streakCount >= 2:
        badges.append((f"{'连胜' if streakType == 'win' else '连败'} {streakCount}",
                       'win' if streakType == 'win' else 'lose'))

    fateFlag = card.get('fateFlag')
    if fateFlag == 'enemy':
        badges.append(('上局对手', 'lose'))
    elif fateFlag == 'ally':
        badges.append(('上局队友', 'muted'))

    if winRate is not None and winRate >= 60:
        badges.append(('高胜率', 'brand'))

    return badges[:2]


def cardFromSummoner(info: dict, side: str, phase='select') -> dict:
    """LCU 数据 -> 卡片数据

    @param info: `parseSummonerGameInfo()` 的返回值
    @param side: 'blue' / 'red'
    """
    info = info or {}
    games = info.get('gamesInfo') or []
    kda = info.get('kda') or [0, 0, 0]

    card = {
        'side': side,
        'phase': phase,
        # summonerId 在新版 LCU 里会缺失（一律 0），直接拿来当 key 会让
        # 五张卡互相覆盖，最后只剩一张；按 puuid -> 名字逐级兜底
        'key': (info.get('summonerId') or info.get('puuid')
                or info.get('name') or 0),
        # 同队开黑要以 summonerId 对齐 LCU 的 teamParticipantId, key 万一
        # 退化成了名字, 没有这个字段就再也匹配不上了
        'summonerId': info.get('summonerId') or 0,
        'name': info.get('name') or '',
        'tagLine': info.get('tagLine') or '',
        'puuid': info.get('puuid') or '',
        'isAnonymous': bool(info.get('isAnonymous')),
        'isPublic': info.get('isPublic', True),
        'championId': info.get('championId') or 0,
        'championIcon': info.get('championIcon') or info.get('icon'),
        'position': normalizePosition(info.get('selectedPosition')),
        'rank': (info.get('rankInfo') or {}).get('solo') or {},
        'kda': list(kda),
        'kdaValue': _ratio(kda),
        'winRate': (info.get('summary') or {}).get('winRate'),
        'recent': (info.get('summary') or {}).get('recent') or [],
        'streak': (info.get('summary') or {}).get('streak') or ('', 0),
        'premade': info.get('premade') or 0,
        'fateFlag': info.get('fateFlag'),
        'mains': topChampions(games),
        'games': games,
        'pickState': info.get('pickState') or '',
    }

    enrichWithPlayerMeta(card)
    card['badges'] = buildBadges(card)

    return card


async def cardFromLive(player: dict, side: str, phase='game',
                       itemIcons=None) -> dict:
    """2999 的 playerList 单项 -> 卡片数据

    @param itemIcons: 可选的 {itemId: iconPath} 缓存, 避免每次刷新都查文件
    """
    championName = player.get('championName') or ''
    championId = resolveChampionId(championName)

    if championId:
        championIcon = await connector.getChampionIcon(championId)
    else:
        championIcon = 'app/resource/images/champion-0.png'

    riotId = player.get('riotId') or player.get('summonerName') or ''
    name, _, tagLine = riotId.partition('#')

    scores = player.get('scores') or {}
    items = []

    for item in (player.get('items') or [])[:6]:
        itemId = item.get('itemID') or 0

        if itemIcons is not None and itemId in itemIcons:
            items.append(itemIcons[itemId])
            continue

        icon = await connector.getItemIcon(itemId)

        if itemIcons is not None:
            itemIcons[itemId] = icon

        items.append(icon)

    card = {
        'side': side,
        'phase': phase,
        'key': riotId or f"{side}-{championId}-{player.get('level', 0)}",
        'name': name.strip(),
        'tagLine': tagLine.strip(),
        'puuid': '',
        'isAnonymous': not bool(name.strip()),
        'isPublic': True,
        'championId': championId,
        'championName': championName,
        'championIcon': championIcon,
        'position': normalizePosition(player.get('position')),
        'rank': {},
        'kda': [scores.get('kills', 0), scores.get('deaths', 0),
                scores.get('assists', 0)],
        'kdaValue': 0.0,
        'winRate': None,
        'recent': [],
        'streak': ('', 0),
        'premade': 0,
        'fateFlag': None,
        'mains': [],
        'games': [],
        'pickState': '',
        'live': {
            'level': player.get('level') or 0,
            'kills': scores.get('kills', 0),
            'deaths': scores.get('deaths', 0),
            'assists': scores.get('assists', 0),
            'cs': scores.get('creepScore', 0),
            'items': items,
            'isDead': bool(player.get('isDead')),
            'respawn': player.get('respawnTimer') or 0,
            'wardScore': scores.get('wardScore', 0),
        },
    }

    card['badges'] = buildBadges(card)

    return card


def averageWinRate(cards) -> float:
    values = [c['winRate'] for c in cards if c.get('winRate') is not None]

    if not values:
        return 0.0

    return round(sum(values) / len(values), 1)


def sortCards(cards, mode='order'):
    """排序: 分路 / 胜率 / KDA

    未知项一律排到最后, 避免出现"没数据的人占了第一行"。
    """
    cards = list(cards)

    if mode == 'winrate':
        return sorted(cards,
                      key=lambda c: (c.get('winRate') is None,
                                     -(c.get('winRate') or 0)))

    if mode == 'kda':
        return sorted(cards,
                      key=lambda c: (not c.get('kdaValue'),
                                     -(c.get('kdaValue') or 0)))

    return alignByPosition(cards)


def alignByPosition(cards):
    """按分路排序, 让两列的上单对得上上单

    分路够用才排。原先是「只要有一张卡没有 position 就整列放弃」, 于是
    对局中只要有一个人的位置数据迟到, 整列立刻退回楼层顺序 —— 这就是
    「楼层对楼层」的来源。现在改成「有分路的按分路排, 没分路的排到最后
    并保持原顺序」, 再要求至少 3 张卡有位置, 免得 BP 阶段敌方一个都没有
    时把楼层顺序搅乱。

    两列各自排, 只要双方都用同一套 `POSITION_ORDER`, 第 N 行就是同一个分路。
    """
    if not cards:
        return []

    known = [c for c in cards if c.get('position') in POSITION_ORDER]

    if len(known) < min(3, len(cards)):
        return list(cards)

    # sorted 是稳定排序: 同样取到 99 的几张卡保持原来的相对顺序
    return sorted(cards,
                  key=lambda c: POSITION_ORDER.index(c['position'])
                  if c.get('position') in POSITION_ORDER else 99)
