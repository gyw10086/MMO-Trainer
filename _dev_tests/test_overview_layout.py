# coding:utf-8
"""总览页版面与 [02] 版本的回归

不连 LCU、不连外网：公告数据用假数据替换，只验证排版与组件契约。
运行: python _dev_tests/test_overview_layout.py
"""
import os
import sys
import time
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme, setThemeColor  # noqa: E402

setTheme(Theme.DARK, save=False)
setThemeColor('#E3B23C', save=False)

from app.common import skin  # noqa: E402
from app.common.design_tokens import color  # noqa: E402

skin.install()

from app.view import overview_interface as OV  # noqa: E402

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


FAKE = {
    'version': '26.23',
    'date': '2026-11-18',
    'url': 'https://lol.qq.com/gicp/news/410/x.html',
    'fetchedAt': 0,
    'stale': False,
    'items': [
        {'kind': '英雄', 'name': '安妮',
         'title': '安妮：R 提伯斯之怒 · 提伯斯生命值 1150 → 1275',
         'date': '2026-11-18', 'url': 'https://x'},
        {'kind': '装备', 'name': '电震涡流剑',
         'title': '电震涡流剑：唯一被动-苍穹 · 额外伤害 9% → 8%',
         'date': '2026-11-18', 'url': 'https://x'},
        {'kind': '系统', 'name': '经典模式',
         'title': '经典模式：新增 泰隆、乌迪尔',
         'date': '2026-11-18', 'url': 'https://x'},
    ] * 5,
}


def build(width: int, height: int):
    with mock.patch.object(OV, 'loadPatchNotes', return_value=FAKE), \
            mock.patch.object(OV, 'readCache', return_value=dict(FAKE)):
        view = OV.OverviewInterface()
        view.resize(width, height)
        view.show()
        app.processEvents()
    return view


print('[02] 面板高度')
for width, height, low, high in ((980, 700, 4, 7), (1080, 800, 4, 7), (1427, 900, 4, 7)):
    view = build(width, height)
    rows = view._OverviewInterface__noticeRows
    panel = view._OverviewInterface__noticePanel
    check(f'{width}x{height} 行数 {rows} 在 [{low},{high}]', low <= rows <= high)
    check(f'{width}x{height} 面板高 = 34 + {rows}*44 + 2',
          panel.height() == 34 + rows * 44 + 2, panel.height())

    today = view._OverviewInterface__todayPanel
    check(f'{width}x{height} 今日对战不低于 150', today.height() >= 150, today.height())
    view.close()

print('\n改动行')
view = build(1280, 860)
box = view._OverviewInterface__noticeBox
rows = [box.itemAt(i).widget() for i in range(box.count())]
rows = [r for r in rows if isinstance(r, OV.PatchNewsRow)]
check('渲染出条目', len(rows) >= 2, len(rows))
check('每行 44px', all(r.height() == 44 for r in rows), [r.height() for r in rows])
check('首行标题非空', bool(rows[0]._PatchNewsRow__title.fullText()))
check('首行来源固定 lol.qq.com', rows[0]._PatchNewsRow__source.text() == 'lol.qq.com')
check('末行无分隔线', rows[-1].property('patchRowLast') is True)
if len(rows) > 1:
    check('中间行有分隔线', rows[0].property('patchRowLast') is False)

print('\n头部角标')
version = view._OverviewInterface__patchVersion
more = view._OverviewInterface__patchMore
check('版本角标带版本号', '26.23' in version.text(), version.text())
check('全部按钮带条数', '15' in more.text(), more.text())
check('有数据时全部按钮可见', more.isVisible())

print('\n过期远端角标')
notice = view._OverviewInterface__noticePanel
old = int(time.time()) - 30 * 86400
view._OverviewInterface__onPatchLoaded(
    dict(FAKE, source='remote', fetchedAt=old, stale=True, staleReason='outdated'))
app.processEvents()
check('角标标出「陈旧」', '陈旧' in version.text(), version.text())
check('角标仍是 18 高', version.height() == 18, version.height())
check('角标没撑破面板头', version.width() < notice.width(),
      f'{version.width()} vs {notice.width()}')
# 不能写死色值：这套断言跑在 cfg.themeMode 决定的主题下，
# 深色的 red 是 #E05A4F、浅色是 #C0433A，写死一个就有一半场景误报
check('陈旧时换警示色', color('red').upper() in version.styleSheet().upper(),
      version.styleSheet()[:60])

view._OverviewInterface__onPatchLoaded(
    dict(FAKE, source='remote', fetchedAt=int(time.time()),
         stale=False, staleReason=''))
app.processEvents()
check('数据新鲜时不带陈旧字样', '陈旧' not in version.text(), version.text())

print('\n空态')
with mock.patch.object(OV, 'loadPatchNotes', side_effect=OV.PatchNotesUnavailable('offline')), \
        mock.patch.object(OV, 'readCache', return_value=None):
    empty = OV.OverviewInterface()
    empty.resize(1280, 860)
    empty.show()
    app.processEvents()
    # 等后台线程失败回落
    for _ in range(30):
        app.processEvents()
        if empty._OverviewInterface__patchError:
            break
    empty._OverviewInterface__renderPatchNotes()

    box = empty._OverviewInterface__noticeBox
    texts = []
    for i in range(box.count()):
        widget = box.itemAt(i).widget()
        if widget:
            texts += [label.text() for label in widget.findChildren(OV.QLabel)]
    check('空态给出可读文案', any('连不上官方公告' in t for t in texts), texts)
    check('空态隐藏全部按钮', not empty._OverviewInterface__patchMore.isVisible())
    empty.close()

print('\n布局不重叠')
view = build(1280, 860)
statusBar = view._OverviewInterface__statusBar
career = view._OverviewInterface__careerPanel
notice = view._OverviewInterface__noticePanel
today = view._OverviewInterface__todayPanel
check('状态条 34', statusBar.height() == 34, statusBar.height())
check('生涯块 92~126', 92 <= career.height() <= 126, career.height())
check('三块加起来不超过视口',
      statusBar.height() + career.height() + notice.height() + today.height()
      <= view.viewport().height(), view.viewport().height())
view.close()

print('\n状态条（S0）')
view = build(1280, 860)
bar = view._OverviewInterface__statusBar
texts = [label.text() for label in bar.findChildren(OV.QLabel)]
for key in ('客户端', '账号', '对局', '对局结束自动刷新'):
    check(f'状态条含「{key}」', key in texts, texts[:12])
check('自动跟随角标跟着配置',
      '自动跟随' in view._OverviewInterface__autoBadge.text(),
      view._OverviewInterface__autoBadge.text())
refresh = view._OverviewInterface__refreshBtn
check('刷新按钮 22x22', (refresh.width(), refresh.height()) == (22, 22),
      (refresh.width(), refresh.height()))
check('状态条不再塞「查询战绩 / 设置」文字按钮',
      '查询战绩' not in texts and '设置' not in texts, texts)

print('\n01 生涯指标')
cell = view._OverviewInterface__statRecent
label = cell.findChild(OV.QLabel, 'statLabel')
value = cell.findChild(OV.QLabel, 'statValue')
check('标签在数值上方（设计稿 .metric）', label.y() < value.y(),
      (label.y(), value.y()))
check('数值 18px 等宽', 'font-size: 18px' in value.styleSheet(),
      value.styleSheet())
recent = view._OverviewInterface__recentText(4, 5)
check('胜负文案是 4胜 5负', '4' in recent and '胜' in recent and '负' in recent,
      recent)

print('\n03 今日对战角标')
check('胜场角标以「胜」结尾',
      view._OverviewInterface__todayWin.text().endswith('胜'),
      view._OverviewInterface__todayWin.text())
check('负场角标以「负」结尾',
      view._OverviewInterface__todayLose.text().endswith('负'),
      view._OverviewInterface__todayLose.text())

print('\n段位徽章代号')
from app.components.rank_card import rankCode  # noqa: E402
check('钻石 Ⅱ -> D2', rankCode('钻石', 'Ⅱ') == 'D2', rankCode('钻石', 'Ⅱ'))
check('大师 -> M', rankCode('大师', '★') == 'M', rankCode('大师', '★'))
check('未定级不炸', rankCode('未定级', '') == '—', rankCode('未定级', ''))
view.close()

print('\n01 双段位')
view = build(1280, 860)
view.updateInfo({
    'name': '测试', 'tagLine': '0001',
    'games': {'games': []}, 'champions': [],
    'rankTable': [
        ['单双排', '118', '56 %', '62', '56', '璀璨钻石 II', '78',
         '华贵铂金 I', '不屈白银 III'],
        ['灵活组排', '20', '50 %', '10', '10', '翡翠 II', '9', '翡翠 II', '--'],
    ],
})
app.processEvents()
soloTexts = [label.text() for label in
             view._OverviewInterface__rankSolo.findChildren(OV.QLabel)]
flexTexts = [label.text() for label in
             view._OverviewInterface__rankFlex.findChildren(OV.QLabel)]
check('单双排行显示 D2', 'D2' in soloTexts, soloTexts)
check('单双排行带队列与 LP',
      any('单双排' in t and '78 LP' in t for t in soloTexts), soloTexts)
check('灵活行显示翡翠', any('翡翠' in t for t in flexTexts), flexTexts)
check('灵活行带队列与 LP',
      any('灵活组排' in t and '9 LP' in t for t in flexTexts), flexTexts)
check('两行都在', view._OverviewInterface__rankFlex.isVisible())
view.close()

print('\n[04] 行数跟着面板高度')
for width, height in ((1427, 881), (1080, 760)):
    view = build(width, height)
    app.processEvents()
    panel = view._OverviewInterface__recordsPanel
    pager = view._OverviewInterface__recordsPager
    slack = (panel.height() - 31 - 20 - pager.height()
             - pager.perPage() * 38)
    check(f'{width}x{height} 每页 {pager.perPage()} 行不溢出', slack >= 0, slack)
    check(f'{width}x{height} 余量小于一行', slack < 38, slack)
    view.close()

print()
if FAILURES:
    print(f'{len(FAILURES)} 项失败: {FAILURES}')
    sys.exit(1)
print('全部通过')
