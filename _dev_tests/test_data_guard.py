# coding:utf-8
"""全量巡检发现的三类隐患的回归

1. **胜负序列写死 20 格** —— 配置把对局数量改到 30 时，「近 30 场」配 20 个格子
2. **最近队友文案写死「近 20 场」** —— 同上，空态和弹窗副标题都对不上
3. **解析缺少字段兜底** —— LCU 对久未登录 / 已注销的号可能不返回 `profileIconId`、
   `displayName`、`privacy`，一个 KeyError 就让整页停在 loading 上

运行: python _dev_tests/test_data_guard.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from unittest.mock import patch

from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme  # noqa: E402

setTheme(Theme.DARK, save=False)

from app.common import skin  # noqa: E402

skin.install()
skin.applyGlobal(app)

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


def runAsync(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def testStripDynamic():
    """格数跟着场次走，宽度跟着格数走"""
    from app.view.career_interface import ResultStrip

    strip = ResultStrip()

    strip.setResults([True] * 30)
    check('30 场 → 30 格宽', strip.width() == 30 * 10 - 2, strip.width())

    strip.setResults([True] * 8)
    check('8 场 → 8 格宽', strip.width() == 8 * 10 - 2, strip.width())

    strip.setResults([])
    check('空列表仍有最小宽度（不会塌成 0）', strip.width() > 0, strip.width())

    strip.close()


def testTeammatesTotal():
    """队友弹窗的场次由调用方传，不再写死 20"""
    import inspect

    from app.view.dialogs.teammates import TeammatesDialog

    sig = inspect.signature(TeammatesDialog.__init__)
    check('TeammatesDialog 接受 total 参数', 'total' in sig.parameters,
          list(sig.parameters))

    src = inspect.getsource(TeammatesDialog.__init__)
    check('副标题用 total 而不是写死 20', '近 {total} 场' in src)


def testParseGuard():
    """字段缺失时返回默认值，而不是抛 KeyError"""
    import app.lol.tools as tools

    async def fakeIcon(_):
        return ''

    async def fakeGames():
        raise RuntimeError('没有对局')

    async def fakeRank():
        return {}

    async def scenario():
        with patch.object(tools.connector, 'getProfileIcon', fakeIcon):
            return await tools.parseSummonerData(
                {'puuid': 'x'}, fakeRank(), fakeGames())

    info = runAsync(scenario())

    check('缺 displayName 时名字兜底成 --', info['name'] == '--', info['name'])
    check('缺 summonerLevel 时等级兜底成 0', info['level'] == 0, info['level'])
    check('缺 privacy 时按不公开处理', info['isPublic'] is False,
          info['isPublic'])
    check('拿不到对局时返回空表', not (info['games'].get('games') or []))


testStripDynamic()
testTeammatesTotal()
testParseGuard()

print()
print(f'失败 {len(_failures)} 项' if _failures else '全部通过')
sys.exit(1 if _failures else 0)
