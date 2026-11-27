# coding:utf-8
"""状态圆点

设计稿 `.dot`：6×6 实心圆，绿色=已连接、金色=进行中、红色=断开。
QSS 的 `border-radius` 被全局去圆角改写成 0（这个项目的机能风一律方角），
圆点只能用 QPainter 画，否则会变成一个小方块。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

from app.common.design_tokens import color


class StatusDot(QWidget):
    def __init__(self, tone: str = 'win', size: int = 6, parent=None):
        super().__init__(parent)
        self.__tone = tone
        self.__size = size
        self.setFixedSize(size, size)
        # 纯装饰，别抢走父级的点击
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def setTone(self, tone: str):
        if tone != self.__tone:
            self.__tone = tone
            self.update()

    def tone(self) -> str:
        return self.__tone

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color(self.__tone)))
        # 减 1 像素，避免抗锯齿把右下角裁掉一半
        painter.drawEllipse(self.rect().adjusted(0, 0, -1, -1))
        painter.end()
