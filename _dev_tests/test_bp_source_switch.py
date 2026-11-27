# coding:utf-8
"""BP 浮窗跟随「OP.GG 数据源（国服 101 / 韩服 / 全部地区）」切换

原来的两个缺口：
  1. `cfg.set(cfg.opggRegion, ...)` 只写在 `__updateTierInterface` 里。
     停在英雄详情页切地区时走的是 `__updateBuildInterface`，它只读不写 ——
     配置没变，浮窗纹丝不动。
  2. 控制器里 `__onRegionChanged` 带 `not self.loading` 门禁，而 `loading`
     在「这一趟被新的一趟取代」时不会复位。撞上一次就永久卡 True，
     之后切数据源全被吞掉，表现是「切了没反应」。

覆盖：
  - 切地区立刻落配置（不依赖后续页面加载成功）
  - 浮窗收到 region 变化后重拉当前阵容，且 force 绕过缓存
  - 上一批还在飞的时候切源，照样发起新的一批（这条改动前必挂）
  - 数据源路由：国服走 101、韩服/全部地区走 OP.GG
  - 缓存键带 region：切回国服不会误命中韩服那份
  - `opggRegion` 不再带 restart=True（否则每次切都弹「重启后生效」）

运行: python _dev_tests/test_bp_source_switch.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme  # noqa: E402

setTheme(Theme.DARK, save=False)

from app.common import skin  # noqa: E402

skin.install()
skin.applyGlobal(app)

from app.lol.bp_counters import bpCounters  # noqa: E402
from app.lol import bp_counters as BP  # noqa: E402
from app.view import bp_overlay as O  # noqa: E402
from app.view import bp_overlay_controller as C  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, fn, *args, **kwargs):
        self.slots.append(fn)

    def disconnect(self, *args, **kwargs):
        pass


class _Item:
    def __init__(self, value=None):
        self.value = value
        self.valueChanged = _Signal()


class FakeCfg:
    """只认浮窗用得到的那几项，绝不读写用户真实 config.json"""

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


ROSTER = [
    {'id': 64, 'name': '千珏', 'lane': 'JUNGLE', 'mine': False},
    {'id': 103, 'name': '九尾妖狐', 'lane': 'MIDDLE', 'mine': True},
    {'id': 202, 'name': '复仇之矛', 'lane': 'BOTTOM', 'mine': False},
    {'id': 412, 'name': '魂锁典狱长', 'lane': 'UTILITY', 'mine': False},
    {'id': 86, 'name': '圣锤之毅', 'lane': 'TOP', 'mine': False},
]


def payload(source='国服 101'):
    return {'weak': [{'championId': 34, 'winRate': 0.37}],
            'strong': [{'championId': 7, 'winRate': 0.55}],
            'version': '16.23', 'lane': '', 'at': 1700000000,
            'source': source}


# ------------------------------------------------------- 切地区时立刻落配置
def testRegionWritesConfig():
    """地区下拉是数据源开关，改了就得当场写进配置

    以前只在榜单页的加载函数里写，停在英雄详情页切地区就不生效。
    """
    path = os.path.join(ROOT, 'app', 'view', 'opgg_window.py')
    src = open(path, encoding='utf-8').read()

    start = src.find('def __onRegionChanged')
    end = src.find('def __initLayout')
    seg = src[start:end] if start >= 0 and end > start else ''

    check('__onRegionChanged 存在', bool(seg), f'{start}..{end}')
    check('切地区当场写 opggRegion',
          'cfg.set(cfg.opggRegion' in seg, seg[:120].replace('\n', ' '))

    # 必须写在 __applySourceState 之前：先落配置（浮窗立刻动），再刷下拉框状态
    writeAt = seg.find('cfg.set(cfg.opggRegion')
    stateAt = seg.find('__applySourceState()')
    check('先落配置再刷控件状态',
          writeAt >= 0 and stateAt >= 0 and writeAt < stateAt,
          f'write={writeAt} state={stateAt}')

    # 详情页的加载函数不该再重复写一份（写了也无害，但两处容易走岔）
    buildStart = src.find('async def __updateBuildInterface')
    buildEnd = src.find('async def ', buildStart + 10)
    buildSeg = src[buildStart:buildEnd] if buildStart >= 0 else ''
    check('详情页加载函数不再重复写配置（唯一入口在下拉回调里）',
          'cfg.set(cfg.opggRegion' not in buildSeg,
          str('cfg.set' in buildSeg))


def testRegionNotRestart():
    """切数据源是即时生效的，不该再标「重启后生效」"""
    from app.common.config import cfg

    check('opggRegion 不再 restart',
          not getattr(cfg.opggRegion, 'restart', False),
          str(getattr(cfg.opggRegion, 'restart', 'n/a')))
    check('候选值仍是三档',
          cfg.opggRegion.validator.options == ['cn', 'kr', 'global']
          if hasattr(cfg.opggRegion.validator, 'options') else True,
          str(getattr(cfg.opggRegion.validator, 'options', 'n/a')))


# ------------------------------------------------------- 浮窗收到变化就重拉
def testSwitchTriggersReload():
    """切 region 后，当前阵容必须重拉，而且 force 绕过缓存"""
    realCfg = C.cfg
    C.cfg = FakeCfg(bpOverlayCollapsed=False, bpOverlayOffset='',
                    bpOverlayOffsetSchema=O.OFFSET_SCHEMA,
                    opggTier='emerald_plus', opggRegion='cn',
                    bpOverlayEnabled=True)

    ctrl = C.BpOverlayController()
    ctrl.lane = 'MIDDLE'
    ctrl.overlay.setRoster(ROSTER)

    calls = []
    realLoad = ctrl._BpOverlayController__loadTeam

    async def fakeLoad(roster, force=False):
        calls.append((tuple(s['id'] for s in roster), force))

    ctrl._BpOverlayController__loadTeam = fakeLoad

    async def scenario():
        ctrl._BpOverlayController__onRegionChanged('kr')
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    try:
        asyncio.run(scenario())

        check('切数据源会重拉', len(calls) == 1, str(calls))
        check('force=True 绕过缓存',
              bool(calls) and calls[0][1] is True, str(calls))
        check('拉的是当前这 5 个人',
              bool(calls) and calls[0][0] == (64, 103, 202, 412, 86),
              str(calls))
    finally:
        ctrl._BpOverlayController__loadTeam = realLoad
        ctrl.close()
        C.cfg = realCfg


def testSwitchWhileInFlight():
    """上一批还没回来时切源，照样要发起新的一批

    改动前这里有 `not self.loading` 门禁，而 loading 在「被新的一趟取代」
    时不会复位 —— 撞上一次就永久卡 True，之后切源全被吞掉。
    """
    realCfg = C.cfg
    C.cfg = FakeCfg(bpOverlayCollapsed=False, bpOverlayOffset='',
                    bpOverlayOffsetSchema=O.OFFSET_SCHEMA,
                    opggTier='emerald_plus', opggRegion='cn',
                    bpOverlayEnabled=True)

    ctrl = C.BpOverlayController()
    ctrl.lane = 'MIDDLE'
    ctrl.overlay.setRoster(ROSTER)

    calls = []
    realLoad = ctrl._BpOverlayController__loadTeam

    async def fakeLoad(roster, force=False):
        calls.append(tuple(s['id'] for s in roster))

    ctrl._BpOverlayController__loadTeam = fakeLoad

    try:
        # 改动前这个属性存在，把它置成 True 就等价于「上一批还在飞」；
        # 改动后它已经被趟号取代，hasattr 为假，跳过即可（这本身就是要的结果）
        hadFlag = hasattr(ctrl, 'loading')

        if hadFlag:
            ctrl.loading = True

        async def scenario():
            ctrl._BpOverlayController__onRegionChanged('kr')
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(scenario())

        check('上一批未返回时切源仍然重拉',
              len(calls) >= 1, f'calls={len(calls)} 旧门禁={hadFlag}')
        check('不再依赖 loading 布尔量',
              not hasattr(ctrl, 'loading'), f'hasattr={hasattr(ctrl, "loading")}')
    finally:
        ctrl._BpOverlayController__loadTeam = realLoad
        ctrl.close()
        C.cfg = realCfg


def testNoRosterNoRequest():
    """浮窗还是「等待敌方锁定」时切源，不该凭空打 5 个请求"""
    realCfg = C.cfg
    C.cfg = FakeCfg(bpOverlayCollapsed=False, bpOverlayOffset='',
                    bpOverlayOffsetSchema=O.OFFSET_SCHEMA,
                    opggTier='emerald_plus', opggRegion='cn',
                    bpOverlayEnabled=True)

    ctrl = C.BpOverlayController()
    calls = []
    realLoad = ctrl._BpOverlayController__loadTeam

    async def fakeLoad(roster, force=False):
        calls.append(roster)

    ctrl._BpOverlayController__loadTeam = fakeLoad

    try:
        async def scenario():
            ctrl._BpOverlayController__onRegionChanged('kr')
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(scenario())
        check('没有敌方阵容时不发请求', calls == [], str(calls))
    finally:
        ctrl._BpOverlayController__loadTeam = realLoad
        ctrl.close()
        C.cfg = realCfg


# ------------------------------------------------------- 数据源真的换了
def testSourceRouting():
    """国服走 101、韩服/全部地区走 OP.GG；两者不能串"""
    seen = []
    realCfg = BP.cfg

    async def fakeCn(championId, lane, tier, limit=5):
        seen.append('101')
        return payload('国服 101')

    async def fakeOpgg(region, championId, lane, tier, limit=5):
        seen.append(region)
        return payload(f'OP.GG 外服')

    realCn = bpCounters._BpCounters__fetch
    realOpgg = bpCounters._BpCounters__fetchOpgg
    bpCounters._BpCounters__fetch = fakeCn
    bpCounters._BpCounters__fetchOpgg = fakeOpgg

    try:
        for region, expect in (('cn', '101'), ('kr', 'kr'), ('global', 'global')):
            seen.clear()
            BP.cfg = FakeCfg(opggRegion=region)
            bpCounters.invalidate()

            got = asyncio.run(bpCounters.getPair(103, 'MIDDLE', 'emerald_plus'))

            check(f'数据源 {region} → {expect}',
                  seen == [expect], str(seen))
            check(f'数据源 {region} 的 source 跟着变',
                  got['source'] == ('国服 101' if region == 'cn' else 'OP.GG 外服'),
                  got['source'])
    finally:
        bpCounters._BpCounters__fetch = realCn
        bpCounters._BpCounters__fetchOpgg = realOpgg
        BP.cfg = realCfg
        bpCounters.invalidate()


def testCacheKeyHasRegion():
    """缓存键带 region：切到韩服再切回国服，不能拿韩服那份数据顶上"""
    realCfg = BP.cfg
    hits = []

    async def fakeCn(championId, lane, tier, limit=5):
        hits.append('101')
        return payload('国服 101')

    async def fakeOpgg(region, championId, lane, tier, limit=5):
        hits.append(region)
        return payload('OP.GG 外服')

    realCn = bpCounters._BpCounters__fetch
    realOpgg = bpCounters._BpCounters__fetchOpgg
    bpCounters._BpCounters__fetch = fakeCn
    bpCounters._BpCounters__fetchOpgg = fakeOpgg

    try:
        bpCounters.invalidate()

        BP.cfg = FakeCfg(opggRegion='kr')
        kr = asyncio.run(bpCounters.getPair(103, 'MIDDLE', 'emerald_plus'))

        BP.cfg = FakeCfg(opggRegion='cn')
        cn = asyncio.run(bpCounters.getPair(103, 'MIDDLE', 'emerald_plus'))

        check('两个源各拉了一次', hits == ['kr', '101'], str(hits))
        check('拿回来的是各自源的数据',
              kr['source'] == 'OP.GG 外服' and cn['source'] == '国服 101',
              f"{kr['source']} / {cn['source']}")

        # 再切回韩服：缓存还在（TTL 内），不该再打一次
        BP.cfg = FakeCfg(opggRegion='kr')
        hits.clear()
        again = asyncio.run(bpCounters.getPair(103, 'MIDDLE', 'emerald_plus'))
        check('TTL 内切回原源走缓存', hits == [], str(hits))
        check('缓存回来的还是韩服那份', again['source'] == 'OP.GG 外服',
              again['source'])
    finally:
        bpCounters._BpCounters__fetch = realCn
        bpCounters._BpCounters__fetchOpgg = realOpgg
        BP.cfg = realCfg
        bpCounters.invalidate()


def testOverlayShowsSource():
    """数据源名要带到界面上，右键菜单能看出当前看的是哪套数据"""
    realCfg = C.cfg
    C.cfg = FakeCfg(bpOverlayCollapsed=False, bpOverlayOffset='',
                    bpOverlayOffsetSchema=O.OFFSET_SCHEMA,
                    opggTier='emerald_plus', opggRegion='cn',
                    bpOverlayEnabled=True)

    overlay = O.BpOverlay()

    try:
        overlay.setRoster(ROSTER)
        overlay.setEnemies(
            [{'id': 103, 'weak': payload('OP.GG 外服')['weak'],
              'strong': payload('OP.GG 外服')['strong']}],
            '16.23', 'OP.GG 外服')

        check('数据源记在浮窗上', overlay.source == 'OP.GG 外服', overlay.source)

        src = open(os.path.join(ROOT, 'app', 'view', 'bp_overlay.py'),
                   encoding='utf-8').read()
        check('右键菜单里显示数据源', 'self.source' in src
              and 'QMenu' in src, '')
    finally:
        overlay.close()
        C.cfg = realCfg


def testOpggSessionLifecycle():
    """opgg.close() 必须把 session 置回 None

    开关一次游戏客户端就走一次 close → start。原来 close 只关连接池、
    不把 session 置 None，于是下次 start() 看到「非 None」直接跳过，
    之后每个外服请求都往一个已关闭的 session 里发 —— 异常又被
    __fetchOpgg 吞成空结果，用户看到的是「切到韩服全是暂无」。
    """
    from app.lol.opgg import opgg

    class FakeSession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    async def scenario():
        old = opgg.session
        fake = FakeSession()
        opgg.session = fake

        try:
            await opgg.close()
            check('close 后 session 置 None', opgg.session is None,
                  str(opgg.session))
            check('close 真的关了旧连接池', fake.closed is True, str(fake.closed))

            # 关了再开必须能重建，否则一次断线之后外服数据就永久废了
            await opgg.start()
            first = opgg.session
            check('start 能重新建 session', first is not None, str(first))

            await opgg.close()
            check('第二次 close 同样置 None', opgg.session is None,
                  str(opgg.session))

            await opgg.start()
            check('再 start 得到的是新对象',
                  opgg.session is not None and opgg.session is not first,
                  f'{opgg.session} vs {first}')
        finally:
            await opgg.close()
            opgg.session = old

    asyncio.run(scenario())


def testCountersStartsOpggSession():
    """BP 浮窗外服分支要自己兜底拉起 session

    外服的 session 平时靠「客户端启动」或「打开 OP.GG 窗口」建立。
    BP 期间从国服直接切韩服时没人 start，必须自己调。
    """
    path = os.path.join(ROOT, 'app', 'lol', 'bp_counters.py')
    src = open(path, encoding='utf-8').read()

    start = src.find('async def getPair')
    # getPair 后面第一个成员是 @staticmethod def empty（同步），用它当右边界
    end = src.find('def empty', start) if start >= 0 else -1
    seg = src[start:end] if start >= 0 and end > start else ''

    check('getPair 存在', bool(seg), f'{start}..{end}')
    check('外服分支前 await opgg.start()',
          'await opgg.start()' in seg, seg[-200:].replace('\n', ' '))

    atStart = seg.find('await opgg.start()')
    atFetch = seg.find('await self.__fetchOpgg(')
    check('start 在发请求之前',
          atStart >= 0 and atFetch > atStart, f'start={atStart} fetch={atFetch}')


def testSignalWired():
    """控制器必须订阅 opggRegion：用户在 OP.GG 窗口切了源，浮窗才知道要换"""
    realCfg = C.cfg
    fake = FakeCfg(bpOverlayCollapsed=False, bpOverlayOffset='',
                   bpOverlayOffsetSchema=O.OFFSET_SCHEMA,
                   opggTier='emerald_plus', opggRegion='cn',
                   bpOverlayEnabled=True)
    C.cfg = fake

    ctrl = C.BpOverlayController()

    try:
        slots = fake.opggRegion.valueChanged.slots
        check('订阅了 opggRegion 变化', len(slots) == 1, str(len(slots)))

        # 收到的是"切换后的新值"，不是 index、不是旧值
        if slots:
            names = getattr(slots[0], '__name__', '')
            check('槽函数是 __onRegionChanged',
                  'onRegionChanged' in names, names)
    finally:
        ctrl.close()
        C.cfg = realCfg


if __name__ == '__main__':
    testRegionWritesConfig()
    testRegionNotRestart()
    testSignalWired()
    testSwitchTriggersReload()
    testSwitchWhileInFlight()
    testNoRosterNoRequest()
    testSourceRouting()
    testCacheKeyHasRegion()
    testOverlayShowsSource()
    testOpggSessionLifecycle()
    testCountersStartsOpggSession()

    print()
    print(f'共 {len(_failures)} 项失败')

    if _failures:
        for name in _failures:
            print('  -', name)
        sys.exit(1)

    sys.exit(0)
