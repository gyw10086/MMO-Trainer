# coding:utf-8
"""国服对位数据（101 数据站）的测试

离线部分跑解析 / 映射；live 部分真的打一次接口，网络不通就跳过，
不让网络问题把整轮测试染红。
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.lol import cn_strategy as C  # noqa: E402

# 真实返回（安妮 中路 翡翠+ 16.23），外层是腾讯代理塞的随机字段名
WRAPPED = {
    'code': 0,
    'data': {
        '_fieldValues': {
            'R17968': '{"dtstatdate":"20261125",'
                      '"low_op_details":"1_246_43.68_-9.05446971#'
                      '2_126_45.26_-7.78843763#3_39_45.68_-8.41727992",'
                      '"high_op_details":"1_86_71.43_16.65739733"}'
        }
    },
    'result': 0,
}

EMPTY = {'code': 0, 'data': {'_fieldValues': {'R17968': ''}}, 'result': 0}


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))
    assert ok, name


def testParse():
    raw = C.extractRaw(WRAPPED)
    check('extractRaw 取出内层 JSON', raw.startswith('{'), raw[:20])

    items = C.parseConfront(raw)
    check('解析出 3 个克制者', len(items) == 3, str(len(items)))
    check('按胜率升序',
          [i['winRate'] for i in items] == sorted(i['winRate'] for i in items),
          str([round(i['winRate'], 3) for i in items]))
    check('胜率归一化到 0~1',
          abs(items[0]['winRate'] - 0.4368) < 1e-6, str(items[0]['winRate']))
    check('英雄 id 正确', [i['championId'] for i in items] == [246, 126, 39],
          str([i['championId'] for i in items]))
    check('带上统计日期', items[0]['date'] == '20261125', items[0]['date'])

    check('空数据返回空列表', C.parseConfront(C.extractRaw(EMPTY)) == [])
    check('脏数据不抛异常', C.parseConfront('not-json') == [])
    check('缺字段不抛异常', C.parseConfront('{"dtstatdate":"1"}') == [])
    check('extractRaw 容忍 None', C.extractRaw(None) == '')
    check('extractRaw 容忍缺字段', C.extractRaw({'data': {}}) == '')


def testMapping():
    check('MID -> MIDDLE', C.CnStrategy.laneOf('MID') == 'MIDDLE')
    check('middle -> MIDDLE', C.CnStrategy.laneOf('middle') == 'MIDDLE')
    check('UTILITY -> SUPPORT', C.CnStrategy.laneOf('UTILITY') == 'SUPPORT')
    check('adc -> BOTTOM', C.CnStrategy.laneOf('adc') == 'BOTTOM')
    check('未知分路返回空', C.CnStrategy.laneOf('') == '')

    check('翡翠+ -> 26', C.CnStrategy.tierOf('emerald_plus') == '26')
    check('所有段位 -> 255', C.CnStrategy.tierOf('all') == '255')
    check('未知段位兜底翡翠+', C.CnStrategy.tierOf('???') == '26')
    check('段位名', C.CnStrategy.tierName('emerald_plus') == '翡翠+')

    label = C.CnStrategy.label('emerald_plus', 'MIDDLE')
    check('来源说明含段位与分路', label == '国服 翡翠+ · 中路', label)


async def live():
    await C.cnStrategy.start()

    try:
        versions = await C.cnStrategy.getVersionNames()
        check('能取到版本列表', bool(versions), str(versions[:3]))

        items = await C.cnStrategy.getChampionCounters(
            1, 'MIDDLE', 'emerald_plus', 3)
        check('安妮中路有克制数据', len(items) == 3, str(len(items)))
        check('克制者胜率都在 30%~60%',
              all(0.3 < i['winRate'] < 0.6 for i in items),
              str([round(i['winRate'], 3) for i in items]))

        # 同一个请求第二次走缓存, 应该秒回
        again = await C.cnStrategy.getChampionCounters(
            1, 'MIDDLE', 'emerald_plus', 3)
        check('缓存结果一致', again == items)

        empty = await C.cnStrategy.getChampionCounters(
            1, '', 'emerald_plus', 3)
        check('没有分路时不发请求', empty == [])
    finally:
        await C.cnStrategy.close()


def main():
    testParse()
    testMapping()

    try:
        asyncio.run(live())
    except AssertionError:
        raise
    except Exception as e:
        print(f'SKIP  live 接口不可用: {type(e).__name__}: {e}')

    print('\n全部通过')


if __name__ == '__main__':
    unittest.main(exit=False)
    main()
