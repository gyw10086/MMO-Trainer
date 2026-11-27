# coding:utf-8
"""生涯页刷新回归

三件事：

1. **刷新按钮要有回报** —— 以前点完按钮什么都不变，拉到了 / 没变化 / 报错
   用户都看不出来，只能靠「回总览再进一次」碰运气。
2. **任何一步失败都不能卡在 loading 页** —— 转圈比报错更像"点了没反应"。
3. **`__onGameStatusChanged` 必须带 `@asyncSlot`** —— PyQt5 调一个裸的
   `async def` 只会生成没人 await 的协程，方法体一行不跑。这个槽管着阶段标题、
   自动切页、打完回大厅刷新生涯，漏装饰器等于整条链路静默失效。

运行: python _dev_tests/test_career_refresh.py
"""
import ast
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

from app.lol.connector import connector  # noqa: E402
from app.view.career_interface import CareerInterface  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


def testRefreshLabels():
    """按钮文案按状态切换，忙的时候禁用"""
    page = CareerInterface()
    setState = page._CareerInterface__setRefreshState

    setState('busy')
    check('刷新中：文案正确', page.refreshButton.text() == '刷新中',
          page.refreshButton.text())
    check('刷新中：按钮禁用', not page.refreshButton.isEnabled())

    setState('done', 3)
    check('有新对局：显示 +3 场', page.refreshButton.text() == '+3 场',
          page.refreshButton.text())
    check('结束后按钮恢复可用', page.refreshButton.isEnabled())

    setState('same')
    check('没新对局：显示「无新对局」',
          page.refreshButton.text() == '无新对局', page.refreshButton.text())

    setState('fail')
    check('失败：显示「刷新失败」',
          page.refreshButton.text() == '刷新失败', page.refreshButton.text())

    page._CareerInterface__resetRefreshState()
    check('定时恢复成「刷新」', page.refreshButton.text() == '刷新',
          page.refreshButton.text())

    # 三个文案宽度不能把 header 撑得跳来跳去
    widths = set()

    for text in ('刷新', '刷新中', '+12 场', '无新对局', '刷新失败'):
        page.refreshButton.setText(text)
        widths.add(page.refreshButton.width())

    check('文案切换时按钮宽度不变（固定宽）', len(widths) == 1, widths)

    page.close()


def testLoadingAlwaysClosed():
    """任何一步抛异常，loading 页都要收掉"""
    page = CareerInterface()
    states = []
    page.setLoadingPageEnabled = lambda v: states.append(v)

    async def boom(*args, **kwargs):
        raise RuntimeError('模拟接口炸了')

    async def run():
        with patch.object(connector, 'getSummonerByPuuid', boom):
            try:
                await page.updateInterface(puuid='x')
            except RuntimeError:
                pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(run())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    check('异常时 loading 被打开过', states and states[0] is True, states)
    check('异常后 loading 一定被收掉', states and states[-1] is False, states)

    page.close()


def testGameStatusSlotDecorated():
    """阶段槽必须带 @asyncSlot，否则方法体不会执行"""
    path = os.path.join(ROOT, 'app', 'view', 'main_window.py')

    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())

    found = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue

        decorated = any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
            and d.func.id == 'asyncSlot' or
            isinstance(d, ast.Name) and d.id == 'asyncSlot'
            for d in node.decorator_list)

        found[node.name] = decorated

    target = found.get('__onGameStatusChanged')

    check('__onGameStatusChanged 存在', target is not None)
    check('__onGameStatusChanged 带 @asyncSlot', target is True,
          '裸 async def 连信号不会执行')


def testDetailGuard():
    """gameId 为空时不能静默 return —— 源码里必须有提示分支"""
    path = os.path.join(ROOT, 'app', 'view', 'main_window.py')

    with open(path, encoding='utf-8') as f:
        src = f.read()

    start = src.find('def openMatchDetail')
    body = src[start:start + 1200]

    check('openMatchDetail 有 gameId 判断', "game.get('gameId')" in body)
    check('gameId 为空时给了提示（不再是直接 return）',
          'InfoBar' in body, '用户点了没反应会以为卡死')


testRefreshLabels()
testLoadingAlwaysClosed()
testGameStatusSlotDecorated()
testDetailGuard()

print()
print(f'失败 {len(_failures)} 项' if _failures else '全部通过')
sys.exit(1 if _failures else 0)
