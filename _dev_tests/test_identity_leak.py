# coding:utf-8
"""身份隔离回归：查完别人，回到「我的」上下文不能串成别人

背景：总览页的数据是被动的 —— 生涯页每加载一次就发 `infoLoaded`，
总览无条件接收。于是「查了别人 → 点总览」，看到的还是别人的段位和战绩。

规则（本文件守住它）：
  1. 只有加载「自己」时才发 `infoLoaded`
  2. 跳转信号带空 puuid 时，按「看我自己」处理，不能直接丢给 updateInterface
  3. 总览/设置页触发刷新时，刷的是自己的数据，不是当前显示的人

运行: python _dev_tests/test_identity_leak.py
"""
import ast
import asyncio
import functools
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


# ------------------------------------------------------------------ 假对象
class _FakeCareer:
    def __init__(self, me='me', current='other'):
        self.loginSummonerPuuid = me
        self.puuid = current
        self.updated = []
        self.refreshed = 0

    def isLoginSummoner(self):
        return self.puuid == self.loginSummonerPuuid

    async def updateInterface(self, puuid=None, summoner=None):
        self.updated.append(puuid)
        self.puuid = puuid

    async def refresh(self):
        self.refreshed += 1


class _FakeWindow:
    def __init__(self, career, running=True):
        self.careerInterface = career
        self.isClientProcessRunning = running
        self.reloads = 0

        # 同步入口内部会 `self.__reloadMyCareer()`（被 mangle 成
        # `_MainWindow__reloadMyCareer`），补一个桩，只数调用次数
        async def _stub():
            self.reloads += 1

        self._MainWindow__reloadMyCareer = _stub


# ------------------------------------------------------------------ 行为
def testReloadMyCareer():
    from app.view.main_window import MainWindow

    reload = MainWindow._MainWindow__reloadMyCareer

    # 1. 生涯页停在别人身上 → 按自己的 puuid 重拉
    career = _FakeCareer(me='me', current='other')
    asyncio.run(reload(_FakeWindow(career)))

    check('别人的页面上刷新会切回自己',
          career.updated == ['me'], str(career.updated))
    check('刷新后生涯页停在自己', career.puuid == 'me')

    # 2. 本来就在自己身上 → 走原来的 refresh（保留模式筛选）
    career = _FakeCareer(me='me', current='me')
    asyncio.run(reload(_FakeWindow(career)))

    check('自己页面上刷新走 refresh', career.refreshed == 1, str(career.refreshed))
    check('不为自己重复 update', career.updated == [], str(career.updated))

    # 3. 客户端没连 → 什么都不做
    career = _FakeCareer(me='me', current='other')
    asyncio.run(reload(_FakeWindow(career, running=False)))

    check('客户端没连时不动', career.updated == [] and career.refreshed == 0)

    # 4. 还没有自己的 puuid（客户端没登录过）→ 不动
    career = _FakeCareer(me=None, current='other')
    asyncio.run(reload(_FakeWindow(career)))

    check('不知道我是谁时不动', career.updated == [] and career.refreshed == 0)


def testRefreshEntry():
    """`clicked` 会带一个 bool 参数，入口签名必须吃得下"""
    from app.view.main_window import MainWindow

    career = _FakeCareer(me='me', current='me')
    win = _FakeWindow(career)

    try:
        # 直接调同步入口：内部 create_task，所以要有个运行中的循环
        async def run():
            MainWindow._MainWindow__refreshMyCareer(win, False)
            await asyncio.sleep(0.01)

        asyncio.run(run())
        passed, detail = win.reloads == 1, f'reloads={win.reloads}'
    except TypeError as e:
        passed, detail = False, repr(e)

    check('刷新入口能接住 clicked 的 bool 参数', passed, detail)


# ------------------------------------------------------------------ 源码规则
def _findFunc(tree, name):
    for node in ast.walk(tree):
        # async def 是 AsyncFunctionDef
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node

    return None


def testInfoLoadedGuarded():
    """`infoLoaded` 必须只在加载自己时发"""
    path = os.path.join(ROOT, 'app', 'view', 'career_interface.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    repaint = _findFunc(tree, 'repaintInterface')

    if repaint is None:
        check('找到 repaintInterface', False)
        return

    guarded = False
    bare = False

    for node in ast.walk(repaint):
        if not isinstance(node, ast.Call):
            continue

        func = node.func

        # signalBus.infoLoaded.emit(...)
        if (isinstance(func, ast.Attribute) and func.attr == 'emit'
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == 'infoLoaded'):
            # 往上找它是否包在 if isLoginSummoner() 里
            for parent in ast.walk(repaint):
                if not isinstance(parent, (ast.If,)):
                    continue

                body_ids = {id(x) for stmt in parent.body for x in ast.walk(stmt)}

                if id(node) in body_ids:
                    cond = ast.dump(parent.test)

                    if 'isLoginSummoner' in cond:
                        guarded = True

            if not guarded:
                bare = True

    check('repaintInterface 里 infoLoaded 有 isLoginSummoner 守卫',
          guarded and not bare, 'guarded' if guarded else '没找到守卫')


def testEmptyPuuidResolved():
    """跳转信号的空 puuid 要按「看我自己」处理"""
    path = os.path.join(ROOT, 'app', 'view', 'main_window.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    switch = _findFunc(tree, '__switchToCareerInterface')

    if switch is None:
        check('找到 __switchToCareerInterface', False)
        return

    src = ast.dump(switch)
    check('空 puuid 有兜底（loginSummonerPuuid）',
          'loginSummonerPuuid' in src, '')

    # 兜底之后仍然要挡一次空值，否则 updateInterface 的 assert 会炸。
    # 结构上看：存在一个 `if not puuid ...: return` 的分支
    has_guard = False

    for node in ast.walk(switch):
        if isinstance(node, ast.If):
            test = ast.dump(node.test)

            if 'Not' in test and "'puuid'" in test:
                has_guard = True

    check('兜底后仍挡空值', has_guard)


def testOverviewHasInfo():
    path = os.path.join(ROOT, 'app', 'view', 'overview_interface.py')
    tree = ast.parse(open(path, encoding='utf-8').read())

    check('总览提供 hasInfo()（切页兜底要用）',
          _findFunc(tree, 'hasInfo') is not None)


def main():
    testReloadMyCareer()
    testRefreshEntry()
    testInfoLoadedGuarded()
    testEmptyPuuidResolved()
    testOverviewHasInfo()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
