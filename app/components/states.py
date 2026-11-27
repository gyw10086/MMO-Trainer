# coding:utf-8
"""加载 / 空 / 错误三态

原来每个页面各写一份：`InfoBar.error` 弹一次 + 一个空白区域，
样式和文案都不统一，用户也分不清「没数据」和「加载失败」。

三个都在同一份 `components.qss` 里定义外观，尺寸由调用方给。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.common.design_tokens import color, font
from app.common.qfluentwidgets import IndeterminateProgressRing


class _StateBase(QWidget):
    def __init__(self, title: str, hint: str = '', height: int = 120, parent=None):
        super().__init__(parent)
        self.setObjectName('stateBase')
        self.setMinimumHeight(height)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 12, 16, 12)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)

        self._title = QLabel(title, self)
        self._title.setObjectName('stateTitle')
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)

        self._hint = QLabel(hint, self)
        self._hint.setObjectName('stateHint')
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setVisible(bool(hint))

        self._layout.addWidget(self._title)
        self._layout.addWidget(self._hint)
        self._layout.addStretch(1)

    def setText(self, title: str, hint: str = ''):
        self._title.setText(title)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))


class EmptyState(_StateBase):
    """没有数据。跟「加载失败」区分开：这里没有重试按钮，因为重试也没用"""

    def __init__(self, title: str = '还没有数据', hint: str = '',
                 height: int = 120, parent=None):
        super().__init__(title, hint, height, parent)
        self.setObjectName('emptyState')


class LoadingState(_StateBase):
    """正在加载：转圈 + 一行说明"""

    def __init__(self, title: str = '正在加载…', hint: str = '',
                 height: int = 120, parent=None):
        super().__init__('', hint, height, parent)
        self.setObjectName('loadingState')
        self._title.hide()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        ring = IndeterminateProgressRing(self)
        ring.setFixedSize(24, 24)
        row.addWidget(ring)
        row.addStretch(1)
        # 放在标题原来的位置，转圈在上、说明在下
        self._layout.insertLayout(1, row)

        self.__label = QLabel(title, self)
        self.__label.setObjectName('stateTitle')
        self.__label.setAlignment(Qt.AlignCenter)
        self._layout.insertWidget(2, self.__label)


class ErrorState(_StateBase):
    """加载失败：给一句人话 + 一个重试按钮

    LCU 的报错原文（英文栈信息）对用户没有意义，原文记日志就够了。
    """

    retry = pyqtSignal()

    def __init__(self, title: str = '读取失败', hint: str = '客户端可能没响应，稍后重试',
                 height: int = 120, parent=None):
        super().__init__('', hint, height, parent)
        self.setObjectName('errorState')
        self._title.hide()

        self.__label = QLabel(title, self)
        self.__label.setObjectName('stateTitle')
        self.__label.setAlignment(Qt.AlignCenter)
        self._layout.insertWidget(1, self.__label)

        self.__button = QPushButton('重试', self)
        self.__button.setObjectName('stateRetry')
        self.__button.setFixedSize(64, 24)
        self.__button.setCursor(Qt.PointingHandCursor)
        self.__button.setStyleSheet(
            f"QPushButton {{ color: {color('text')}; background: transparent;"
            f"  border: 1px solid {color('lineStrong')}; {font(10)} }}"
            f"QPushButton:hover {{ color: {color('brand')};"
            f"  border-color: {color('brand')}; }}")

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addStretch(1)
        row.addWidget(self.__button)
        row.addStretch(1)
        self._layout.insertLayout(3, row)

        self.__button.clicked.connect(self.retry)

    def setText(self, title: str, hint: str = ''):
        super().setText(title, hint)
        self.__label.setText(title)
