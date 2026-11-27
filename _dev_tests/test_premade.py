# coding:utf-8
"""同队开黑识别 + 战损着色的回归

不联网、不起真实窗口：全部用手造的近几场战绩跑。

覆盖：
  - 同 gameId 且胜负相同才算同队；胜负相反的对手不计
  - 重开局 / gameId 为 0 不计
  - 2+2+1、3+2、5 排的分组与人数
  - 只在本局同队内部合并，不跨阵营串联
  - 链式巧合 (A-B 1 场 / B-C 1 场) 合成一组但置信度记 1
  - LCU 的 teamParticipantId 覆盖战绩推断
  - 战损比着色三档 + 摘要行带战损
  - 卡片底边色条与「双排」标签用同一个颜色

运行: python _dev_tests/test_premade.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme, setThemeColor  # noqa: E402

setTheme(Theme.DARK, save=False)
setThemeColor('#E3B23C', save=False)

from app.common import skin  # noqa: E402

skin.install()

from app.components.player_card import Chip, PlayerCard, RecentGames  # noqa: E402
from app.lol.game_data import buildBadges  # noqa: E402
from app.lol.premade import (PREMADE_COLORS,  # noqa: E402
                             PREMADE_COLORS_LIGHT, colorAt, detectGroups,
                             groupName, groupsFromColors, kdaRatio,
                             mergeGroups, sameTeamGames)
from app.view.game_info_interface import TeamBoard  # noqa: E402

failures = []


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}'
          + (f'  ({detail})' if detail and not cond else ''))

    if not cond:
        failures.append(name)


def game(gameId, win=True, remake=False, kills=5, deaths=2, assists=8):
    return {
        'gameId': gameId,
        'win': win,
        'remake': remake,
        'kills': kills,
        'deaths': deaths,
        'assists': assists,
    }


def card(key, side='blue', games=None, premade=0):
    return {'key': key, 'side': side, 'games': games or [], 'premade': premade}


CHAMPION = 'x' * 90


# ------------------------------------------------------------------ 单对判据
print('同队场次')


def testSameTeam():
    a = [game(1), game(2), game(3)]

    check('同场同胜负 2 场 -> 2',
          sameTeamGames(a, [game(1), game(2), game(4)]) == 2)
    check('同场但胜负相反 (对手) 一场都不算',
          sameTeamGames(a, [game(1, win=False), game(2, win=False)]) == 0)
    check('没有交集 -> 0', sameTeamGames(a, [game(9), game(10)]) == 0)
    check('空战绩 -> 0', sameTeamGames(a, []) == 0)
    check('gameId 为 0 不参与', sameTeamGames([game(0)], [game(0)]) == 0)
    check('重开局不参与',
          sameTeamGames([game(1, remake=True)], [game(1, remake=True)]) == 0)
    check('对局 id 用字符串也能对上',
          sameTeamGames([game('100')], [game('100')]) == 1)
    check('一胜一负、一负一胜混合只数相同的那场',
          sameTeamGames([game(1), game(2)], [game(1, win=False), game(2)]) == 1)


testSameTeam()

# ------------------------------------------------------------------ 分组
print('分组 (按本局阵营)')


def testTwoTwoOne():
    cards = [
        card('A', games=[game(1), game(2), game(3)]),
        card('B', games=[game(1), game(2), game(9)]),
        card('C', games=[game(4), game(5)]),
        card('D', games=[game(4), game(5), game(6)]),
        card('E', games=[game(7), game(8)]),
    ]

    groups = detectGroups(cards)

    check('2+2+1 -> 只有 4 个人成组', len(groups) == 4, len(groups))
    check('A/B 人数 = 2', groups['A']['size'] == 2, groups['A']['size'])
    check('A/B 同队场次 = 2', groups['A']['games'] == 2, groups['A']['games'])
    check('A 和 B 同组号', groups['A']['index'] == groups['B']['index'])
    check('C/D 是另一组',
          groups['C']['index'] != groups['A']['index'])
    check('C/D 人数 = 2', groups['C']['size'] == 2)
    check('E 落单', 'E' not in groups)
    check('两组颜色不同', groups['A']['color'] != groups['C']['color'])
    check('标签写成「双排」', groups['A']['name'] == '双排', groups['A']['name'])


testTwoTwoOne()


def testThreeTwo():
    cards = [
        card('A', games=[game(1), game(2), game(3)]),
        card('B', games=[game(1), game(2)]),
        card('C', games=[game(1), game(2), game(3)]),
        card('D', games=[game(6)]),
        card('E', games=[game(6)]),
    ]

    groups = detectGroups(cards)

    check('3+2 -> 五个人都成组', len(groups) == 5, len(groups))
    check('三人车人数 = 3', groups['A']['size'] == 3, groups['A']['size'])
    check('三人车标签「三排」', groups['A']['name'] == '三排')
    check('三人车置信度取最弱一条边 = 2', groups['A']['games'] == 2,
          groups['A']['games'])
    check('双人车人数 = 2', groups['D']['size'] == 2)
    check('两组颜色不同', groups['A']['color'] != groups['D']['color'])


testThreeTwo()


def testFullSquad():
    games = [game(1), game(2), game(3)]
    cards = [card(str(i), games=games) for i in range(5)]

    groups = detectGroups(cards)

    check('5 排 -> 五个人同一组', len(groups) == 5)
    check('5 排标签「五排」', groups['0']['name'] == '五排')
    check('5 排只占一个颜色',
          len({info['color'] for info in groups.values()}) == 1)


testFullSquad()


def testNoCrossSide():
    cards = [
        card('A', 'blue', games=[game(1)]),
        card('B', 'blue', games=[game(1)]),
        card('C', 'red', games=[game(1)]),
    ]

    groups = detectGroups(cards)

    check('蓝方 A/B 成组', groups.get('A', {}).get('size') == 2)
    check('红方 C 不会因为同一场被拖进来', 'C' not in groups)


testNoCrossSide()


def testChain():
    cards = [
        card('A', games=[game(1)]),
        card('B', games=[game(1), game(2)]),
        card('C', games=[game(2)]),
    ]

    groups = detectGroups(cards)

    check('链式巧合仍会连成一排', len(groups) == 3)
    check('但置信度只记 1 场 (最弱一条边)', groups['A']['games'] == 1,
          groups['A']['games'])


testChain()


def testPremadePriority():
    """开黑标签要能挤掉「大腿」, 它是本局信息"""
    strong = {
        'winRate': 70, 'kdaValue': 6.0, 'streak': ('win', 3),
        'premade': 2, 'premadeLabel': '双排',
    }
    badges = buildBadges(strong)

    check('双排排在第一个', badges[0][0] == '双排', badges)
    check('双排用 premade 色调', badges[0][1] == 'premade', badges)

    lone = {'winRate': 52, 'kdaValue': 3.0, 'streak': ('', 0), 'premade': 0}
    check('单排没有开黑标签',
          not any(b[0].endswith('排') for b in buildBadges(lone)))

    tagged = {**strong, 'userTags': ['朋友', '老板']}
    check('自己的标签仍然最优先',
          [b[0] for b in buildBadges(tagged)] == ['朋友', '老板'],
          buildBadges(tagged))


testPremadePriority()


# ------------------------------------------------------------------ LCU 覆盖
print('LCU 预组队')

COLORS = {101: 0, 102: 0, 103: 1, 104: 1, 105: 2}


def testFromColors():
    cards = [card(i, 'blue', games=[]) for i in (101, 102, 103, 104, 105)]

    groups = groupsFromColors(COLORS, cards)

    check('四个成组的人拿到分组 (105 单人被丢)',
          len(groups) == 4, len(groups))
    check('101/102 一组', groups[101]['index'] == groups[102]['index'])
    check('标记为客户端确认', groups[101]['confirmed'] is True)
    check('确认过的置信度记 0 (不参与"?"判断)',
          groups[101]['games'] == 0, groups[101]['games'])
    check('单人的组被丢掉', 105 not in groups)
    check('key 退了名字也能按 summonerId 反查',
          'name-103' in groupsFromColors(COLORS, [
              {'key': 'name-103', 'summonerId': 103, 'side': 'blue'}]))


testFromColors()


def testMerge():
    detected = {'A': {'index': 0, 'size': 2, 'games': 1, 'color': '#111',
                      'name': '双排'}}
    confirmed = {'B': {'index': 0, 'size': 2, 'games': 0, 'color': '#222',
                       'name': '双排', 'confirmed': True}}

    check('没有客户端数据时用推断', mergeGroups(detected, {}) == detected)
    check('没有推断时用客户端', mergeGroups({}, confirmed) == confirmed)
    check('都有时客户端整侧覆盖', mergeGroups(detected, confirmed) == confirmed,
          mergeGroups(detected, confirmed))


testMerge()

# ------------------------------------------------------------------ 战损
print('战损')


def testKdaRatio():
    check('10/2/10 -> 10.0', abs(kdaRatio(game(1, kills=10, deaths=2,
                                               assists=10)) - 10.0) < 1e-6)
    check('0 死按 1 当分母', kdaRatio(game(1, kills=3, deaths=0,
                                         assists=2)) == 5.0)
    check('0/0/0 算出 0 而不是无穷', kdaRatio(game(1, kills=0, deaths=0,
                                                 assists=0)) == 0.0)
    check('多死少杀 < 1', kdaRatio(game(1, kills=1, deaths=8,
                                       assists=2)) < 1)


testKdaRatio()


def testKdaTint():
    _, strong = RecentGames.kdaTint(game(1, kills=10, deaths=2, assists=12))

    from app.common.design_tokens import color

    check('战损 11 用金色', strong == color('gold'), strong)

    _, weak = RecentGames.kdaTint(game(1, kills=1, deaths=8, assists=1))
    check('战损 0.25 用红色', weak == color('red'), weak)

    _, normal = RecentGames.kdaTint(game(1, kills=4, deaths=3, assists=6))
    check('战损 3.3 用次要文字色', normal == color('textSub'), normal)

    _, remake = RecentGames.kdaTint(game(1, remake=True))
    check('重开局灰掉', remake == color('textMuted'))


testKdaTint()


# ------------------------------------------------------------------ 卡片
print('卡片')


def testCard():
    data = {
        'side': 'blue', 'name': '念尘词', 'tagLine': '78063',
        'games': [game(1, kills=11, deaths=3, assists=17),
                  game(2, kills=2, deaths=9, assists=1)],
        'premade': 2, 'premadeLabel': '双排', 'premadeColor': PREMADE_COLORS[1],
        'premadeGames': 3, 'premadeConfirmed': False,
        'badges': [('双排', 'premade')],
    }

    cardWidget = PlayerCard('blue')
    cardWidget.resize(520, 120)
    cardWidget.setData(data)

    check('底边色条用组排色',
          PREMADE_COLORS[1] in cardWidget.styleSheet(),
          cardWidget.styleSheet().split('border-bottom')[1][:40]
          if 'border-bottom' in cardWidget.styleSheet() else 'no border-bottom')
    check('chip1 用了组排色',
          cardWidget.chip1.accent == PREMADE_COLORS[1],
          cardWidget.chip1.accent)
    check('chip1 文字是双排', cardWidget.chip1.text() == '双排')
    check('摘要行写了战损', '战损' in cardWidget.gamesSummaryLabel.text(),
          cardWidget.gamesSummaryLabel.text())
    check('战损值是 (11+17+2+1)/12 = 2.6',
          '2.6' in cardWidget.gamesSummaryLabel.text(),
          cardWidget.gamesSummaryLabel.text())
    check('开黑 chip 带说明', '同队' in cardWidget.chip1.toolTip(),
          cardWidget.chip1.toolTip())

    # 单排: 底边回到普通描边
    lone = PlayerCard('blue')
    lone.resize(520, 120)
    lone.setData({**data, 'premade': 0, 'premadeColor': '',
                  'premadeLabel': '', 'badges': [('大腿', 'gold')]})

    check('单排没有组排色边框',
          all(c not in lone.styleSheet() for c in PREMADE_COLORS),
          lone.styleSheet())
    check('单排 chip 不带 accent', lone.chip1.accent == '', lone.chip1.accent)

    chip = Chip('三排', 'premade')
    chip.setAccent(PREMADE_COLORS[0])
    check('Chip 独立设置组排色也能染上',
          PREMADE_COLORS[0] in chip.styleSheet(), chip.styleSheet())


testCard()


def testEnemyKeepsGames():
    """进游戏后敌方按名字合并，近几场战绩不能被 2999 数据冲掉

    冲掉的话敌方「N 排」标签会在开局的第一次实时刷新后消失 ——
    而那正是最需要它的时刻。
    """
    board = TeamBoard('red', '红方')
    cards = [{
        'key': 100 + i, 'side': 'red', 'puuid': f'p{i}',
        'name': f'Enemy{i}', 'tagLine': f'1000{i}',
        'championId': 10 + i, 'championIcon': '',
        'position': '', 'badges': [], 'isPlaceholder': False,
        'slotText': f'{i + 1} 楼', 'games': [game(1), game(2)],
    } for i in range(5)]
    board.setCards(cards, 'select')

    live = [{
        'key': f'Enemy{i}#1000{i}', 'side': 'red', 'name': f'Enemy{i}',
        'tagLine': f'1000{i}', 'championId': 10 + i,
        'championIcon': '', 'position': 'TOP',
        'live': {'kills': 1, 'cs': 10, 'items': []},
    } for i in range(5)]

    board.mergeLive(live, 'game')

    kept = [c for c in board.datas.values() if c.get('games')]
    check('合并后 5 张卡都还留着战绩', len(kept) == 5, len(kept))
    check('合并后补上了分路',
          all(c.get('position') for c in board.datas.values()),
          [c.get('position') for c in board.datas.values()])


testEnemyKeepsGames()


def testEnemyIdentity():
    """有身份才按名字合并；匿名卡仍然要整列换掉"""
    from app.view.game_info_interface import GameInfoInterface

    page = GameInfoInterface()

    def enemy(puuid, name, anonymous=False):
        return {
            'summonerId': 0, 'puuid': puuid, 'name': name,
            'tagLine': '1000', 'championId': 1, 'championIcon': '',
            'selectedPosition': '', 'rankInfo': {},
            'gamesInfo': [game(1), game(2)], 'kda': [0, 0, 0],
            'summary': {'winRate': 50, 'recent': [], 'streak': ('', 0)},
            'premade': 0, 'isAnonymous': anonymous, 'isPublic': True,
            'pickState': '',
        }

    page.updateEnemySummoners({
        'summoners': [enemy(f'p{i}', f'Enemy{i}') for i in range(5)],
        'champions': {}, 'order': [f'p{i}' for i in range(5)],
    })

    flags = [c.get('isPlaceholder') for c in page.redBoard.datas.values()]
    check('进了游戏 (有 puuid) 就不标占位', not any(flags), flags)

    page.clear()
    page.updateEnemySummoners({
        'summoners': [enemy('', '匿名玩家', True)],
        'champions': {}, 'order': [],
    })

    check('匿名卡仍然标占位（2999 数据一条都匹配不上）',
          all(c.get('isPlaceholder')
              for c in page.redBoard.datas.values()),
          [c.get('isPlaceholder') for c in page.redBoard.datas.values()])


testEnemyIdentity()


def testBoardGroups():
    board = TeamBoard('blue', '蓝方')
    cards = [
        {'key': 'A', 'side': 'blue', 'name': 'A', 'games': [],
         'badges': [], 'winRate': 50, 'kdaValue': 3},
        {'key': 'B', 'side': 'blue', 'name': 'B', 'games': [],
         'badges': [], 'winRate': 50, 'kdaValue': 3},
        {'key': 'C', 'side': 'blue', 'name': 'C', 'games': [],
         'badges': [], 'winRate': 50, 'kdaValue': 3},
    ]
    board.setCards(cards, 'select')

    board.applyGroups({
        'A': {'index': 0, 'size': 2, 'games': 3, 'color': PREMADE_COLORS[0],
              'name': '双排'},
        'B': {'index': 0, 'size': 2, 'games': 3, 'color': PREMADE_COLORS[0],
              'name': '双排'},
        'C': {'index': 1, 'size': 2, 'games': 1, 'color': PREMADE_COLORS[2],
              'name': '双排'},
    })

    a, b, c = (board.datas[k] for k in ('A', 'B', 'C'))

    check('A/B 拿到同一个颜色', a['premadeColor'] == b['premadeColor'])
    check('A/B 标签是「双排」', a['premadeLabel'] == '双排', a['premadeLabel'])
    check('只同队 1 场的加问号', c['premadeLabel'] == '双排?',
          c['premadeLabel'])
    check('A 的 badges 里有双排',
          any(x[0] == '双排' for x in a['badges']), a['badges'])
    check('C 的 badges 里是「双排?」',
          any(x[0] == '双排?' for x in c['badges']), c['badges'])

    board.applyGroups({})
    check('清空分组后颜色归零', board.datas['A']['premadeColor'] == '')
    check('清空分组后标签也没了',
          not any(x[0].endswith('排') for x in board.datas['A']['badges']),
          board.datas['A']['badges'])


testBoardGroups()

def testAlignByPosition():
    """两列按分路对齐 —— 「楼层对楼层」就是这里退化的"""
    from app.lol.game_data import alignByPosition

    def lane(key, position):
        return {'key': key, 'position': position}

    full = [lane('B', 'JUNGLE'), lane('A', 'TOP'), lane('C', 'MIDDLE'),
            lane('D', 'BOTTOM'), lane('E', 'UTILITY')]
    check('五人齐按分路排',
          [c['key'] for c in alignByPosition(full)] == list('ABCDE'),
          [c['key'] for c in alignByPosition(full)])

    partial = [lane('X', ''), lane('B', 'JUNGLE'), lane('A', 'TOP'),
               lane('C', 'MIDDLE'), lane('Y', '')]
    order = [c['key'] for c in alignByPosition(partial)]
    check('四个人有分路就按分路排，没分路的垫后保持原序',
          order[:3] == ['A', 'B', 'C'] and order[3:] == ['X', 'Y'], order)

    none = [lane('X', ''), lane('Y', ''), lane('Z', '')]
    check('全都没分路保持原序',
          [c['key'] for c in alignByPosition(none)] == ['X', 'Y', 'Z'])

    two = [lane('X', ''), lane('A', 'TOP'), lane('Y', ''), lane('Z', ''),
           lane('B', 'MIDDLE')]
    check('只有 2 个有分路时不排（会打乱楼层）',
          [c['key'] for c in alignByPosition(two)]
          == ['X', 'A', 'Y', 'Z', 'B'],
          [c['key'] for c in alignByPosition(two)])


testAlignByPosition()


# ------------------------------------------------------------------ 杂项
print('杂项')

check('颜色按序号轮转',
      colorAt(0) == colorAt(len(PREMADE_COLORS))
      and colorAt(0) != colorAt(1))
check('色板跟随主题（浅色要压暗，白底上才有对比度）',
      len(set(PREMADE_COLORS) & set(PREMADE_COLORS_LIGHT)) == 0,
      '两套色板不该有重复值')
check('人数叫法', (groupName(2), groupName(5)) == ('双排', '五排'))
check('陌生人数不炸', groupName(9) == '9 人')

print()
if failures:
    print(f'{len(failures)} 项失败: ' + ', '.join(failures))
    sys.exit(1)

print('开黑识别全部通过')
