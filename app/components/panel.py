# coding:utf-8
"""面板壳：机能风卡片

设计稿里每个区块统一是「[编号] 标题 ──── 右上角操作」+ 内容区。
把编号、标题、右上角插槽固化下来，避免每个页面各写一套 header。

样式用属性选择器 `QFrame[panel="true"]`，不用类名选择器 ——
PyQt 里 Python 子类的 QSS 类名匹配不稳定，属性选择器是可靠的。

`primary=True` 是设计稿的 `.panel.primary`：底色抬一档（#171A1E）、
边框提到 #2C3138，并在左上 / 右下各画一个 9px 的 L 形角标 ——
用来标出「这屏最重要的一块」，每屏最多一块。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.common.design_tokens import color, font, mono

# 角标边长（设计稿 .panel.primary::before 的 9px）
CORNER = 9


class Panel(QFrame):
    """带编号标题的面板

    Parameters
    ----------
    code: 面板编号，如 '01'；留空则不显示（弹窗、二级页里不需要）
    title: 标题文字
    dense: 紧凑模式，标题行 26px 而不是 30px
    primary: 主面板（抬底色 + 四角标）
    """

    def __init__(self, code: str = '', title: str = '', dense: bool = False,
                 primary: bool = False, parent: QWidget = None):
        super().__init__(parent)
        self.setProperty('panel', True)
        self.__primary = primary

        if primary:
            self.setProperty('primary', True)

        self.__header = QWidget(self)
        self.__header.setObjectName('panelHeader')
        self.__header.setFixedHeight(26 if dense else 30)

        self.__codeLabel = QLabel(f'[{code}]', self.__header)
        self.__codeLabel.setObjectName('panelCode')
        self.__codeLabel.setStyleSheet(mono(11))

        self.__titleLabel = QLabel(title, self.__header)
        self.__titleLabel.setObjectName('panelTitle')
        self.__titleLabel.setStyleSheet(font(14, bold=True))

        self.__headerLayout = QHBoxLayout(self.__header)
        self.__headerLayout.setContentsMargins(14, 0, 12, 0)
        self.__headerLayout.setSpacing(8)
        if code:
            self.__headerLayout.addWidget(self.__codeLabel)
        self.__headerLayout.addWidget(self.__titleLabel)
        self.__headerLayout.addStretch(1)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)

        self.__layout = QVBoxLayout(self)
        self.__layout.setContentsMargins(1, 0, 1, 1)
        self.__layout.setSpacing(0)
        self.__layout.addWidget(self.__header)
        self.__layout.addLayout(self.body, 1)

        self.__header.setProperty('panelHeader', True)

    def addAction(self, widget: QWidget) -> QWidget:
        """往标题行右侧塞控件（筛选、页码、刷新…）"""
        self.__headerLayout.addWidget(widget, 0, Qt.AlignVCenter)
        return widget

    def paintEvent(self, event):
        # 底色和 1px 边框由 qss 的 QFrame[panel="true"] 画，这里只补角标
        super().paintEvent(event)

        if not self.__primary:
            return

        painter = QPainter(self)
        painter.setPen(QPen(QColor(color('brandMuted')), 1))
        painter.setBrush(Qt.NoBrush)

        w, h = self.width(), self.height()
        # 左上 L
        painter.drawLine(0, 0, CORNER, 0)
        painter.drawLine(0, 0, 0, CORNER)
        # 右下 L
        painter.drawLine(w - 1 - CORNER, h - 1, w - 1, h - 1)
        painter.drawLine(w - 1, h - 1, w - 1, h - 1 - CORNER)

    def addActionSpacing(self, px: int = 6):
        self.__headerLayout.addSpacing(px)

    def setTitle(self, text: str):
        self.__titleLabel.setText(text)

    def setCode(self, code: str):
        self.__codeLabel.setText(f'[{code}]')

    def header(self) -> QWidget:
        return self.__header
