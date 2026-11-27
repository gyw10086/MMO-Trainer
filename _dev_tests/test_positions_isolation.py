# coding:utf-8
"""跨玩家分路缓存隔离回归（黑盒）

## 问题

`positions.resolve(puuid, games)` 的分路缓存只按 `gameId` 存，没带 puuid。
同一场对局里 A 和 B 的分路当然不一样，但 A 查过之后这场 `gameId` 就算
「已缓存」，B 再查直接命中 A 的分路 —— 生涯页的分路会跨玩家串味
（实测表现：查完朋友再看自己，自己那场显示成朋友的分路）。

## 这份测试的边界

只看公开行为：`resolve()` 的返回值 + `CACHE_PATH` 里的 JSON 内容。
不看内部实现 —— 缓存键怎么拼、schema 版本怎么调、有没有 `_lastFill`，
换个写法都不该影响这里。所以：

- 只 patch `positions.connector.getSummonerGamesByPuuidViaSGP`（SGP 取数）
- 缓存文件摊平后只认「像分路的值」，不认键名
- 重置状态时顺手清 `_lastFill`（存在才清，不依赖它一定存在）

运行: python _dev_tests/test_positions_isolation.py
"""
import asyncio
import functools
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

from app.lol import positions  # noqa: E402

CACHE_FILE = os.path.join(tempfile.gettempdir(),
                          'mmo_test_pos_isolation.json')

# 三场对局：正常峡谷 / SGP 里没有自己 / 大乱斗
GAME = 111
GAME_WITHOUT_SELF = 222
GAME_ARAM = 333

# 缓存里可能出现的位置值；摊平 JSON 时只认这些
LANES = {'TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY', 'NONE'}

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  -> {detail}' if detail else ''))

    if not ok:
        _failures.append(name)


# ------------------------------------------------------------------ 造数据
def participant(pid, puuid, lane, championId=None):
    """match-v5 里的一个参赛者"""
    return {
        'puuid': puuid,
        'participantId': pid,
        'championId': championId if championId is not None else pid,
        'teamPosition': lane,
        'individualPosition': lane,
        'spell1Id': 4,
        'spell2Id': 12,
    }


def canyonMatch():
    """同一场十人局：A 在中路（第 8 位）、B 在下路（第 4 位）

    故意把本人放在 [7] 这种非首位：实现若偷懒取 `participants[0]`
    会拿到别人，这里能兜住。
    """
    others = [participant(pid, f'x{pid}', '') for pid in (1, 2, 3, 5, 6, 7, 9, 10)]
    roster = [
        participant(8, 'A', 'MIDDLE'),      # A：中路，第 8 位
        participant(4, 'B', 'BOTTOM'),      # B：下路，第 4 位
    ] + others
    roster.sort(key=lambda item: item['participantId'])

    return {'gameId': GAME, 'participants': roster}


def matchWithoutSelf():
    """SGP 回来了，但十个人里没有 puuid='A'（好友视角 / puuid 变了）"""
    roster = [participant(pid, f'y{pid}', '') for pid in range(1, 11)]

    return {'gameId': GAME_WITHOUT_SELF, 'participants': roster}


def aramMatch():
    """大乱斗 / 斗魂：官方两个位置字段都是空 / Invalid"""
    roster = [participant(pid, f'z{pid}', '') for pid in range(1, 11)]

    for item in roster:
        item['teamPosition'] = ''
        item['individualPosition'] = 'Invalid'

    roster[0]['puuid'] = 'A'

    return {'gameId': GAME_ARAM, 'participants': roster}


def lcuGame(gameId):
    """LCU 的对局列表条目：只有自己一条 participant，**没有 puuid 字段**

    这条记录本身判不出任何分路（lane=NONE / role=SUPPORT），所以 `resolve`
    返回的位置只可能来自 SGP 缓存 —— 断言才干净。
    """
    return {
        'gameId': gameId,
        'participants': [{
            'championId': 3,
            'spell1Id': 4,
            'spell2Id': 12,
            'timeline': {'lane': 'NONE', 'role': 'SUPPORT'},
        }],
    }


# ------------------------------------------------------------------ 桩
class FakeSGP:
    """替掉 `connector.getSummonerGamesByPuuidViaSGP`"""

    def __init__(self):
        self.payload = {}      # puuid -> [match-v5 dict]
        self.calls = []        # 记录谁被查过，失败时好定位

    async def fetch(self, puuid, *args, **kwargs):
        self.calls.append(puuid)

        return {'games': [{'json': raw} for raw in self.payload.get(puuid, [])]}


fake = FakeSGP()


# ------------------------------------------------------------------ 工具
def resetState():
    """清内存缓存 + 删缓存文件（外加 `_lastFill`，有就清）"""
    positions._cache = None

    lastFill = getattr(positions, '_lastFill', None)

    try:
        if hasattr(lastFill, 'clear'):
            lastFill.clear()
    except Exception:
        pass

    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)


def readCache():
    try:
        with open(CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def positionLeaves(node, path=''):
    """把缓存 JSON 摊平成 [(路径, 分路值)]

    键怎么拼不管（`"111"` / `"A:111"` / `{"A": {"111": ...}}` 都行），
    只挑值落在分路词表里的叶子，并跳过版本号。
    """
    found = []

    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in ('version', 'schema', 'schemaversion'):
                continue
            found += positionLeaves(value, f'{path}.{key}' if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += positionLeaves(value, f'{path}[{index}]')
    elif isinstance(node, str) and node.upper() in LANES:
        found.append((path, node.upper()))

    return found


def at(result, gameId):
    """结果取值，兼容 int / str 两种键"""
    if not isinstance(result, dict):
        return ''

    return result.get(gameId, result.get(str(gameId), ''))


# ------------------------------------------------------------------ 断言
async def run():
    # ---- 1 基线：A 自己那场是中路
    resetState()
    fake.payload = {'A': [canyonMatch()], 'B': [canyonMatch()]}
    fake.calls.clear()

    resultA = await positions.resolve('A', [lcuGame(GAME)])
    check('1 基线：查 A 得到 MIDDLE',
          at(resultA, GAME) == 'MIDDLE', f'实际 {at(resultA, GAME)!r}')

    # ---- 2 核心：紧接着拿同一场查 B，不该拿到 A 的中路
    resultB = await positions.resolve('B', [lcuGame(GAME)])
    check('2 跨玩家隔离：同一场查 B 得到 BOTTOM（不是 A 的 MIDDLE）',
          at(resultB, GAME) == 'BOTTOM', f'实际 {at(resultB, GAME)!r}')

    # ---- 3 回头再查 A，仍应是自己的中路
    resultA2 = await positions.resolve('A', [lcuGame(GAME)])
    check('3 回头再查 A 仍是 MIDDLE（没有被 B 覆盖）',
          at(resultA2, GAME) == 'MIDDLE', f'实际 {at(resultA2, GAME)!r}')

    # ---- 4 缓存里两条独立记录
    cached = readCache()
    leaves = positionLeaves(cached)
    middles = [path for path, lane in leaves if lane == 'MIDDLE']
    bottoms = [path for path, lane in leaves if lane == 'BOTTOM']

    check('4 缓存文件里 A / B 各有一条独立记录（键不同）',
          bool(middles) and bool(bottoms) and not set(middles) & set(bottoms),
          f'MIDDLE 在 {middles}，BOTTOM 在 {bottoms}，'
          f'缓存={json.dumps(cached, ensure_ascii=False)[:200]}')

    # ---- 5 SGP 里找不到自己就不写缓存
    resetState()
    fake.payload = {'A': [matchWithoutSelf()]}

    before = len(positionLeaves(readCache()))
    resultNoSelf = await positions.resolve('A', [lcuGame(GAME_WITHOUT_SELF)])
    after = len(positionLeaves(readCache()))

    check('5 SGP 里找不到自己时不写缓存（条目数不变）',
          after == before,
          f'{before} -> {after}，返回 {at(resultNoSelf, GAME_WITHOUT_SELF)!r}')

    # ---- 6 大乱斗 / 斗魂返回空串
    resetState()
    fake.payload = {'A': [aramMatch()]}

    resultAram = await positions.resolve('A', [lcuGame(GAME_ARAM)])
    check("6 大乱斗（teamPosition='' / individualPosition='Invalid'）返回空串",
          at(resultAram, GAME_ARAM) == '', f'实际 {at(resultAram, GAME_ARAM)!r}')

    # ---- 7 附加：与查询顺序无关
    resetState()
    fake.payload = {'A': [canyonMatch()], 'B': [canyonMatch()]}
    fake.calls.clear()

    firstB = await positions.resolve('B', [lcuGame(GAME)])
    thenA = await positions.resolve('A', [lcuGame(GAME)])
    check('7 附加：先查 B 再查 A，两边都不串味',
          at(firstB, GAME) == 'BOTTOM' and at(thenA, GAME) == 'MIDDLE',
          f'B={at(firstB, GAME)!r} A={at(thenA, GAME)!r}')

    print(f'\nSGP 调用记录（本轮）: {fake.calls}')


def main():
    realFetch = positions.connector.getSummonerGamesByPuuidViaSGP
    oldPath = positions.CACHE_PATH
    oldCache = positions._cache

    positions.connector.getSummonerGamesByPuuidViaSGP = fake.fetch
    positions.CACHE_PATH = CACHE_FILE
    positions._cache = None

    try:
        asyncio.run(run())
    finally:
        positions.connector.getSummonerGamesByPuuidViaSGP = realFetch
        positions.CACHE_PATH = oldPath
        positions._cache = oldCache

        for path in (CACHE_FILE, CACHE_FILE + '.tmp'):
            if os.path.exists(path):
                os.remove(path)

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
