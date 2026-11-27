# coding:utf-8
"""标题栏配色

qfluentwidgets 的 `TitleBar.paintEvent` 自己画背景：

    painter.setBrush(self.backgroundColor)
    painter.drawRect(self.rect())

而 `backgroundColor` 在 Mica 打开时是 `QColor(0, 0, 0, 0)` —— 完全透明，
透出来的是窗口那一层黑，实测 (0,0,0)。qss 的 `QWidget#titleBar` 也盖不住，
因为自绘在 super() 之后又刷了一遍。

设计稿 `.titlebar` 是 rgba(16,18,21,.72) 叠在 #0A0B0D 上 → 实测 (14,16,18)，
底边还有 1px #1F2329 的分隔线。这里直接把 paintEvent 换掉。

底色/分隔线走 `color()`, 不再写死 —— 浅色主题下原来那条 #0E1012 会变成
一道压在页面顶上的黑边。
"""
from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QPainter

from app.common.design_tokens import color

_installed = False


def _paint(self, event):
    from qframelesswindow.titlebar import TitleBarBase

    TitleBarBase.paintEvent(self, event)

    painter = QPainter(self)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color('bg1')))
    painter.drawRect(self.rect())

    painter.setBrush(QColor(color('line')))
    painter.drawRect(QRect(0, self.height() - 1, self.width(), 1))
    painter.end()


def register(titleBar) -> None:
    """让标题栏跟着主题重绘

    底色是 `paintEvent` 里现读 `color('bg1')` 画的，库里不会因为换主题就重画 ——
    深色切浅色时标题栏会留着那条黑底，直到下一次重绘。
    """
    from app.common import theme
    theme.registerPaintRoot(titleBar)


def install() -> None:
    global _installed
    if _installed:
        return

    from qframelesswindow.titlebar import TitleBar

    TitleBar.paintEvent = _paint
    _installed = True
