# coding:utf-8
"""M6：弹窗、三态、设置导航、英雄筛选"""
import os
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication(sys.argv)
from app.common.qfluentwidgets import setTheme, setThemeColor, Theme  # noqa: E402
setTheme(Theme.DARK, save=False)
setThemeColor('#E3B23C', save=False)
from app.common import skin  # noqa: E402
skin.install()
skin.applyGlobal(app)

from app.components.states import EmptyState, ErrorState, LoadingState  # noqa: E402
from app.view.dialogs.champions import ChampionPoolDialog, _ChampionCell  # noqa: E402
from app.view.dialogs.teammates import TeammateRow, TeammatesDialog  # noqa: E402

passed = failed = 0


def check(name, ok, detail=None):
    global passed, failed
    if ok:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}  {detail}')


def settle():
    for _ in range(3):
        app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


champs = [
    {'name': '暗裔剑魔', 'icon': '', 'total': 6, 'wins': 4, 'losses': 2},
    {'name': '九尾妖狐', 'icon': '', 'total': 4, 'wins': 3, 'losses': 1},
    {'name': '盲僧', 'icon': '', 'total': 4, 'wins': 1, 'losses': 3},
    {'name': '暗夜猎手', 'icon': '', 'total': 3, 'wins': 2, 'losses': 1},
    {'name': '诡术妖姬', 'icon': '', 'total': 2, 'wins': 2, 'losses': 0},
    {'name': '青钢影', 'icon': '', 'total': 1, 'wins': 1, 'losses': 0},
    {'name': '魂锁典狱长', 'icon': '', 'total': 1, 'wins': 0, 'losses': 1},
]

print('三态')
for cls in (EmptyState, LoadingState, ErrorState):
    w = cls()
    w.show()
    settle()
    check(f'{cls.__name__} 能构建且有高度', w.height() >= 100, w.height())
err = ErrorState()
got = []
err.retry.connect(lambda: got.append(1))
err.findChild(type(err.findChild(__import__('PyQt5.QtWidgets', fromlist=['QPushButton']).QPushButton))).click()
check('错误态点重试会发信号', got == [1], got)

print('常用英雄弹窗')
dlg = ChampionPoolDialog(champs, games=20)
dlg.show()
settle()
cells = dlg.findChildren(_ChampionCell)
check('格子数等于英雄数', len(cells) == len(champs), len(cells))
firstByGames = cells[0].champ['name']
check('默认按场次排', firstByGames == '暗裔剑魔', firstByGames)

picked = []
dlg.picked.connect(picked.append)
# 切换成按胜率
btns = [b for b in dlg.findChildren(__import__('PyQt5.QtWidgets', fromlist=['QPushButton']).QPushButton)
        if b.text() in ('场次', '胜率')]
check('有两个排序按钮', len(btns) == 2, [b.text() for b in btns])
btns[1].click()
settle()
cells = dlg.findChildren(_ChampionCell)
check('按胜率排后第一个是 100% 的', cells[0].champ['name'] in ('青钢影', '诡术妖姬'),
      cells[0].champ)
QTest.mouseClick(cells[0], Qt.LeftButton, pos=QPoint(10, 10))
settle()
check('点格子发出英雄名', picked and picked[0] == cells[0].champ['name'], picked)
check('点格子后弹窗关闭', not dlg.isVisible())

print('最近队友弹窗')
summoners = [
    {'puuid': f'p{i}', 'name': f'队友{i}', 'icon': '', 'total': 5 - i,
     'wins': 3, 'losses': 2, 'lastStr': f'11-2{i}'}
    for i in range(6)
]
tdlg = TeammatesDialog(summoners)
tdlg.show()
settle()
rows = tdlg.findChildren(TeammateRow)
check('队友行数等于人数', len(rows) == 6, len(rows))
names = [l.text() for l in rows[0].findChildren(QLabel)]
check('行里带名字与最近时间', '队友0' in names and '11-20' in names, names)
taken = []
tdlg.picked.connect(taken.append)
QTest.mouseClick(rows[0], Qt.LeftButton, pos=QPoint(10, 10))
settle()
check('点行发出 puuid', taken == ['p0'], taken)

print('设置页导航')
from app.view.setting_interface import SettingInterface  # noqa: E402
page = SettingInterface()
page.show()
settle()
check('六个分组按钮', len(page.navButtons) == 6, len(page.navButtons))
check('六页堆栈', page.stack.count() == 6, page.stack.count())
check('默认第一组', page.stack.currentIndex() == 0)
page.navButtons[4].click()
settle()
check('点「更新与关于」切到第 5 页', page.stack.currentIndex() == 4, page.stack.currentIndex())
check('只有当前按钮是选中态',
      [b.isChecked() for b in page.navButtons] == [False, False, False, False, True, False],
      [b.isChecked() for b in page.navButtons])

print('生涯英雄筛选')
from app.components.match_detail_view import MatchDetailView  # noqa: E402
from app.view.career_interface import CareerInterface  # noqa: E402

career = CareerInterface()
career.resize(1200, 800)
career.show()
settle()

games = [
    {'gameId': i, 'win': i % 2 == 0, 'remake': False, 'kills': 3, 'deaths': 2,
     'assists': 9, 'cs': 180, 'score': 7.0, 'damage': 20000, 'queueId': 420,
     'name': '单双排位', 'position': '中单', 'championName': '盲僧' if i < 3 else '剑魔',
     'championIcon': '', 'time': '2026-11-26 12:00', 'shortTime': '11-26',
     'timeStamp': 1764000000000 + i, 'duration': '30:00'}
    for i in range(6)
]
career.allGames = games
career.champions = champs
career.filtered = list(games)
getattr(career, '_CareerInterface__onModeChanged')(0)
settle()
check('未筛选时全部 6 场', len(career.filtered) == 6, len(career.filtered))
getattr(career, '_CareerInterface__filterByChampion')('盲僧')
settle()
check('按英雄筛只剩 3 场', len(career.filtered) == 3, len(career.filtered))
check('筛选提示条可见且带英雄名',
      not career.champChip.isHidden() and '盲僧' in career.champChip.text(),
      career.champChip.text())
getattr(career, '_CareerInterface__clearChampFilter')()
settle()
check('清除后恢复 6 场', len(career.filtered) == 6, len(career.filtered))
check('筛选提示条隐藏', career.champChip.isHidden())

print('详情错误态')
view = MatchDetailView()
view.resize(900, 600)
view.show()
view.setError('对局详情读取失败', '稍后重试')
settle()
check('错误态里有点重试的按钮', view.findChild(ErrorState) is not None)
retried = []
view.retry.connect(lambda: retried.append(1))
btn = view.findChild(ErrorState).findChild(__import__('PyQt5.QtWidgets', fromlist=['QPushButton']).QPushButton)
btn.click()
check('重试转发到视图信号', retried == [1], retried)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
