# coding:utf-8
"""「看别人的对局详情」回归

Bug 现场：看好友生涯、点他自己单排的对局，全部显示「对局详情读取失败」。
根因不是接口挂了（LCU 逐场返回 200），是解析时按**当前登录账号**的 puuid
去对局里找「我」—— 别人单排的局里当然找不到，`win` 一直是 None，函数
直接 `return None`，浮层只能显示错误页。

覆盖：
  - 指定 puuid 不在场上时不再整份详情失败（win=None / remake=False / 两阵营都在）
  - puuid 匹配上时胜负照旧
  - 浮层 `load()` 接受 puuid，且「重试 / 翻场」都带着它
  - 生涯页把「这页是谁的」传给浮层
  - 标题条对 win=None 显示「—」而不是判成「负」

运行: python _dev_tests/test_detail_owner.py
"""
import asyncio
import functools
import inspect
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

from app.lol import tools  # noqa: E402
from app.lol.connector import connector  # noqa: E402
from app.lol.exceptions import SummonerRankInfoNotFound  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail and not ok else ''))

    if not ok:
        _failures.append(name)


# ------------------------------------------------------------------ 假数据
def makeStats(win=True, remake=False):
    return {
        'win': win,
        'gameEndedInEarlySurrender': remake,
        'kills': 3, 'deaths': 2, 'assists': 9,
        'goldEarned': 12000,
        'totalMinionsKilled': 180,
        'neutralMinionsKilled': 4,
        'champLevel': 14,
        'totalDamageDealtToChampions': 20000,
        'perk0': 8000,
        'item0': 3153, 'item1': 3006, 'item2': 0, 'item3': 0,
        'item4': 0, 'item5': 0, 'item6': 3340,
    }


def makeGame():
    """两个人的最小对局：mate-a 在蓝方赢，mate-b 在红方输"""
    return {
        'gameId': 500958678158,
        'queueId': 420,
        'mapId': 11,
        'gameCreation': 1764000000000,
        'gameDuration': 1800,
        'teams': [
            {'teamId': 100, 'win': 'Win', 'bans': [], 'baronKills': 1,
             'dragonKills': 2, 'riftHeraldKills': 1, 'hordeKills': 0,
             'towerKills': 8, 'inhibitorKills': 2},
            {'teamId': 200, 'win': 'Fail', 'bans': [], 'baronKills': 0,
             'dragonKills': 1, 'riftHeraldKills': 0, 'hordeKills': 0,
             'towerKills': 3, 'inhibitorKills': 0},
        ],
        'participantIdentities': [
            {'participantId': 1, 'player': {'puuid': 'mate-a', 'gameName': '队友A',
                                            'tagLine': '111', 'summonerName': '队友A'}},
            {'participantId': 2, 'player': {'puuid': 'mate-b', 'gameName': '队友B',
                                            'tagLine': '222', 'summonerName': '队友B'}},
        ],
        'participants': [
            {'participantId': 1, 'teamId': 100, 'championId': 103,
             'spell1Id': 4, 'spell2Id': 14, 'stats': makeStats(True, False)},
            {'participantId': 2, 'teamId': 200, 'championId': 64,
             'spell1Id': 11, 'spell2Id': 4, 'stats': makeStats(False, False)},
        ],
    }


class FakeManager:
    def getNameMapByQueueId(self, queueId):
        return {'name': '单双排位', 'map': '召唤师峡谷'}

    def getMapNameById(self, mapId):
        return '召唤师峡谷'

    def getChampionNameById(self, championId):
        return f'英雄{championId}'

    def getMapIconByMapId(self, mapId, win=None):
        return 'app/resource/images/map.png'


async def fakeIcon(*args, **kwargs):
    return ''


async def fakeSummoner(puuid):
    return {'privacy': 'PUBLIC'}


async def fakeRank(puuid):
    raise SummonerRankInfoNotFound()


class patchConnector:
    """把 parseGameDetailData 碰到的外部依赖全换成假的"""

    targets = ('getChampionIcon', 'getSummonerSpellIcon', 'getItemIcon',
               'getRuneIcon', 'getSummonerByPuuid', 'getRankedStatsByPuuid',
               'manager')

    def __enter__(self):
        self.saved = {name: getattr(connector, name, None) for name in self.targets}

        connector.getChampionIcon = fakeIcon
        connector.getSummonerSpellIcon = fakeIcon
        connector.getItemIcon = fakeIcon
        connector.getRuneIcon = fakeIcon
        connector.getSummonerByPuuid = fakeSummoner
        connector.getRankedStatsByPuuid = fakeRank
        connector.manager = FakeManager()

        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is None:
                try:
                    delattr(connector, name)
                except AttributeError:
                    pass
            else:
                setattr(connector, name, value)

        return False


# ------------------------------------------------------------------ 断言
def testOwnerNotInGame():
    """被查的人不在场上：详情照常返回，只是胜负未知"""
    with patchConnector():
        detail = asyncio.run(tools.parseGameDetailData('someone-else', makeGame()))

    check('puuid 不在场上时不返回 None', detail is not None, str(detail))
    check('胜负留空而不是猜', detail.get('win') is None, str(detail.get('win')))
    check('remake 有初值（不会 NameError）', detail.get('remake') is False,
          str(detail.get('remake')))
    check('两个阵营都在', 100 in detail['teams'] and 200 in detail['teams'],
          str(list(detail['teams'])))
    check('十人列表照常填',
          len(detail['teams'][100]['summoners']) == 1
          and len(detail['teams'][200]['summoners']) == 1,
          str({k: len(v['summoners']) for k, v in detail['teams'].items()}))


def testOwnerWin():
    """puuid 匹配上时胜负 / 重开照旧"""
    with patchConnector():
        win = asyncio.run(tools.parseGameDetailData('mate-a', makeGame()))
        lose = asyncio.run(tools.parseGameDetailData('mate-b', makeGame()))

        remakeGame = makeGame()
        remakeGame['participants'][0]['stats'] = makeStats(True, True)
        remake = asyncio.run(tools.parseGameDetailData('mate-a', remakeGame))

    check('赢的一方 win=True', win['win'] is True, str(win['win']))
    check('输的一方 win=False', lose['win'] is False, str(lose['win']))
    check('重开标记读得到', remake['remake'] is True, str(remake['remake']))
    check('isCurrent 只标一个人',
          sum(len(t['summoners']) for t in win['teams'].values()) == 2)


def testDialogSignature():
    from app.view.match_detail_dialog import MatchDetailDialog

    params = inspect.signature(MatchDetailDialog.load).parameters
    check('load() 接受 puuid', 'puuid' in params, str(list(params)))
    check('puuid 默认空串（不传就按当前账号）',
          params.get('puuid') is not None and params['puuid'].default == '',
          str(params.get('puuid').default if 'puuid' in params else None))

    source = inspect.getsource(MatchDetailDialog)

    check('重试会带上同一个归属人',
          'self.load(self.__game, self.__context, self.__puuid)' in source)
    check('翻场也带上归属人',
          'self.load(self.__context[target], self.__context, self.__puuid)' in source
          or 'self.__context, self.__puuid' in source)


def testCareerPassesOwner():
    from app.view import career_interface as career

    # 折掉换行再匹配：调用是跨行写的，别断言得太死
    flat = ' '.join(inspect.getsource(career.CareerInterface).split())

    check('生涯页把「这页是谁的」传给浮层',
          "openMatchDetail( game, list(self.filtered), getattr(self, 'puuid'" in flat,
          '未找到传参')
    check('传的是本页 puuid', "getattr(self, 'puuid'" in flat)


def testHeadNeutral():
    from app.components.match_detail_view import MatchHead

    head = MatchHead()
    head.updateHead({'gameId': 1, 'win': None, 'remake': False})

    label = head._MatchHead__result
    check('win=None 时标题条显示「—」', label.text() == '—', label.text())

    head.updateHead({'gameId': 2, 'win': True, 'remake': False})
    check('win=True 仍显示「胜」', label.text() == '胜', label.text())

    head.updateHead({'gameId': 3, 'win': False, 'remake': False})
    check('win=False 仍显示「负」', label.text() == '负', label.text())


def main():
    testOwnerNotInGame()
    testOwnerWin()
    testDialogSignature()
    testCareerPassesOwner()
    testHeadNeutral()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
