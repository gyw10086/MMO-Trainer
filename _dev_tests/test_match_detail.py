# coding:utf-8
"""单场详情 MatchDetailView：字段完整性与自算指标"""
import os
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402
from PyQt5.QtCore import QCoreApplication, QEvent  # noqa: E402

app = QApplication(sys.argv)
from app.common.qfluentwidgets import setTheme, setThemeColor, Theme  # noqa: E402
setTheme(Theme.DARK, save=False)
setThemeColor('#E3B23C', save=False)
from app.common import skin  # noqa: E402
skin.install()
skin.applyGlobal(app)
from app.components.match_detail_view import MatchDetailView, fmtK, rankText  # noqa: E402

passed = failed = 0


def settle():
    """离屏下布局要转几圈才稳定；顺便把上一场 deleteLater 的旧行回收掉"""
    for _ in range(3):
        app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def check(name, ok, detail=None):
    global passed, failed
    if ok:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}  {detail}')


def summoner(i, team_kills):
    return {
        'summonerName': f'玩家{i}', 'puuid': f'p{i}', 'isCurrent': i == 0,
        'championId': 64, 'championName': '盲僧', 'championIcon': '',
        'rankInfo': True, 'tier': '钻石' if i % 2 else '大师', 'division': 'II',
        'lp': 78 + i, 'rankIcon': '', 'spell1Icon': '', 'spell2Icon': '',
        'itemIcons': ['', '', '', '', '', '', ''],
        'kills': 11 - i, 'deaths': 3, 'assists': 17 - i, 'cs': 190 + i,
        'gold': 12000 + i * 100, 'runeIcon': '', 'champLevel': 16,
        'demage': 28400 - i * 1500, 'subteamPlacement': None, 'isPublic': True,
    }


def team(teamId, win):
    return {
        'win': win, 'bans': [], 'banNames': ['亚索', '永恩'],
        'baronKills': 1, 'dragonKills': 4, 'riftHeraldKills': 2, 'hordeKills': 1,
        'towerKills': 9, 'inhibitorKills': 3,
        'kills': 38, 'deaths': 19, 'assists': 62, 'gold': 54200,
        'summoners': [summoner(i, 38) for i in range(5)],
    }


game = {
    'gameId': 7834210956, 'mapIcon': '', 'gameCreation': '2026-11-26 02:14',
    'gameDuration': '32:41', 'modeName': '单双排位', 'mapName': '召唤师峡谷',
    'queueId': 420, 'win': True, 'cherryResult': None, 'remake': False,
    'teams': {100: team(100, 'Win'), 200: team(200, 'Fail')},
}

print('格式化函数')
check('fmtK 千位', fmtK(54200) == '54.2k', fmtK(54200))
check('fmtK 不足千', fmtK(190) == '190')
check('rankText 分段位', rankText(game['teams'][100]['summoners'][1]) == '钻石 Ⅱ · 79 LP',
      rankText(game['teams'][100]['summoners'][1]))
check('rankText 大段位', rankText(game['teams'][100]['summoners'][0]) == '大师 · 78 LP')
check('rankText 未定级', rankText({'rankInfo': True, 'tier': ''}) == '未定级')
check('rankText 关闭段位显示', rankText({'rankInfo': False}) == '')

print('渲染')
view = MatchDetailView()
view.resize(1000, 700)
view.show()
view.updateGame(game)
settle()

rows = view.findChildren(QLabel, 'rowName')
check('10 名玩家行', len(rows) == 10, len(rows))
check('当前玩家行存在', any(r.property('current') for r in view.findChildren(type(rows[0].parentWidget()))))
head = view.findChild(QLabel, 'matchResult')
check('结果方块为胜', head is not None and head.text() == '胜', head.text() if head else None)
title = view.findChild(QLabel, 'matchTitle')
check('标题带地图与模式', '召唤师峡谷' in title.text() and '单双排位' in title.text(), title.text())

kp = [l.text() for l in view.findChildren(QLabel, 'rowNum') if l.text().endswith('%')]
# 玩家0: (11+17)/38 = 74%
check('参团率自算', kp and kp[0] == '74%', kp[:3])

best = [l.text() for l in view.findChildren(QLabel, 'bestValue')]
check('本场之最四项', len(best) == 4, best)
check('最高伤害取全场', best and best[0] == '28.4k', best)

blocks = view.findChildren(type(view.findChild(QLabel, 'teamSide').parentWidget().parentWidget()))
check('两个队伍块', len(blocks) == 2, len(blocks))
sides = [l.text() for l in view.findChildren(QLabel, 'teamSide')]
check('蓝红双方', sides == ['蓝方', '红方'], sides)

view.grab().save('E:/seraphine/design/detail-offscreen.png')

# 窄屏收列
view.resize(660, 700)
settle()
csVisible = any(l.isVisible() for l in view.findChildren(QLabel, 'rowNum') if not l.text().endswith('%'))
check('窄屏收起补刀列', not csVisible)
view.grab().save('E:/seraphine/design/detail-offscreen-narrow.png')

# 斗魂竞技场 8 队
cherry = dict(game, queueId=1700, cherryResult=1, teams={
    k * 100: {'win': None, 'bans': [], 'banNames': [], 'baronKills': 0, 'dragonKills': 0,
              'riftHeraldKills': 0, 'hordeKills': 0, 'towerKills': 0, 'inhibitorKills': 0,
              'kills': 6, 'deaths': 4, 'assists': 4, 'gold': 20000,
              'summoners': [dict(summoner(i, 6), subteamPlacement=k) for i in range(2)]}
    for k in range(1, 9)})
view.resize(1000, 700)
view.updateGame(cherry)
settle()
check('斗魂 8 队 16 人', len(view.findChildren(QLabel, 'rowName')) == 16)
check('斗魂结果为名次', view.findChild(QLabel, 'matchResult').text() == '1')
check('斗魂先锋格隐藏', not any(
    l.isVisible() for l in view.findChildren(QLabel, 'teamLabel') if l.text() == '先锋'))

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
