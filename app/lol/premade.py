"""同队开黑识别

对局信息页要回答一个问题：这十个人里，谁和谁是一起排进来的。

LCU 只在**选人阶段给我方** `teamParticipantId`（`countPremade()` 用的就是它），
敌方匿名时连召唤师 ID 都不下发，进了对局更是完全没有预组队字段。所以这里
用近几场战绩反推：

1. 两个人近期出现在同一个 `gameId` → 有交集
2. 同一场里**胜负相同** → 同队（对手的胜负必然相反）

第二条是关键。只看 gameId 交集会把「上把撞车打过」的人也算成一伙 —— 排位
高峰时段同段位反复相遇并不罕见，而「胜负相反即对手」能把这类误判全部排掉。

结论的语义是「最近一起打过」，不等于「这一把一定是一起来的」：同队 1 场
可能是巧合，2 场以上几乎只能是开黑。所以分组会带上 `games`（最弱那条边
的同队场次），UI 按它决定是否加「?」。

另外**只在本局同队内部合并**。A 上把和 B 同队、这把和 C 同队，不能让
A、B、C 串成一个组 —— B 和 C 其实从没一起排过。每侧 5 人各自聚类，
天然避开了这个问题。
"""

from collections import defaultdict

from app.common.qfluentwidgets import isDarkTheme

# 组排色: 与阵营色 (青 #3FC7C0 / 珊瑚红 #E05A4F) 色相拉开, 深色底上都够亮。
# 一侧最多出现 2~3 组（2+2+1 / 3+2 / 4+1）, 四个足够轮转。
PREMADE_COLORS = ('#E3B23C', '#9678FF', '#E58BD0', '#FF9F45')

# 浅色主题下同色相压暗两档: 亮金 / 亮粉铺在白底上, 文字对比度只有 2:1 左右,
# 标签会糊成一片淡色。色相保持一致, 只是明度换到浅色该有的档位。
PREMADE_COLORS_LIGHT = ('#8A6A15', '#5B3FD1', '#B03D8E', '#B35C00')

# 组内人数 -> 叫法
GROUP_NAMES = {2: '双排', 3: '三排', 4: '四排', 5: '五排'}


def groupName(size: int) -> str:
    return GROUP_NAMES.get(size, f'{size} 人')


def palette() -> tuple:
    """当前主题的组排色板"""
    return PREMADE_COLORS if isDarkTheme() else PREMADE_COLORS_LIGHT


def colorAt(index: int) -> str:
    colors = palette()

    return colors[index % len(colors)]


def kdaRatio(game) -> float:
    """单场战损比 (K + A) / D

    死 0 次时用 1 当分母, 不写成无穷大 —— 0 死 0 杀 0 助的挂机局应该显示
    "没打", 而不是"完美 KDA"。
    """
    kills = game.get('kills', 0) or 0
    deaths = game.get('deaths', 0) or 0
    assists = game.get('assists', 0) or 0

    return (kills + assists) / max(deaths, 1)


def sameTeamGames(aGames, bGames) -> int:
    """两人近期同队同排的场次

    重开局不参与: 重开时双方 `win` 可能都是 False, 拿胜负判断同队会失效,
    而且重开本来也说明不了是不是一起排的。
    """
    index = {}

    for game in aGames or []:
        gameId = game.get('gameId')

        if not gameId or game.get('remake'):
            continue

        index[gameId] = bool(game.get('win'))

    if not index:
        return 0

    count = 0

    for game in bGames or []:
        gameId = game.get('gameId')

        if not gameId or game.get('remake') or gameId not in index:
            continue

        if bool(game.get('win')) == index[gameId]:
            count += 1

    return count


def detectGroups(cards, minGames=1) -> dict:
    """按本局阵营分别聚类, 返回 {cardKey: 分组信息}

    @param minGames: 认定为同组所需的最少同队场次

    @return: {key: {'index', 'size', 'games', 'color', 'name'}}
             `games` 是组内最弱一条边的同队场次; 1 表示"可能", 2 以上基本实锤。
    """
    bySide = defaultdict(list)

    for card in cards or []:
        key = card.get('key')

        if key is None:
            continue

        bySide[card.get('side') or 'blue'].append(card)

    groups = {}
    groupIndex = 0

    for members in bySide.values():
        if len(members) < 2:
            continue

        parent = {card['key']: card['key'] for card in members}

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]

            return value

        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]

                if sameTeamGames(a.get('games'), b.get('games')) < minGames:
                    continue

                left, right = find(a['key']), find(b['key'])

                if left != right:
                    parent[right] = left

        buckets = defaultdict(list)

        for card in members:
            buckets[find(card['key'])].append(card)

        # 组号按组内第一个人的原始顺序给, 保证同一局面每次刷新颜色不变
        for cardsInGroup in buckets.values():
            if len(cardsInGroup) < 2:
                continue

            weakest = None

            for i in range(len(cardsInGroup)):
                for j in range(i + 1, len(cardsInGroup)):
                    count = sameTeamGames(cardsInGroup[i].get('games'),
                                          cardsInGroup[j].get('games'))

                    if weakest is None or count < weakest:
                        weakest = count

            size = len(cardsInGroup)
            info = {
                'index': groupIndex,
                'size': size,
                'games': weakest or 1,
                'color': colorAt(groupIndex),
                'name': groupName(size),
            }

            for card in cardsInGroup:
                groups[card['key']] = dict(info)

            groupIndex += 1

    return groups


def groupsFromColors(colors: dict, cards=None) -> dict:
    """LCU 的 `teamParticipantId` 分组 -> 与 `detectGroups` 同构的结果

    @param colors: {summonerId: 预组队编号}, 来自 `countPremade()` / teamColors
    @param cards:  可选, 用来补同队场次 (BP 阶段敌方没有战绩, 只能给 0)

    LCU 给的是本局真实预组队, 比战绩反推可靠, 所以有它就不用猜 ——
    `games` 填 0 表示"客户端确认", UI 不加「?」。
    """
    buckets = defaultdict(list)

    for summonerId, group in (colors or {}).items():
        if group is None or group < 0:
            continue

        buckets[group].append(summonerId)

    byKey = {card.get('key'): card for card in cards or []}

    result = {}
    index = 0

    for summonerIds in buckets.values():
        if len(summonerIds) < 2:
            continue

        size = len(summonerIds)
        info = {
            'index': index,
            'size': size,
            'games': 0,
            'color': colorAt(index),
            'name': groupName(size),
            'confirmed': True,
        }

        for summonerId in summonerIds:
            result[summonerId] = dict(info)

            # key 不一定是 summonerId (新版 LCU 里它常常是 0), 用卡片反查一次
            for key, card in byKey.items():
                if card.get('summonerId') == summonerId:
                    result[key] = dict(info)

        index += 1

    return result


def mergeGroups(detected: dict, confirmed: dict) -> dict:
    """合并两个来源: LCU 确认的分组覆盖战绩推断的分组

    覆盖是"整侧覆盖"而不是"逐人对齐" —— 一半人来自客户端、一半人靠猜,
    颜色会对不上号, 不如整侧用可信的那份。
    """
    if not confirmed:
        return dict(detected or {})

    # detected 里有、confirmed 里没有的人: 说明 LCU 认为他单排, 但战绩显示
    # 他和某人同队过。这种以客户端为准, 整侧丢掉推断结果 —— 混着用会让
    # 同一组人一半有颜色一半没有, 反而更难读。
    return dict(confirmed)
