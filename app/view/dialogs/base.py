# coding:utf-8
"""弹窗外壳

S11 的两个「查看全部」都是同一副样子：无边框、深色壳、标题行 + 关闭、
内容区自定、底部一行说明。抽出来避免两处各画一遍。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout,
                             QWidget)

from app.common.design_tokens import color, font, mono
from app.common import theme


class _CloseButton(QLabel):
    def __init__(self, parent=None):
        super().__init__('✕', parent)
        self.setFixedSize(22, 22)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.__idle = f"color: {color('textSub')}; {font(11)} border: 1px solid transparent;"
        self.setStyleSheet(self.__idle)
        self.setToolTip('关闭 (Esc)')

    def enterEvent(self, e):
        self.setStyleSheet(
            f"color: {color('red')}; {font(11)}"
            f"border: 1px solid {color('red')}66; background: {color('red')}14;")
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setStyleSheet(self.__idle)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.parent():
            self.parent().window().close()
        super().mouseReleaseEvent(e)


class _DragBar(QWidget):
    """标题行兼拖动把手"""

    def __init__(self, dialog: QDialog, parent=None):
        super().__init__(parent)
        self.__dialog = dialog
        self.__from = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.__from = e.globalPos() - self.__dialog.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.__from is not None and e.buttons() & Qt.LeftButton:
            self.__dialog.move(e.globalPos() - self.__from)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.__from = None
        super().mouseReleaseEvent(e)


class ModalShell(QDialog):
    """无边框弹窗壳

    子类往 `self.body` 里塞内容，往 `self.footLeft` / `self.footRight` 里加页脚。
    """

    def __init__(self, title: str, subtitle: str = '', width: int = 560,
                 height: int = 420, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(width, height)
        self.setMinimumSize(width - 80, height - 60)

        self.__shell = QFrame(self)
        self.__shell.setObjectName('modalShell')

        titleLabel = QLabel(title, self.__shell)
        # 不写 color 会落到 Qt 默认调色板（浅色），在深底上等于隐形
        titleLabel.setStyleSheet(f"color: {color('text')}; " + font(13, bold=True))

        subLabel = QLabel(subtitle, self.__shell)
        subLabel.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))
        subLabel.setVisible(bool(subtitle))

        bar = _DragBar(self, self.__shell)
        barLayout = QHBoxLayout(bar)
        barLayout.setContentsMargins(12, 0, 8, 0)
        barLayout.setSpacing(8)
        barLayout.addWidget(titleLabel)
        barLayout.addWidget(subLabel)
        barLayout.addStretch(1)
        barLayout.addWidget(_CloseButton(self.__shell))

        self.body = QVBoxLayout()
        self.body.setContentsMargins(12, 10, 12, 10)
        self.body.setSpacing(6)

        self.footLeft = QHBoxLayout()
        self.footLeft.setContentsMargins(0, 0, 0, 0)
        self.footLeft.setSpacing(6)
        self.footRight = QHBoxLayout()
        self.footRight.setContentsMargins(0, 0, 0, 0)
        self.footRight.setSpacing(6)

        foot = QWidget(self.__shell)
        foot.setObjectName('modalFoot')
        foot.setFixedHeight(34)
        footLayout = QHBoxLayout(foot)
        footLayout.setContentsMargins(12, 0, 12, 0)
        footLayout.setSpacing(6)
        footLayout.addLayout(self.footLeft)
        footLayout.addStretch(1)
        footLayout.addLayout(self.footRight)

        shellLayout = QVBoxLayout(self.__shell)
        shellLayout.setContentsMargins(1, 0, 1, 1)
        shellLayout.setSpacing(0)
        shellLayout.addWidget(bar)
        shellLayout.addLayout(self.body, 1)
        shellLayout.addWidget(foot)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.__shell)

        # 弹窗是独立顶层窗口, 不在主窗口子树里: 组件 qss（列表行 / 格子 / 分段）
        # 得自己挂一份, 否则深色下弹窗内容是浅色、浅色下又反过来
        theme.registerQssRoot(self)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)

    def footNote(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {color('textMuted')}; " + mono(9))
        self.footLeft.addWidget(label)
        return label
