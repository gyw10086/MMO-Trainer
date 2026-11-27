# coding:utf-8
"""他人单排的对局详情：解析不能整份失败

## 问题

看好友生涯时点开「好友自己单排、我不在场」的那局，弹窗报「对局详情读取失败」；
「好友和我一起打」的局却正常。

根因是 `parseGameDetailData()` 把「找到调用方自己」当成了必须条件：
`win` 只在对局里出现调用方 puuid 时才赋值，然后 `if win == None: return None`
把整份详情丢掉了。而调用方传进来的是**当前登录账号**的 puuid，不是「这局属于谁」，
于是别人的单排局一个人都对不上 → 整页打不开。

## 这份测试盯什么

用真实的 LCU 对局结构造数据（`participantIdentities` 十人 / `participants` 十人 /
`teams` 两队 / queueId=420 / mapId=11），只把 `connector` 上会联网的方法打桩。

- 别人单排的局：**结构要照常返回**，只是 `win is None`、`remake is False`
- 十个人一个不少，`isCurrent` 全 False（拿不到「我」就别硬指一个）
- 我自己参与的局（回归）：`win` 必须是布尔值，且那一行 `isCurrent is True`
- 重开局（回归）：`remake is True`

运行: python _dev_tests/test_detail_foreign_game.py
"""
import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.lol import tools  # noqa: E402
from app.lol.connector import connector  # noqa: E402

MY_PUUID = 'my-puuid'
FOREIGN_PUUIDS = [f'foreign-{i}' for i in range(1, 11)]

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  -> {detail}' if detail else ''))

    if not ok:
        _failures.append(name)


# ------------------------------------------------------------------ 造数据
def participantStats(win=True, earlySurrender=False):
    return {
        'kills': 7,
        'deaths': 3,
        'assists': 11,
        'goldEarned': 12345,
        'perk0': 8010,
        'item0': 3153, 'item1': 3006, 'item2': 0,
        'item3': 0, 'item4': 0, 'item5': 0, 'item6': 3340,
        'totalMinionsKilled': 180,
        'neutralMinionsKilled': 12,
        'champLevel': 16,
        'totalDamageDealtToChampions': 24000,
        'gameEndedInEarlySurrender': earlySurrender,
        'win': win,
    }


def buildGame(puuids, myIndex=-1, myStats=None):
    """一场 10 人 5v5：pid 1~5 在 100 队（赢），6~10 在 200 队（输）

    `myIndex` 指定「我」在 `puuids` 里的下标（-1 = 这局没有我），
    只会影响 stats 里的 win / 重开标记，不影响 puuid 本身。
    """
    identities = []
    participants = []

    for index, puuid in enumerate(puuids):
        pid = index + 1
        teamId = 100 if pid <= 5 else 200
        win = teamId == 100
        stats = participantStats(win=win)

        if index == myIndex and myStats:
            stats.update(myStats)

        identities.append({
            'participantId': pid,
            'player': {
                'puuid': puuid,
                'gameName': f'玩家{pid}',
                'summonerName': f'玩家{pid}',
                'tagLine': 'CN1',
            },
        })
        participants.append({
            'participantId': pid,
            'teamId': teamId,
            'championId': 60 + pid,
            'spell1Id': 4,
            'spell2Id': 12,
            'stats': stats,
        })

    return {
        'gameId': 5566778899,
        'queueId': 420,
        'mapId': 11,
        'gameCreation': 1730000000000,
        'gameDuration': 1830,
        'teams': [
            {'teamId': 100, 'win': True, 'bans': [{'championId': 1}],
             'baronKills': 1, 'dragonKills': 3, 'riftHeraldKills': 1,
             'hordeKills': 0, 'towerKills': 8, 'inhibitorKills': 2},
            {'teamId': 200, 'win': False, 'bans': [{'championId': 266}],
             'baronKills': 0, 'dragonKills': 1, 'riftHeraldKills': 0,
             'hordeKills': 1, 'towerKills': 2, 'inhibitorKills': 0},
        ],
        'participantIdentities': identities,
        'participants': participants,
    }


# ------------------------------------------------------------------ 打桩
async def fakeIcon(*args, **kwargs):
    return ''


async def fakeSummonerByPuuid(puuid, *args, **kwargs):
    return {'privacy': 'PUBLIC', 'puuid': puuid}


async def fakeRankedStats(puuid, *args, **kwargs):
    return {'queueMap': {'RANKED_SOLO_5x5': {'tier': 'GOLD', 'division': 'II',
                                            'leaguePoints': 45}}}


async def fakeCurrentSummoner(*args, **kwargs):
    return {'puuid': MY_PUUID}


def fakeManager():
    return types.SimpleNamespace(
        getNameMapByQueueId=lambda queueId: {'name': '单双排',
                                             'map': '召唤师峡谷'},
        getChampionNameById=lambda championId: '',
        getMapNameById=lambda mapId: '召唤师峡谷',
        getMapIconByMapId=lambda mapId, win=None: '',
    )


STUBS = {
    'getChampionIcon': fakeIcon,
    'getSummonerSpellIcon': fakeIcon,
    'getItemIcon': fakeIcon,
    'getRuneIcon': fakeIcon,
    'getSummonerByPuuid': fakeSummonerByPuuid,
    'getRankedStatsByPuuid': fakeRankedStats,
    'getCurrentSummoner': fakeCurrentSummoner,
}


# ------------------------------------------------------------------ 取值
def resolveDetail(puuid, game):
    """跑一次解析；异常也当结果返回，免得一条炸掉整个测试"""
    try:
        return asyncio.run(tools.parseGameDetailData(puuid, game)), None
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def summonersOf(data, teamId):
    if not isinstance(data, dict):
        return []

    team = (data.get('teams') or {}).get(teamId) or {}

    return team.get('summoners') or []


def allSummoners(data):
    return summonersOf(data, 100) + summonersOf(data, 200)


def pick(data, key):
    return data.get(key) if isinstance(data, dict) else '<整份详情为 None>'


def brief(data):
    """详情对象太大，只留关键字段做证词"""
    if not isinstance(data, dict):
        return repr(data)

    return (f'gameId={data.get("gameId")} win={data.get("win")!r} '
            f'remake={data.get("remake")!r} '
            f'模式={data.get("modeName")!r} 队列={data.get("queueId")}')


# ------------------------------------------------------------------ 断言
def testForeignGame():
    print('—— 别人单排的局（我不在场）——')
    game = buildGame(FOREIGN_PUUIDS, myIndex=-1)
    data, error = resolveDetail(MY_PUUID, game)

    check('1 别人单排的局能解析（返回值不是 None）',
          data is not None, error or brief(data))

    check('2 胜负未知时不瞎猜：win is None 且 remake is False',
          isinstance(data, dict) and data.get('win') is None
          and data.get('remake') is False,
          f'win={pick(data, "win")!r} remake={pick(data, "remake")!r}')

    rows = allSummoners(data)
    check('3 十个人都在（100 队 5 + 200 队 5）',
          len(rows) == 10, f'实际 {len(rows)} 条')

    currents = [row.get('isCurrent') for row in rows]
    check('4 所有行 isCurrent 都是 False（拿不到「我」就不指人）',
          bool(currents) and not any(currents), str(currents))

    if isinstance(data, dict):
        check('4b 队伍胜负仍然来自 LCU（与「我在不在场」无关）',
              (data['teams'][100]['win'] is True
               and data['teams'][200]['win'] is False),
              f'100={data["teams"][100]["win"]} 200={data["teams"][200]["win"]}')


def testOwnGame():
    print('\n—— 回归：我自己参与的局 ——')
    game = buildGame(FOREIGN_PUUIDS, myIndex=2, myStats={'win': True})
    game['participantIdentities'][2]['player']['puuid'] = MY_PUUID

    data, error = resolveDetail(MY_PUUID, game)
    win = pick(data, 'win')

    check('5 win 是布尔值（不是 None、也不是异常）',
          isinstance(win, bool), error or f'win={win!r}')

    rows = allSummoners(data)
    mine = [row for row in rows if row.get('isCurrent')]
    check('5b 我自己那一行 isCurrent 为 True（且只有一行）',
          len(mine) == 1, f'实际命中 {len(mine)} 行')

    check('5c 十个人依然齐全', len(rows) == 10, f'实际 {len(rows)} 条')

    if mine:
        check('5d win 与 LCU 里我的 stats.win 一致（我在 100 队 → True）',
              win is True, f'win={win!r}')


def testRemake():
    print('\n—— 回归：重开局 ——')
    game = buildGame(FOREIGN_PUUIDS, myIndex=6,
                     myStats={'win': False, 'gameEndedInEarlySurrender': True})
    game['participantIdentities'][6]['player']['puuid'] = MY_PUUID

    data, error = resolveDetail(MY_PUUID, game)

    check('6 重开局 remake 为 True',
          isinstance(data, dict) and data.get('remake') is True,
          error or f'remake={pick(data, "remake")!r}')

    check('6b 重开局的 win 仍是布尔值', isinstance(pick(data, 'win'), bool),
          f'win={pick(data, "win")!r}')


def main():
    saved = {name: getattr(connector, name) for name in STUBS}
    saved['manager'] = connector.manager

    try:
        for name, func in STUBS.items():
            setattr(connector, name, func)

        connector.manager = fakeManager()

        testForeignGame()
        testOwnGame()
        testRemake()
    finally:
        for name, value in saved.items():
            setattr(connector, name, value)

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
