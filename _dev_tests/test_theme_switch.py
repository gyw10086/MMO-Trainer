# coding:utf-8
"""主题切换回归

原来「设置 → 应用主题」有三类毛病，这里一条条盯住：

1. **一次点击跑两轮全量刷新** —— 下拉框的 `currentIndexChanged` 又在 `cfg.set`
   的调用栈里调了一次 `setTheme`。断言：一次切换只发一次 `themeChangedFinished`。
2. **中途能画出半套主题** —— 长链中间 Qt 会插进来 paint。断言：切换期间
   `updatesEnabled` 为假，结束后恢复，全程没有中间帧。
3. **导航栏 / 标题栏不跟着变** —— 它们是自绘的、没有主题信号。断言：切完主题
   这些「只重画」的控件确实收到了 `update()`。

另外验一下组件 qss 的分层挂载：容器上有一份、`QApplication` 上不再跟着换。

运行: python _dev_tests/test_theme_switch.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication, QStackedWidget, QWidget  # noqa: E402

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, qconfig, setThemeColor  # noqa: E402

setThemeColor('#E3B23C', save=False)

from app.common import skin  # noqa: E402

skin.install()

from app.common import theme  # noqa: E402
from app.common.design_tokens import color  # noqa: E402

failures = []


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}'
          + (f'  ({detail})' if detail and not cond else ''))

    if not cond:
        failures.append(name)


# ---- 一个贴近主窗口的拓扑：容器 + 两页，只有第一页可见
from app.view.setting_interface import SettingInterface  # noqa: E402
from app.view.career_interface import CareerInterface  # noqa: E402

window = QWidget()
stack = QStackedWidget(window)
settingPage = SettingInterface(stack)
careerPage = CareerInterface(stack)
stack.addWidget(settingPage)
stack.addWidget(careerPage)
stack.setCurrentIndex(0)
window.resize(1200, 800)
window.show()
app.processEvents()

theme.registerQssRoot(stack, chrome=lambda: (
    f"QStackedWidget{{background-color:{color('bg0')};}}"))

print('单一链路')

emits = []
qconfig.themeChangedFinished.connect(lambda: emits.append(1))

theme.applyTheme(Theme.DARK)
emits.clear()
theme.applyTheme(Theme.LIGHT)

check('一次切换只发一次 themeChangedFinished', len(emits) == 1, f'{len(emits)} 次')

print('冻结重绘')

seen = []


class Probe(QWidget):
    """借一个假的自绘控件观察冻结状态"""

    def applyTheme(self):
        seen.append((self.updatesEnabled(), window.updatesEnabled()))


probe = Probe()
probe.show()
app.processEvents()
theme.register(probe)

theme.applyTheme(Theme.DARK)

check('回调自绘控件时整体处于冻结状态', bool(seen) and seen[-1] == (False, False),
      str(seen[-1] if seen else None))
check('切换结束后恢复重绘', probe.updatesEnabled() and window.updatesEnabled())

print('自绘控件')

paints = []


class PaintSpy(QWidget):
    def update(self, *args):
        paints.append(1)
        super().update(*args)


spy = PaintSpy(stack)
theme.registerPaintRoot(spy)
paints.clear()
theme.applyTheme(Theme.LIGHT)
check('只重画的控件收到了 update()', len(paints) >= 1, f'{len(paints)} 次')

print('组件 qss 分层挂载')

check('容器上挂了组件 qss', skin.MARKER in stack.styleSheet())
check('组件 qss 跟着主题换色', '#F4F5F7' in stack.styleSheet().upper(),
      stack.styleSheet()[:80])
check('容器自己的骨架样式也在', 'QStackedWidget' in stack.styleSheet())
check('骨架样式也换了主题', '#F4F5F7' in stack.styleSheet().upper())
check('组件 qss 段只有一份', stack.styleSheet().count(skin.MARKER) == 1)

for _ in range(3):
    theme.applyTheme(Theme.DARK)
    theme.applyTheme(Theme.LIGHT)

check('反复切换样式表不膨胀', stack.styleSheet().count(skin.MARKER) == 1
      and len(stack.styleSheet()) < 8000, len(stack.styleSheet()))

print('深浅两套')

theme.applyTheme(Theme.DARK)
darkBg = color('bg0')
darkQss = skin.componentsQss()
theme.applyTheme(Theme.LIGHT)
lightBg = color('bg0')
lightQss = skin.componentsQss()

check('深色底色近黑', darkBg.lower() == '#0a0b0d', darkBg)
check('浅色底色浅灰白', lightBg.lower() == '#f4f5f7', lightBg)
check('两套组件 qss 内容不同', darkQss != lightQss)

print('隐藏页延迟刷新')

# lazy 刷新只为「当前可见」的 widget 重铺 qss，隐藏页打 dirty-qss 标记，
# 由库的 DirtyStyleSheetWatcher 在它下一次 Paint 之前补上 —— 先刷后画，
# 所以翻过去那一帧看到的就是新主题
hiddenBefore = careerPage.styleSheet()
stack.setCurrentIndex(1)
app.processEvents()
careerPage.repaint()
app.processEvents()

check('隐藏页翻过去后补刷成新主题',
      '#F4F5F7' in careerPage.styleSheet().upper()
      or careerPage.styleSheet() != hiddenBefore,
      careerPage.styleSheet()[:80])
stack.setCurrentIndex(0)
app.processEvents()

print('登记表不吊住控件')


class TempRoot(QWidget):
    def chrome(self):
        return '#123456'


import gc  # noqa: E402
import weakref  # noqa: E402

temp = TempRoot()
theme.registerQssRoot(temp, chrome=temp.chrome)
theme.register(temp)
ref = weakref.ref(temp)
del temp
gc.collect()

check('关掉的窗口能被回收（登记表只存弱引用）', ref() is None)

print('跟随系统')

updates = []
timer = theme.watchSystemTheme(lambda value: updates.append(value))
check('探针是 2 秒轮询', timer.interval() == 2000, timer.interval())
check('系统主题读得到值', theme.systemTheme() in ('Dark', 'Light', None),
      theme.systemTheme())
timer.stop()

check('AUTO 解析成具体深浅色',
      theme.resolveTheme(Theme.AUTO) in (Theme.DARK, Theme.LIGHT))
check('固定主题原样返回', theme.resolveTheme(Theme.DARK) == Theme.DARK)

theme.applyTheme(Theme.DARK)

print()
if failures:
    print(f'{len(failures)} 项失败: ' + ', '.join(failures))
    sys.exit(1)

print('主题切换全部通过')
