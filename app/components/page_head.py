# coding:utf-8
"""页面头部：面包屑 + 标题 + SEC 编号

设计稿的 `.page-head` 是一条 32px 高的横排，面包屑和标题**同一行**，
不额外占高度：

    [总览 ›] [设置 ›]  常规   SEC-01

- 面包屑前级 11px 弱灰、可点，悬停变黄并出现下划线
- 分隔符是 5×5 旋转 45° 的 L 形，不是「›」字符
- 标题 18px / 600
- SEC 编号 11px 等宽，跟在标题后面 10px

只在设置页 / 其他功能页用（这两页的标题原本是 `move(36, 30)` 的独立
QLabel）；生涯页顶部是身份条、查询页没有标题，等结构统一后再接。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.common import theme
from app.common.design_tokens import color, font, mono

HEAD_H = 32


class Caret(QWidget):
    """面包屑分隔符：5×5 的 L 形旋转 45°"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(5, 5)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(color('lineStrong')), 1))
        painter.drawLine(2, 0, 2, 4)
        painter.drawLine(2, 4, 4, 2)


class CrumbLabel(QLabel):
    """面包屑里的一级，可点"""

    clicked = pyqtSignal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self.applyTheme()
        # 悬停态要按当前主题重拼，交给统一登记表比各自 connect 稳
        theme.register(self)

    def applyTheme(self):
        """按当前主题重设样式（含悬停态）"""
        if self._hover:
            self.setStyleSheet(
                f'color: {color("brand")}; {font(11)}'
                f' border: none; background: transparent; padding: 0;'
                f' border-bottom: 1px solid {color("brandMuted")};')
        else:
            self.setStyleSheet(
                f'color: {color("textMuted")}; {font(11)}'
                f' border: none; background: transparent; padding: 0;')

    def enterEvent(self, event):
        self._hover = True
        self.applyTheme()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.applyTheme()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.text())
        super().mouseReleaseEvent(event)


class PageHead(QWidget):
    """面包屑 + 标题 + SEC 编号"""

    crumbClicked = pyqtSignal(str)

    def __init__(self, title: str = '', crumbs=('总览',), sec: str = '',
                 parent=None):
        super().__init__(parent)

        self.__layout = QHBoxLayout(self)
        self.__layout.setContentsMargins(0, 0, 0, 0)
        self.__layout.setSpacing(0)

        self.__crumbs = []

        for i, name in enumerate(crumbs):
            if i:
                self.__layout.addSpacing(7)
                self.__layout.addWidget(Caret(self), 0, Qt.AlignVCenter)
                self.__layout.addSpacing(7)

            label = CrumbLabel(name, self)
            label.clicked.connect(self.crumbClicked)
            self.__layout.addWidget(label, 0, Qt.AlignVCenter)
            self.__crumbs.append(label)

        if crumbs:
            self.__layout.addSpacing(7)
            self.__layout.addWidget(Caret(self), 0, Qt.AlignVCenter)
            self.__layout.addSpacing(8)

        self.__title = QLabel(title, self)
        # 标题以前是构造时把 `color('text')` 拼死：切到浅色后它还是 #EAECEF，
        # 画在 #F4F5F7 的页面上等于隐形（对比度 1.1:1）。四个页头都中招
        theme.styled(self.__title, lambda: (
            f'color: {color("text")}; {font(18, bold=True)}'
            f' background: transparent; border: none;'))
        self.__layout.addWidget(self.__title, 0, Qt.AlignVCenter)

        # 标题后缀：生涯页的「本人」那种金色小标签，没有就整块不占位。
        # 间距用控件自己的 margins，不用 layout spacing —— spacing 是固定占位，
        # badge 隐藏后标题和 SEC 之间会多出一截空白
        self.__badge = QLabel('', self)
        theme.styled(self.__badge, lambda: (
            f'color: {color("brand")}; {font(10)}'
            f' background: {color("brandSoft")};'
            f' border: 1px solid {color("brandMuted")}; padding: 0 5px;'))
        self.__badge.setAlignment(Qt.AlignCenter)
        self.__badge.setFixedHeight(17)
        self.__badge.setContentsMargins(8, 0, 0, 0)
        self.__badge.setVisible(False)
        self.__layout.addWidget(self.__badge, 0, Qt.AlignVCenter)

        self.__sec = QLabel(sec, self)
        theme.styled(self.__sec, lambda: (
            f'color: {color("textMuted")}; {mono(11)}'
            f' background: transparent; border: none;'))
        # 标题 → SEC 的 10px 留白做成 SEC 自己的左 margin，
        # badge 显示时它自然接在 badge 后面
        self.__sec.setContentsMargins(10, 0, 0, 0)
        self.__layout.addWidget(self.__sec, 0, Qt.AlignVCenter)

        self.__layout.addStretch(1)
        self.setFixedHeight(HEAD_H)

        # Caret 的线色是在 paintEvent 里现读 `color('lineStrong')` 的，
        # 不补一次 update()，切完主题它会停在旧色直到鼠标划过
        theme.registerPaintRoot(self)

    # ------------------------------------------------------------------ 接口
    def setTitle(self, text: str):
        self.__title.setText(text)

    def setBadge(self, text: str):
        """标题后面的小标签（生涯页的「本人」）；传空串就整块隐藏"""
        self.__badge.setText(text)
        self.__badge.setVisible(bool(text))

    def setSec(self, text: str):
        self.__sec.setText(text)

    def addAction(self, widget: QWidget) -> QWidget:
        """右侧插槽（状态芯片、分段控件…）"""
        self.__layout.addWidget(widget, 0, Qt.AlignVCenter)
        return widget

    def setCrumbs(self, names):
        """面包屑前级改名时用（暂未用到，留在接口上）"""
        for label, name in zip(self.__crumbs, names):
            label.setText(name)
