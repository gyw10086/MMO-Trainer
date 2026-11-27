# coding:utf-8
"""版本改动「查看全部」

[02] 面板一屏只放得下 6 行，一个版本公告通常有 20 条左右改动，
剩下的在这里按类别看全，点任意一条跳到官方公告原文。

数据就是总览页已经拿到的那份，不发新请求。
"""
import webbrowser

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from app.common.design_tokens import color, font
from app.common.qfluentwidgets import SmoothScrollArea
from app.components.patch_news import PatchNewsRow
from app.view.dialogs.base import ModalShell

FILTERS = ('全部', '英雄', '装备', '系统')


class PatchNotesDialog(ModalShell):
    """版本改动列表：类别筛选 + 滚动"""

    def __init__(self, data: dict, parent=None):
        data = data or {}
        items = data.get('items') or []
        version = data.get('version') or ''
        date = (data.get('date') or '')[:10]
        shortDate = date[5:] if len(date) >= 10 else date

        subtitle = ' · '.join(part for part in (
            f'{version} 版本' if version else '',
            f'{shortDate} 更新' if shortDate else '',
            f'{len(items)} 条改动') if part)

        super().__init__('版本改动', subtitle, width=640, height=580, parent=parent)
        self.__data = data
        self.__items = list(items)
        self.__filter = '全部'
        self.__rows = []

        self.__buildToolbar()
        self.__buildList()
        self.footNote('来源：英雄联盟官方版本更新公告（lol.qq.com）')

        self.__applyFilter()

    # ---------------------------------------------------------------- 布局
    def __buildToolbar(self):
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(0)

        self.__filterButtons = []
        for index, text in enumerate(FILTERS):
            button = QPushButton(text, self)
            button.setCheckable(True)
            button.setFixedHeight(24)
            button.setMinimumWidth(48)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, name=text: self.__setFilter(name))
            bar.addWidget(button)
            self.__filterButtons.append(button)

        bar.addStretch(1)

        self.__openButton = QPushButton('打开官方公告', self)
        self.__openButton.setFixedHeight(24)
        self.__openButton.setCursor(Qt.PointingHandCursor)
        self.__openButton.setStyleSheet(
            f"QPushButton {{ color: {color('brand')}; background: transparent;"
            f" border: 1px solid {color('line')}; padding: 0 10px; {font(10)} }}"
            f"QPushButton:hover {{ border-color: {color('brand')}; }}")
        self.__openButton.clicked.connect(self.__openNews)
        bar.addWidget(self.__openButton)

        self.body.addLayout(bar)

    def __buildList(self):
        self.__scroll = SmoothScrollArea(self)
        self.__scroll.setObjectName('patchScroll')
        self.__scroll.setWidgetResizable(True)
        self.__scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.__scroll.setStyleSheet(
            "QScrollArea#patchScroll { background: transparent; border: none; }"
            "QScrollArea#patchScroll > QWidget { background: transparent; }")

        host = QWidget(self.__scroll)
        host.setStyleSheet('background: transparent;')
        self.__list = QVBoxLayout(host)
        self.__list.setContentsMargins(0, 0, 0, 0)
        self.__list.setSpacing(0)
        self.__scroll.setWidget(host)

        self.body.addWidget(self.__scroll, 1)

    # ---------------------------------------------------------------- 数据
    def __setFilter(self, name: str):
        self.__filter = name
        self.__applyFilter()

    def __applyFilter(self):
        style = (
            f"QPushButton {{ color: {color('textSub')}; background: transparent;"
            f"  border: 1px solid {color('line')}; {font(10)} }}"
            f"QPushButton:checked {{ color: {color('brand')};"
            f"  border-color: {color('brand')}; background: {color('brandSoft')}; }}")
        for button in self.__filterButtons:
            button.setStyleSheet(style)
            button.setChecked(button.text() == self.__filter)

        while self.__list.count():
            item = self.__list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.__rows = []

        visible = [it for it in self.__items
                   if self.__filter == '全部' or it.get('kind') == self.__filter]

        if not visible:
            hint = QLabel('这个类别下没有改动。', self)
            hint.setAlignment(Qt.AlignCenter)
            hint.setFixedHeight(120)
            hint.setStyleSheet(f"color: {color('textMuted')}; " + font(11))
            self.__list.addWidget(hint)
            self.__list.addStretch(1)
            return

        for index, item in enumerate(visible):
            row = PatchNewsRow(item, last=(index == len(visible) - 1))
            row.clicked.connect(self.__openItem)
            self.__list.addWidget(row)
            self.__rows.append(row)

        self.__list.addStretch(1)

    # ---------------------------------------------------------------- 跳转
    def __openItem(self, item: dict):
        self.__open((item or {}).get('url'))

    def __openNews(self):
        self.__open(self.__data.get('url'))

    @staticmethod
    def __open(url):
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass
