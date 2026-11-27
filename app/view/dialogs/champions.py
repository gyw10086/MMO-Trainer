# coding:utf-8
"""常用英雄「查看全部」（设计稿 S11 左）

右栏只有 5 行、按场次排，看不到「胜率高的那个」；这里给 3 列网格 + 两种排序，
点一格直接回到生涯按这个英雄筛。数据全部来自 `info['champions']`，不发新请求。
"""
import math

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from app.common.design_tokens import color, font, mono
from app.view.dialogs.base import ModalShell

SORTS = ('场次', '胜率')


class _ChampionCell(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, champ: dict, parent=None):
        super().__init__(parent)
        self.setObjectName('champCell')
        self.champ = champ
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(64)

        games = max(0, champ.get('total', 0) or 0)
        wins = max(0, champ.get('wins', 0) or 0)
        # 胜率封顶 100: 数据里出现「1 场 4 胜」这类脏值时别显示 400%
        rate = min(100, wins * 100 // max(1, games))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        icon = QLabel(self)
        icon.setFixedSize(22, 22)
        icon.setScaledContents(True)
        pix = QPixmap(champ.get('icon') or '')
        if not pix.isNull():
            icon.setPixmap(pix)
        top.addWidget(icon)

        name = QLabel(champ.get('name') or '--', self)
        name.setStyleSheet(f"color: {color('text')}; " + font(11))
        top.addWidget(name, 1)
        layout.addLayout(top)

        kv = QHBoxLayout()
        kv.setContentsMargins(0, 0, 0, 0)
        meta = QLabel(f'{games} 场', self)
        meta.setStyleSheet(f"color: {color('textMuted')}; " + mono(9))
        rateLabel = QLabel(f'{rate}%', self)
        rateLabel.setStyleSheet(
            f"color: {color('win') if rate >= 50 else color('red')}; " + mono(10, bold=True))
        kv.addWidget(meta)
        kv.addStretch(1)
        kv.addWidget(rateLabel)
        layout.addLayout(kv)

        bar = QFrame(self)
        bar.setObjectName('champBar')
        bar.setFixedHeight(3)
        fill = QFrame(bar)
        fill.setStyleSheet(
            f"background: {color('win') if rate >= 50 else color('red')};")
        fill.setFixedSize(max(1, int(150 * rate / 100)), 3)
        layout.addWidget(bar)

        self.setProperty('rate', rate)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.champ)
        super().mouseReleaseEvent(e)


class ChampionPoolDialog(ModalShell):
    """3 列网格；点格子 -> picked(championName)，由生涯页去筛"""

    picked = pyqtSignal(str)

    def __init__(self, champions: list, games: int = 0, parent=None):
        # 高度跟着行数走: 只有 3 个英雄时不该留 200px 空白
        rows = max(1, math.ceil(len(champions) / 3))
        height = max(320, min(640, 120 + rows * 68))
        super().__init__('常用英雄', f'近 {games} 场 · {len(champions)} 个英雄',
                         width=620, height=height, parent=parent)
        self.__champions = list(champions)
        self.__sort = 0

        sortBar = QHBoxLayout()
        sortBar.setContentsMargins(0, 0, 0, 0)
        sortBar.setSpacing(0)
        self.__sortButtons = []
        for i, text in enumerate(SORTS):
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setMinimumWidth(52)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self.__setSort(idx))
            sortBar.addWidget(btn)
            self.__sortButtons.append(btn)
        sortBar.addStretch(1)

        self.__hint = QLabel('点一个英雄，回到生涯只看他的场次', self)
        self.__hint.setStyleSheet(f"color: {color('textMuted')}; " + mono(9))
        sortBar.addWidget(self.__hint)
        self.body.addLayout(sortBar)

        self.__gridHost = QWidget(self)
        self.__grid = QGridLayout(self.__gridHost)
        self.__grid.setContentsMargins(0, 2, 0, 0)
        self.__grid.setSpacing(6)
        self.body.addWidget(self.__gridHost, 1)

        self.footNote(f'胜率 = 胜场 ÷ 出场 · 共 {sum(c.get("total", 0) for c in self.__champions)} 场')

        self.__setSort(0, initial=True)

    def __setSort(self, index: int, initial: bool = False):
        self.__sort = index
        style = (
            f"QPushButton {{ color: {color('textSub')}; background: transparent;"
            f"  border: 1px solid {color('line')}; {font(10)} }}"
            f"QPushButton:checked {{ color: {color('brand')};"
            f"  border-color: {color('brand')}; background: {color('brandSoft')}; }}")
        for i, btn in enumerate(self.__sortButtons):
            btn.setChecked(i == index)
            btn.setStyleSheet(style)

        if index == 0:
            data = sorted(self.__champions, key=lambda c: c.get('total', 0), reverse=True)
        else:
            data = sorted(
                self.__champions,
                key=lambda c: (c.get('wins', 0) * 100 // max(1, c.get('total', 0)),
                               c.get('total', 0)),
                reverse=True)

        while self.__grid.count():
            item = self.__grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, champ in enumerate(data):
            cell = _ChampionCell(champ, self.__gridHost)
            cell.clicked.connect(self.__pick)
            self.__grid.addWidget(cell, i // 3, i % 3)

        # 空格补齐，最后一行的格子不会被拉宽
        rest = len(data) % 3
        if rest:
            for col in range(rest, 3):
                filler = QWidget(self.__gridHost)
                filler.setFixedHeight(64)
                self.__grid.addWidget(filler, len(data) // 3, col)

        self.__grid.setRowStretch(len(data) // 3 + 1, 1)

    def __pick(self, champ: dict):
        self.picked.emit(champ.get('name') or '')
        self.accept()
