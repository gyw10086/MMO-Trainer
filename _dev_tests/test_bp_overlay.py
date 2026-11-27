# coding:utf-8
"""BP 对位浮窗的离线测试

覆盖三块：
1. 选人会话的纯逻辑（分路 / 阵容 / 该看谁）
2. 101 双向对位的解析、缓存与单英雄失效
3. 切到韩服时走 OP.GG、切回国服走 101（数据源跟随地区）

不连网、不起窗口，只跑逻辑。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.lol import bp_counters as D  # noqa: E402
from app.view import bp_overlay_controller as C  # noqa: E402
from app.view.bp_overlay import lane101, laneName  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL

    if cond:
        PASS += 1
        print(f'PASS {name}' + (f'   {detail}' if detail else ''))
    else:
        FAIL += 1
        print(f'FAIL {name}   {detail}')


def wrap(detail):
    return {'code': 0, 'data': {'_fieldValues': {'R1': detail}}, 'result': 0}


# 真实结构（16.23 翡翠+ 中路暮光星灵，截断到 6 条以验证 limit=5）
CONFRONT = json.dumps({
    'dtstatdate': '20261125',
    'low_op_details': '#'.join([
        '1_34_37.25_0', '2_69_38.64_0', '3_910_42.82_0',
        '4_1_44.1_0', '5_2_44.5_0', '6_3_45.0_0',
    ]),
    'high_op_details': '#'.join([
        '1_84_59.41_0', '2_893_58.88_0', '3_14_58.54_0',
        '4_4_57.2_0', '5_5_56.1_0', '6_6_55.0_0',
    ]),
})


class FakeCfg:
    """只回答 opggRegion，别的键一律返回 None"""

    opggRegion = 'opggRegion'

    def __init__(self, region='cn'):
        self.region = region

    def get(self, key):
        return self.region if key == 'opggRegion' else None


def test_logic():
    # 分路映射
    check('MIDDLE -> 101 MIDDLE', lane101('MIDDLE') == 'MIDDLE')
    check('UTILITY -> SUPPORT', lane101('utility') == 'SUPPORT')
    check('BOTTOM -> BOTTOM', lane101('BOTTOM') == 'BOTTOM')
    check('空分路不猜', lane101('') == '')
    check('中文名', laneName('MIDDLE') == '中路')

    # 一局的身份
    check('sessionKey 用 gameId',
          C.sessionKeyOf({'gameId': 123, 'myTeam': []}) == '123')
    check('sessionKey 没有 gameId 时用 cellId',
          C.sessionKeyOf({'myTeam': [{'cellId': 1}, {'cellId': 0}]})
          == 'cells:0,1')
    check('sessionKey 全空返回空串', C.sessionKeyOf({}) == '')

    # 我的分路
    data = {
        'localPlayerCellId': 2,
        'myTeam': [{'cellId': 0}, {'cellId': 2, 'assignedPosition': 'MIDDLE'}],
    }
    check('localLane 按 cellId 找', C.localLaneOf(data) == 'MIDDLE')
    check('localLane 找不到返回空', C.localLaneOf({'myTeam': []}) == '')

    # 换位：补位到辅助、再跟队友换去中路，assignedPosition 还是 UTILITY
    swapped = {
        'localPlayerCellId': 3,
        'myTeam': [{'cellId': 3, 'assignedPosition': 'UTILITY',
                    'selectedPosition': 'MIDDLE'}],
    }
    check('换位后按 selectedPosition（不是辅助）',
          C.localLaneOf(swapped) == 'MIDDLE', C.localLaneOf(swapped))

    filler = {
        'localPlayerCellId': 3,
        'myTeam': [{'cellId': 3, 'assignedPosition': 'MIDDLE',
                    'selectedPosition': 'UNSELECTED'}],
    }
    check('selectedPosition 没值时退回 assignedPosition',
          C.localLaneOf(filler) == 'MIDDLE', C.localLaneOf(filler))

    check('两个位置都没有返回空',
          C.localLaneOf({'localPlayerCellId': 3,
                         'myTeam': [{'cellId': 3}]}) == '')

    # 阵容与"我的对位"
    team = [
        {'championId': 711, 'assignedPosition': 'MIDDLE'},
        {'championId': 64, 'assignedPosition': 'JUNGLE'},
        {'championId': 0, 'assignedPosition': 'TOP'},
    ]
    roster, mineId = C.buildRoster(team, 'MIDDLE')
    check('阵容只放已锁', len(roster) == 2, f'{len(roster)} 个')
    check('我的对位标记', mineId == 711 and roster[0]['mine'],
          f'mine={mineId}')
    check('其他人不标 mine', not roster[1]['mine'])

    _, noMine = C.buildRoster(team, '')
    check('没有分路时不标 mine', noMine == 0)

    # 对面也换位：assignedPosition 是 UTILITY，selectedPosition 是 MIDDLE
    swappedTeam = [{'championId': 3, 'assignedPosition': 'UTILITY',
                    'selectedPosition': 'MIDDLE'}]
    swappedRoster, swappedMine = C.buildRoster(swappedTeam, 'MIDDLE')
    check('buildRoster 也认换位后的分路',
          swappedMine == 3 and swappedRoster[0]['lane'] == 'MIDDLE',
          f'mine={swappedMine}')

    # 该看谁
    check('新锁的是我的对位 -> 优先',
          C.pickTarget({711, 64}, {64}, 64, 711) == 711)
    check('新锁的不是我对位 -> 看新锁的',
          C.pickTarget({711, 64, 86}, {711}, 711, 711) == 86)
    check('没有新锁 -> 保持当前',
          C.pickTarget({711, 64}, {711, 64}, 64, 711) == 64)
    check('当前目标没了 -> 回到我对位',
          C.pickTarget({711, 64}, {711, 64}, 999, 711) == 711)
    check('敌方全空 -> 0', C.pickTarget(set(), set(), 711, 711) == 0)


async def test_counters():
    calls = {'n': 0}

    async def fakeVersions():
        return ('16.23', '16.22')

    async def fakeRequest(path, params):
        calls['n'] += 1
        return wrap(CONFRONT)

    D.cnStrategy.getVersionNames = fakeVersions
    D.cnStrategy.request = fakeRequest
    D.cfg = FakeCfg('cn')

    payload = await D.bpCounters.getPair(711, 'MIDDLE', 'emerald_plus', 5)

    check('国服 source', payload['source'] == '国服 101', payload['source'])
    check('国服版本', payload['version'] == '16.23', payload['version'])
    check('打不过取 5 个', len(payload['weak']) == 5, str(len(payload['weak'])))
    check('压制取 5 个', len(payload['strong']) == 5, str(len(payload['strong'])))
    check('打不过按胜率升序', payload['weak'][0]['championId'] == 34,
          str(payload['weak'][0]))
    check('胜率换算成 0~1', abs(payload['weak'][0]['winRate'] - 0.3725) < 1e-6,
          str(payload['weak'][0]['winRate']))
    check('压制按胜率降序', payload['strong'][0]['championId'] == 84,
          str(payload['strong'][0]))
    check('请求只打了一次', calls['n'] == 1, f"calls={calls['n']}")

    # 再查一次应命中缓存
    await D.bpCounters.getPair(711, 'MIDDLE', 'emerald_plus', 5)
    check('第二次命中缓存', calls['n'] == 1, f"calls={calls['n']}")

    # 手动刷新：只失效这个英雄
    D.bpCounters.invalidate(711)
    await D.bpCounters.getPair(711, 'MIDDLE', 'emerald_plus', 5)
    check('失效后重新请求', calls['n'] == 2, f"calls={calls['n']}")

    # 切到韩服：走 OP.GG，且不命中 101 的缓存
    def fakeRows(items):
        return [{'championId': i, 'winRate': w} for i, w in items]

    async def fakeBuild(region, mode, championId, position, tier):
        return {
            'data': {'counters': {
                'weakAgainst': fakeRows([(34, 0.44), (69, 0.45)]),
                'strongAgainst': fakeRows([(84, 0.58), (893, 0.57)]),
            }},
            'version': '15.1.1',
        }

    D.opgg.getChampionBuild = fakeBuild
    D.cfg = FakeCfg('kr')

    other = await D.bpCounters.getPair(103, 'MIDDLE', 'emerald_plus', 5)

    check('韩服 source', other['source'] == 'OP.GG 外服', other['source'])
    check('韩服版本', other['version'] == '15.1.1', other['version'])
    check('韩服打不过', len(other['weak']) == 2, str(len(other['weak'])))
    check('韩服压制', len(other['strong']) == 2, str(len(other['strong'])))
    check('韩服胜率', abs(other['weak'][0]['winRate'] - 0.44) < 1e-6,
          str(other['weak'][0]))

    # 切回国服，同一个英雄要重新走 101（缓存键含数据源）
    D.cfg = FakeCfg('cn')
    before = calls['n']
    back = await D.bpCounters.getPair(103, 'MIDDLE', 'emerald_plus', 5)
    check('切回国服重新取 101', calls['n'] == before + 1,
          f'calls={calls["n"]}')
    check('切回国服 source', back['source'] == '国服 101', back['source'])


def main():
    test_logic()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(test_counters())
    finally:
        loop.close()

    print(f'\n{PASS} passed, {FAIL} failed')
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
