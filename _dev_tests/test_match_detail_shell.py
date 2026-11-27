# coding:utf-8
"""对局详情浮层的形态回归

用户反馈两点：字太小、弹窗不明显（看起来像又开了一个页面）。
这里钉住「像个弹窗」的三条硬指标：

1. 浮层铺满父窗口 —— 只盖中间一块的话，四周页面还亮着，就还是页面的观感
2. 面板居中、且四周留白
3. 点面板外面能关掉（主流弹窗的常识交互）

运行: python _dev_tests/test_match_detail_shell.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme  # noqa: E402

setTheme(Theme.DARK, save=False)

from app.common import skin  # noqa: E402

skin.install()
skin.applyGlobal(app)

from app.common import theme as themeMod  # noqa: E402
from app.view.match_detail_dialog import MARGIN, MatchDetailDialog  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


def makeHost(w=1400, h=900):
    host = QWidget()
    host.resize(w, h)
    host.show()

    for _ in range(4):
        app.processEvents()

    return host


def testGeometry():
    host = makeHost()
    dialog = MatchDetailDialog(host)
    dialog.show()

    for _ in range(8):
        app.processEvents()

    # frameGeometry 含窗口边框，比 size() 大一点点，按「至少盖住」断言
    check('浮层铺满父窗口（遮罩要盖住整页）',
          dialog.width() >= host.width() and dialog.height() >= host.height(),
          f'{dialog.size()} vs {host.size()}')

    shell = dialog._MatchDetailDialog__shell
    check('面板没有铺满（是浮起来的，不是页面）',
          shell.width() < dialog.width() and shell.height() < dialog.height(),
          f'{shell.size()}')

    check('左右留白对称',
          abs(shell.x() - (dialog.width() - shell.width() - shell.x())) <= 1,
          f'x={shell.x()} w={shell.width()} host={dialog.width()}')

    check('上下留白对称',
          abs(shell.y() - (dialog.height() - shell.height() - shell.y())) <= 1,
          f'y={shell.y()} h={shell.height()} host={dialog.height()}')

    check('留白不小于约定值', shell.x() >= MARGIN - 1, shell.x())

    dialog.close()
    host.close()


def testClickOutsideCloses():
    host = makeHost()
    dialog = MatchDetailDialog(host)
    dialog.show()

    for _ in range(8):
        app.processEvents()

    check('打开时可见', dialog.isVisible())

    # 面板左上角之外的位置 = 遮罩
    QTest.mouseClick(dialog, Qt.LeftButton, pos=QPoint(6, 6))

    for _ in range(4):
        app.processEvents()

    check('点遮罩关闭浮层', not dialog.isVisible())

    host.close()


def testClickInsideKeepsOpen():
    host = makeHost()
    dialog = MatchDetailDialog(host)
    dialog.show()

    for _ in range(8):
        app.processEvents()

    shell = dialog._MatchDetailDialog__shell
    inside = shell.geometry().center()
    QTest.mouseClick(dialog, Qt.LeftButton, pos=inside)

    for _ in range(4):
        app.processEvents()

    check('点面板内部不关闭', dialog.isVisible())

    dialog.close()
    host.close()


def testContrast():
    """面板底色要和页面底色拉开一档，否则遮罩也救不回来"""
    from app.common.design_tokens import color

    host = makeHost()
    dialog = MatchDetailDialog(host)
    dialog.show()

    for _ in range(8):
        app.processEvents()

    shell = dialog._MatchDetailDialog__shell
    ss = shell.styleSheet().lower()

    check('面板底色不是 bg0（和页面同色就分不开）',
          color('bg0').lower() not in ss, ss[:80])

    dialog.close()
    host.close()


def testTitleSize():
    """标题字号不能回到 12px"""
    src = open(os.path.join(ROOT, 'app', 'view', 'match_detail_dialog.py'),
               encoding='utf-8').read()

    check('标题字号 >= 15', 'font(15, bold=True)' in src, '原来是 12px')
    check('标题栏有固定高度', 'setFixedHeight(44)' in src)
    check('标题栏有底色（WA_StyledBackground）',
          'WA_StyledBackground' in src,
          'QWidget 子类不加这个，qss 的 border/background 不画')


testGeometry()
testClickOutsideCloses()
testClickInsideKeepsOpen()
testContrast()
testTitleSize()

print()
print(f'失败 {len(_failures)} 项' if _failures else '全部通过')
sys.exit(1 if _failures else 0)
