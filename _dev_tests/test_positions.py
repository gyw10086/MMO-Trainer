# coding:utf-8
"""分路判定回归：三层判据 + 缓存

不联网、不起窗口：SGP 取数换成假函数，只验判据优先级与缓存行为。

覆盖：
  - SGP teamPosition 优先于 individualPosition，`Invalid` 被过滤
  - 算法兜底只认「惩戒=打野」「辅助装=辅助」，没线索返回空（不猜）
  - 老 lane/role：中路 + role=SUPPORT **不再**误判成辅助（原来的主 bug）
  - 缓存：首次补齐、二次命中零请求、哨兵 NONE 不重拉、坏文件不炸
  - 缓存键是 `puuid|gameId`：同一场对局被不同玩家查过不会互相覆盖
  - 判据指纹变了整表作废；puuid 为空不写脏键；表长上限
  - `lookup` 兼容 int / str 两种 gameId 键

运行: python _dev_tests/test_positions.py
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

from app.lol import positions

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


# ------------------------------------------------------------------ 单条判据
def testFromSGP():
    check('teamPosition 优先',
          positions.fromSGP({'teamPosition': 'MIDDLE',
                             'individualPosition': 'BOTTOM'}) == 'MIDDLE')
    check('teamPosition 空时用 individualPosition',
          positions.fromSGP({'teamPosition': '',
                             'individualPosition': 'UTILITY'}) == 'UTILITY')
    check('非峡谷的 Invalid 被过滤',
          positions.fromSGP({'teamPosition': None,
                             'individualPosition': 'Invalid'}) == '')
    check('两个都空返回空',
          positions.fromSGP({}) == '')
    check('None 不炸', positions.fromSGP(None) == '')


def testFromItems():
    check('惩戒 → 打野',
          positions.fromItems({'spell1Id': 11, 'spell2Id': 4}) == 'JUNGLE')
    check('惩戒在 2 号位也认',
          positions.fromItems({'spell1Id': 4, 'spell2Id': 11}) == 'JUNGLE')
    check('辅助装 → 辅助',
          positions.fromItems({'spell1Id': 4, 'spell2Id': 7,
                               'stats': {'item0': 3869}}) == 'UTILITY')
    check('辅助装在顶层 key 也认（SGP 结构）',
          positions.fromItems({'spell1Id': 4, 'spell2Id': 7,
                               'item1': 3870}) == 'UTILITY')
    check('没线索返回空（上单/中单不猜）',
          positions.fromItems({'spell1Id': 4, 'spell2Id': 12,
                               'stats': {'item0': 3153}}) == '')
    check('空对象不炸', positions.fromItems(None) == '')


def testFromLegacy():
    # 这是原来的主 bug：中路局也返回 role=SUPPORT，被无条件当成辅助
    check('中路 + role=SUPPORT 不再误判辅助',
          positions.fromLegacy({'timeline': {'lane': 'NONE',
                                             'role': 'SUPPORT'}}) == '')
    check('下路 + role=SUPPORT → 辅助',
          positions.fromLegacy({'timeline': {'lane': 'BOTTOM',
                                             'role': 'SUPPORT'}}) == 'UTILITY')
    check('下路 + role=CARRY → 下路',
          positions.fromLegacy({'timeline': {'lane': 'BOTTOM',
                                             'role': 'CARRY'}}) == 'BOTTOM')
    check('下路 + role=DUO → 下路（旧代码这里判空）',
          positions.fromLegacy({'timeline': {'lane': 'BOTTOM',
                                             'role': 'DUO'}}) == 'BOTTOM')
    check('上路直读', positions.fromLegacy({'timeline': {'lane': 'TOP'}}) == 'TOP')
    check('扁平结构也认（SGP 那套）',
          positions.fromLegacy({'lane': 'JUNGLE', 'role': 'NONE'}) == 'JUNGLE')


def testHelpers():
    check('positionLabel 标准值',
          positions.positionLabel('UTILITY') == '辅助')
    check('positionLabel 别名',
          positions.positionLabel('ADC') == '下路')
    check('positionLabel 认不出返回空',
          positions.positionLabel('NONE') == '')

    mapping = {123: 'MIDDLE', '456': 'TOP'}
    check('lookup 认 int 键', positions.lookup(mapping, 123) == 'MIDDLE')
    check('lookup 认 str 键', positions.lookup(mapping, 456) == 'TOP')
    check('lookup 空表返回空', positions.lookup(None, 123) == '')

    check('resolveOne 已知优先',
          positions.resolveOne({'spell1Id': 11}, 'TOP') == 'TOP')
    check('resolveOne 无已知时用算法',
          positions.resolveOne({'spell1Id': 11, 'spell2Id': 4}) == 'JUNGLE')


# ------------------------------------------------------------------ 缓存
def testCache():
    oldPath = positions.CACHE_PATH
    oldCache = positions._cache
    oldFill = dict(positions._lastFill)
    realFill = positions._fillFromSGP
    positions.CACHE_PATH = os.path.join(tempfile.gettempdir(),
                                        'mmo_test_positions.json')
    positions._cache = None
    positions._lastFill.clear()

    if os.path.exists(positions.CACHE_PATH):
        os.remove(positions.CACHE_PATH)

    try:
        filled = []

        async def fakeFill(puuid, games):
            filled.append(len(games))
            cache = positions._load()
            cache[positions._key(puuid, 1)] = 'MIDDLE'
            cache[positions._key(puuid, 3)] = 'BOTTOM'
            # 官方明确没位置
            cache[positions._key(puuid, 2)] = positions.NO_POSITION
            positions._save()
            return True

        positions._fillFromSGP = fakeFill

        games = [
            {'gameId': 1, 'participants': [{'timeline': {'lane': 'NONE',
                                                         'role': 'SUPPORT'}}]},
            {'gameId': 2, 'participants': [{'spell1Id': 11, 'spell2Id': 4}]},
            {'gameId': 3, 'participants': [{'timeline': {'lane': 'NONE'}}]},
        ]

        first = asyncio.run(positions.resolve('puuid', games))

        check('首次补齐：SGP 被调用一次', filled == [3], str(filled))
        check('缓存命中直接给官方值', first[1] == 'MIDDLE', str(first))
        check('哨兵 NONE 返回空而不是回退算法', first[2] == '',
              f'{first[2]!r}')
        check('官方给了就用官方', first[3] == 'BOTTOM', str(first))

        # 再跑一次：不该再请求
        filled.clear()
        second = asyncio.run(positions.resolve('puuid', games))

        check('第二次零请求', filled == [], str(filled))
        check('两次结果一致', first == second)

        # 新对局才会再补
        games.append({'gameId': 4, 'participants': [{'spell1Id': 4}]})
        filled.clear()
        asyncio.run(positions.resolve('puuid', games))
        check('出现新对局才重新拉', filled == [4], str(filled))

        # 空入参
        check('空列表不炸', asyncio.run(positions.resolve('puuid', [])) == {})
        check('没有 gameId 的条目被跳过',
              asyncio.run(positions.resolve('puuid', [{'x': 1}])) == {})

        # 坏文件当空表
        with open(positions.CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write('{ 不是 json')
        positions._cache = None
        check('缓存损坏按空表处理', positions._load() == {})

        # 旧 schema 整表作废: 早期版本按 participants[0] 判出来的错值
        # 一旦落盘就再也不会重查 (resolve 只在"有局不在表里"时才拉 SGP)
        with open(positions.CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write('{"version": 1, "positions": {"9": "TOP", "10": "BOTTOM"}}')
        positions._cache = None
        check('旧版本缓存整表作废', positions._load() == {})

        # v2 就是只用 gameId 当键、会被队友顶掉的那版，同样不能信
        with open(positions.CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write('{"version": 2, "positions": {"9": "TOP"}}')
        positions._cache = None
        check('v2（只用 gameId 的毒版）整表作废', positions._load() == {})

        # 判据指纹变了也要作废：值是按旧判法算的，留着等于永久错
        with open(positions.CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write('{"version": %d, "rules": 0, "positions": {"9": "TOP"}}'
                    % positions.CACHE_SCHEMA)
        positions._cache = None
        check('判据版本不符整表作废', positions._load() == {})

        with open(positions.CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write('{"version": %d, "rules": %d, "positions": {"9": "TOP"}}'
                    % (positions.CACHE_SCHEMA, positions.RULES_VERSION))
        positions._cache = None
        check('当前版本正常读出',
              positions._load() == {'9': 'TOP'}, positions._load())

        # 表长上限：每局一条，长年累月会无限增长
        positions._cache = {f'{i}|{i}': 'TOP'
                            for i in range(positions.MAX_ENTRIES + 20)}
        positions._save()

        with open(positions.CACHE_PATH, encoding='utf-8') as f:
            trimmed = json.load(f).get('positions') or {}

        check('缓存条目裁剪到上限',
              len(trimmed) == positions.MAX_ENTRIES, len(trimmed))

    finally:
        positions._fillFromSGP = realFill
        positions.CACHE_PATH = oldPath
        positions._cache = oldCache
        positions._lastFill.clear()
        positions._lastFill.update(oldFill)

        if os.path.exists(oldPath + '.tmp'):
            os.remove(oldPath + '.tmp')


def testCrossPlayer():
    """同一场对局被不同玩家查过，分路不能串号

    这是本轮的根因：缓存只用 gameId 当键，值是「某个 puuid 在这一局的分路」，
    后写的会把先写的顶掉 —— 实测 48 条缓存里 18 条是双排队友的分路。
    """
    oldPath = positions.CACHE_PATH
    oldCache = positions._cache
    oldFill = dict(positions._lastFill)
    realFill = positions._fillFromSGP

    positions.CACHE_PATH = os.path.join(tempfile.gettempdir(),
                                        'mmo_test_positions_cross.json')
    positions._cache = None
    positions._lastFill.clear()

    if os.path.exists(positions.CACHE_PATH):
        os.remove(positions.CACHE_PATH)

    try:
        async def sgpFill(puuid, games):
            """SGP 只认识 a 和 b，且同一局两个人的分路不同

            只用 gameId 当键的话，后写的那个一定顶掉先写的
            """
            cache = positions._load()

            if puuid in ('a', 'b'):
                cache[positions._key(puuid, 1)] = (
                    'MIDDLE' if puuid == 'a' else 'UTILITY')
                positions._save()

            return True

        positions._fillFromSGP = sgpFill

        a = asyncio.run(positions.resolve(
            'a', [{'gameId': 1, 'participants': []}]))
        check('A 拿到自己的分路', a[1] == 'MIDDLE', f'{a[1]!r}')

        b = asyncio.run(positions.resolve(
            'b', [{'gameId': 1, 'participants': []}]))
        check('B 查同一场不会被 A 的值污染', b[1] == 'UTILITY', f'{b[1]!r}')
        check('A 的值也没被 B 顶掉',
              positions.cached('a', 1) == 'MIDDLE', positions.cached('a', 1))

        with open(positions.CACHE_PATH, encoding='utf-8') as f:
            saved = json.load(f).get('positions') or {}

        check('缓存文件里 a|1 与 b|1 两条键共存',
              saved.get(positions._key('a', 1)) == 'MIDDLE'
              and saved.get(positions._key('b', 1)) == 'UTILITY',
              str(sorted(saved)))

        # 第三个人不在 SGP 里（查别人生涯时顺带拉到的对局）：走算法兜底，
        # 绝不能捡到前两个人的值
        me = {'puuid': 'c', 'spell1Id': 11, 'spell2Id': 4}
        c = asyncio.run(positions.resolve(
            'c', [{'gameId': 1, 'participants': [me]}]))
        check('SGP 里没有的人走算法兜底，不捡别人的值',
              c[1] == 'JUNGLE', f'{c[1]!r}')

        # puuid 为空：只走算法，缓存一个字节都不该写
        os.remove(positions.CACHE_PATH)
        positions._cache = None

        empty = asyncio.run(positions.resolve('', [{
            'gameId': 7, 'participants': [{'spell1Id': 11, 'spell2Id': 4}]}]))
        check('puuid 为空仍能用算法给出分路',
              empty[7] == 'JUNGLE', f'{empty[7]!r}')
        check('puuid 为空不落盘（不会出现 |7 这种脏键）',
              not os.path.exists(positions.CACHE_PATH), positions.CACHE_PATH)
    finally:
        positions._fillFromSGP = realFill
        positions.CACHE_PATH = oldPath
        positions._cache = oldCache
        positions._lastFill.clear()
        positions._lastFill.update(oldFill)

        if os.path.exists(positions.CACHE_PATH + '.tmp'):
            os.remove(positions.CACHE_PATH + '.tmp')


def testPickSelf():
    """十人局里必须按 puuid 找自己

    SGP 返回的 `participants` 是完整十个人，取 `[0]` 拿到的是队友 ——
    早期版本就栽在这，缓存里 30 场错 20 场（ADC 显示成上单 / 打野）。
    """
    oldPath = positions.CACHE_PATH
    oldCache = positions._cache
    realFill = positions._fillFromSGP

    positions.CACHE_PATH = os.path.join(tempfile.gettempdir(),
                                        'mmo_test_positions_self.json')
    positions._cache = None

    if os.path.exists(positions.CACHE_PATH):
        os.remove(positions.CACHE_PATH)

    try:
        async def emptyFill(puuid, games):
            return          # 故意不写缓存，逼着走算法兜底

        positions._fillFromSGP = emptyFill

        mates = [
            {'puuid': 'mate1', 'spell1Id': 11, 'spell2Id': 4},   # 打野
            {'puuid': 'mate2', 'item0': 3869},                    # 辅助装
            {'puuid': 'mate3', 'timeline': {'lane': 'TOP'}},
        ]
        me = {'puuid': 'me', 'spell1Id': 4, 'spell2Id': 14}

        result = asyncio.run(positions.resolve('me', [{
            'gameId': 1, 'participants': [me] + mates}]))
        check('不会拿队友的惩戒判成打野', result[1] == '', f'{result[1]!r}')

        smite = {'puuid': 'me', 'spell1Id': 11, 'spell2Id': 4}
        result = asyncio.run(positions.resolve('me', [{
            'gameId': 2, 'participants': mates + [smite]}]))
        check('自己带惩戒才算打野', result[2] == 'JUNGLE', f'{result[2]!r}')

        result = asyncio.run(positions.resolve('nobody', [{
            'gameId': 3, 'participants': [smite]}]))
        check('puuid 对不上时退回第一个，不给空', result[3] == 'JUNGLE',
              f'{result[3]!r}')
    finally:
        positions._fillFromSGP = realFill
        positions.CACHE_PATH = oldPath
        positions._cache = oldCache

        if os.path.exists(oldPath + '.tmp'):
            os.remove(oldPath + '.tmp')


def main():
    testFromSGP()
    testFromItems()
    testFromLegacy()
    testHelpers()
    testCache()
    testCrossPlayer()
    testPickSelf()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
