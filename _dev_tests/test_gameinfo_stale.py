# coding:utf-8
"""对局信息页"换局不刷新"的回归

覆盖：
  - summonerId 缺失（所有卡 key 都是 0）时 setCards 必须整列重建，
    不能因为 keys 看起来没变就走 updateCards（只会刷新最后一张）
  - 同一批 summonerId 重复下发时仍走 updateCards，不重建控件
  - applyLive 遇到整局换人（新旧名单零交集）时整列重建，不保留上一把

运行: python _dev_tests/test_gameinfo_stale.py
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

from app.view.game_info_interface import GameInfoInterface  # noqa: E402

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


def summoner(sid, name, champion=1):
    return {
        'summonerId': sid,
        'name': name,
        'tagLine': '',
        'puuid': f'puuid-{name}',
        'icon': '',
        'championId': champion,
        'selectedPosition': 'TOP',
        'kda': [0, 0, 0],
        'summary': {},
        'gamesInfo': [],
        'rankInfo': {},
        'isPublic': True,
        'isAnonymous': False,
    }


def info(items):
    return {
        'summoners': items,
        'champions': {s['summonerId']: s['championId'] for s in items},
        'order': [s['summonerId'] for s in items],
        'isAram': False,
    }


def liveCard(name, champion):
    return {
        'key': name,
        'name': name,
        'tagLine': '',
        'championId': champion,
        'championIcon': '',
        'position': 'TOP',
        'live': {'kda': [1, 2, 3], 'cs': 10, 'items': []},
    }


page = GameInfoInterface()

print('summonerId 缺失（key 全 0）')
page.updateAllySummoners(info([summoner(0, n) for n in 'ABCDE']))
first = len(page.blueBoard.datas)

page.updateAllySummoners(info([summoner(0, n) for n in 'FGHIJ']))
names = {c.get('name') for c in page.blueBoard.datas.values()}

check('第二次下发整列换成新人', len(page.blueBoard.datas) == 5,
      f'cards={len(page.blueBoard.datas)}')
check('旧名字一个都不留', names == set('FGHIJ'), names)
check('第一次也是 5 张', first == 5, first)

print('同一批 summonerId 重复下发')
page.updateAllySummoners(info([summoner(i, n) for i, n in
                               zip(range(1, 6), 'VWXYZ')]))
cards = list(page.blueBoard.cards)

page.updateAllySummoners(info([summoner(i, n) for i, n in
                               zip(range(1, 6), 'VWXYZ')]))

check('复用同一批控件', page.blueBoard.cards[0] is cards[0])

print('applyLive 整局换人')
page.clear()
page.updateAllySummoners(info([summoner(i, f'旧蓝{i}') for i in range(1, 6)]))
page.updateEnemySummoners(info([summoner(i, f'旧红{i}') for i in range(6, 11)]))

snapshot = {
    'blue': [liveCard(f'新蓝{i}', 10 + i) for i in range(5)],
    'red': [liveCard(f'新红{i}', 20 + i) for i in range(5)],
    'blueWinRate': 50,
    'redWinRate': 50,
    'gameTime': 120,
    'gameMode': 'CLASSIC',
    'mapName': "召唤师峡谷",
    'spectating': False,
}

page.applyLive(snapshot)
allNames = {c.get('name') for c in
            list(page.blueBoard.datas.values())
            + list(page.redBoard.datas.values())}

check('10 张卡全是新局的人',
      allNames == {f'新蓝{i}' for i in range(5)}
      | {f'新红{i}' for i in range(5)}, allNames)
check('没有旧局残留',
      not any(n.startswith('旧') for n in allNames), allNames)

print()
if FAILURES:
    print(f'{len(FAILURES)} 项失败: {FAILURES}')
    sys.exit(1)

print('全部通过')
