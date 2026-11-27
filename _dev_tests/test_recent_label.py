# coding:utf-8
"""近况场次文案回归

用户报「明明是 20 场，显示的近 10 场」：生涯页的 `_Stat('近 10 场')` 是构造时
写死的文案，而 `__renderSummary` 统计的是取回来的全部场次（默认 20）——
「2 胜 18 负」配「近 10 场」，数字自相矛盾。

这里钉住三条：
1. 生涯页标签跟着真实场次走
2. 总览页不再把列表截前 10 场（两页口径要一致）
3. 没有数据时不留一个假的「近 10 场」

运行: python _dev_tests/test_recent_label.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication, QLabel

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme  # noqa: E402

setTheme(Theme.DARK, save=False)

from app.common import skin  # noqa: E402

skin.install()
skin.applyGlobal(app)

from app.view.career_interface import CareerInterface  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}'
          + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


def labelOf(stat):
    """取 `_Stat` 的文案标签（`value` 是数字，别拿错）"""
    return getattr(stat, 'label', None)


def makeGame(i, win):
    return {'gameId': 1000 + i, 'win': win, 'remake': False,
            'kills': 3, 'deaths': 2, 'assists': 8,
            'damage': 20000, 'score': 6.0, 'cs': 180, 'gold': 12000}


def testCareerLabel():
    page = CareerInterface()

    # 20 场：2 胜 18 负，和用户截图里的数字一致
    page.allGames = [makeGame(i, i < 2) for i in range(20)]
    page._CareerInterface__renderSummary()

    label = labelOf(page.statRecent)
    check('生涯页：20 场时标签写「近 20 场」',
          label is not None and label.text() == '近 20 场',
          label.text() if label else None)

    check('生涯页：数值仍是 2 胜 18 负',
          '2' in page.statRecent.value.text()
          and '18' in page.statRecent.value.text(),
          page.statRecent.value.text())

    # 场次变化后标签要跟着变
    page.allGames = [makeGame(i, i < 5) for i in range(8)]
    page._CareerInterface__renderSummary()

    check('生涯页：8 场时标签写「近 8 场」',
          labelOf(page.statRecent).text() == '近 8 场',
          labelOf(page.statRecent).text())

    # 没数据时不能留一个假的「近 10 场」
    page.allGames = []
    page._CareerInterface__renderSummary()

    text = labelOf(page.statRecent).text()
    check('生涯页：无数据时不留死数字', text in ('近况', '--'), text)

    page.close()


def testCareerStripLabel():
    page = CareerInterface()
    page.allGames = [makeGame(i, i < 2) for i in range(20)]
    page._CareerInterface__renderRecent()

    text = page._CareerInterface__stripLabel.text()
    check('胜负序列标签按实际格数写', text == '最近 20 场', text)

    page.allGames = [makeGame(i, i < 2) for i in range(6)]
    page._CareerInterface__renderRecent()
    text = page._CareerInterface__stripLabel.text()
    check('场次不足时也写实际数', text == '最近 6 场', text)

    page.close()


def testOverviewScope():
    """总览页不能只统计前 10 场"""
    path = os.path.join(ROOT, 'app', 'view', 'overview_interface.py')

    with open(path, encoding='utf-8') as f:
        src = f.read()

    start = src.find('def __updateCareer')
    body = src[start:start + 1500]

    check('总览页不再截前 10 场', 'games[:10]' not in body,
          '和生涯页口径不一致会对不上')

    check('总览页标签是动态的', 'setHint(f\'近 {len(recent)} 场\')' in body,
          '写死「近 10 场」就会和真实场次矛盾')


testCareerLabel()
testCareerStripLabel()
testOverviewScope()

print()
print(f'失败 {len(_failures)} 项' if _failures else '全部通过')
sys.exit(1 if _failures else 0)
