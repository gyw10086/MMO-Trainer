# coding:utf-8
"""最近队友「查看全部」（设计稿 S11 右）

右栏只放 5 行、只写「同队 N 场」。可半年前一起打的 9 场和昨天打的 9 场，
在右栏里长得一模一样 —— 这里补上「最近同队时间」和共同胜率。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from app.common.design_tokens import color, font, mono
from app.view.dialogs.base import ModalShell


class TeammateRow(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, summoner: dict, parent=None):
        super().__init__(parent)
        self.setObjectName('teammateRow')
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self.__puuid = summoner.get('puuid') or ''

        total = summoner.get('total', 0)
        wins = summoner.get('wins', 0)
        rate = min(100, wins * 100 // max(1, total))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        icon = QLabel(self)
        icon.setFixedSize(24, 24)
        icon.setScaledContents(True)
        pix = QPixmap(summoner.get('icon') or '')
        if not pix.isNull():
            icon.setPixmap(pix)
            icon.setStyleSheet(f"border: 1px solid {color('lineStrong')};")
        layout.addWidget(icon)

        name = QLabel(summoner.get('name') or '--', self)
        name.setStyleSheet(f"color: {color('text')}; " + font(11))
        name.setMinimumWidth(120)
        layout.addWidget(name, 1)

        last = QLabel(summoner.get('lastStr') or '--', self)
        last.setStyleSheet(f"color: {color('textMuted')}; " + mono(9))
        last.setFixedWidth(90)
        last.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        last.setToolTip('最近一次同队的时间')
        layout.addWidget(last)

        games = QLabel(f'{total} 场', self)
        games.setStyleSheet(f"color: {color('textSub')}; " + mono(10))
        games.setFixedWidth(46)
        games.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(games)

        bar = QFrame(self)
        bar.setObjectName('champBar')
        bar.setFixedSize(56, 3)
        fill = QFrame(bar)
        fill.setStyleSheet(
            f"background: {color('win') if rate >= 50 else color('red')};")
        fill.setFixedSize(max(1, int(56 * rate / 100)), 3)
        layout.addWidget(bar)

        rateLabel = QLabel(f'{rate}%', self)
        rateLabel.setFixedWidth(34)
        rateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rateLabel.setStyleSheet(
            f"color: {color('win') if rate >= 50 else color('red')}; " + mono(10, bold=True))
        rateLabel.setToolTip('共同胜率 = 同队胜场 ÷ 同队场次')
        layout.addWidget(rateLabel)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.__puuid:
            self.clicked.emit(self.__puuid)
        super().mouseReleaseEvent(e)


class TeammatesDialog(ModalShell):
    """全部重复队友；点一行进对方生涯"""

    picked = pyqtSignal(str)

    def __init__(self, summoners: list, total: int = 20, parent=None):
        height = max(300, min(560, 120 + len(summoners) * 40 + 40))
        # 场次由调用方给：以前写死「近 20 场」，配置改成别的数量就对不上
        super().__init__('最近队友',
                         f'近 {total} 场里重复遇到的 {len(summoners)} 人',
                         width=560, height=height, parent=parent)

        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 8, 2)
        head.setSpacing(8)
        # 表头要跟行对齐：行首是 24px 头像 + 8px 间距，这里空出同样宽度
        head.addSpacing(32)
        for text, width, align in (('队友', 0, Qt.AlignLeft),
                                   ('最近同队', 90, Qt.AlignRight),
                                   ('场次', 46, Qt.AlignRight),
                                   ('共同胜率', 98, Qt.AlignRight)):
            label = QLabel(text, self)
            label.setStyleSheet(f"color: {color('textMuted')}; " + font(9))
            label.setAlignment(align | Qt.AlignVCenter)
            if width:
                label.setFixedWidth(width)
            head.addWidget(label, 1 if width == 0 else 0)
        self.body.addLayout(head)

        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        for summoner in summoners:
            row = TeammateRow(summoner, self)
            row.clicked.connect(self.__pick)
            box.addWidget(row)
        box.addStretch(1)
        self.body.addLayout(box, 1)

        self.footNote('共同胜率只统计同队的场次；重开局不计入胜负')

    def __pick(self, puuid: str):
        self.picked.emit(puuid)
        self.accept()
