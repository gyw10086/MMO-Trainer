# coding:utf-8
"""详情浮层收起回归：切生涯页前必须先把浮层关掉

背景：详情浮层是独立顶层窗口，切页发生在它下面。不先收起的话用户点了
浮层里的人名，只看到浮层原样盖着新页面，会以为点击失效。

不联网、不起窗口，只测三件事：
  - `__dismissMatchDetail` 在「没有浮层 / 正常关 / 已被销毁」三种情况下都不抛
  - 收起后主窗口的引用会被清空（否则下次 openMatchDetail 会拿到死对象）
  - 源码里两处调用点还在（防止以后重构把调用删了）

运行: python _dev_tests/test_detail_dismiss.py
"""
import ast
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


class _FakeWindow:
    """拿着 `_MainWindow__matchDetailDialog` 就够跑了，不需要真窗口"""

    _MainWindow__matchDetailDialog = None


class _FakeDialog:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _DeadDialog:
    """模拟 WA_DeleteOnClose 之后访问 C++ 对象抛的那个异常"""

    def close(self):
        raise RuntimeError('wrapped C/C++ object of type MatchDetailDialog '
                           'has been deleted')


def testDismiss():
    from app.view.main_window import MainWindow

    dismiss = MainWindow._MainWindow__dismissMatchDetail

    # 1. 没有浮层时静默返回
    win = _FakeWindow()
    try:
        dismiss(win)
        passed, detail = True, ''
    except Exception as e:
        passed, detail = False, repr(e)

    check('没有浮层时不抛异常', passed, detail)

    # 2. 正常关闭
    win = _FakeWindow()
    dialog = _FakeDialog()
    win._MainWindow__matchDetailDialog = dialog
    dismiss(win)

    check('浮层被 close()', dialog.closed == 1, f'closed={dialog.closed}')
    check('引用被清空', win._MainWindow__matchDetailDialog is None,
          str(win._MainWindow__matchDetailDialog))

    # 3. 已被销毁：close() 会抛 RuntimeError，必须吞掉
    win = _FakeWindow()
    win._MainWindow__matchDetailDialog = _DeadDialog()
    try:
        dismiss(win)
        passed, detail = True, ''
    except RuntimeError as e:
        passed, detail = False, repr(e)
    except Exception as e:
        passed, detail = False, repr(e)

    check('浮层已销毁时不抛异常', passed, detail)
    check('已销毁也清空引用', win._MainWindow__matchDetailDialog is None,
          str(win._MainWindow__matchDetailDialog))


def __funcCalls(node, name) -> list:
    """函数体里按出现顺序列出被调用的方法名"""
    calls = []

    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            calls.append((sub.lineno, sub.func.attr))

    calls.sort()
    return [attr for _, attr in calls]


def testCallSites():
    path = os.path.join(ROOT, 'app', 'view', 'main_window.py')
    tree = ast.parse(open(path, encoding='utf-8').read())

    found = {}

    for node in ast.walk(tree):
        # async def 是 AsyncFunctionDef，不是 FunctionDef
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
                '__switchToCareerInterface', 'openMatchDetail'):
            found[node.name] = node

    switch = found.get('__switchToCareerInterface')

    if switch is None:
        check('找到 __switchToCareerInterface', False)
        return

    calls = __funcCalls(switch, 'x')
    has = '__dismissMatchDetail' in calls

    check('切生涯页前调用了收起浮层', has, ', '.join(calls[:6]))

    if has and 'checkAndSwitchTo' in calls:
        check('收起在切页之前（顺序不能反）',
              calls.index('__dismissMatchDetail') < calls.index('checkAndSwitchTo'),
              f'{calls.index("__dismissMatchDetail")} < '
              f'{calls.index("checkAndSwitchTo")}')

    openDetail = found.get('openMatchDetail')

    if openDetail is not None:
        check('开新浮层前先收旧的（防连点堆两个）',
              '__dismissMatchDetail' in __funcCalls(openDetail, 'x'))


def main():
    testDismiss()
    testCallSites()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
