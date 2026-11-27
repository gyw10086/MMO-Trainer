# coding:utf-8
"""分段控件

下拉等于把 N 个选项藏起 N-1 个，分段一次看全。筛选模式这种
「总共就四五个、且经常来回切」的场景，分段比下拉少一次点击。

总览的「我的战绩」和生涯的「逐场筛选」用同一份，不再各写一套。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QWidget

from app.common.design_tokens import color, font


class SegmentControl(QWidget):
    """等宽分段按钮

    items: [(文字, 附加值), ...]，附加值原样通过 changed(index) 传出去
    """

    changed = pyqtSignal(int)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.__items = list(items)
        self.__index = -1
        self.__buttons = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        for i, (label, _) in enumerate(self.__items):
            btn = QPushButton(label, self)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(20)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, idx=i: self.setIndex(idx))
            layout.addWidget(btn)
            self.__buttons.append(btn)

        self.setIndex(0)

    def setIndex(self, index: int, emit: bool = True):
        if not 0 <= index < len(self.__buttons):
            return

        self.__index = index

        for i, btn in enumerate(self.__buttons):
            active = i == index
            last = i == len(self.__buttons) - 1
            btn.setChecked(active)
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  color: {color('text') if active else color('textMuted')};"
                f"  background: {color('brand') + '1F' if active else 'transparent'};"
                f"  border: 1px solid {color('brand') if active else color('line')};"
                f"  {'border-right: none;' if not last else ''}"
                f"  padding: 0 8px; {font(10)}"
                f"}}"
                f"QPushButton:hover {{ color: {color('text')}; }}")

        if emit:
            self.changed.emit(index)

    def index(self) -> int:
        return self.__index

    def value(self):
        return self.__items[self.__index][1]
