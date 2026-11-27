# coding:utf-8
"""左侧导航跟随主题切换

用户实拍的问题：从浅色切到深色后，标题栏 / 内容区 / 导航**容器**都变深了，
**每一个导航项**还是一条浅灰白横条（#F0F0F0），项与项之间才露出深色缝隙。

根因是「项这一层」的底色有两个来源，只有一个是活的：

1. `paintEvent` 自绘（`nav_skin._paint_button`）—— 现读 `color()`，一定跟得上；
2. `autoFillBackground` 垫的那层 `QPalette.Window` —— 这是**一次性快照**，
   而且 `theme.applyTheme()` 里的 `updateStyleSheet()` 会让库把
   `FluentStyleSheet.NAVIGATION_INTERFACE` 重新挂到 `NavigationPanel` 上，
   Qt 重抛光整棵子树时会把子控件的 `QPalette` 解析回
   `QStyle::standardPalette()`（浅色 #F0F0F0）—— 于是深色主题下整块发白。

相应地，修法是给 `paintEvent` 补上不透明底色（不再依赖调色板），
并让 `nav_skin.register()` 挂一个「重画之前重铺调色板」的钩子兜住
不走本模块 paintEvent 的 `NavigationWidget` 子类。

这里锁住四件事：

* 深浅两套下，未选中项的像素都等于当前主题的 bg0（不是写死的浅灰）
* 容器 / 项 / 分割线用同一套调色板 —— 整张导航图里不出现系统默认浅灰
* 双向切换都成立（LIGHT → DARK → LIGHT → DARK）
* 调色板层也跟着主题重算，且钩子在 `update()` 之前跑

运行: python _dev_tests/test_nav_theme.py
"""
import gc
import os
import sys
import weakref

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import Qt                                      # noqa: E402
from PyQt5.QtGui import QPalette                                 # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget                # noqa: E402

app = QApplication(sys.argv)

from app.common.qfluentwidgets import (                           # noqa: E402
    Theme, setThemeColor, NavigationInterface, NavigationItemPosition,
    FluentIcon)

setThemeColor('#E3B23C', save=False)

from app.common import skin                                       # noqa: E402

skin.install()

from app.common import nav_skin, theme                            # noqa: E402
from app.common.design_tokens import color                        # noqa: E402
from app.components.avatar_widget import NavigationAvatarWidget    # noqa: E402
from qfluentwidgets.components.navigation.navigation_widget import (  # noqa: E402
    NavigationWidget, NavigationTreeItem)
from qfluentwidgets.components.navigation.navigation_interface import (  # noqa: E402
    NavigationInterface as _NavInterface)

failures = []


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}'
          + (f'  ({detail})' if detail and not cond else ''))

    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 拓扑
# 照 MainWindow.__initNavigation 搭：SCROLL 里 5 项、BOTTOM 里 3 项 + 头像卡、
# 两条分割线，边距 0/0/1/0（右侧 1px 留给容器的分割线）
def buildNav():
    host = QWidget()
    host.resize(900, 700)

    nav = NavigationInterface(host, showMenuButton=False,
                              showReturnButton=False, collapsible=False)
    nav.setObjectName('navigationInterface')
    nav.setAttribute(Qt.WA_TranslucentBackground, False)
    nav.setAttribute(Qt.WA_StyledBackground, True)
    nav.setExpandWidth(186)
    nav.setMinimumExpandWidth(0)
    nav.move(0, 0)
    nav.resize(186, 700)

    nav.addSeparator(NavigationItemPosition.TOP)
    for key, icon, text in (
            ('overview', FluentIcon.HOME, '总览'),
            ('gameinfo', FluentIcon.GAME, '对局信息'),
            ('career', FluentIcon.PEOPLE, '生涯'),
            ('search', FluentIcon.SEARCH, '战绩查询'),
            ('aux', FluentIcon.DEVELOPER_TOOLS, '其他功能')):
        nav.addItem(routeKey=key, icon=icon, text=text,
                    selectable=True, position=NavigationItemPosition.SCROLL)

    bottom = NavigationItemPosition.BOTTOM
    for key, text in (('opgg', 'OP.GG'), ('fix', '回到房间'),
                      ('notice', '公告')):
        nav.addItem(routeKey=key, icon=FluentIcon.GLOBE, text=text,
                    onClick=lambda *_: None, selectable=False,
                    position=bottom)

    nav.insertSeparator(3, bottom)

    avatar = NavigationAvatarWidget(
        avatar='app/resource/images/game.png', name='示例召唤师')
    nav.addWidget(routeKey='avatar', widget=avatar, position=bottom)

    for layout in (nav.panel.scrollLayout, nav.panel.bottomLayout):
        layout.setContentsMargins(0, 0, 1, 0)

    # 主窗口里的 __fixNavigationPaint
    nav_skin.fixItemBackground(nav)
    nav_skin.register(nav)

    host.show()
    nav.panel.expand(useAni=False)
    app.processEvents()
    nav.setCurrentItem('overview')
    app.processEvents()
    return host, nav, avatar


def settle(times=8):
    for _ in range(times):
        app.processEvents()


def itemProbes(nav):
    """每个可见导航项的取样点

    x=6 落在图标（11.5~27.5）左边、选中强调条（0~2）右边，不会读到文字或图标；
    y 取中线。这样拿到的就是「项这一层的底色」本身。
    """
    out = []

    for item in nav.findChildren(NavigationWidget):
        if not item.isVisible() or item.width() < 40 or item.height() < 20:
            continue

        point = item.mapTo(nav, item.rect().topLeft())
        out.append((item, point.x() + 6, point.y() + item.height() // 2))

    return out


def leftStripe(img, x0=4, x1=9):
    """导航项里「纯底色」的一条窄带

    避开了左侧强调条（0~2）和图标（11.5~27.5），也避开了文字 ——
    这一带每一行都应该是底色（选中项是 bg3，其余是 bg0，分割线行是 line），
    出现别的颜色就说明项这一层垫了别的东西（比如 QPalette 的系统浅灰）。
    """
    seen = set()

    for y in range(img.height()):
        for x in range(x0, x1):
            seen.add(img.pixelColor(x, y).name().lower())

    return seen


def dominant(img, step=3):
    counts = {}

    for y in range(0, img.height(), step):
        for x in range(0, img.width(), step):
            name = img.pixelColor(x, y).name().lower()
            counts[name] = counts.get(name, 0) + 1

    return max(counts.items(), key=lambda kv: kv[1])[0]


host, nav, avatar = buildNav()
settle()

# ---------------------------------------------------------------- 深浅两套
print('导航项跟着主题走')

BAD_GRAYS = {'#f0f0f0', '#efefef'}   # QStyle::standardPalette 的浅色 Window
snapshot = {}

for tag, target in (('LIGHT', Theme.LIGHT), ('DARK', Theme.DARK),
                    ('LIGHT-again', Theme.LIGHT), ('DARK-again', Theme.DARK)):
    theme.applyTheme(target)
    settle()
    img = nav.grab().toImage()
    bg0 = color('bg0').lower()
    bg3 = color('bg3').lower()
    line = color('line').lower()

    rows = []
    for item, x, y in itemProbes(nav):
        rows.append((item, img.pixelColor(x, y).name().lower()))

    stripe = leftStripe(img)
    snapshot[tag] = (bg0, bg3, rows, stripe, dominant(img), img)

    unselected = [(i, px) for i, px in rows if not i.isSelected]
    selected = [(i, px) for i, px in rows if i.isSelected]

    check(f'{tag}: 采到了导航项', len(rows) >= 8, f'{len(rows)} 个')
    check(f'{tag}: 选中项存在（容器和项都进了采集）', len(selected) >= 1,
          f'{len(selected)} 个')
    check(f'{tag}: 未选中项底色 = 当前主题 bg0',
          bool(unselected) and all(px == bg0 for _, px in unselected),
          str([px for _, px in unselected]))
    check(f'{tag}: 选中项底色 = 当前主题 bg3',
          bool(selected) and all(px == bg3 for _, px in selected),
          str([px for _, px in selected]))
    check(f'{tag}: 整张导航图的主色是 bg0', dominant(img) == bg0, dominant(img))
    check(f'{tag}: 窄带里只有 bg0 / bg3 / line（没垫系统浅灰）',
          stripe <= {bg0, bg3, line}, str(sorted(stripe - {bg0, bg3, line})))
    check(f'{tag}: 系统默认浅灰彻底不出现（用户看到的那条 #F0F0F0）',
          not (stripe & BAD_GRAYS), str(sorted(stripe & BAD_GRAYS)))

light = snapshot['LIGHT']
dark = snapshot['DARK']

check('深浅两套的 bg0 不同', light[0] != dark[0], f'{light[0]} / {dark[0]}')
check('浅色切深色：导航项底色确实变了',
      [px for _, px in light[2]] != [px for _, px in dark[2]],
      f'{light[2][0][1]} -> {dark[2][0][1]}')
check('切回浅色后与第一次浅色逐项一致',
      [px for _, px in light[2]] == [px for _, px in snapshot['LIGHT-again'][2]])
check('再切深色后与第一次深色逐项一致',
      [px for _, px in dark[2]] == [px for _, px in snapshot['DARK-again'][2]])

print('容器和子控件用同一套调色板')

theme.applyTheme(Theme.DARK)
settle()
img = nav.grab().toImage()

separator = nav.findChildren(
    __import__('qfluentwidgets.components.navigation.navigation_widget',
               fromlist=['NavigationSeparator']).NavigationSeparator)
check('分割线控件存在', len(separator) >= 1, f'{len(separator)} 个')

sepBad = []
for sep in separator:
    origin = sep.mapTo(nav, sep.rect().topLeft())

    for row in range(sep.height()):
        px = img.pixelColor(origin.x() + 40, origin.y() + row).name().lower()
        if px not in {color('bg0').lower(), color('line').lower()}:
            sepBad.append((row, px))

check('分割线 3px 每一行都是 bg0 / line（不露调色板垫底）',
      not sepBad, str(sepBad[:6]))

check('分割线右侧 1px 是 line', img.pixelColor(nav.width() - 1, 4).name().lower()
      == color('line').lower(), img.pixelColor(nav.width() - 1, 4).name())

print('调色板层也跟上（兜底 NavigationWidget 子类）')

item = nav.findChildren(NavigationTreeItem)[0]
check('切主题后 QPalette.Window = 当前主题 bg0',
      item.palette().color(QPalette.Window).name().lower() == color('bg0').lower(),
      item.palette().color(QPalette.Window).name())
check('autoFillBackground 仍然开着（后备缓冲兜底没丢）',
      item.autoFillBackground())

theme.applyTheme(Theme.LIGHT)
settle()
check('切回浅色后 QPalette.Window 也跟着回去',
      item.palette().color(QPalette.Window).name().lower() == color('bg0').lower(),
      item.palette().color(QPalette.Window).name())

print('刷新钩子')

order = []


class HookSpy(QWidget):
    def applyTheme(self):
        pass

    def update(self, *args):
        order.append('update')
        super().update(*args)


spy = HookSpy()
spy.show()
settle()
theme.registerPaintRoot(spy, lambda: order.append('hook'))
theme.applyTheme(Theme.DARK)

check('登记时带的补丁跑过了', 'hook' in order, str(order))
check('补丁在 update() 之前跑', order[:2] == ['hook', 'update']
      or 'hook' in order[:order.index('update') + 1], str(order))

order.clear()
theme.applyTheme(Theme.LIGHT)
check('每次切换都重跑补丁', 'hook' in order, str(order))

# 不传 refresh 的老用法不能坏（标题栏 / 既有测试都这么调）
plain = HookSpy()
plain.show()
settle()
theme.registerPaintRoot(plain)
order.clear()
theme.applyTheme(Theme.DARK)
check('registerPaintRoot 单参数仍可用', 'update' in order, str(order))

print('走用户真实入口（设置 → 窗口与外观 → 应用主题）')

# 用户不是直接调 `applyTheme()` 的：他点下拉框，
# `ComboBoxSettingCard` 内部 `cfg.set(cfg.themeMode)` → `valueChanged`
# → `QTimer.singleShot(0, __applyThemeNow)` → `theme.applyTheme(...)`。
# 这条链上任何一环断了，前面的断言全都白搭（延后一拍那步尤其容易丢）。
#
# `cfg.set` 全程强制 `save=False`：这个测试不许碰用户真实的 config.json。
from qfluentwidgets.common.config import QConfig                # noqa: E402
from app.common.config import cfg, LOCAL_PATH                    # noqa: E402

configPath = os.path.join(LOCAL_PATH, 'config.json')
mtimeBefore = os.path.getmtime(configPath) if os.path.exists(configPath) else None

_originalSet = QConfig.set


def _noSaveSet(self, item, value, save=True, copy=True):
    return _originalSet(self, item, value, save=False, copy=copy)


modeBefore = cfg.get(cfg.themeMode)
QConfig.set = _noSaveSet

try:
    from app.view.setting_interface import SettingInterface      # noqa: E402

    settingsHost = QWidget()
    settingPage = SettingInterface(settingsHost)
    settingsHost.hide()

    combo = settingPage.themeCard.comboBox
    labels = [combo.itemText(i) for i in range(combo.count())]

    def pickTheme(text):
        combo.setCurrentIndex(labels.index(text))

        for _ in range(20):
            app.processEvents()

    def navColors():
        frame = nav.grab().toImage()
        out = []

        for one, x, y in itemProbes(nav):
            out.append(frame.pixelColor(x, y).name().lower())

        return out

    pickTheme('深色')
    darkByCombo = navColors()
    check('下拉框选「深色」：导航项全部变成深色 bg0',
          all(px in (color('bg0').lower(), color('bg3').lower())
              for px in darkByCombo), str(darkByCombo))
    check('下拉框选「深色」：确实没有浅灰残留（用户看到的那条）',
          not (set(darkByCombo) & BAD_GRAYS), str(sorted(set(darkByCombo) & BAD_GRAYS)))

    pickTheme('浅色')
    lightByCombo = navColors()
    check('下拉框选「浅色」：导航项回到浅色 bg0',
          all(px in (color('bg0').lower(), color('bg3').lower())
              for px in lightByCombo), str(lightByCombo))
    check('下拉框双向切换颜色真的不同',
          set(darkByCombo) != set(lightByCombo),
          f'{set(darkByCombo)} / {set(lightByCombo)}')

    pickTheme('深色')
    check('下拉框反复切换结果稳定', navColors() == darkByCombo)
finally:
    QConfig.set = _originalSet
    # 只在内存里还原，同样不落盘
    _originalSet(cfg, cfg.themeMode, modeBefore, save=False)
    theme.applyTheme(modeBefore)

check('用户真实的 config.json 一个字节都没被写过',
      mtimeBefore == (os.path.getmtime(configPath)
                      if os.path.exists(configPath) else None),
      f'{configPath}')

print('登记表不吊住控件')

# 钩子必须挂在弱表里，而且不能是捕获了容器的闭包 ——
# 「值持有键」会让 WeakKeyDictionary 的键永远死不掉，等于泄漏
hook = theme._paintHooks.get(nav)

check('容器挂上了补丁', hook is not None)
check('补丁是模块级函数，不带闭包', hook is nav_skin._syncBaseColors
      and getattr(hook, '__closure__', None) is None, repr(hook))
check('nav_skin 的容器表是 WeakSet',
      isinstance(nav_skin._interfaces, weakref.WeakSet))
check('theme 的钩子表是 WeakKeyDictionary',
      isinstance(theme._paintHooks, weakref.WeakKeyDictionary))

probe = QWidget()
theme.registerPaintRoot(probe, lambda: None)
probeRef = weakref.ref(probe)
del probe
gc.collect()

check('钩子表不吊住控件', probeRef() is None)

print()
if failures:
    print(f'{len(failures)} 项失败: ' + ', '.join(failures))
    sys.exit(1)

print(f'导航主题 {len(failures) == 0 and "全部通过" or "有失败"}')
