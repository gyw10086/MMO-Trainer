import itertools
import time
from copy import deepcopy

import asyncio
from PyQt5.QtCore import QObject

from .exceptions import SummonerRankInfoNotFound
from .positions import fromItems, fromSGP, lookup, resolve, resolveOne
from ..common.config import cfg, Language
from ..common.logger import logger
from ..lol.connector import connector
from ..common.signals import signalBus


SERVERS_NAME = {
    "NJ100": "联盟一区", "GZ100": "联盟二区", "CQ100": "联盟三区", "TJ100": "联盟四区", "TJ101": "联盟五区",
    "HN10": "黑色玫瑰", "HN1": "艾欧尼亚", "BGP2": "峡谷之巅"
}

SERVERS_SUBSET = {
    "NJ100": ["祖安", "皮尔特沃夫", "巨神峰", "教育网", "男爵领域", "均衡教派", "影流", "守望之海"],
    "GZ100": ["卡拉曼达", "暗影岛", "征服之海", "诺克萨斯", "战争学院", "雷瑟守备"],
    "CQ100": ["班德尔城", "裁决之地", "水晶之痕", "钢铁烈阳", "皮城警备"],
    "TJ100": ["比尔吉沃特", "弗雷尔卓德", "扭曲丛林"],
    "TJ101": ["德玛西亚", "无畏先锋", "恕瑞玛", "巨龙之巢"]
}


class ToolsTranslator(QObject):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.top = self.tr("TOP")
        self.jungle = self.tr("JUG")
        self.middle = self.tr("MID")
        self.bottom = self.tr("BOT")
        self.support = self.tr("SUP")

        # 显示文案直接写中文：i18n 文件里没有 TOP/MID 这些词条，
        # 之前 OP.GG 出装页标题一直挂着英文「MID」
        self.positionMap = {
            "TOP": "上路",
            "JUNGLE": "打野",
            "MID": "中路",
            "ADC": "下路",
            "SUPPORT": "辅助",
        }

        self.rankedSolo = self.tr('Ranked Solo')
        self.rankedFlex = self.tr("Ranked Flex")

        self.unranked = self.tr("Unranked")
        self.unknown = self.tr("Unknown")


def translateTier(orig: str, short=False) -> str:
    if orig == '':
        return "--"

    maps = {
        'Iron': ['坚韧黑铁', '黑铁'],
        'Bronze': ['英勇黄铜', '黄铜'],
        'Silver': ['不屈白银', '白银'],
        'Gold': ['荣耀黄金', '黄金'],
        'Platinum': ['华贵铂金', '铂金'],
        'Emerald': ['流光翡翠', '翡翠'],
        'Diamond': ['璀璨钻石', '钻石'],
        'Master': ['超凡大师', '大师'],
        'Grandmaster': ['傲世宗师', '宗师'],
        'Challenger': ['最强王者', '王者'],
    }

    index = 1 if short else 0

    if cfg.language.value == Language.ENGLISH:
        return orig.capitalize()
    else:
        return maps[orig.capitalize()][index]


def timeStampToStr(stamp):
    """
    @param stamp: Millisecond timestamp
    """
    timeArray = time.localtime(stamp / 1000)
    return time.strftime("%Y/%m/%d %H:%M", timeArray)


def timeStampToShortStr(stamp):
    timeArray = time.localtime(stamp / 1000)
    return time.strftime("%m/%d", timeArray)


def secsToStr(secs):
    return time.strftime("%M:%S", time.gmtime(secs))


async def getRecentTeammates(games, puuid):
    summoners = {}

    for game in games:
        gameId = game['gameId']
        # 详情请求会覆盖 game, 时间要先取出来
        lastStr = game.get('time') or ''
        lastStamp = game.get('timeStamp') or 0
        game = await connector.getGameDetailByGameId(gameId)
        teammates = getTeammates(game, puuid)

        for p in teammates['summoners']:
            if p['summonerId'] == 0:
                continue

            if p['puuid'] not in summoners:
                summonerIcon = await connector.getProfileIcon(p['icon'])
                summoners[p['puuid']] = {
                    "name": p['name'], 'icon': summonerIcon,
                    "total": 0, "wins": 0, "losses": 0, "puuid": p["puuid"],
                    "lastStr": lastStr, "lastStamp": lastStamp}
            elif lastStamp > summoners[p['puuid']].get('lastStamp', 0):
                # games 是按时间倒序给的, 但还是按时间戳判一次, 别依赖顺序
                summoners[p['puuid']]['lastStr'] = lastStr
                summoners[p['puuid']]['lastStamp'] = lastStamp

            summoners[p['puuid']]['total'] += 1

            if not teammates['remake']:
                if teammates['win']:
                    summoners[p['puuid']]['wins'] += 1
                else:
                    summoners[p['puuid']]['losses'] += 1

    ret = {"puuid": puuid, "summoners": [
        item for item in summoners.values()]}

    # 右栏只放 5 行, 但「查看全部」要看得到更多人; 上限 12 是防止
    # 一次搜出几十个只同队一把的人, 反而看不出规律
    ret['summoners'] = sorted(ret['summoners'],
                              key=lambda x: x['total'], reverse=True)[:12]

    return ret


async def parseSummonerData(summoner, rankTask, gameTask):
    # 这几个字段用 get 兜底：LCU 对久未登录 / 已注销的号可能返回不全，
    # 一个 KeyError 会让整个生涯页停在 loading 上，比显示 0 级难查得多
    iconId = summoner.get('profileIconId') or 0
    icon = await connector.getProfileIcon(iconId)
    level = summoner.get('summonerLevel') or 0
    xpSinceLastLevel = summoner.get('xpSinceLastLevel') or 0
    xpUntilNextLevel = summoner.get('xpUntilNextLevel') or 0

    try:
        gamesInfo = await gameTask
    except:
        champions = []
        games = {}
    else:
        games = {
            "gameCount": gamesInfo.get("gameCount") or 0,
            "wins": 0,
            "losses": 0,
            "kills": 0,
            "deaths": 0,
            "assists": 0,
            "games": [],
        }
        rawGames = gamesInfo.get("games") or []
        # 分路另走一份 SGP：缓存命中时几乎零成本，拿不到就返回空表，
        # 由 parseGameData 退回算法 / 老字段，不影响整页加载
        positions = await resolve(summoner['puuid'], rawGames)

        for game in rawGames:
            # 单场解析失败只跳过这一场：一场数据结构异常不该让整页空白
            try:
                info = await parseGameData(game, positions)
            except Exception as e:
                logger.warning(f'parse game failed: {e}', 'ParseSummoner')
                continue

            if time.time() - info["timeStamp"] / 1000 > 60 * 60 * 24 * 365:
                continue
            if not info["remake"] and info["queueId"] != 0:
                games["kills"] += info["kills"]
                games["deaths"] += info["deaths"]
                games["assists"] += info["assists"]
                if info["win"]:
                    games["wins"] += 1
                else:
                    games["losses"] += 1
            games["games"].append(info)

        champions = getRecentChampions(games['games'])

    try:
        rankInfo = await rankTask
    except SummonerRankInfoNotFound:
        rankInfo = {}

    return {
        'name': summoner.get("gameName") or summoner.get('displayName') or '--',
        'icon': icon,
        'level': level,
        'xpSinceLastLevel': xpSinceLastLevel,
        'xpUntilNextLevel': xpUntilNextLevel,
        'puuid': summoner.get('puuid') or '',
        'rankInfo': rankInfo,
        'games': games,
        'champions': champions,
        # 缺 privacy 时按「不公开」处理：宁可少显示段位，也别把私密号的信息摊开
        'isPublic': summoner.get('privacy') == "PUBLIC",
        'tagLine': summoner.get("tagLine"),
    }


def gameScore(kills, deaths, assists, damage, cs, durationSec):
    """0-10 表现分 (自算, 非官方评分, 只用于横向比较)

    重开和缺失时长返回 0, 让"没打完的局"不要混进平均分。
    三个维度都做了封顶, 避免大顺风局把分数拉成 12 分这种离谱值。
    """
    minutes = (durationSec or 0) / 60

    if minutes <= 0:
        return 0.0

    kda = (kills + assists) / max(1, deaths)
    dpm = damage / minutes      # 分均伤害
    cspm = cs / minutes         # 分均补刀

    score = (min(kda, 8) / 8) * 5.5 + min(dpm / 1200, 1) * 2.5 + min(
        cspm / 9, 1) * 2

    return round(min(10.0, score), 1)


async def parseGameData(game, positions=None):
    """解析一条 LCU 对局记录

    @param positions: `{gameId: 'TOP'|'JUNGLE'|...}`，由 `positions.resolve()`
                      从 SGP 拿到的权威分路。不给就退回算法 + 老 lane/role。
    """
    timeStamp = game["gameCreation"]  # 毫秒级时间戳
    time = timeStampToStr(game['gameCreation'])
    shortTime = timeStampToShortStr(game['gameCreation'])
    gameId = game['gameId']
    duration = secsToStr(game['gameDuration'])
    queueId = game['queueId']

    nameAndMap = connector.manager.getNameMapByQueueId(queueId)
    modeName = nameAndMap['name']

    if queueId != 0:
        mapName = nameAndMap['map']
    else:
        mapName = connector.manager.getMapNameById(game['mapId'])

    participant = game['participants'][0]
    championId = participant['championId']
    championIcon = await connector.getChampionIcon(championId)
    spell1Id = participant['spell1Id']
    spell2Id = participant['spell2Id']
    spell1Icon = await connector.getSummonerSpellIcon(spell1Id)
    spell2Icon = await connector.getSummonerSpellIcon(spell2Id)
    stats = participant['stats']

    champLevel = stats['champLevel']
    kills = stats['kills']
    deaths = stats['deaths']
    assists = stats['assists']
    itemIds = [
        stats['item0'],
        stats['item1'],
        stats['item2'],
        stats['item3'],
        stats['item4'],
        stats['item5'],
        stats['item6'],
    ]

    itemIcons = [await connector.getItemIcon(itemId) for itemId in itemIds]
    runeId = stats['perk0']
    runeIcon = await connector.getRuneIcon(runeId)

    cs = stats['totalMinionsKilled'] + stats['neutralMinionsKilled']
    gold = stats['goldEarned']
    damage = stats.get('totalDamageDealtToChampions', 0) or 0
    durationSec = game.get('gameDuration') or 0
    remake = stats['gameEndedInEarlySurrender']
    win = stats['win']

    # 分路：优先用上层从 SGP 拿到的官方结果（teamPosition），拿不到才退回
    # 算法（惩戒/辅助装）和老 lane+role。原来无条件认 `role == 'SUPPORT'`，
    # 中路局它也返回 SUPPORT，于是「中路显示辅助」。
    position = ''

    if queueId in [420, 440]:
        position = resolveOne(participant, lookup(positions, gameId))

    return {
        'queueId': queueId,
        'gameId': gameId,
        'time': time,
        'shortTime': shortTime,
        'name': modeName,
        'map': mapName,
        'duration': duration,
        'remake': remake,
        'win': win,
        'championId': championId,
        # 行内要显示英雄名, 原来只取图标, 名字得再从管理器查一次
        'championName': connector.manager.getChampionNameById(championId) or '',
        'championIcon': championIcon,
        'spell1Icon': spell1Icon,
        'spell2Icon': spell2Icon,
        'champLevel': champLevel,
        'kills': kills,
        'deaths': deaths,
        'assists': assists,
        'itemIcons': itemIcons,
        'runeIcon': runeIcon,
        'cs': cs,
        'gold': gold,
        'damage': damage,
        'durationSec': durationSec,
        'score': gameScore(kills, deaths, assists, damage, cs, durationSec),
        'timeStamp': timeStamp,
        'position': position,
    }


async def parseGameDetailData(puuid, game):
    queueId = game['queueId']
    mapId = game['mapId']

    names = connector.manager.getNameMapByQueueId(queueId)
    modeName = names['name']
    if queueId != 0:
        mapName = names['map']
    else:
        mapName = connector.manager.getMapNameById(mapId)

    def origTeam(teamId):
        return {
            'win': None,
            'bans': [],
            'banNames': [],
            'baronKills': 0,
            'baronIcon': f"app/resource/images/baron-{teamId}.png",
            'dragonKills': 0,
            'dragonIcon': f'app/resource/images/dragon-{teamId}.png',
            'riftHeraldKills': 0,
            'riftHeraldIcon': f'app/resource/images/herald-{teamId}.png',
            'inhibitorKills': 0,
            'inhibitorIcon': f'app/resource/images/inhibitor-{teamId}.png',
            'hordeKills': 0,
            'towerKills': 0,
            'towerIcon': f'app/resource/images/tower-{teamId}.png',
            'kills': 0,
            'deaths': 0,
            'assists': 0,
            'gold': 0,
            'summoners': []
        }

    teams = {
        100: origTeam("100"),
        200: origTeam("200"),
        300: origTeam("100"),
        400: origTeam("200"),
        500: origTeam("100"),
        600: origTeam("200"),
        700: origTeam("100"),
        800: origTeam("200"),
    }

    cherryResult = None
    win = None
    # 重开标记只在「这一局里有我」那个分支里赋值；被查的人不在场上时必须有个
    # 初值，否则下面 return 里引用它就是 NameError
    remake = False

    for team in game['teams']:
        teamId = team['teamId']

        if teamId == 0:
            teamId = 200

        teams[teamId]['win'] = team['win']
        teams[teamId]['bans'] = [
            await connector.getChampionIcon(item['championId'])
            for item in team['bans']
        ]
        # 详情页的禁用格要带名字做 tooltip, 只有图标认不出是谁
        teams[teamId]['banNames'] = [
            connector.manager.getChampionNameById(item['championId']) or ''
            for item in team['bans']
        ]
        teams[teamId]['baronKills'] = team['baronKills']
        teams[teamId]['dragonKills'] = team['dragonKills']
        teams[teamId]['riftHeraldKills'] = team['riftHeraldKills']
        teams[teamId]['hordeKills'] = team['hordeKills']
        teams[teamId]['towerKills'] = team['towerKills']
        teams[teamId]['inhibitorKills'] = team['inhibitorKills']

    for participant in game['participantIdentities']:
        participantId = participant['participantId']
        summonerName = participant['player'].get(
            'gameName') or participant['player'].get('summonerName')  # 兼容外服
        summonerPuuid = participant['player']['puuid']
        isCurrent = (summonerPuuid == puuid)

        if summonerPuuid == '00000000-0000-0000-0000-000000000000':  # AI
            isPublic = True
        else:
            t = await connector.getSummonerByPuuid(summonerPuuid)
            isPublic = t.get("privacy") == "PUBLIC"

        for summoner in game['participants']:
            if summoner['participantId'] == participantId:
                stats = summoner['stats']

                if queueId != 1700:
                    subteamPlacement = None
                    tid = summoner['teamId']
                else:
                    subteamPlacement = stats['subteamPlacement']
                    tid = subteamPlacement * 100

                if isCurrent:
                    remake = stats['gameEndedInEarlySurrender']
                    win = stats['win']

                    if queueId == 1700:
                        cherryResult = subteamPlacement

                championId = summoner['championId']
                championIcon = await connector.getChampionIcon(championId)

                spell1Id = summoner['spell1Id']
                spell1Icon = await connector.getSummonerSpellIcon(spell1Id)
                spell2Id = summoner['spell2Id']
                spell2Icon = await connector.getSummonerSpellIcon(spell2Id)

                kills = stats['kills']
                deaths = stats['deaths']
                assists = stats['assists']
                gold = stats['goldEarned']

                teams[tid]['kills'] += kills
                teams[tid]['deaths'] += deaths
                teams[tid]['assists'] += assists
                teams[tid]['gold'] += gold

                runeIcon = await connector.getRuneIcon(stats['perk0'])

                itemIds = [
                    stats['item0'],
                    stats['item1'],
                    stats['item2'],
                    stats['item3'],
                    stats['item4'],
                    stats['item5'],
                    stats['item6'],
                ]

                itemIcons = [
                    await connector.getItemIcon(itemId) for itemId in itemIds
                ]

                getRankInfo = cfg.get(cfg.showTierInGameInfo)

                tier = division = lp = rankIcon = ""
                if getRankInfo:
                    try:
                        rank = await connector.getRankedStatsByPuuid(
                            summonerPuuid)
                    except SummonerRankInfoNotFound:
                        ...
                    else:
                        rank = rank['queueMap']

                        if queueId == 1700 and 'CHERRY' in rank:
                            rankInfo = rank["CHERRY"]
                            lp = rankInfo['ratedRating']
                        else:
                            rankInfo = rank[
                                'RANKED_FLEX_SR'] if queueId == 440 else rank['RANKED_SOLO_5x5']

                            tier = rankInfo['tier']
                            division = rankInfo['division']
                            lp = rankInfo['leaguePoints']

                            if tier == '':
                                rankIcon = 'app/resource/images/unranked.png'
                            else:
                                rankIcon = f'app/resource/images/{tier.lower()}.png'
                                tier = translateTier(tier, True)

                            if division == 'NA':
                                division = ''

                item = {
                    'summonerName': summonerName,
                    'championId': championId,
                    # 行里名字下面要写英雄名; 图标认英雄在 19px 下不现实
                    'championName': connector.manager.getChampionNameById(championId) or '',
                    'puuid': summonerPuuid,
                    'isCurrent': isCurrent,
                    'championIcon': championIcon,
                    'rankInfo': getRankInfo,
                    'tier': tier,
                    'division': division,
                    'lp': lp,
                    'rankIcon': rankIcon,
                    'spell1Icon': spell1Icon,
                    'spell2Icon': spell2Icon,
                    'itemIcons': itemIcons,
                    'kills': kills,
                    'deaths': deaths,
                    'assists': assists,
                    'cs': stats['totalMinionsKilled'] + stats['neutralMinionsKilled'],
                    'gold': gold,
                    'runeIcon': runeIcon,
                    'champLevel': stats['champLevel'],
                    'demage': stats['totalDamageDealtToChampions'],
                    'subteamPlacement': subteamPlacement,
                    'isPublic': isPublic
                }
                teams[tid]['summoners'].append(item)

                break

    mapIcon = connector.manager.getMapIconByMapId(mapId, win)

    if win is None:
        # 拿不到「我是谁」不该让整份详情打不开 —— 看好友单排的对局时，
        # 调用方传的是被查人的 puuid 而不是登录账号，这局里可能一个人都对不上。
        # 结构照常返回，只是胜负未知（视图那边显示「—」）
        logger.warning(
            f'对局 {game["gameId"]} 里找不到 puuid={str(puuid)[:8]}，胜负未知',
            'GameDetail')

    return {
        'gameId': game['gameId'],
        'mapIcon': mapIcon,
        'gameCreation': timeStampToStr(game['gameCreation']),
        'gameDuration': secsToStr(game['gameDuration']),
        'modeName': modeName,
        'mapName': mapName,
        'queueId': queueId,
        'win': win,
        'cherryResult': cherryResult,
        'remake': remake,
        'teams': teams,
    }


def playerDisplayName(player: dict) -> str:
    """对局里的玩家显示名

    新版 LCU 把 summonerName 置空了（改名 gameName + tagLine），
    直接取 summonerName 会得到一串空字符串，界面上就是一行「—」。
    """
    name = (player.get('summonerName') or player.get('gameName') or '').strip()
    tag = (player.get('tagLine') or '').strip()

    if name and tag:
        return f'{name} #{tag}'

    return name

def getTeammates(game, targetPuuid):
    """
    通过 game 信息获取目标召唤师的队友

    @param game: @see connector.getGameDetailByGameId
    @param targetPuuid: 目标召唤师 puuid
    @return: @see res
    """
    targetParticipantId = None

    for participant in game['participantIdentities']:
        puuid = participant['player']['puuid']

        if puuid == targetPuuid:
            targetParticipantId = participant['participantId']
            break

    assert targetParticipantId is not None

    for player in game['participants']:
        if player['participantId'] == targetParticipantId:
            if game['queueId'] != 1700:
                tid = player['teamId']
            else:  # 斗魂竞技场
                tid = player['stats']['subteamPlacement']

            win = player['stats']['win']
            remake = player['stats']['teamEarlySurrendered']

            break

    res = {
        'queueId': game['queueId'],
        'win': win,
        'remake': remake,
        'summoners': [],  # 队友召唤师 (由于兼容性, 未修改字段名)
        'enemies': []  # 对面召唤师, 若有多个队伍会全放这里面
    }

    for player in game['participants']:

        if game['queueId'] != 1700:
            cmp = player['teamId']
        else:
            cmp = player['stats']['subteamPlacement']

        p = player['participantId']
        s = game['participantIdentities'][p - 1]['player']

        if cmp == tid:
            if s['puuid'] != targetPuuid:
                res['summoners'].append(
                    {'summonerId': s['summonerId'],
                     'name': playerDisplayName(s),
                     'puuid': s['puuid'], 'icon': s['profileIcon']})
            else:
                # 当前召唤师在该对局使用的英雄, 自定义对局没有该字段
                res["championId"] = player.get('championId', -1)
        else:
            res['enemies'].append(
                {'summonerId': s['summonerId'],
                 'name': playerDisplayName(s),
                 'puuid': s['puuid'], 'icon': s['profileIcon']})

    return res


def getRecentChampions(games):
    champions = {}

    for game in games:
        if game['queueId'] == 0:
            continue

        championId = game['championId']

        if championId not in champions:
            # manager 在客户端没连上时是 None，这里不该让整页解析挂掉
            name = ''

            if connector.manager:
                name = connector.manager.getChampionNameById(championId) or ''

            champions[championId] = {
                'championId': championId,
                'name': name,
                'icon': game['championIcon'], 'wins': 0, 'losses': 0, 'total': 0}

        champions[championId]['total'] += 1

        if not game['remake']:
            if game['win']:
                champions[championId]['wins'] += 1
            else:
                champions[championId]['losses'] += 1

    ret = [item for item in champions.values()]
    ret.sort(key=lambda x: x['total'], reverse=True)

    maxLen = 10

    return ret if len(ret) < maxLen else ret[:maxLen]


def parseRankInfo(info):
    """
    解析 `connector.getRankedStatsByPuuid()` 的数据。


    api: `/lol-ranked/v1/ranked-stats/{puuid}`

    :param info: 接口返回值, 允许为空（接口异常时抛出 `SummonerRankInfoNotFound`, 需要捕获置空）

    """
    tt = ToolsTranslator()

    soloIcon = flexIcon = "app/resource/images/UNRANKED.svg"
    soloTier = flexTier = tt.unknown
    soloDivision = flexDivision = ""
    soloRankInfo = flexRankInfo = {"leaguePoints": ""}

    if info:
        soloRankInfo = info["queueMap"]["RANKED_SOLO_5x5"]
        flexRankInfo = info["queueMap"]["RANKED_FLEX_SR"]

        soloTier = soloRankInfo["tier"]
        soloDivision = soloRankInfo["division"]

        if soloTier == "":
            soloIcon = "app/resource/images/UNRANKED.svg"
            soloTier = tt.unranked
        else:
            soloIcon = f"app/resource/images/{soloTier}.svg"
            soloTier = translateTier(soloTier, True)
        if soloDivision == "NA":
            soloDivision = ""

        flexTier = flexRankInfo["tier"]
        flexDivision = flexRankInfo["division"]

        if flexTier == "":
            flexIcon = "app/resource/images/UNRANKED.svg"
            flexTier = tt.unranked
        else:
            flexIcon = f"app/resource/images/{flexTier}.svg"
            flexTier = translateTier(flexTier, True)
        if flexDivision == "NA":
            flexDivision = ""

    return {
        "solo": {
            "tier": soloTier,
            "icon": soloIcon,
            "division": soloDivision,
            "lp": soloRankInfo["leaguePoints"],
        },
        "flex": {
            "tier": flexTier,
            "icon": flexIcon,
            "division": flexDivision,
            "lp": flexRankInfo["leaguePoints"],
        },
    }


def parseRankInfoFromSGP(info):
    '''解析来自 `connector.getRankedStatsByPuuidViaSGP()` 的数据'''

    tt = ToolsTranslator()

    soloIcon = flexIcon = "app/resource/images/UNRANKED.svg"
    soloTier = flexTier = tt.unknown
    soloDivision = flexDivision = ""
    soloRankInfo = flexRankInfo = {"leaguePoints": ""}

    if info:
        for queue in info['queues']:
            type = queue['queueType']
            if type == 'RANKED_FLEX_SR':
                flexRankInfo = queue
            elif type == 'RANKED_SOLO_5x5':
                soloRankInfo = queue

        soloTier = soloRankInfo.get("tier", "")
        soloDivision = soloRankInfo.get("rank", "NA")

        if soloTier == "":
            soloIcon = "app/resource/images/UNRANKED.svg"
            soloTier = tt.unranked
        else:
            soloIcon = f"app/resource/images/{soloTier}.svg"
            soloTier = translateTier(soloTier, True)
        if soloDivision == "NA":
            soloDivision = ""

        flexTier = flexRankInfo.get("tier", "")
        flexDivision = flexRankInfo.get("rank", "NA")

        if flexTier == "":
            flexIcon = "app/resource/images/UNRANKED.svg"
            flexTier = tt.unranked
        else:
            flexIcon = f"app/resource/images/{flexTier}.svg"
            flexTier = translateTier(flexTier, True)
        if flexDivision == "NA":
            flexDivision = ""

    return {
        "solo": {
            "tier": soloTier,
            "icon": soloIcon,
            "division": soloDivision,
            "lp": soloRankInfo["leaguePoints"],
        },
        "flex": {
            "tier": flexTier,
            "icon": flexIcon,
            "division": flexDivision,
            "lp": flexRankInfo["leaguePoints"],
        },
    }


# 没打过的队列整条不会出现在 queueMap 里
EMPTY_RANK = {
    'tier': '', 'division': '', 'highestTier': '', 'highestDivision': '',
    'previousSeasonEndTier': '', 'previousSeasonEndDivision': '',
    'wins': 0, 'losses': 0, 'leaguePoints': 0,
}


def parseDetailRankInfo(rankInfo):
    """解析单双 / 灵活两行段位

    `queueMap` 只包含打过的队列 —— 号没打过单双或灵活时那一项根本不存在。
    原来直接 `rankInfo['queueMap']['RANKED_SOLO_5x5']` 取值，一个 KeyError
    就让 `repaintInterface` 整条断掉，表现是生涯页一片空白（这次用户报的
    「数据全没了」就是它）。缺队列时按「未定级」显示。
    """
    queues = rankInfo.get('queueMap') or {}
    soloRankInfo = queues.get('RANKED_SOLO_5x5') or EMPTY_RANK
    soloTier = translateTier(soloRankInfo['tier'])
    soloDivision = soloRankInfo['division']
    if soloTier == '--' or soloDivision == 'NA':
        soloDivision = ""

    soloHighestTier = translateTier(soloRankInfo['highestTier'])
    soloHighestDivision = soloRankInfo['highestDivision']
    if soloHighestTier == '--' or soloHighestDivision == 'NA':
        soloHighestDivision = ""

    solxPreviousSeasonEndTier = translateTier(
        soloRankInfo['previousSeasonEndTier'])
    soloPreviousSeasonDivision = soloRankInfo[
        'previousSeasonEndDivision']
    if solxPreviousSeasonEndTier == '--' or soloPreviousSeasonDivision == 'NA':
        soloPreviousSeasonDivision = ""

    soloWins = soloRankInfo['wins']
    soloLosses = soloRankInfo['losses']
    soloTotal = soloWins + soloLosses
    soloWinRate = soloWins * 100 // soloTotal if soloTotal != 0 else 0
    soloLp = soloRankInfo['leaguePoints']

    flexRankInfo = queues.get('RANKED_FLEX_SR') or EMPTY_RANK
    flexTier = translateTier(flexRankInfo['tier'])
    flexDivision = flexRankInfo['division']
    if flexTier == '--' or flexDivision == 'NA':
        flexDivision = ""

    flexHighestTier = translateTier(flexRankInfo['highestTier'])
    flexHighestDivision = flexRankInfo['highestDivision']
    if flexHighestTier == '--' or flexHighestDivision == 'NA':
        flexHighestDivision = ""

    flexPreviousSeasonEndTier = translateTier(
        flexRankInfo['previousSeasonEndTier'])
    flexPreviousSeasonEndDivision = flexRankInfo[
        'previousSeasonEndDivision']

    if flexPreviousSeasonEndTier == '--' or flexPreviousSeasonEndDivision == 'NA':
        flexPreviousSeasonEndDivision = ""

    flexWins = flexRankInfo['wins']
    flexLosses = flexRankInfo['losses']
    flexTotal = flexWins + flexLosses
    flexWinRate = flexWins * 100 // flexTotal if flexTotal != 0 else 0
    flexLp = flexRankInfo['leaguePoints']

    pt = ToolsTranslator()

    return [
        [
            pt.rankedSolo,
            str(soloTotal),
            str(soloWinRate) + ' %' if soloTotal != 0 else '--',
            str(soloWins),
            str(soloLosses),
            f'{soloTier} {soloDivision}',
            str(soloLp),
            f'{soloHighestTier} {soloHighestDivision}',
            f'{solxPreviousSeasonEndTier} {soloPreviousSeasonDivision}',
        ],
        [
            pt.rankedFlex,
            str(flexTotal),
            str(flexWinRate) + ' %' if flexTotal != 0 else '--',
            str(flexWins),
            str(flexLosses),
            f'{flexTier} {flexDivision}',
            str(flexLp),
            f'{flexHighestTier} {flexHighestDivision}',
            f'{flexPreviousSeasonEndTier} {flexPreviousSeasonEndDivision}',
        ],
    ]


def parseGames(games, targetId=0):
    f"""
    解析 Games 数据

    @param targetId: 需要查询的游戏模式, 不传则收集所有模式的数据
    @param games: 由 @see: {parseGameData} 获取到的games数据
    @return: hitGame, K, D, A, win, loss
    @rtype: tuple[list, int, int, int, int, int, int]
    """

    kills, deaths, assists, wins, losses = 0, 0, 0, 0, 0
    hitGames = []

    for game in games:
        if not targetId or game['queueId'] == targetId:
            hitGames.append(game)

            if not game['remake']:
                kills += game['kills']
                deaths += game['deaths']
                assists += game['assists']

                if game['win']:
                    wins += 1
                else:
                    losses += 1

    return hitGames, kills, deaths, assists, wins, losses


def buildGameSummary(gamesInfo):
    """从已解析的战绩里算出卡面需要的汇总数据

    @return: {'winRate': int|None, 'recent': list[str], 'streak': (type, n)}
    """
    games = [g for g in gamesInfo or [] if g and not g.get('remake')]
    wins = sum(1 for g in games if g.get('win'))
    total = len(games)

    recent = ['W' if g.get('win') else 'L' for g in games[:5]]

    streakType, streakCount = '', 0

    if recent:
        streakType = 'win' if recent[0] == 'W' else 'lose'
        target = 'W' if streakType == 'win' else 'L'

        for result in recent:
            if result != target:
                break

            streakCount += 1

    return {
        'winRate': round(wins / total * 100) if total else None,
        'recent': recent,
        'streak': (streakType, streakCount),
        'wins': wins,
        'losses': total - wins,
    }


def countPremade(team):
    """统计同队开黑人数 (含自己), 返回 {summonerId: 人数}

    `teamParticipantId` 相同即为同一预组队, LCU 在选人和 gameflow 里都会给。
    """
    groups = {}

    for player in team or []:
        participantId = player.get('teamParticipantId')

        if not participantId:
            continue

        groups.setdefault(participantId, []).append(player.get('summonerId'))

    result = {}

    for ids in groups.values():
        if len(ids) < 2:
            continue

        for summonerId in ids:
            if summonerId:
                result[summonerId] = len(ids)

    return result


def getPickState(item):
    """选人状态: 已锁定 / 选择中 / 未选"""
    if item.get('championId'):
        return 'locked'

    if item.get('championPickIntent'):
        return 'picking'

    return ''


async def makeAnonymousSummoner(item):
    """匿名 / 主播模式玩家的占位数据

    客户端把身份抹成 `nameVisibilityType = HIDDEN` 并只给 obfuscated 字段, 这里
    **不做任何还原**, 只保留客户端公开的阵营、分路、英雄与选人状态, 让这一行不再
    整个消失 (否则连"对面几楼是什么位置"都看不出来)。
    """
    championId = item.get('championId') or 0

    try:
        championIcon = await connector.getChampionIcon(championId)
    except Exception:
        championIcon = 'app/resource/images/champion-0.png'

    cellId = item.get('cellId') or 0

    # 自定义房间等场景敌方身份是可见的, 有名字就照实显示
    return {
        'name': item.get('gameName') or '',
        'tagLine': item.get('tagLine') or '',
        'icon': championIcon,
        'championId': championId,
        'championIcon': championIcon,
        'level': 0,
        'rankInfo': {'solo': {}, 'flex': {}},
        'gamesInfo': [],
        'xpSinceLastLevel': 0,
        'xpUntilNextLevel': 0,
        'puuid': '',
        # 负数 + cellId, 保证同队多个匿名玩家不会互相覆盖
        'summonerId': -(cellId + 1),
        'kda': [0, 0, 0],
        'cellId': cellId,
        'selectedPosition': item.get('assignedPosition') or '',
        'fateFlag': None,
        'isPublic': False,
        'isAnonymous': True,
        'summary': {'winRate': None, 'recent': [], 'streak': ('', 0)},
        'recentlyChampionName': '',
        'pickState': getPickState(item),
    }


async def parseAllyGameInfo(session, currentSummonerId, queueID, useSGP=False):

    if useSGP and connector.isInTencent():
        # 如果是国服就优先尝试 SGP
        try:
            tasks = [getSummonerGamesInfoViaSGP(item, queueID, currentSummonerId)
                     for item in session['myTeam']]
            summoners = await asyncio.gather(*tasks)
        except:
            tasks = [parseSummonerGameInfo(item, queueID, currentSummonerId)
                     for item in session['myTeam']]
            summoners = await asyncio.gather(*tasks)

    else:
        tasks = [parseSummonerGameInfo(item, queueID, currentSummonerId)
                 for item in session['myTeam']]
        summoners = await asyncio.gather(*tasks)

    summoners = [summoner for summoner in summoners if summoner]

    # 按照楼层排序
    summoners = sorted(
        summoners, key=lambda x: x["cellId"])

    premade = countPremade(session.get('myTeam'))

    for summoner in summoners:
        summoner['summary'] = buildGameSummary(summoner.get('gamesInfo'))
        summoner['premade'] = premade.get(summoner.get('summonerId'), 0)

    champions = {summoner['summonerId']: summoner['championId']
                 for summoner in summoners}
    order = [summoner['summonerId'] for summoner in summoners]

    return {'summoners': summoners, 'champions': champions, 'order': order, "isAram": session.get('benchEnabled', False)}


async def parseEnemyGameInfo(session):
    """选人阶段的敌方 5 人

    客户端对敌方只下发**已锁定**的 `championId`, 其余全部抹掉:
    `nameVisibilityType = HIDDEN`、`summonerId = 0`、`assignedPosition = ""`、
    `spell1Id/spell2Id = 0`, 连 `championPickIntent` 也恒为 0 —— 也就是说
    "对面正在犹豫哪个英雄"这件事客户端根本不给, 不是本工具拿不到。

    因此这里只按 `cellId` 造占位卡, **不查任何战绩**: 排位里查不到, 自定义房间
    也没必要在 BP 阶段打满 LCU 请求额度。
    """
    theirTeam = session.get('theirTeam') or []

    # 大乱斗是 10 人各自从英雄池里选, 没有"敌方阵容"这个概念
    if not theirTeam or session.get('benchEnabled'):
        return None

    summoners = await asyncio.gather(
        *[makeAnonymousSummoner(item) for item in theirTeam])

    # cellId 就是楼层顺序 (0-4), 敌方没有分路可用, 不要再按胜率排
    summoners = sorted(summoners, key=lambda x: x['cellId'])

    champions = {summoner['summonerId']: summoner['championId']
                 for summoner in summoners}
    order = [summoner['summonerId'] for summoner in summoners]

    return {'summoners': summoners, 'champions': champions, 'order': order}


def collectExcludedChampions(session):
    """本局已经被 ban 或已被选的英雄

    用来过滤"克制英雄"推荐: 已经被 ban 掉的、两队已经选走的都不能再推荐。

    注意**不能读 `session['bans']`**: 实测国服那个字段经常是空的
    (`numBans = 0`, 两个数组都空), 真正的 ban 记录在 `actions` 里。
    """
    excluded = set()

    for group in session.get('actions') or []:
        for action in group:
            championId = action.get('championId') or 0

            if championId and action.get('type') in ('ban', 'pick'):
                excluded.add(championId)

    for team in ('myTeam', 'theirTeam'):
        for player in session.get(team) or []:
            championId = player.get('championId') or 0

            if championId:
                excluded.add(championId)

    return excluded


def parseSummonerOrder(team):
    summoners = [{
        'summonerId': s['summonerId'],
        'cellId': s['cellId']
    } for s in team]

    summoners.sort(key=lambda x: x['cellId'])
    return [s['summonerId'] for s in summoners if s['summonerId'] != 0]


async def parseGameInfoByGameflowSession(session, currentSummonerId, side, useSGP=False):
    data = session['gameData']
    queueId = data['queue']['id']

    if queueId in (1700, 1090, 1100, 1110, 1130, 1160):  # 斗魂 云顶匹配 (排位)
        return None

    if side == 'enemy':
        _, team = separateTeams(data, currentSummonerId)
    else:
        team, _ = separateTeams(data, currentSummonerId)

    if useSGP and connector.isInTencent():
        # 如果是国服就优先尝试 SGP
        try:
            tasks = [getSummonerGamesInfoViaSGP(item, queueId, currentSummonerId)
                     for item in team]
            summoners = await asyncio.gather(*tasks)

        except:
            tasks = [parseSummonerGameInfo(item, queueId, currentSummonerId)
                     for item in team]
            summoners = await asyncio.gather(*tasks)

    else:
        tasks = [parseSummonerGameInfo(item, queueId, currentSummonerId)
                 for item in team]
        summoners = await asyncio.gather(*tasks)

    summoners = [summoner for summoner in summoners if summoner]

    if queueId in [420, 440]:
        s = sortedSummonersByGameRole(summoners)

        if s != None:
            summoners = s

    premade = countPremade(team)

    for summoner in summoners:
        summoner['summary'] = buildGameSummary(summoner.get('gamesInfo'))
        summoner['premade'] = premade.get(summoner.get('summonerId'), 0)

    champions = {summoner['summonerId']: summoner['championId']
                 for summoner in summoners}
    order = [summoner['summonerId'] for summoner in summoners]

    return {'summoners': summoners, 'champions': champions, 'order': order}


GAME_ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def getSummonerGameRole(summoner):
    """取一条名单数据的分路, 归一化成 GAME_ROLES 里的值

    两个来源的字段不一样, 必须都兼容:
    - 解析后的召唤师 (parseSummonerGameInfo) 用 `selectedPosition`
    - gameflow 原始名单 (session.gameData.teamOne) 只有 `assignedPosition`,
      而且是小写 (`top` / `middle`)

    取不到就返回空串, 由调用方决定降级方式 —— 原来这里直接下标取值,
    进对局时 gameflow 数据没有 `selectedPosition`, 会抛 KeyError 把整个
    状态机打崩。
    """
    raw = summoner.get('selectedPosition') or summoner.get(
        'assignedPosition') or ''
    raw = str(raw).upper()

    if raw == 'MID':
        raw = 'MIDDLE'

    return raw if raw in GAME_ROLES else ''


def sortedSummonersByGameRole(summoners: list):
    if any(not getSummonerGameRole(x) for x in summoners):
        return None

    return sorted(summoners, key=lambda x: GAME_ROLES.index(
        getSummonerGameRole(x)))


def getAllyOrderByGameRole(session, currentSummonerId):
    data = session['gameData']
    queueId = data['queue']['id']

    # 只有排位模式下有返回值
    if queueId not in (420, 440):
        return None

    ally, _ = separateTeams(data, currentSummonerId)
    ally = sortedSummonersByGameRole(ally)

    if ally == None:
        return None

    return [x['summonerId'] for x in ally]


def getTeamColor(session, currentSummonerId):
    '''
    输入 session 以及当前召唤师 id，输出 summonerId -> 颜色的映射
    '''
    data = session['gameData']
    ally, enemy = separateTeams(data, currentSummonerId)

    def makeTeam(team):
        # teamParticipantId => [summonerId]
        tIdToSIds = {}

        for s in team:
            summonerId = s.get('summonerId')
            if not summonerId:
                continue

            teamParticipantId = s.get('teamParticipantId')
            if not teamParticipantId:
                continue

            summoners = tIdToSIds.get(teamParticipantId)

            if not summoners:
                tIdToSIds[teamParticipantId] = [summonerId]
            else:
                tIdToSIds[teamParticipantId].append(summonerId)

        # summonerId => color
        res = {}

        currentColor = 0

        for ids in tIdToSIds.values():
            if len(ids) == 1:
                res[ids[0]] = -1
            else:
                for id in ids:
                    res[id] = currentColor

                currentColor += 1

        return res

    return makeTeam(ally), makeTeam(enemy)


def separateTeams(data, currentSummonerId):
    """分出 (我方, 敌方)

    观战 / 回放时 10 人名单里没有自己, 两边都找不到 —— 这种情况按
    `teamOne` 就是蓝方来处理: 2999 的 ORDER/CHAOS 与 LCU 的 teamOne/teamTwo
    是同序的, 原来那句"找不到就当 teamTwo 是我方"会让蓝红整体反过来,
    观战面板和对局信息页的实时数据一条都合并不上。
    """
    team1 = data['teamOne']
    team2 = data['teamTwo']

    if any(summoner.get('summonerId') == currentSummonerId for summoner in team1):
        return team1, team2

    if any(summoner.get('summonerId') == currentSummonerId for summoner in team2):
        return team2, team1

    return team1, team2


async def parseGamesDataConcurrently(games, positions=None):
    tasks = [parseGameData(game, positions) for game in games]
    return await asyncio.gather(*tasks)


async def parseSummonerGameInfo(item, queueId, currentSummonerId):
    summonerId = item.get('summonerId', None)

    if item.get('nameVisibilityType') == 'HIDDEN':
        return await makeAnonymousSummoner(item)

    if summonerId == 0 or summonerId == None:
        return None

    summoner = await connector.getSummonerById(summonerId)

    championId = item.get('championId') or 0
    icon = await connector.getChampionIcon(championId)

    puuid = summoner.get("puuid", None)

    if puuid == "00000000-0000-0000-0000-000000000000" or not puuid:
        return None

    try:
        origRankInfo = await connector.getRankedStatsByPuuid(puuid)
    except SummonerRankInfoNotFound:
        origRankInfo = None

    rankInfo = parseRankInfo(origRankInfo)

    try:
        origGamesInfo = await connector.getSummonerGamesByPuuid(
            puuid, 0, 14)

        queueFilterList = cfg.get(cfg.queueFilter)
        queueIds = queueFilterList.get(f"{queueId}")
        if queueIds:
            origGamesInfo["games"] = [
                game for game in origGamesInfo["games"] if game["queueId"] in queueIds]

            begIdx = 15
            while len(origGamesInfo["games"]) < 11 and begIdx <= 70:
                endIdx = begIdx + 5
                new = (await connector.getSummonerGamesByPuuid(puuid, begIdx, endIdx))["games"]

                for game in new:
                    if game["queueId"] in queueIds:
                        origGamesInfo['games'].append(game)

                begIdx = endIdx + 1
    except:
        gamesInfo = []
    else:
        games = origGamesInfo["games"][:11]
        positions = await resolve(puuid, games)
        tasks = [parseGameData(game, positions) for game in games]
        gamesInfo = await asyncio.gather(*tasks)

    _, kill, deaths, assists, _, _ = parseGames(gamesInfo)

    teammatesInfo = [
        getTeammates(
            await connector.getGameDetailByGameId(game["gameId"]),
            puuid
        ) for game in gamesInfo[:1]  # 避免空报错, 查上一局的队友(对手)
    ]

    recentlyChampionName = ""
    fateFlag = None

    if teammatesInfo:  # 判个空, 避免太久没有打游戏的玩家或新号引发异常
        if currentSummonerId in [t['summonerId'] for t in teammatesInfo[0]['summoners']]:
            # 上把队友
            fateFlag = "ally"
        elif currentSummonerId in [t['summonerId'] for t in teammatesInfo[0]['enemies']]:
            # 上把对面
            fateFlag = "enemy"
        recentlyChampionId = max(
            teammatesInfo and teammatesInfo[0]['championId'], 0)  # 取不到时是-1, 如果-1置为0
        recentlyChampionName = connector.manager.champs.get(
            recentlyChampionId)

    return {
        "name": summoner.get("gameName") or summoner.get("internalName"),
        'tagLine': summoner.get("tagLine"),
        "icon": icon,
        "championIcon": icon,
        'championId': championId,
        "level": summoner["summonerLevel"],
        "rankInfo": rankInfo,
        "gamesInfo": gamesInfo,
        "xpSinceLastLevel": summoner["xpSinceLastLevel"],
        "xpUntilNextLevel": summoner["xpUntilNextLevel"],
        "puuid": puuid,
        "summonerId": summonerId,
        "kda": [kill, deaths, assists],
        "cellId": item.get("cellId"),
        "selectedPosition": item.get("selectedPosition"),
        "fateFlag": fateFlag,
        "isPublic": summoner["privacy"] == "PUBLIC",
        # 最近游戏的英雄 (用于上一局与与同一召唤师游玩之后显示)
        "recentlyChampionName": recentlyChampionName,
        "pickState": getPickState(item),
        "isAnonymous": False,
    }


async def getSummonerGamesInfoViaSGP(item, queueID, currentSummonerId):
    '''
    使用 SGP 接口取战绩信息
    '''
    puuid = item.get('puuid')

    if item.get('nameVisibilityType') == 'HIDDEN':
        return await makeAnonymousSummoner(item)

    if puuid == "00000000-0000-0000-0000-000000000000" or not puuid:
        return None

    championId = item.get('championId') or 0
    icon = await connector.getChampionIcon(championId)
    summoner = await connector.getSummonerByPuuidViaSGP(puuid)

    try:
        origRankInfo = await connector.getRankedStatsByPuuidViaSGP(puuid)
    except SummonerRankInfoNotFound:
        origRankInfo = None

    rankInfo = parseRankInfoFromSGP(origRankInfo)

    try:
        origGamesInfo = await connector.getSummonerGamesByPuuidViaSGP(puuid, 0, 14)

        queueFilterList = cfg.get(cfg.queueFilter)
        queueIds = queueFilterList.get(f"{queueID}")
        if queueIds:
            origGamesInfo["games"] = [
                game for game in origGamesInfo["games"] if game['json']["queueId"] in queueIds]

            begIdx = 15
            while len(origGamesInfo["games"]) < 11 and begIdx <= 70:
                endIdx = begIdx + 10
                new = (await connector.getSummonerGamesByPuuidViaSGP(puuid, begIdx, endIdx))["games"]

                for game in new:
                    if game['json']["queueId"] in queueIds:
                        origGamesInfo['games'].append(game)

                begIdx = endIdx + 1
    except:
        gamesInfo = []
    else:
        games = origGamesInfo['games']

        if games:
            summonerName, tagLine = getNameTagLineFromGame(games[0], puuid)
        else:
            # 一局都没有的新号: 原来的写法会在这里 IndexError, 然后被外层当成
            # "SGP 整体失败" 降级, 拖累同队其他人的查询
            summonerName = summoner.get('gameName') or summoner.get('name') or ''
            tagLine = summoner.get('tagLine') or ''

        tasks = [parseGamesDataFromSGP(game, puuid) for game in games[:11]]
        gamesInfo = await asyncio.gather(*tasks)

    _, kill, deaths, assists, _, _ = parseGames(gamesInfo)

    teammatesInfo = [
        getTeammatesFromSGPGame(
            game,
            puuid
        ) for game in origGamesInfo['games'][:1]  # 避免空报错, 查上一局的队友(对手)
    ]

    recentlyChampionName = ""
    fateFlag = None

    if teammatesInfo:  # 判个空, 避免太久没有打游戏的玩家或新号引发异常
        if currentSummonerId in [t['summonerId'] for t in teammatesInfo[0]['summoners']]:
            # 上把队友
            fateFlag = "ally"
        elif currentSummonerId in [t['summonerId'] for t in teammatesInfo[0]['enemies']]:
            # 上把对面
            fateFlag = "enemy"
        recentlyChampionId = max(
            teammatesInfo and teammatesInfo[0]['championId'], 0)  # 取不到时是-1, 如果-1置为0
        recentlyChampionName = connector.manager.champs.get(
            recentlyChampionId)

    # 适用于 LCU API 返回值
    # return {
    #     "name": summoner.get("gameName"),
    #     'tagLine': summoner.get('tagLine'),
    #     "icon": icon,
    #     'championId': championId,
    #     "level": summoner["summonerLevel"],
    #     "rankInfo": rankInfo,
    #     "gamesInfo": gamesInfo,
    #     "xpSinceLastLevel": summoner["xpSinceLastLevel"],
    #     "xpUntilNextLevel": summoner["xpUntilNextLevel"],
    #     "puuid": puuid,
    #     "summonerId": summoner['summonerId'],
    #     "kda": [kill, deaths, assists],
    #     "cellId": item.get("cellId"),
    #     "selectedPosition": item.get("selectedPosition"),
    #     "fateFlag": fateFlag,
    #     "isPublic": summoner["privacy"] == "PUBLIC",
    #     # 最近游戏的英雄 (用于上一局与与同一召唤师游玩之后显示)
    #     "recentlyChampionName": recentlyChampionName
    # }

    # 适用于 SGP API 返回值
    return {
        "name": summonerName,
        'tagLine': tagLine,
        "icon": icon,
        "championIcon": icon,
        'championId': championId,
        "level": summoner["level"],
        "rankInfo": rankInfo,
        "gamesInfo": gamesInfo,
        "xpSinceLastLevel": summoner["expPoints"],
        "xpUntilNextLevel": summoner["expToNextLevel"],
        "puuid": puuid,
        "summonerId": summoner['id'],
        "kda": [kill, deaths, assists],
        "cellId": item.get("cellId"),
        "selectedPosition": item.get("selectedPosition"),
        "fateFlag": fateFlag,
        "isPublic": summoner["privacy"] == "PUBLIC",
        # 最近游戏的英雄 (用于上一局与与同一召唤师游玩之后显示)
        "recentlyChampionName": recentlyChampionName,
        "pickState": getPickState(item),
        "isAnonymous": False,
    }


def getTeammatesFromSGPGame(game, puuid):
    json = game['json']
    queueId = json['queueId']

    for player in json['participants']:
        if player['puuid'] == puuid:
            if queueId != 1700:
                tid = player['teamId']
            else:  # 斗魂竞技场
                tid = player['subteamPlacement']

            win = player['win']
            remake = player['teamEarlySurrendered']

            break

    res = {
        'queueId': queueId,
        'win': win,
        'remake': remake,
        'summoners': [],  # 队友召唤师 (由于兼容性, 未修改字段名)
        'enemies': []  # 对面召唤师, 若有多个队伍会全放这里面
    }

    for player in json['participants']:
        if queueId != 1700:
            cmp = player['teamId']
        else:
            cmp = player['subteamPlacement']

        if cmp == tid:
            if player['puuid'] != puuid:
                res['summoners'].append({
                    'summonerId': player['summonerId'],
                    'name': player['summonerName'],
                    'puuid': player['puuid'],
                    'icon': player['profileIcon']
                })
            else:
                # 当前召唤师在该对局使用的英雄, 自定义对局没有该字段
                res["championId"] = player.get('championId', -1)
        else:
            res['enemies'].append({
                'summonerId': player['summonerId'],
                'name': player['summonerName'],
                'puuid': player['puuid'],
                'icon': player['profileIcon']
            })

    return res


async def parseGamesDataFromSGP(game, puuid):
    """
    解析由 SGP 接口得到的具体到某一局的对局记录信息
    """

    game = game['json']

    timeStamp = game["gameCreation"]  # 毫秒级时间戳
    time = timeStampToStr(game['gameCreation'])
    shortTime = timeStampToShortStr(game['gameCreation'])
    gameId = game['gameId']
    duration = secsToStr(game['gameDuration'])
    queueId = game['queueId']

    nameAndMap = connector.manager.getNameMapByQueueId(queueId)
    modeName = nameAndMap['name']

    if queueId != 0:
        mapName = nameAndMap['map']
    else:
        mapName = connector.manager.getMapNameById(game['mapId'])

    participant = None
    for p in game['participants']:
        if p['puuid'] == puuid:
            participant = p

    championId = participant['championId']
    championIcon = await connector.getChampionIcon(championId)
    spell1Id = participant['spell1Id']
    spell2Id = participant['spell2Id']
    spell1Icon = await connector.getSummonerSpellIcon(spell1Id)
    spell2Icon = await connector.getSummonerSpellIcon(spell2Id)

    champLevel = participant['champLevel']
    kills = participant['kills']
    deaths = participant['deaths']
    assists = participant['assists']

    itemIds = [
        participant['item0'],
        participant['item1'],
        participant['item2'],
        participant['item3'],
        participant['item4'],
        participant['item5'],
        participant['item6'],
    ]

    itemIcons = [await connector.getItemIcon(itemId) for itemId in itemIds]
    runeId = participant['perks']['styles'][0]['selections'][0]['perk']
    runeIcon = await connector.getRuneIcon(runeId)

    cs = participant['totalMinionsKilled'] + \
        participant['neutralMinionsKilled']
    gold = participant['goldEarned']
    damage = participant.get('totalDamageDealtToChampions', 0) or 0
    durationSec = game.get('gameDuration') or 0
    remake = participant['gameEndedInEarlySurrender']
    win = participant['win']

    # 这条链路本来就是 match-v5 结构，直接读官方的 teamPosition；
    # 老代码读的 `participant['lane']/['role']` 是废弃字段，判不对
    position = ''

    if queueId in [420, 440]:
        position = fromSGP(participant) or fromItems(participant)

    return {
        'queueId': queueId,
        'gameId': gameId,
        'time': time,
        'shortTime': shortTime,
        'name': modeName,
        'map': mapName,
        'duration': duration,
        'remake': remake,
        'win': win,
        'championId': championId,
        'championIcon': championIcon,
        'spell1Icon': spell1Icon,
        'spell2Icon': spell2Icon,
        'champLevel': champLevel,
        'kills': kills,
        'deaths': deaths,
        'assists': assists,
        'itemIcons': itemIcons,
        'runeIcon': runeIcon,
        'cs': cs,
        'gold': gold,
        'damage': damage,
        'durationSec': durationSec,
        'score': gameScore(kills, deaths, assists, damage, cs, durationSec),
        'timeStamp': timeStamp,
        'position': position,
    }


def getNameTagLineFromGame(game, puuid):
    for player in game['json']['participants']:
        if player['puuid'] == puuid:
            return player['riotIdGameName'], player['riotIdTagline']

    return None


class ChampionSelection:
    def __init__(self):
        self.isSummonerSpellSetted = False
        self.isChampionShowed = False
        self.isChampionBanned = False
        self.isChampionPicked = False
        self.isChampionPickedCompleted = False
        self.isSkinPicked = False
        self.opggShowChampionId = None
        self.queueId = None

    def reset(self):
        self.__init__()


PICK_CHAMPION_CONFIG = {
    'top': 'autoSelectChampionTop',
    'jungle': 'autoSelectChampionJug',
    'middle': 'autoSelectChampionMid',
    'bottom': 'autoSelectChampionBot',
    'utility': 'autoSelectChampionSup',
}

BAN_CHAMPION_CONFIG = {
    'top': 'autoBanChampionTop',
    'jungle': 'autoBanChampionJug',
    'middle': 'autoBanChampionMid',
    'bottom': 'autoBanChampionBot',
    'utility': 'autoBanChampionSup',
}

SPELL_CONFIG = {
    'top': 'autoSetSummonerSpellTop',
    'jungle': 'autoSetSummonerSpellJug',
    'middle': 'autoSetSummonerSpellMid',
    'bottom': 'autoSetSummonerSpellBot',
    'utility': 'autoSetSummonerSpellSup',
}


def getLocalPlayerPosition(data):
    """取本地玩家在选人会话中的分路 (top/jungle/middle/bottom/utility), 未分配返回空串"""
    cellId = data.get('localPlayerCellId')

    for player in data.get('myTeam') or []:
        if player.get('cellId') == cellId:
            return (player.get('assignedPosition') or '').lower()

    return ''


def getPositionConfig(position, configMap):
    """只取当前分路自己的配置, 不做全局兜底"""
    itemName = configMap.get(position)
    if not itemName:
        return []

    return deepcopy(cfg.get(getattr(cfg, itemName)))


def getCandidatesByPosition(position, configMap, defaultItem):
    """按分路取候选英雄: 分路配置在前, 全局兜底配置在后

    @param position:   分路, 如 'top'
    @param configMap:  {'top': 'autoSelectChampionTop', ...}
    @param defaultItem: 全局兜底配置项名, 如 'autoSelectChampion'
    """
    candidates = getPositionConfig(position, configMap)
    candidates.extend(cfg.get(getattr(cfg, defaultItem)))

    return candidates


def getPickCandidates(position):
    return getCandidatesByPosition(position, PICK_CHAMPION_CONFIG, 'autoSelectChampion')


def getBanCandidates(position):
    return getCandidatesByPosition(position, BAN_CHAMPION_CONFIG, 'autoBanChampion')


async def autoSwap(data, selection: ChampionSelection):
    """
    选用顺序交换请求发生时，自动接受
    """

    if not cfg.get(cfg.autoAcceptCeilSwap):
        return

    for pickOrderSwap in data['pickOrderSwaps']:
        if 'RECEIVED' == pickOrderSwap['state']:
            await asyncio.sleep(0.5)
            await connector.acceptSwap(pickOrderSwap['id'])

            selection.isChampionPickedCompleted = False
            return True


async def autoTrade(data, selection):
    """
    英雄交换请求发生时，自动接受
    """
    if not cfg.get(cfg.autoAcceptChampTrade):
        return False

    for trade in data['trades']:
        if 'RECEIVED' == trade['state']:
            await asyncio.sleep(0.5)
            await connector.acceptTrade(trade['id'])

            return True

    return False


async def showOpggBuild(data, selection: ChampionSelection):
    cellId = data['localPlayerCellId']

    # 只有在英雄已经选定后才会尝试刷新 OPGG 界面
    for actionGroup in data['actions']:
        # 这里必须遍历完所有的 actorCellId == cellId 的所有 action
        for action in actionGroup:
            if not action['actorCellId'] == cellId:
                continue

            if action['type'] != 'pick':
                continue

            if not action['completed']:
                return False

    # 拿一下位置和英雄 ID
    position, championId = "", 0

    for player in data['myTeam']:
        if player['cellId'] == cellId:
            position = player.get('assignedPosition', "")
            championId = player['championId'] or player['championPickIntent']
            break

    # 大乱斗模式下，即使锁定了也可能会换英雄，这里判断一下
    if championId == selection.opggShowChampionId:
        return False

    map = {
        'TOP': "TOP",
        'JUNGLE': "JUNGLE",
        'MIDDLE': "MID",
        'BOTTOM': "ADC",
        'UTILITY': "SUPPORT",
    }

    position = map.get(position, "")

    if championId == 0:
        return False

    if selection.queueId == None:
        if data.get('benchEnabled'):
            mode = "aram"
        elif len(data['myTeam']) == 2:
            mode = 'arena'
        else:
            mode = ""
    else:
        if selection.queueId == 450:
            mode = 'aram'
        elif selection.queueId in (1700, 1710):
            mode = 'arena'
        elif selection.queueId == 1300:
            mode = 'nexus_blitz'
        elif selection.queueId in (900, 1900):
            mode = 'urf'
        else:
            mode = 'ranked'

    selection.opggShowChampionId = championId
    signalBus.toOpggBuildInterface.emit(championId, mode, position)

    return True


async def autoPick(data, selection: ChampionSelection):
    """
    自动选用英雄
    """

    if not cfg.get(cfg.enableAutoSelectChampion) or selection.isChampionPicked:
        return

    localPlayerCellId = data['localPlayerCellId']

    for player in data['myTeam']:
        if player["cellId"] != localPlayerCellId:
            continue

        if bool(player['championId']) or bool(player['championPickIntent']):
            selection.isChampionPicked = True
            return

        break

    bans = itertools.chain(data["bans"]['myTeamBans'],
                           data["bans"]['theirTeamBans'])

    candidates = getPickCandidates(getLocalPlayerPosition(data))
    candidates = [x for x in candidates if x not in bans]

    if not candidates:
        selection.isChampionPicked = True
        return

    championId = candidates[0]

    for actionGroup in reversed(data['actions']):
        for action in actionGroup:
            if (action["actorCellId"] == localPlayerCellId
                    and action['type'] == "pick"):

                await connector.selectChampion(action['id'], championId)
                selection.isChampionPicked = True
                return True


async def autoComplete(data, selection: ChampionSelection):
    """
    超时自动选定（当前选中英雄）
    """
    isAutoCompleted = cfg.get(cfg.enableAutoSelectTimeoutCompleted)
    if not isAutoCompleted or selection.isChampionPickedCompleted:
        return

    if not (localPlayerCellId := data.get('localPlayerCellId', None)):
        return

    for actionGroup in reversed(data['actions']):
        for action in actionGroup:
            if action['actorCellId'] != localPlayerCellId:
                continue

            if action['type'] != 'pick':
                continue

            if not action['isInProgress']:
                return False

            if action['completed']:
                selection.isChampionPickedCompleted = True
                return False

            break

    selection.isChampionPickedCompleted = True

    sleepTime = int(data['timer']['adjustedTimeLeftInPhase'] / 1000) - 4
    await asyncio.sleep(sleepTime)

    data = await connector.getChampSelectSession()

    if not data:
        return

    # 双方选过的英雄
    cantSelect = []

    # 双方 ban 掉的英雄
    bans = itertools.chain(data["bans"]['myTeamBans'],
                           data["bans"]['theirTeamBans'])

    championIntent = 0
    for actionGroup in data['actions']:
        for action in actionGroup:
            if (action['type'] == 'pick' and action['completed']
                    and action['actorCellId'] != localPlayerCellId):
                cantSelect.append(action['championId'])

            if action['actorCellId'] != localPlayerCellId:
                continue

            if action['type'] != 'pick':
                continue

            if action['completed']:
                return

            # 现在亮着的英雄
            championIntent = action['championId']
            actionId = action['id']

    if not championIntent:
        return

    cantSelect.extend(bans)

    if championIntent not in cantSelect:
        await connector.selectChampion(actionId, championIntent, True)
        return True

    candidates = getPickCandidates(getLocalPlayerPosition(data))
    candidates = [x for x in candidates if x not in cantSelect]

    if len(candidates) == 0:
        return

    await connector.selectChampion(actionId, candidates[0], True)

    return True


async def autoBan(data, selection: ChampionSelection):
    """
    自动禁用英雄
    """
    isAutoBan = cfg.get(cfg.enableAutoBanChampion)

    if not isAutoBan or selection.isChampionBanned:
        return

    localPlayerCellId = data['localPlayerCellId']
    for actionGroup in data['actions']:
        for action in actionGroup:
            if (action["actorCellId"] == localPlayerCellId
                    and action['type'] == 'ban'
                    and action["isInProgress"]):

                candidates = getBanCandidates(getLocalPlayerPosition(data))

                bans = itertools.chain(data["bans"]['myTeamBans'],
                                       data["bans"]['theirTeamBans'])
                candidates = [x for x in candidates if x not in bans]

                # 给队友一点预选的时间
                await asyncio.sleep(cfg.get(cfg.autoBanDelay))

                isFriendly = cfg.get(cfg.pretentBan)
                if isFriendly:
                    myTeam = (await connector.getChampSelectSession()).get("myTeam")

                    if not myTeam:
                        return

                    intents = [player["championPickIntent"]
                               for player in myTeam]
                    candidates = [x for x in candidates if x not in intents]

                if not candidates:
                    return

                championId = candidates[0]
                await connector.banChampion(action['id'], championId, True)
                selection.isChampionBanned = True

                return True


async def autoSetSummonerSpell(data, selection: ChampionSelection):
    if selection.isSummonerSpellSetted:
        return False

    selection.isSummonerSpellSetted = True

    if not cfg.get(cfg.enableAutoSetSpells):
        return False

    spells = getPositionConfig(getLocalPlayerPosition(data), SPELL_CONFIG)

    # 分路没配 (或还是默认的 54/未选) 时退回全局配置, 仍不合法则放弃
    if len(spells) < 2 or 54 in spells:
        spells = deepcopy(cfg.get(cfg.autoSetSummonerSpell))

    if len(spells) < 2 or 54 in spells:
        return False

    await connector.setSummonerSpells(spells[0], spells[1])


async def autoShow(data, selection: ChampionSelection):
    '''在 B/P 前展示英雄'''
    if selection.isChampionShowed:
        return

    if not cfg.get(cfg.enableAutoSelectChampion):
        return

    cellId = data['localPlayerCellId']

    for player in data['myTeam']:
        if player['cellId'] != cellId:
            continue

        if (player['championId'] != 0
                or player['championPickIntent'] != 0):
            selection.isChampionShowed = True
            return

        break

    candidates = getPickCandidates(getLocalPlayerPosition(data))

    if len(candidates) == 0:
        selection.isChampionShowed = True
        return

    championId = candidates[0]
    for actionGroup in reversed(data['actions']):
        for action in actionGroup:
            if (action['actorCellId'] == cellId and
                    action['type'] == 'pick'):

                await connector.selectChampion(action['id'], championId)
                selection.isChampionShowed = True

                return True


async def rollAndSwapBack():
    """
    摇骰子并切换回之前的英雄
    todo: 界面
    """
    championId = await connector.getCurrentChampion()

    await connector.reroll()
    await connector.benchSwap(championId)


async def createAndSetRunePage(name, primaryId, secondaryId, perks):
    pages = await connector.createRunePage(name, primaryId)

    if id := pages.get('id'):
        await connector.putRunePage(id, name, primaryId,
                                    secondaryId, perks)


async def fixLCUWindow() -> bool:
    '''按客户端当前的缩放比重设客户端窗口尺寸并居中

    以前这里是 `ShellExecuteW(runas, fix_lcu_window.exe)`：提权拉起一个
    随包分发的未签名 exe。现在改成本进程内直接调 user32（见
    app/common/win_window.py），不再落盘额外程序、不再提权。

    取缩放比要先走 LCU，客户端没连上时这一步就会失败。这里统一吞成 False，
    让调用方按「没修成」提示用户，而不是把异常甩进 qasync 的槽里。

    :return: True 成功；False 表示没连上客户端 / 客户端没开 / 最小化 / 权限不够等，
             失败原因已记进日志
    '''
    # 局部导入: 让 tools 的导入链不牵扯 ctypes 绑定, 也方便单独替换实现
    from app.common.win_window import applyZoom

    try:
        zoom = await connector.getClientZoom()
    except Exception as e:
        logger.warning(f'failed to read client zoom: {e}')
        return False

    return applyZoom(zoom)
