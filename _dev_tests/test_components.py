# coding:utf-8
"""总览页与通用组件的最小回归

不依赖 LCU：全部用手造数据跑 offscreen Qt。
覆盖：
  - Pager 的页数计算与边界（0 条、刚好整除、翻到最后一页）
  - RankCard 的段位拆分与未定级态
  - GameRow 两种变体的点击信号
  - notice.parse 对 CHANGELOG 的解析
运行: python _dev_tests/test_components.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel

app = QApplication(sys.argv)

from app.common.qfluentwidgets import setThemeColor, setTheme, Theme
setTheme(Theme.DARK, save=False)
setThemeColor('#E3B23C', save=False)

from app.common import notice
from app.common import skin
skin.install()

from app.components.game_row import GameRow
from app.components.pager import Pager
from app.components.rank_card import RankCard, splitRank

failures = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        failures.append(name)


print('Pager')
pager = Pager(4, '场')
pages = []
pager.pageChanged.connect(pages.append)

pager.setTotal(0)
check('空列表至少 1 页', pager.pageCount() == 1)
pager.setTotal(9)
check('9 条 / 每页 4 = 3 页', pager.pageCount() == 3, pager.pageCount())
pager.setTotal(8)
check('8 条 / 每页 4 = 2 页', pager.pageCount() == 2)
pager.go(1)
check('翻到第 2 页', pager.currentPage() == 1)
pager.go(99)
check('越界钳到末页', pager.currentPage() == 1)
pager.go(-5)
check('负数钳到首页', pager.currentPage() == 0)

print('RankCard')
check('璀璨钻石 II', splitRank('璀璨钻石 II') == ('钻石', 'Ⅱ'), splitRank('璀璨钻石 II'))
check('超凡大师 I -> 星', splitRank('超凡大师 I') == ('大师', '★'))
check('未定级', splitRank('--') == ('未定级', ''))

card = RankCard(['单双排', '118', '56 %', '62', '56', '璀璨钻石 II', '78',
                 '华贵铂金 I', '不屈白银 III'], compact=True)
nameLabel = card.findChild(QLabel, 'rankName')
check('段位名用简称', nameLabel is not None and '钻石' in nameLabel.text(),
      nameLabel.text() if nameLabel else None)

unranked = RankCard(['单双排', '0', '--', '0', '0', '--', '--', '--', '--'])
check('未定级不抛异常', unranked is not None)

print('GameRow')
game = {'gameId': 1, 'win': True, 'remake': False, 'kills': 10, 'deaths': 2,
        'assists': 5, 'cs': 201, 'score': 8.4, 'name': '经典', 'position': '中单',
        'championName': '暗裔剑魔', 'time': '2026-11-26 20:15', 'timeStamp': 1764000000000}

row = GameRow(game, 'today')
got = []
row.clicked.connect(got.append)
QTest.mouseClick(row, Qt.LeftButton, pos=QPoint(80, 19))
check('今日行点击发出信号', len(got) == 1)
texts = [c.text() for c in row.findChildren(QLabel)]
check('今日行显示评分而不是补刀', any('8.4' in t for t in texts), texts)

full = GameRow(game, 'full')
check('完整行固定高 38', full.height() == 38, full.height())

remake = GameRow({**game, 'remake': True}, 'full')
check('重开不崩', remake is not None)

print('Notice')
changes = notice.recentChanges(5)
check('能从 CHANGELOG 解析出条目', len(changes) > 0, len(changes))
check('条目带版本号', bool(changes[0]['version']), changes[0] if changes else None)
check('标题只有三类标签之一或为空',
      all(c['tag'] in ('新增', '优化', '修复', '') for c in changes),
      [c['tag'] for c in changes])

print('RankCard 双栏 meta')
card = RankCard()
card.resize(560, 96)
card.update(['RANKED_SOLO_5x5', '30', '36%', '11', '19',
             '铂金 Ⅲ', '1', '铂金 Ⅰ', '铂金 Ⅳ'])
app.processEvents()
check('左栏是场次与胜负',
      '11 胜 19 负' in card._RankCard__meta.text(), card._RankCard__meta.text())
check('右栏只有最高与上季',
      card._RankCard__metaRight.text().startswith('最高')
      and '上季' in card._RankCard__metaRight.text(),
      card._RankCard__metaRight.text())
check('右栏自带右对齐',
      card._RankCard__metaRight.alignment() & Qt.AlignRight)

print('生涯页页头')
from app.view.career_interface import CareerInterface  # noqa: E402
career = CareerInterface()
career.resize(1200, 900)
app.processEvents()

badge = career.pageHead._PageHead__badge
check('页头挂在布局第一项', career.view.layout().itemAt(0).widget() is career.pageHead)
check('页头高 32', career.pageHead.height() == 32, career.pageHead.height())

career.pageHead.setBadge('本人')
check('badge 能显示', not badge.isHidden() and badge.text() == '本人')
career.pageHead.setBadge('')
check('badge 清空后收起', badge.isHidden())

check('两张段位卡等宽', abs(career.soloCard.width() - career.flexCard.width()) <= 2,
      f'{career.soloCard.width()} / {career.flexCard.width()}')
check('近况带不与段位卡同排',
      career.strip.parent() is not career.soloCard.parent())

print()
if failures:
    print(f'{len(failures)} 项失败: {failures}')
    sys.exit(1)

print('全部通过')
