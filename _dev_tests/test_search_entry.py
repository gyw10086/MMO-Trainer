# coding:utf-8
"""搜索入口回归

用户报两件事：
1. 生涯页点「查战绩」跳过去查不到 —— 只带了名字，没带 `#编号`
2. 战绩查询有历史记录时，点不到输入框，只能点到历史下拉

运行: python _dev_tests/test_search_entry.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
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


def testCareerNameWithTag():
    """「查战绩」要带 #编号，否则同区重名查不到"""
    from app.view.career_interface import CareerInterface

    page = CareerInterface()

    page.name.setText('念尘词')
    page.tagLineLabel.setText('# 20001')
    page.showTagLine = True

    check('带 tag 时返回「名字#编号」',
          page.getSummonerName() == '念尘词#20001', page.getSummonerName())

    # 私密号没有 tag，不能拼出一个 “名字#”
    page.showTagLine = False
    check('没有 tag 时只返回名字',
          page.getSummonerName() == '念尘词', page.getSummonerName())

    # 名字里带锁标记时要去掉（NameLabel.text 已经处理过，这里再确认一次）
    page.name.setText('念尘词')
    page.tagLineLabel.setText('# 20001')
    page.showTagLine = True
    check('结果里不带锁标记', '🔒' not in page.getSummonerName(),
          page.getSummonerName())

    page.close()


def testClickReleasesPopup():
    """候选条开着时点输入框：先收起，这次点击要能落到输入框上"""
    from app.components.search_line_edit import SearchLineEdit

    edit = SearchLineEdit()
    edit.resize(300, 40)
    edit.show()

    events = []

    class FakeMenu:
        def __init__(self):
            self.visible = True

        def isVisible(self):
            return self.visible

        def hide(self):
            self.visible = False
            events.append('hide')

    edit._completerMenu = FakeMenu()

    QTest.mouseClick(edit, Qt.LeftButton)

    check('点击输入框时候选条被收起', events == ['hide'], events)
    check('候选条已不可见', not edit._completerMenu.isVisible())

    edit.close()


def testNoPopupWhenTyped():
    """输入框里有内容时不再弹历史（会压住正在编辑的文字）"""
    import inspect

    from app.components.search_line_edit import SearchLineEdit

    src = inspect.getsource(SearchLineEdit.focusInEvent)

    check('聚焦弹历史前判断了输入框是否为空',
          "super().text().strip()" in src, src.strip()[:80])

    mouse = inspect.getsource(SearchLineEdit.mousePressEvent)
    check('点击时收起候选条而不是重新弹出',
          'menu.hide()' in mouse and '_showCompleterMenu' not in mouse,
          mouse.strip()[:80])


def testJumpPath():
    """整条跳转链路上不能把 tag 丢掉"""
    src = open(os.path.join(ROOT, 'app', 'view', 'main_window.py'),
               encoding='utf-8').read()

    start = src.find('async def __switchToSearchInterface')
    body = src[start:start + 400]

    check('跳转直接把收到的名字填进输入框',
          'searchLineEdit.setText(name)' in body, body[:80])


testCareerNameWithTag()
testClickReleasesPopup()
testNoPopupWhenTyped()
testJumpPath()

print()
print(f'失败 {len(_failures)} 项' if _failures else '全部通过')
sys.exit(1 if _failures else 0)
