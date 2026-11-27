# coding:utf-8
"""BP 对位浮窗「5 张独立卡片」回归

以前是一个面板里 5 行，每行只有 1 条「怕」+ 1 条「压」，第 2~5 名看不见。
现在一个敌人一张卡，每张卡都能铺满 5+5。

覆盖：
  - 一张卡一个人：roster 第 i 个占第 i 张，空位是 pending
  - 每张卡 5+5，超出的裁掉、不足的照常画
  - 按 championId 对号入座，加人不冲掉已有数据
  - 每张卡独立 loading / empty，一个人挂了不连累别人
  - 点卡片右上角的 ⟳ 只刷这一个人
  - 卡片之间的间隙不进鼠标事件（mask 打掉了，点下去穿透到客户端）
  - 折叠成 5 条时宽度和 mask 跟着变
  - 面板尺寸全由常量算出来（改行高这里立刻报）

运行: python _dev_tests/test_bp_cards.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme  # noqa: E402

setTheme(Theme.DARK, save=False)

from app.common import skin  # noqa: E402

skin.install()
skin.applyGlobal(app)

from app.lol.bp_counters import bpCounters  # noqa: E402
from app.view import bp_overlay as O  # noqa: E402
from app.view import bp_overlay_controller as C  # noqa: E402
from app.view.bp_overlay import BpOverlay  # noqa: E402


class _Signal:
    def connect(self, *args, **kwargs):
        pass

    def disconnect(self, *args, **kwargs):
        pass


class _Item:
    def __init__(self, value=None):
        self.value = value
        self.valueChanged = _Signal()


class FakeCfg:
    """只认浮窗用得到的那几项，不读写用户真实的 config.json

    属性必须缓存 —— 每次 __getattr__ 新建一个的话，`get(cfg.x)` 拿到的对象
    和连接信号的那个不是同一个，值就对不上了。
    """

    def __init__(self, **values):
        self._values = values

    def __getattr__(self, name):
        item = _Item(self._values.get(name))
        object.__setattr__(self, name, item)
        return item

    def get(self, item):
        return item.value

    def set(self, item, value):
        item.value = value


# 浮窗构造时会读 collapsed / offset / tier。用假配置顶掉，
# 否则每跑一次测试都在改用户真实的位置和折叠状态。
O.cfg = FakeCfg(bpOverlayCollapsed=False, bpOverlayOffset='',
                bpOverlayOffsetSchema=O.OFFSET_SCHEMA,
                opggTier='emerald_plus', opggRegion='cn',
                bpOverlayEnabled=True)

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


ROSTER = [
    {'id': 64, 'name': '千珏', 'lane': 'JUNGLE', 'mine': False},
    {'id': 103, 'name': '九尾妖狐', 'lane': 'MIDDLE', 'mine': True},
    {'id': 202, 'name': '复仇之矛', 'lane': 'BOTTOM', 'mine': False},
    {'id': 412, 'name': '魂锁典狱长', 'lane': 'UTILITY', 'mine': False},
    {'id': 86, 'name': '圣锤之毅', 'lane': 'TOP', 'mine': False},
]

WEAK5 = [{'championId': 34, 'winRate': 0.372 + i / 100} for i in range(5)]
STRONG5 = [{'championId': 7, 'winRate': 0.551 + i / 100} for i in range(5)]


def payload(weak=None, strong=None):
    return {'weak': weak if weak is not None else list(WEAK5),
            'strong': strong if strong is not None else list(STRONG5),
            'version': '16.23', 'lane': '', 'at': 1700000000,
            'source': '国服 101'}


# ---------------------------------------------------------------- 布局常量
def testLayout():
    body = O.CARD_HEAD_H + O.SEC_H + O.LINE_H * O.ROWS_PER_SIDE

    check('卡片高度放得下标题 + 表头 + 5 行', O.PANEL_H >= body,
          f'{O.PANEL_H} vs {body}')
    check('底部余量克制（不超过 12px）', 0 <= O.PANEL_H - body <= 12,
          str(O.PANEL_H - body))
    check('每侧 5 条', O.ROWS_PER_SIDE == 5, str(O.ROWS_PER_SIDE))
    check('5 张卡横排总宽 = 5 卡 + 4 间隙',
          O.BASE_W == O.CARD_W * 5 + O.CARD_GAP * 4, str(O.BASE_W))
    check('折叠态总宽 = 5 条 + 4 间隙',
          O.BAR_TOTAL_W == O.BAR_W * 5 + O.CARD_GAP * 4, str(O.BAR_TOTAL_W))
    check('行高放得下 18px 头像 + 文字', O.LINE_H >= 19, str(O.LINE_H))
    check('一张卡放得下两列（各 140 宽）', O.CARD_W >= 2 * 140 + 24,
          str(O.CARD_W))
    check('5 张卡放得进 1600 基准客户端', O.BASE_W <= 1600, str(O.BASE_W))


# ---------------------------------------------------------------- 卡格模型
def testRoster():
    w = BpOverlay()
    w.setRoster(ROSTER[:2])

    check('锁了 2 个也摆出 5 张卡', len(w.enemies) == 5, str(len(w.enemies)))
    check('前两格是已锁的', [e['id'] for e in w.enemies[:2]] == [64, 103],
          str([e['id'] for e in w.enemies[:2]]))
    check('后三格是 pending',
          all(e['state'] == 'pending' and not e['id'] for e in w.enemies[2:]),
          str([e['state'] for e in w.enemies[2:]]))
    check('新锁的人先是 loading',
          all(e['state'] == 'loading' for e in w.enemies[:2]),
          str([e['state'] for e in w.enemies[:2]]))
    check('名字带过来', w.enemies[0]['name'] == '千珏', w.enemies[0]['name'])
    check('你的对位标记', w.enemies[1]['mine'] is True)

    # 已经有数据了，再锁一个不应该把前面的清掉
    w.setEnemies([{'id': 64, 'weak': WEAK5[:2], 'strong': []}])
    w.setRoster(ROSTER[:3])
    check('加人不会冲掉已有数据',
          len(w.enemies[0]['weak']) == 2 and w.enemies[0]['state'] == 'ready',
          str(w.enemies[0]['state']))
    check('新加的那格是 loading', w.enemies[2]['state'] == 'loading',
          w.enemies[2]['state'])
    check('顺序按 roster 走', [e['id'] for e in w.enemies[:3]] == [64, 103, 202],
          str([e['id'] for e in w.enemies[:3]]))

    w.deleteLater()


def testFill():
    w = BpOverlay()
    w.setRoster(ROSTER)
    w.setEnemies([
        {'id': 103, 'weak': WEAK5, 'strong': STRONG5},
        {'id': 64, 'strong': STRONG5[:1]},
        {'id': 202, 'weak': [], 'strong': []},
    ], '16.23', '国服 101')

    byId = {e['id']: e for e in w.enemies}
    check('每张卡都有 5 条', len(byId[103]['weak']) == 5
          and len(byId[103]['strong']) == 5,
          f"{len(byId[103]['weak'])}/{len(byId[103]['strong'])}")
    check('按 id 对号入座，不看顺序',
          byId[103]['weak'][0]['championId'] == 34, str(byId[103]['weak'][:1]))
    check('有数据 = ready', byId[64]['state'] == 'ready', byId[64]['state'])
    check('两条都空 = empty', byId[202]['state'] == 'empty', byId[202]['state'])
    check('没给数据的保持 loading', byId[412]['state'] == 'loading',
          byId[412]['state'])
    check('版本号带过来', w.version == '16.23', w.version)
    check('数据源带过来', w.source == '国服 101', w.source)

    # 多给几条也不能撑破卡片
    w.setEnemies([{'id': 103, 'weak': WEAK5 * 2, 'strong': STRONG5 * 2}])
    check('超出 5 条被裁掉', len(byId[103]['weak']) == 5
          and len(byId[103]['strong']) == 5,
          f"{len(byId[103]['weak'])}/{len(byId[103]['strong'])}")

    w.deleteLater()


def testLoadingState():
    w = BpOverlay()
    w.setRoster(ROSTER)

    w.markEnemiesLoading([202])
    byId = {e['id']: e for e in w.enemies}
    check('只标记指定的那个', byId[202]['state'] == 'loading'
          and byId[64]['state'] == 'loading', '两个都是 loading')

    w.setEnemies([{'id': 64, 'weak': WEAK5, 'strong': []}])
    byId = {e['id']: e for e in w.enemies}
    check('填完一个不影响另一个的 loading',
          byId[64]['state'] == 'ready' and byId[412]['state'] == 'loading',
          f"{byId[64]['state']}/{byId[412]['state']}")

    w.deleteLater()


# ---------------------------------------------------------------- 点击区域
def testHitTest():
    w = BpOverlay()
    w.k = 1.0
    w.setRoster(ROSTER)
    w.setEnemies([])

    hit = w._BpOverlay__hitTest

    check('第 1 张卡命中 card:0', hit(QPoint(60, 10)) == 'card:0',
          hit(QPoint(60, 10)))
    check('第 5 张卡命中 card:4',
          hit(QPoint(4 * (O.CARD_W + O.CARD_GAP) + 60, 10)) == 'card:4',
          hit(QPoint(4 * (O.CARD_W + O.CARD_GAP) + 60, 10)))
    check('卡片之间的间隙不算任何卡',
          hit(QPoint(O.CARD_W + 2, 60)) == '',
          hit(QPoint(O.CARD_W + 2, 60)))
    check('刷新键在标题栏最右',
          hit(QPoint(O.CARD_W - 12, 10)) == 'refresh:0',
          hit(QPoint(O.CARD_W - 12, 10)))
    check('卡片下方空白不算卡片',
          hit(QPoint(60, O.PANEL_H + 5)) == '',
          hit(QPoint(60, O.PANEL_H + 5)))

    # 空卡片的右上角不该有刷新键
    w.setRoster(ROSTER[:1])
    check('空卡片右上角不响应刷新',
          hit(QPoint(3 * (O.CARD_W + O.CARD_GAP) + O.CARD_W - 12, 10))
          == 'card:3',
          hit(QPoint(3 * (O.CARD_W + O.CARD_GAP) + O.CARD_W - 12, 10)))

    w.deleteLater()


def testMask():
    w = BpOverlay()
    w.k = 1.0
    w.resizeToFit()

    rects = w.mask().rects()
    check('mask 是 5 块', len(rects) == 5, str(len(rects)))
    check('每块宽度 = 卡片宽', all(r.width() == O.CARD_W for r in rects),
          str([r.width() for r in rects][:3]))
    check('第二块紧跟第一块 + 间隙',
          rects[1].x() == O.CARD_W + O.CARD_GAP, str(rects[1].x()))
    check('窗口总宽 = 5 卡 + 4 间隙', w.width() == O.BASE_W, str(w.width()))

    w.setCollapsed(True, persist=False)
    rects = w.mask().rects()
    check('折叠后还是 5 块', len(rects) == 5, str(len(rects)))
    check('折叠后每块宽 = 条宽', all(r.width() == O.BAR_W for r in rects),
          str([r.width() for r in rects][:3]))
    check('折叠后高度 = 条高', w.height() == O.BAR_H, str(w.height()))

    w.deleteLater()


def testOffsetMigration():
    """2.2.43 的单面板只有 360 宽，它的位置偏移套到 1314 宽的 5 张卡上会甩飞"""
    cfg = O.cfg

    cfg.set(cfg.bpOverlayOffsetSchema, O.OFFSET_SCHEMA)
    cfg.set(cfg.bpOverlayOffset, '100.0,-50.0')

    w = BpOverlay()
    check('新版偏移照常读出来',
          w.hasCustomOffset() and w._BpOverlay__offset == (100.0, -50.0),
          str(w._BpOverlay__offset))
    w.deleteLater()

    # 升级用户的样子：版次还是 0，位置是给 360 宽面板存的
    cfg.set(cfg.bpOverlayOffsetSchema, 0)
    cfg.set(cfg.bpOverlayOffset, '1187.0,-237.0')

    w2 = BpOverlay()
    check('旧版偏移被丢弃', not w2.hasCustomOffset(),
          str(w2._BpOverlay__offset))
    check('迁移后配置清空', cfg.get(cfg.bpOverlayOffset) == '',
          repr(cfg.get(cfg.bpOverlayOffset)))
    check('迁移后版次写到位',
          cfg.get(cfg.bpOverlayOffsetSchema) == O.OFFSET_SCHEMA,
          str(cfg.get(cfg.bpOverlayOffsetSchema)))
    w2.deleteLater()


# ---------------------------------------------------------------- 控制器
def testLoadTeam():
    calls = []

    async def fakePair(championId, position, tier, limit=5):
        calls.append((championId, position))
        return payload()

    real = bpCounters.getPair
    bpCounters.getPair = fakePair

    try:
        ctrl = C.BpOverlayController()
        ctrl.lane = 'MIDDLE'
        ctrl.overlay.setRoster(ROSTER)

        asyncio.run(ctrl._BpOverlayController__loadTeam(ROSTER))

        check('5 个人都查了', len(calls) == 5, str(len(calls)))
        check('每个人用自己的分路',
              dict(calls) == {64: 'JUNGLE', 103: 'MIDDLE', 202: 'BOTTOM',
                              412: 'UTILITY', 86: 'TOP'},
              str(sorted(calls)))
        check('不是拿我的分路查所有人',
              all(lane != 'MIDDLE' for cid, lane in calls if cid != 103),
              str(calls))
        check('5 张卡都填上了',
              sum(1 for e in ctrl.overlay.enemies if e['state'] == 'ready') == 5,
              str([e['state'] for e in ctrl.overlay.enemies]))
        check('每张卡 5+5 条数据',
              all(len(e['weak']) == 5 and len(e['strong']) == 5
                  for e in ctrl.overlay.enemies),
              str([(len(e['weak']), len(e['strong'])) for e in
                   ctrl.overlay.enemies]))
        check('记下已加载的阵容', ctrl.loadedIds == (64, 103, 202, 412, 86),
              str(ctrl.loadedIds))

        ctrl.close()
    finally:
        bpCounters.getPair = real


def testPartialFailure():
    async def fakePair(championId, position, tier, limit=5):
        if championId == 202:
            raise RuntimeError('101 挂了')

        return payload()

    real = bpCounters.getPair
    bpCounters.getPair = fakePair

    try:
        ctrl = C.BpOverlayController()
        ctrl.lane = 'MIDDLE'
        ctrl.overlay.setRoster(ROSTER)

        asyncio.run(ctrl._BpOverlayController__loadTeam(ROSTER))

        states = {e['id']: e['state'] for e in ctrl.overlay.enemies}
        check('一个人挂了不连累其他人',
              states[64] == 'ready' and states[412] == 'ready', str(states))
        check('挂掉那张显示暂无，不是一直转圈',
              states[202] == 'empty', states[202])

        ctrl.close()
    finally:
        bpCounters.getPair = real


def testRefreshOne():
    """点某张卡右上角的 ⟳：只重拉这一个人"""
    calls = []

    async def fakePair(championId, position, tier, limit=5):
        calls.append(championId)
        return payload(weak=WEAK5[:1], strong=[])

    real = bpCounters.getPair
    bpCounters.getPair = fakePair

    try:
        ctrl = C.BpOverlayController()
        ctrl.lane = 'MIDDLE'
        ctrl.overlay.setRoster(ROSTER)

        asyncio.run(ctrl._BpOverlayController__onRefreshOne(202))

        check('只查了被点的那一个', calls == [202], str(calls))
        byId = {e['id']: e for e in ctrl.overlay.enemies}
        check('被点的那张更新了', byId[202]['state'] == 'ready',
              byId[202]['state'])
        check('别人没被顺带重拉', byId[64]['state'] == 'loading',
              byId[64]['state'])

        calls.clear()
        asyncio.run(ctrl._BpOverlayController__onRefreshOne(999))
        check('点一个不存在的 id 不请求', calls == [], str(calls))

        ctrl.close()
    finally:
        bpCounters.getPair = real


def testNoRepeatRequest():
    """选人阶段每秒都有事件，阵容没变就不该重复打 5 个请求"""
    calls = []

    async def fakePair(championId, position, tier, limit=5):
        calls.append(championId)
        return payload()

    realPair = bpCounters.getPair
    realCfg = C.cfg
    bpCounters.getPair = fakePair
    C.cfg = FakeCfg(bpOverlayEnabled=True, opggTier='diamond_plus',
                    opggRegion='cn')

    def session(gameId, team):
        return {'data': {'gameId': gameId, 'localPlayerCellId': 2,
                         'myTeam': [{'cellId': 2, 'selectedPosition': 'MIDDLE'}],
                         'theirTeam': team}}

    try:
        ctrl = C.BpOverlayController()
        team = [
            {'championId': 64, 'selectedPosition': 'JUNGLE'},
            {'championId': 103, 'selectedPosition': 'MIDDLE'},
            {'championId': 202, 'selectedPosition': 'BOTTOM'},
            {'championId': 412, 'selectedPosition': 'UTILITY'},
            {'championId': 86, 'selectedPosition': 'TOP'},
        ]

        asyncio.run(ctrl._BpOverlayController__onSession(session(1, team)))
        first = len(calls)

        check('第一次进来查了 5 个', first == 5, str(first))
        check('5 张卡都是 ready',
              all(e['state'] == 'ready' for e in ctrl.overlay.enemies),
              str([e['state'] for e in ctrl.overlay.enemies]))

        asyncio.run(ctrl._BpOverlayController__onSession(session(1, team)))
        asyncio.run(ctrl._BpOverlayController__onSession(session(1, team)))
        check('阵容没变不重复请求', len(calls) == first, f'{len(calls)} vs {first}')

        team2 = team[:4] + [{'championId': 7, 'selectedPosition': 'TOP'}]
        asyncio.run(ctrl._BpOverlayController__onSession(session(1, team2)))
        check('阵容变了才重拉', len(calls) == first + 5,
              f'{len(calls)} vs {first + 5}')
        check('换掉的人进来了',
              [e['id'] for e in ctrl.overlay.enemies] == [64, 103, 202, 412, 7],
              str([e['id'] for e in ctrl.overlay.enemies]))

        ctrl.close()
    finally:
        bpCounters.getPair = realPair
        C.cfg = realCfg


def main():
    testLayout()
    testRoster()
    testFill()
    testLoadingState()
    testHitTest()
    testMask()
    testOffsetMigration()
    testLoadTeam()
    testPartialFailure()
    testRefreshOne()
    testNoRepeatRequest()

    print()

    if _failures:
        print(f'失败 {len(_failures)} 项: {_failures}')
        return 1

    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
