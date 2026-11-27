# coding:utf-8
"""BP 浮窗总开关回归

覆盖两件事：

1. **设置里的开关能开能关，选人过程中也立刻生效** —— 关闭时窗口必须马上消失，
   重新打开要自己把当前这局补回来（关着的时候会话事件全被丢掉了）。
2. **async 槽真的会被执行** —— PyQt5 把 `async def` 直接 connect 到信号上时
   只会生成一个没人 await 的协程，方法体一行都不跑。控制器里所有异步槽都
   经 `__spawn` 转 task，这里用「emit 之后 sessionKey 有没有变」来兜住这个坑。

运行: python _dev_tests/test_bp_toggle.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from unittest.mock import patch

from PyQt5.QtWidgets import QApplication

p = print

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme  # noqa: E402

setTheme(Theme.DARK, save=False)

# 设置页的卡片 / 分组 / 开关都是自绘的，创建控件前必须先装一遍
from app.common import skin  # noqa: E402

skin.install()
skin.applyGlobal(app)

from app.common.config import cfg  # noqa: E402
from app.common.signals import signalBus  # noqa: E402
from app.lol.bp_counters import bpCounters  # noqa: E402
from app.view.bp_overlay_controller import BpOverlayController  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


class OverlaySpy:
    """记录浮窗「该不该显示」

    offscreen 平台不映射 `Qt.Tool` 窗口 —— show() 之后 isVisible() 仍然是 False，
    拿它断言会把正确行为判成失败。这里代理 showOverlay/hideOverlay 的调用，
    断言的是逻辑状态。
    """

    def __init__(self, overlay):
        self.shown = False
        self.__show = overlay.showOverlay
        self.__hide = overlay.hideOverlay
        overlay.showOverlay = self.show
        overlay.hideOverlay = self.hide

    def show(self, *args):
        self.shown = True
        self.__show(*args)

    def hide(self, *args):
        self.shown = False
        self.__hide(*args)


def session(gameId=880001, myLane='MIDDLE', myCell=2):
    """一个最小可用的选人会话"""
    return {
        'gameId': gameId,
        'localPlayerCellId': myCell,
        'myTeam': [
            {'cellId': 1, 'championId': 1, 'selectedPosition': 'TOP'},
            {'cellId': myCell, 'championId': 7, 'selectedPosition': myLane},
        ],
        'theirTeam': [
            {'cellId': 6, 'championId': 103, 'selectedPosition': 'MIDDLE'},
            {'cellId': 7, 'championId': 0, 'selectedPosition': ''},
        ],
    }


def testToggle():
    """关 / 开 的即时生效，以及不同阶段下的行为"""
    original = cfg.get(cfg.bpOverlayEnabled)

    ctrl = BpOverlayController()
    spy = OverlaySpy(ctrl.overlay)
    ctrl.overlay.hideOverlay()

    # 不在选人阶段：打开也不该冒出来（还没有阵容可看）
    cfg.set(cfg.bpOverlayEnabled, False)
    check('关闭后浮窗隐藏', not spy.shown)

    ctrl.phase = ''
    cfg.set(cfg.bpOverlayEnabled, True)
    check('非选人阶段打开不弹窗', not spy.shown)

    # 选人阶段：打开立刻显示
    ctrl.phase = 'ChampSelect'
    cfg.set(cfg.bpOverlayEnabled, False)
    check('选人中关闭立刻隐藏', not spy.shown)

    cfg.set(cfg.bpOverlayEnabled, True)
    check('选人中打开立刻显示', spy.shown)

    # 手动「关闭本局」之后去设置里重开，应当重新出现
    ctrl.sessionKey = '880001'
    ctrl.dismissedKey = '880001'
    ctrl.overlay.hideOverlay()
    cfg.set(cfg.bpOverlayEnabled, False)
    cfg.set(cfg.bpOverlayEnabled, True)
    check('手动关掉本局后从设置重开能回来',
          spy.shown and ctrl.dismissedKey == '')

    ctrl.close()
    cfg.set(cfg.bpOverlayEnabled, original)


def testAsyncSlotRuns():
    """emit 一次会话事件，确认 __onSession 的方法体真的跑到了"""

    async def fakePair(*args, **kwargs):
        return bpCounters.empty()

    ctrl = BpOverlayController()
    spy = OverlaySpy(ctrl.overlay)
    original = cfg.get(cfg.bpOverlayEnabled)
    cfg.set(cfg.bpOverlayEnabled, True)

    async def scenario():
        with patch.object(bpCounters, 'getPair', fakePair):
            signalBus.champSelectChanged.emit({'data': session()})
            # 让 create_task 排的协程跑完
            for _ in range(6):
                await asyncio.sleep(0)

            check('会话事件真的进入 __onSession（sessionKey 已更新）',
                  ctrl.sessionKey == '880001', ctrl.sessionKey)
            check('识别出我的分路', ctrl.lane == 'MIDDLE', ctrl.lane)
            check('选中了对位英雄', ctrl.targetId == 103, ctrl.targetId)
            check('敌方阵容入格', len(ctrl.overlay.roster) == 1,
                  len(ctrl.overlay.roster))
            check('浮窗已显示', spy.shown)

            # 离开选人：立刻收起并清空
            signalBus.gameStatusChanged.emit('InProgress')
            await asyncio.sleep(0)

            check('离开选人后收起', not spy.shown)
            check('离开选人后清空目标', ctrl.targetId == 0, ctrl.targetId)
            check('离开选人后清空会话', ctrl.sessionKey == '', ctrl.sessionKey)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    ctrl.close()
    cfg.set(cfg.bpOverlayEnabled, original)


def testResyncOnEnable():
    """开关从关到开：要自己补一次会话，而不是显示一块空板"""

    async def fakePair(*args, **kwargs):
        return bpCounters.empty()

    async def fakeSession():
        return session(gameId=990002)

    ctrl = BpOverlayController()
    spy = OverlaySpy(ctrl.overlay)
    original = cfg.get(cfg.bpOverlayEnabled)

    async def scenario():
        # 模拟「这局一开始开关就是关的」：事件被丢掉，sessionKey 还是空
        cfg.set(cfg.bpOverlayEnabled, False)
        ctrl.phase = 'ChampSelect'
        signalBus.champSelectChanged.emit({'data': session(990002)})
        await asyncio.sleep(0)
        check('关闭状态下不处理会话', ctrl.sessionKey == '', ctrl.sessionKey)

        with patch.object(bpCounters, 'getPair', fakePair), \
                patch('app.view.bp_overlay_controller.connector'
                      '.getChampSelectSession', fakeSession):
            cfg.set(cfg.bpOverlayEnabled, True)
            for _ in range(6):
                await asyncio.sleep(0)

        check('打开时补拉到了当前这局', ctrl.sessionKey == '990002',
              ctrl.sessionKey)
        check('补拉后浮窗可见', spy.shown)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    ctrl.close()
    cfg.set(cfg.bpOverlayEnabled, original)


def testPhaseResync():
    """程序在 BP 中途启动：只收到阶段、没有会话事件时也要能自己补上"""

    async def fakePair(*args, **kwargs):
        return bpCounters.empty()

    async def fakeSession():
        return session(gameId=770003)

    ctrl = BpOverlayController()
    spy = OverlaySpy(ctrl.overlay)
    original = cfg.get(cfg.bpOverlayEnabled)
    cfg.set(cfg.bpOverlayEnabled, True)

    async def scenario():
        with patch.object(bpCounters, 'getPair', fakePair),                 patch('app.view.bp_overlay_controller.connector'
                      '.getChampSelectSession', fakeSession):
            signalBus.gameStatusChanged.emit('ChampSelect')
            for _ in range(6):
                await asyncio.sleep(0)

        check('只有阶段事件时也能补到会话', ctrl.sessionKey == '770003',
              ctrl.sessionKey)
        check('补到会话后浮窗显示', spy.shown)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    ctrl.close()
    cfg.set(cfg.bpOverlayEnabled, original)


def testSettingCard():
    """设置页要真的挂上这张卡，并且指向同一个配置项"""
    from app.view.setting_interface import SettingInterface

    page = SettingInterface()
    card = getattr(page, 'bpOverlayCard', None)

    check('设置页有 BP 浮窗卡片', card is not None)
    check('卡片绑的是 bpOverlayEnabled',
          card is not None and card.configItem is cfg.bpOverlayEnabled)

    if card is not None:
        names = [w.titleLabel.text()
                 for w in page.functionGroup.findChildren(type(card))]

        check('卡片在「功能」分组里', 'BP 对位浮窗' in names, names)

        # 点卡片上的开关 = 改配置（库里 connect 的 checkedChanged）
        original = cfg.get(cfg.bpOverlayEnabled)
        card.switchButton.setChecked(not original)
        check('拨动卡片开关会写进配置',
              cfg.get(cfg.bpOverlayEnabled) == (not original),
              cfg.get(cfg.bpOverlayEnabled))

        card.switchButton.setChecked(original)
        check('拨回去配置跟着回原值',
              cfg.get(cfg.bpOverlayEnabled) == original)


testToggle()
testAsyncSlotRuns()
testResyncOnEnable()
testPhaseResync()
testSettingCard()

print()
print(f'失败 {len(_failures)} 项' if _failures else '全部通过')
sys.exit(1 if _failures else 0)
