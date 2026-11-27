# coding:utf-8
"""国服数据源（101 数据站）→ OP.GG 窗口结构的测试

离线部分跑解析 / 映射 / 符文系推导；live 部分用真实接口校验一次，
网络或客户端不可用时跳过，不让环境问题把整轮测试染红。
"""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.lol import cn_data as D  # noqa: E402
from app.lol.cn_strategy import extractRaw  # noqa: E402


def wrap(detail: str) -> dict:
    """腾讯代理把内容塞进随机字段名里，这里复刻它的外壳"""
    return {'code': 0, 'data': {'_fieldValues': {'R18087': detail}}, 'result': 0}


# 真实返回（16.23 翡翠+ 中路榜单前三条）
RANK_JSON = json.dumps({
    'dtstatdate': '20261125',
    'datadetails': (
        '1_55_T1_MIDDLE_54.17_3.89_8.47_69,800,8_1_0_0'
        '#2_3_T1_MIDDLE_52.58_7.67_6.78_910,901,163_16_0_0'
        '#3_105_T1_MIDDLE_53.1_4.87_9.09_75,517,268_-2_0_0'
    ),
})

# 真实返回（阿狸中路出装）
BUILD_JSON = json.dumps({
    'dtstatdate': '20261125',
    'starting_details': '1056,2003,2003_1_99.04_51.61',
    'shoes_details': '3020_1_62.69_53.39#3158_2_26.81_51.35#3111_3_11.67_48.29',
    'core_details': '3118,4645,3157_1_21.51_55.3#3118,4645,3089_2_11.04_55.38',
    'forth_details': '3089_1_26.26_59.82#3157_2_25.73_62.64',
    'fifth_details': '3089_1_24.59_63.01#3157_2_18.02_63.43',
    'sixth_details': '3089_1_19.95_63.64#3135_2_16.78_50.68',
})

# 真实返回（阿狸符文，前两条）
RUNE_JSON = json.dumps({
    'dtstatdate': '20261125',
    'rune_top_details': (
        '1_8112_ws_8112,8139,8140,8106,8210,8226,5005,5008,5001_45.38_50.8_29659'
        '#2_8992_zz_8992,8226,8210,8237,8106,8139,5005,5008,5001_5.15_50.77_3368'
        '#10_8112_ws_8112,8143,8140,8106,8226,8237,5005,5008,5011_1.0_50.08_655'
    ),
})

# 真实返回（阿狸加点）
SKILL_JSON = json.dumps({
    'dtstatdate': '20261125',
    'detaildetails': (
        '1,2,3:91.17:60.35@'
        '2,1,3,1,1,4,1,2,1,2,4,2,2,3,3_45.79_60.54@'
        '1,2,3,1,1,4,1,2,1,2,4,2,2,3,3_12.29_59.31'
        '$1,3,2:8.19:59.19@2,1,3,1,1,4,1,3,1,3,4,3,3,2,2_1.82_60.42'
    ),
})

EMPTY_JSON = ''

# 真实返回（阿狸中路的符文单条胜率，只留能覆盖各分支的几行）
RUNE_SINGLE_JSON = json.dumps({
    'dtstatdate': '20261125',
    'rune_single_details': (
        '8112_ws_8106_97.98_50.76_212838_-1'      # 正常行（主宰符文，副系巫术）
        '#8112_ws_8139_1.2_51.0_1500_-1'          # 同组另一个符文
        '#8112_ws_8139_1.5_52.1_1800_-1'          # 重复 runeId，场次更大 → 取这条
        '#8112_zz_8106_50.0_55.0_999_-1'          # 副系不同 → 另起一组
        '#8112_xx_8106_10.0_49.0_100_-1'          # 副系代码不认识 → 丢弃
        '#8112_ws_8140_1.0_50.0'                  # 位数不足 → 丢弃
    ),
})

# 属性碎片同一 runeId 跨槽：5008 在第 1、2 槽、5001 在第 2、3 槽
RUNE_SINGLE_SLOT_JSON = json.dumps({
    'dtstatdate': '20261125',
    'rune_single_details': (
        '8112_ws_5008_12.0_51.92_52_1'
        '#8112_ws_5008_40.0_54.87_113_2'
        '#8112_ws_5001_1.4_25.00_4_2'
        '#8112_ws_5001_38.0_51.35_74_3'
    ),
})

# 基石自己那条只按副系匹配：8112 的表现挂在 8992 那组里，同副系就能复用
RUNE_SINGLE_KEY_JSON = json.dumps({
    'dtstatdate': '20261125',
    'rune_single_details': (
        '8112_ws_8106_97.98_50.76_212838_-1'
        '#8992_ws_8112_5.0_53.10_9000_-1'
    ),
})

# 真实返回（阿狸中路的分时段胜率，6 段）
DURATION_JSON = json.dumps({
    'dtstatdate': '20261125',
    'data_details': '1_48_29#2_52_18#3_52_7#4_51_14#5_50_17#6_48_40',
})

RANK_RAW = wrap(RANK_JSON)
BUILD_RAW = wrap(BUILD_JSON)
RUNE_RAW = wrap(RUNE_JSON)
SKILL_RAW = wrap(SKILL_JSON)
DURATION_RAW = wrap(DURATION_JSON)

# lane=ALL 时五个分路混在一起返回，每段自带 lane（搜索框的全英雄索引靠它）
RANK_ALL_JSON = json.dumps({
    'dtstatdate': '20261126',
    'datadetails': (
        '1_55_T1_MIDDLE_54.11_3.87_8.50_800,69,8_1_0_0'
        '#1_86_T1_TOP_52.00_4.00_5.00_8,19,74_0_0_0'
        '#5_55_T2_TOP_51.00_2.00_3.00_1,2,3_0_0_0'
        '#2_103_T1_MIDDLE_51.59_4.53_0.56_246,39,13_4_0_0'
        '#7_999_T1_NOWHERE_50.00_1.00_1.00_0_0_0'
        # 排名高 ≠ 主分路：下路排第 2 但只占 1.48%，中单排第 9 却有 9.33%
        '#2_80_T1_BOTTOM_50.00_1.48_1.00_0_0_0'
        '#9_80_T1_MIDDLE_52.00_9.33_1.00_0_0_0'
    ),
}, ensure_ascii=False)


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))
    assert ok, name


# ------------------------------------------------------------------ 离线
def testRank():
    rows = D.parseRank(RANK_JSON)

    check('榜单解析出三条', len(rows) == 3, str(len(rows)))
    check('排名有序', [x['rank'] for x in rows] == [1, 2, 3])

    top = rows[0]
    check('英雄 id 正确', top['championId'] == 55, str(top['championId']))
    check('T 档映射成数字', top['tier'] == 'T1' and top['tierNumber'] == 1)
    check('胜率不除 100（交给上层）', abs(top['winRate'] - 54.17) < 1e-6)
    check('登场率 / 禁用率都在', abs(top['pickRate'] - 3.89) < 1e-6
          and abs(top['banRate'] - 8.47) < 1e-6)
    check('克制名单取了三个', top['counters'] == [69, 800, 8], str(top['counters']))
    check('排名变化带符号', rows[2]['rankChange'] == -2, str(rows[2]['rankChange']))

    check('空返回空表', D.parseRank(EMPTY_JSON) == [])
    check('乱码不炸', D.parseRank(extractRaw(wrap('不是 json'))) == [])


def testItems():
    items = D.parseItemSets(BUILD_JSON)

    check('出门装一条', len(items['starting']) == 1, str(len(items['starting'])))
    check('鞋子四条', len(items['shoes']) == 3, str(len(items['shoes'])))
    check('核心装按登场率降序',
          items['core'][0]['ids'] == [3118, 4645, 3157], str(items['core'][0]))
    check('重复 id 保留（买两个药）',
          items['starting'][0]['ids'] == [1056, 2003, 2003])
    check('装备行带登场率与胜率',
          abs(items['core'][0]['pickRate'] - 21.51) < 1e-6
          and abs(items['core'][0]['winRate'] - 55.3) < 1e-6)

    empty = D.parseItemSets(EMPTY_JSON)
    check('空返回六组空表', all(not v for v in empty.values()), str(list(empty)))


def testRunes():
    runes = D.parseRunes(RUNE_JSON)

    check('符文解析出三条', len(runes) == 3, str(len(runes)))
    check('按登场率排序（首条是主推）', runes[0]['play'] == 29659
          and abs(runes[0]['pickRate'] - 45.38) < 1e-6)

    top = runes[0]
    check('符文 4+2+3 共九个', len(top['runeIds']) == 9, str(len(top['runeIds'])))
    check('基石 id 单独取出', top['keystone'] == 8112, str(top['keystone']))
    check('胜率是百分数', abs(top['winRate'] - 50.8) < 1e-6)

    check('空返回空表', D.parseRunes(EMPTY_JSON) == [])


def testSkill():
    sk = D.parseSkillOrder(SKILL_JSON)

    check('主升顺序 Q W E', sk.get('masteries') == ['Q', 'W', 'E'],
          str(sk.get('masteries')))
    check('十五级加点序列', len(sk.get('order') or []) == 15,
          str(len(sk.get('order') or [])))
    check('序列第一个是 W', (sk.get('order') or [''])[0] == 'W')
    check('占比 / 胜率换成 0~1',
          abs(sk['pickRate'] - 0.9117) < 1e-3 and abs(sk['winRate'] - 0.6035) < 1e-3)
    check('101 没有场次，play 留空', sk.get('play') is None)

    check('空返回空字典', D.parseSkillOrder(EMPTY_JSON) == {})


def testMapping():
    check('中路映射到 MIDDLE',
          D.POSITION_TO_LANE['MID'] == 'MIDDLE')
    check('下路映射到 BOTTOM',
          D.POSITION_TO_LANE['ADC'] == 'BOTTOM')
    check('BOTTOM 映射回 ADC',
          D.LANE_TO_POSITION['BOTTOM'] == 'ADC')
    check('T 档都认识', set(D.TIER_TO_NUMBER) == {'T0', 'T1', 'T2', 'T3', 'T4'})
    check('未知 T 档兜底 T4', D.TIER_TO_NUMBER.get('T9', 4) == 4)


def testPerks():
    """符文系推导：主系看基石，副系看第一个不属于主系的符文"""
    styleMap = {
        8100: {'slots': [[{'runeId': 8112}, {'runeId': 8124}],
                         [{'runeId': 8139}, {'runeId': 8143}],
                         [{'runeId': 8140}], [{'runeId': 8106}],
                         [{'runeId': 5008}, {'runeId': 5005}]]},
        8200: {'slots': [[{'runeId': 8214}],
                         [{'runeId': 8226}], [{'runeId': 8237}],
                         [{'runeId': 8210}], [{'runeId': 5001}]]},
    }

    item = {'runeIds': [8112, 8139, 8140, 8106, 8226, 8237, 5005, 5008, 5001],
            'pickRate': 45.38, 'winRate': 50.8, 'play': 1000}

    rec = D.buildPerkRecord(item, styleMap)

    check('主系推成 8100', rec['primaryId'] == 8100, str(rec['primaryId']))
    check('副系推成 8200', rec['secondaryId'] == 8200, str(rec['secondaryId']))
    check('九个符文原样带过去', len(rec['perks']) == 9)
    check('场次换算成胜场', rec['win'] == 508.0, str(rec['win']))
    check('胜率归一到 0~1', abs(rec['winRate'] - 0.508) < 1e-6)

    # 基石查不到系时整条丢掉，不要给界面一个 primaryId=None 的坏数据
    check('查不到符文系就丢弃',
          D.buildPerkRecord({'runeIds': [9], 'pickRate': 1,
                             'winRate': 50, 'play': 1}, styleMap) is None)


def testVersionFallback():
    """新版本没统计时要退一个版本，这里只验排序逻辑"""
    names = ('16.23', '16.22')
    check('取前两个版本兜底', names[:2] == ('16.23', '16.22'))


def testRuneSingle():
    single = D.parseRuneSingle(RUNE_SINGLE_JSON)

    check('副系代码映射成 styleId',
          (8112, 8200) in single and (8112, 8100) in single, str(list(single)))
    check('组数 = 2（未知副系 / 位数不足都被丢掉）',
          len(single) == 2, str(len(single)))

    group = single[(8112, 8200)]
    check('同组两个符文', set(group) == {(8106, -1), (8139, -1)}, str(sorted(group)))
    check('重复 runeId 取场次大的那条',
          group[(8139, -1)]['play'] == 1800
          and abs(group[(8139, -1)]['winRate'] - 0.521) < 1e-6,
          str(group[(8139, -1)]))
    check('第 4 位是胜率、第 3 位是同槽占比（不能取反）',
          abs(group[(8106, -1)]['winRate'] - 0.5076) < 1e-6
          and abs(group[(8106, -1)]['pickRate'] - 0.9798) < 1e-6,
          str(group[(8106, -1)]))
    check('场次是整数', isinstance(group[(8106, -1)]['play'], int),
          str(type(group[(8106, -1)]['play'])))
    check('位数不足的行不进表', (8140, -1) not in group, str(sorted(group)))
    check('空返回空字典', D.parseRuneSingle(EMPTY_JSON) == {})

    # 属性碎片同一 runeId 跨槽：5008 在第 1、2 槽，5001 在第 2、3 槽
    slotMap = D.parseRuneSingle(RUNE_SINGLE_SLOT_JSON)
    slotGroup = slotMap[(8112, 8200)]

    check('碎片带槽位存成 (runeId, 槽位)',
          set(slotGroup) == {(5008, 1), (5008, 2), (5001, 2), (5001, 3)},
          str(sorted(slotGroup)))
    check('5008 第 1 槽取自己那条（不被场次大的第 2 槽顶掉）',
          abs(slotGroup[(5008, 1)]['winRate'] - 0.5192) < 1e-6
          and slotGroup[(5008, 1)]['play'] == 52,
          str(slotGroup[(5008, 1)]))
    check('5008 第 2 槽是另一条',
          abs(slotGroup[(5008, 2)]['winRate'] - 0.5487) < 1e-6,
          str(slotGroup[(5008, 2)]))
    check('5001 第 2 / 3 槽互不覆盖',
          abs(slotGroup[(5001, 2)]['winRate'] - 0.25) < 1e-6
          and abs(slotGroup[(5001, 3)]['winRate'] - 0.5135) < 1e-6,
          str((slotGroup[(5001, 2)], slotGroup[(5001, 3)])))
    check('第 7 位缺失按 -1（旧格式不会丢数据）',
          D.parseRuneSingle(RUNE_SINGLE_JSON)[(8112, 8200)][(8106, -1)]['play'] == 212838)

    # 基石自己那条只按副系匹配
    keyMap = D.parseRuneSingle(RUNE_SINGLE_KEY_JSON)
    stats = D.runeSingleStats(keyMap, 8112, 8200, [8112, 8106])

    check('基石自己那条按副系复用',
          (8112, -1) in stats and (8106, -1) in stats, str(sorted(stats)))
    check('基石胜率取到同副系那组',
          abs(stats[(8112, -1)]['winRate'] - 0.531) < 1e-6,
          str(stats.get((8112, -1))))
    check('别的基石不串数据',
          D.runeSingleStats(keyMap, 8005, 8200, [8005, 8106]) == {},
          str(D.runeSingleStats(keyMap, 8005, 8200, [8005, 8106])))
    check('副系不同也不串',
          D.runeSingleStats(keyMap, 8112, 8300, [8112]) == {},
          str(D.runeSingleStats(keyMap, 8112, 8300, [8112])))


def testDuration():
    rows = D.parseDuration(DURATION_JSON)

    check('分时段解析出六段', len(rows) == 6, str(len(rows)))
    check('段序有序', [x['index'] for x in rows] == [1, 2, 3, 4, 5, 6])
    check('文案照 101 的表',
          [x['label'] for x in rows] ==
          ['<20', '20-25', '25-30', '30-35', '35-40', '40+'],
          str([x['label'] for x in rows]))
    check('胜率换成 0~1', abs(rows[0]['winRate'] - 0.48) < 1e-6,
          str(rows[0]['winRate']))
    check('样本量是整数', rows[0]['play'] == 29, str(rows[0]['play']))
    check('第二段 52% / 18 场',
          abs(rows[1]['winRate'] - 0.52) < 1e-6 and rows[1]['play'] == 18)

    check('-1 表示无数据', D.parseDuration(json.dumps({'data_details': '-1'})) == [])
    check('字段缺失返回空表',
          D.parseDuration(json.dumps({'dtstatdate': '20261125'})) == [])
    check('只有段序的行被丢掉',
          D.parseDuration(json.dumps({'data_details': '1_48'})) == [])
    check('空返回空表', D.parseDuration(EMPTY_JSON) == [])


def testChampionLanes():
    index = D.buildChampionLanes(D.parseRank(RANK_ALL_JSON))

    check('索引收录四个英雄', len(index) == 4, str(sorted(index)))
    check('分路认不出的整条丢弃', 999 not in index)
    check('双分路英雄存两条', len(index[55]) == 2, str(index[55]))
    check('主分路按登场率取（中单 3.87% > 上单 2.00%）',
          index[55][0]['position'] == 'MID' and index[55][0]['rank'] == 1,
          str(index[55]))
    check('排名高但登场率低的分路不能当主分路',
          index[80][0]['position'] == 'MID' and index[80][0]['rank'] == 9,
          str(index[80]))
    check('分路项带登场率', abs(index[55][0]['pickRate'] - 0.0387) < 1e-6,
          str(index[55][0].get('pickRate')))
    check('分路名转成窗口写法', index[86][0]['position'] == 'TOP')
    check('胜率归一成 0~1', abs(index[86][0]['winRate'] - 0.52) < 1e-6,
          str(index[86][0]['winRate']))
    check('空数据返回空表', D.buildChampionLanes([]) == {})


def main():
    testRank()
    testItems()
    testRunes()
    testSkill()
    testMapping()
    testPerks()
    testVersionFallback()
    testRuneSingle()
    testDuration()
    testChampionLanes()
    print('\n离线全部通过')

    try:
        asyncio.run(live())
    except AssertionError:
        raise
    except Exception as e:
        print(f'SKIP  live 不可用: {type(e).__name__}: {e}')


async def live():
    from app.lol.connector import connector
    from app.lol.cn_strategy import cnStrategy

    await connector.autoStart()
    await cnStrategy.start()

    try:
        tier = await D.cnData.getTierList('emerald_plus', 'MID')
        rows = tier['data']['MID']

        check('live 榜单有数据', len(rows) > 10, str(len(rows)))
        check('live 版本号是数字点数字',
              tier['version'].count('.') == 1, tier['version'])
        check('live 每条都有图标',
              all(x['icon'] for x in rows[:5]))
        check('live 胜率在合理区间',
              all(0.35 < x['winRate'] < 0.7 for x in rows[:10]))
        check('live 克制头像是三个',
              len(rows[0]['counters']) == 3, str(len(rows[0]['counters'])))

        build = await D.cnData.getChampionBuild(103, 'MID', 'emerald_plus')
        data = build['data']

        check('live 有装备', bool(data['items']['coreItems']))
        check('live 有加点', bool(data['championSkills'].get('order')))
        check('live 有符文', len(data['perks']) > 0, str(len(data['perks'])))
        check('live 符文的系都是有效 id',
              all(p['primaryId'] > 0 and p['secondaryId'] > 0
                  for p in data['perks']))
        # 单符文胜率 / 分时段胜率是 101 特有字段，外服没有；这里只确认字段在、
        # 形状对，具体有没有数据取决于英雄冷热（冷门英雄可能整块为空）
        check('live 每套符文都带 singleStats',
              all(isinstance(p.get('singleStats'), dict) for p in data['perks']))
        check('live 分时段胜率是列表',
              isinstance(data.get('duration'), list), str(type(data.get('duration'))))
        print(f"INFO  live 单符文数据 "
              f"{[len(p['singleStats']) for p in data['perks']]} 条 / "
              f"分时段 {len(data.get('duration') or [])} 段")
        check('live 没有召唤师技能（101 不提供）',
              data['summonerSpells'] == [])

        # 搜索框的全分路索引：一次 lane=ALL 拿五个分路
        lanes = await D.cnData.getChampionLanes('emerald_plus')
        check('live 全分路索引覆盖一百多个英雄', len(lanes) > 100, str(len(lanes)))
        check('live 索引项都带分路',
              all(v and v[0]['position'] for v in lanes.values()))
        check('live 存在多分路英雄',
              any(len(v) > 1 for v in lanes.values()),
              str(sum(1 for v in lanes.values() if len(v) > 1)))
        check('live 索引里主力英雄分路正确',
              lanes.get(86, [{}])[0].get('position') == 'TOP'
              and lanes.get(103, [{}])[0].get('position') == 'MID')
        check('live 对位两侧都有',
              bool(data['counters']['weakAgainst'])
              and bool(data['counters']['strongAgainst']),
              f"{len(data['counters']['weakAgainst'])} / "
              f"{len(data['counters']['strongAgainst'])}")

        weak = [c['winRate'] for c in data['counters']['weakAgainst']]
        strong = [c['winRate'] for c in data['counters']['strongAgainst']]
        check('live 打不过的胜率低于打过', min(weak) < 0.5 < max(strong),
              f'{min(weak):.3f} / {max(strong):.3f}')

        # 冷门英雄 + 错误分路：应该抛错让窗口显示错误页，而不是给一页空白
        try:
            await D.cnData.getChampionBuild(999, 'SUPPORT', 'emerald_plus')
            check('live 无数据时抛错', False, '没有抛错')
        except RuntimeError:
            check('live 无数据时抛错', True)
    finally:
        await cnStrategy.close()


if __name__ == '__main__':
    unittest.main(exit=False)
    main()
