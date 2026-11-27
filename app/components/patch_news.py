# coding:utf-8
"""版本改动条目（总览 [02] 近期改动）

设计稿对应 `.news-item`：一行 44px，从左到右
    日期 44px（等宽 10px）─ 类别角标 18px ─ 标题（13px，超出省略）─ 来源（等宽 10px）
底部一条 1px 分隔线，最后一条不画。

数据来自 `app.lol.patch_notes`：真实解析官方版本公告，
标题已经是「安妮：R 提伯斯之怒 · 提伯斯生命值 1150 → 1275」这种一句话结构，
所以这一层只负责排版，不再做任何文案加工。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

from app.common.design_tokens import color, font, mono, withAlpha

# 三类改动对应设计稿的三个角标色：英雄=红、装备=金、系统=青
KIND_TONE = {
    '英雄': 'red',
    '装备': 'brand',
    '系统': 'accent',
}

_ROW_HEIGHT = 44
_DATE_WIDTH = 44


class MicroBadge(QLabel):
    """18px 小角标

    设计稿 `.badge`：1px 边框 + 同色 8% 底 + 9px 等宽字。
    比 QPushButton 轻，又不用每处重抄一遍样式。
    """

    clicked = pyqtSignal()

    def __init__(self, text: str = '', tone: str = 'info', parent=None):
        super().__init__(text, parent)
        self.setObjectName('microBadge')
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(18)
        self.setCursor(Qt.PointingHandCursor)
        self.applyTone(tone)

    def applyTone(self, tone: str):
        accent = color(tone) if tone in ('red', 'brand', 'accent', 'win', 'textMuted') else color('textSub')
        self.setStyleSheet(
            f"QLabel#microBadge {{"
            f" color: {accent};"
            f" border: 1px solid {withAlpha(accent, 0x66)};"
            f" background: {withAlpha(accent, 0x12)};"
            f" padding: 0 6px;"
            f" {mono(9)} }}")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ElidedLabel(QLabel):
    """单行省略：QLabel 自己不会打省略号，只能按当前宽度算一遍"""

    def __init__(self, text: str = '', parent=None):
        super().__init__(parent)
        self.__full = text or ''
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(self.__full)

    def setText(self, text: str):
        self.__full = text or ''
        self.__apply()

    def fullText(self) -> str:
        return self.__full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.__apply()

    def __apply(self):
        if not self.__full:
            super().setText('')
            return

        metrics = self.fontMetrics()
        available = max(0, self.width() - 2)
        super().setText(metrics.elidedText(self.__full, Qt.ElideRight, available))


class PatchNewsRow(QFrame):
    """一条版本改动

    `item` 来自 `patch_notes.loadPatchNotes()['items']`：
        kind / name / title / detail / date / url
    """

    clicked = pyqtSignal(dict)

    def __init__(self, item: dict, last: bool = False, parent=None):
        super().__init__(parent)
        self.item = item or {}
        self.setProperty('patchRow', True)
        self.setProperty('patchRowLast', bool(last))
        self.setFixedHeight(_ROW_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        # 公告日期是 `2026-11-18`，列表列宽只放得下 `11-18`；
        # 年份对「这个版本什么时候上的」没有意义，看版本号就够了
        rawDate = self.item.get('date') or ''
        shortDate = rawDate[5:] if len(rawDate) >= 10 else rawDate

        self.__date = QLabel(shortDate or '--', self)
        self.__date.setFixedWidth(_DATE_WIDTH)
        self.__date.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))

        self.__kind = MicroBadge(self.item.get('kind') or '系统',
                                 KIND_TONE.get(self.item.get('kind'), 'info'), self)
        self.__kind.setCursor(Qt.ArrowCursor)

        self.__title = ElidedLabel(self.item.get('title') or '', self)
        self.__title.setStyleSheet(f"color: {color('text')}; " + font(13))

        self.__source = QLabel('lol.qq.com', self)
        self.__source.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))

        # 角标和来源只是信息，不参与点击 —— 让事件落到整行上
        for widget in (self.__date, self.__kind, self.__title, self.__source):
            widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        layout.addWidget(self.__date)
        layout.addWidget(self.__kind, 0, Qt.AlignVCenter)
        layout.addWidget(self.__title, 1)
        layout.addWidget(self.__source)

        tip = self.item.get('detail') or self.item.get('title') or ''
        if tip:
            self.setToolTip(f"{tip}\n\n点击查看官方版本公告")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self.item)
        super().mouseReleaseEvent(event)
