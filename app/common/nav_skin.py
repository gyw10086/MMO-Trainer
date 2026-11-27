# coding:utf-8
"""左侧导航的机能风绘制

qfluentwidgets 的导航项是自绘的：选中态是一层 6%~10% 白的圆角块，叠在
#0A0B0D 上只有 #141517，和设计稿的 #262C33 差一档；圆角 5px 也不是机能风。
库的 qss 只管容器，选中态写死在 paintEvent 里，只能接过来重画。

设计稿实测值（design/shot-s0.png）：
    容器底色   #0A0B0D   （与页面同色，右侧 1px #1F2329 分割线）
    悬停       #1F2329
    选中       #262C33   + 左侧 2×22 强调条 #E3B23C（上下各内缩 8）
    文字       #9BA3AD   → 选中 / 悬停 #EAECEF
    分割线     1px #1F2329，左右各内缩 12

## 为什么每一项都要自己铺满底色（别退回"不画就是透明"）

`main_window.__fixNavigationPaint()` 给导航项开了 `autoFillBackground(True)`，
那一层是 Qt 用 `palette().Window` 画的 —— 于是导航项**并不是透明的**，
它背后永远有一块由调色板决定的实底。

而调色板是一次性快照：`QColor(color('bg0'))` 在建窗口那一刻取值，写死进
`QPalette`。更要命的是这个快照活不过一次主题切换 ——
`applyTheme()` 里的 `updateStyleSheet()` 会让库把 `FluentStyleSheet.NAVIGATION_INTERFACE`
重新挂到 `NavigationPanel` 上，Qt 重抛光整棵子树时把子控件的 `QPalette`
重新解析回 `QStyle::standardPalette()`（浅色 #F0F0F0）。

实测（离屏，抓像素）：

    刚构造            palette.Window = #efefef  （系统默认）
    __fixNavigationPaint 之后            #0a0b0d  （我们的深色）
    applyTheme(LIGHT) 之后               #efefef  ← 被 updateStyleSheet 打回
    applyTheme(DARK)  之后的像素          #efefef  ← 整块浅灰，就是用户看到的那条

所以「项这一层没跟着切」不是没重绘（父容器 `update()` 会连带子控件），
而是**重绘时拿到的底色本来就没跟着主题变**。修法：
`_paint_button` / `_paint_separator` 自己用实时 `color()` 铺满整块，
不再依赖 `QPalette`；`register()` 再把调色板按当前主题重算一次，
兜住那些不走本模块 paintEvent 的 `NavigationWidget` 子类（树形容器、自定义项）。
"""
import weakref

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QCursor, QPainter, QPalette, QPen

from app.common.design_tokens import color

# 登记过的导航容器：切主题时要重铺它们的项调色板
_interfaces = weakref.WeakSet()


def _paint_base(painter, rect) -> None:
    """铺满容器底色

    必须不透明：导航项的 `autoFillBackground` 会先垫一层 `QPalette.Window`，
    那层在主题切换后是系统浅灰（见模块头）。这里用实时 `color()` 覆盖掉它。
    """
    painter.fillRect(rect, QColor(color('bg0')))


def _paint_button(self, e):
    """导航项：不透明容器底色 + 方角选中块 + 左侧强调条（替换库里的 6% 白圆角块）"""
    from qfluentwidgets.common.icon import drawIcon

    painter = QPainter(self)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                           | QPainter.SmoothPixmapTransform)
    painter.setPen(Qt.NoPen)

    # 先按满不透明度铺底，再进 pressed / disabled 的降透明：
    # 顺序反了的话按下时底色也变半透明，又要把底层调色板露出来
    _paint_base(painter, self.rect())

    if self.isPressed:
        painter.setOpacity(0.7)
    if not self.isEnabled():
        painter.setOpacity(0.4)

    # 库用全局坐标判断鼠标是否真的在控件上，避免 leaveEvent 漏掉时留下悬停块
    hovered = (self.isEnter and self.isEnabled()
               and QRect(self.mapToGlobal(QPoint(0, 0)),
                         self.size()).contains(QCursor.pos()))

    if self.isSelected:
        painter.fillRect(self.rect(), QColor(color('bg3')))
        # 设计稿：2px 宽、上下各缩 8px，方角
        painter.fillRect(QRect(0, 8, 2, max(0, self.height() - 16)),
                         QColor(color('brand')))
    elif hovered:
        painter.fillRect(self.rect(), QColor(color('line')))

    drawIcon(self._icon, painter,
             QRectF(11.5, (self.height() - 16) / 2, 16, 16))

    if self.isCompacted:
        return

    painter.setFont(self.font())
    painter.setPen(QColor(color('text') if (self.isSelected or hovered)
                          else color('textSub')))
    painter.drawText(QRectF(44, 0, self.width() - 13 - 44, self.height()),
                     Qt.AlignVCenter, self.text())


def _paint_separator(self, e):
    """分组分割线：1px #1F2329，左右内缩 12（库是 15% 白、通栏）

    控件高 3px，只画第 2 行的话上下会各留 1px 的调色板垫底（浅灰），
    同样要自己铺满。
    """
    painter = QPainter(self)
    _paint_base(painter, self.rect())

    pen = QPen(QColor(color('line')))
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.drawLine(12, 1, max(12, self.width() - 12), 1)


def _paint_interface(self, e):
    """导航容器：铺 #0A0B0D + 右侧 1px #1F2329

    NavigationInterface 自带 WA_TranslucentBackground，qss 的 background-color
    对它不生效（父窗口的规则也传不下来），只能自己画。
    """
    painter = QPainter(self)
    _paint_base(painter, self.rect())

    w = self.width()
    pen = QPen(QColor(color('line')))
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.drawLine(w - 1, 0, w - 1, self.height() - 1)


def fixItemBackground(interface) -> None:
    """给导航项铺一层不透明底色

    qfluentwidgets 的导航项是自绘的：选中时画一层 4% 白的圆角块，
    但它没先清底色。Windows 上的后备缓冲是白的，4% 白叠在白底上
    就是纯白一块，文字也跟着看不见（对局信息页选中项就是这样）。
    打开 autoFillBackground 让 Qt 先用调色板 Window 铺一遍，再自绘。

    **调色板是一次性快照**：`QPalette` 在切主题时会被
    `updateStyleSheet()` 的重抛光打回系统默认浅灰，所以这个函数必须在
    每次切主题后重跑一次（见 `register`）—— 不能只在这里调一次。
    """
    from qfluentwidgets.components.navigation.navigation_widget import (
        NavigationWidget)

    background = QColor(color('bg0'))

    for item in interface.findChildren(NavigationWidget):
        palette = item.palette()
        palette.setColor(QPalette.Window, background)
        item.setPalette(palette)
        item.setAutoFillBackground(True)


def _syncBaseColors() -> None:
    """把登记过的导航容器按当前主题重铺一遍项调色板

    做成模块级函数（不是 lambda / 闭包）是有意的：`theme` 那边把它当弱回调
    存着，闭包捕获 interface 会形成「值持有键」的循环，控件就回收不掉了。
    """
    for interface in list(_interfaces):
        try:
            fixItemBackground(interface)
        except RuntimeError:
            # C++ 对象已经销毁，弱集里还留着一个壳
            pass


def register(interface) -> None:
    """让导航跟着主题重绘

    导航项的选中/悬停底色是 `paintEvent` 里现读 `color()` 画出来的，库里没有
    任何主题信号 —— 不补一次 `update()`，切完主题它会一直停在旧色，直到鼠标
    划过或者切页才"突然变过来"。这就是「点了以后过一会儿才变色」的一半原因。

    只 `update()` 不够：`autoFillBackground` 垫的那层用的是 `QPalette`，
    它在切主题时会被库的重抛光打回系统浅灰（见模块头）。所以额外挂一个
    刷新钩子，在重绘之前把调色板按当前主题重算一遍。
    """
    from app.common import theme

    _interfaces.add(interface)
    fixItemBackground(interface)
    theme.registerPaintRoot(interface, _syncBaseColors)


def install() -> None:
    """进程级安装，必须在建窗口之前调用"""
    from qfluentwidgets.components.navigation.navigation_interface import (
        NavigationInterface)
    from qfluentwidgets.components.navigation.navigation_widget import (
        NavigationPushButton, NavigationSeparator)

    NavigationInterface.paintEvent = _paint_interface
    NavigationPushButton.paintEvent = _paint_button
    NavigationSeparator.paintEvent = _paint_separator
