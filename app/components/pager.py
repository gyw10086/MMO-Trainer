# coding:utf-8
"""分页条

设计稿里出现在每个列表底部，形态统一：
    ‹  1 / 3  ›            共 28 场 · 每页 10 条

只做纯前端翻页 —— 数据一次性拿回来（LCU 一次最多给几十场），
翻页不重新请求，这样切换页码不会有网络等待。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.common.design_tokens import color, font, mono


class _Hit(QLabel):
    """可点的小方块箭头"""
    clicked = pyqtSignal()

    def __init__(self, text: str, parent: QWidget = None):
        super().__init__(text, parent)
        self.setObjectName('pagerArrow')
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Pager(QWidget):
    """页码 + 总数 + 每页条数

    pageChanged 从 0 开始；外部只需接这个信号去重画列表。
    """

    pageChanged = pyqtSignal(int)

    def __init__(self, perPage: int = 4, unit: str = '场', parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName('pager')
        self.__perPage = max(1, perPage)
        self.__unit = unit
        self.__total = 0
        self.__page = 0

        self.__prev = _Hit('‹', self)
        self.__next = _Hit('›', self)

        self.__pageLabel = QLabel('1', self)
        self.__pageLabel.setObjectName('pagerCur')
        self.__pageLabel.setAlignment(Qt.AlignCenter)
        self.__pageLabel.setStyleSheet(mono(11, bold=True))

        self.__slash = QLabel('/', self)
        self.__slash.setStyleSheet(mono(11))

        self.__pages = QLabel('1', self)
        self.__pages.setObjectName('pagerTotal')
        self.__pages.setStyleSheet(mono(11))

        self.__summary = QLabel('', self)
        self.__summary.setObjectName('pagerSummary')
        self.__summary.setStyleSheet(font(11))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.__prev)
        layout.addWidget(self.__pageLabel)
        layout.addWidget(self.__slash)
        layout.addWidget(self.__pages)
        layout.addWidget(self.__next)
        layout.addStretch(1)
        layout.addWidget(self.__summary)

        self.__prev.clicked.connect(lambda: self.go(self.__page - 1))
        self.__next.clicked.connect(lambda: self.go(self.__page + 1))

        self.__refresh()

    # ---- 对外 ----
    def setTotal(self, total: int):
        self.__total = max(0, total)
        self.__page = 0
        self.__refresh()
        self.pageChanged.emit(0)

    def setPerPage(self, perPage: int):
        self.__perPage = max(1, perPage)
        self.__page = 0
        self.__refresh()

    def go(self, page: int):
        pages = self.__pageCount()
        page = max(0, min(page, pages - 1))
        if page == self.__page:
            return
        self.__page = page
        self.__refresh()
        self.pageChanged.emit(page)

    def setCompact(self, compact: bool):
        """窄窗口下把右侧统计藏掉，只留 页码 / 总页数"""
        self.__summary.setVisible(not compact)

    def currentPage(self) -> int:
        return self.__page

    def perPage(self) -> int:
        return self.__perPage

    def pageCount(self) -> int:
        return self.__pageCount()

    # ---- 内部 ----
    def __pageCount(self) -> int:
        return max(1, (self.__total + self.__perPage - 1) // self.__perPage)

    def __refresh(self):
        pages = self.__pageCount()
        self.__pageLabel.setText(str(self.__page + 1))
        self.__pages.setText(str(pages))
        self.__summary.setText(
            f'共 {self.__total} {self.__unit} · 每页 {self.__perPage} 条')

        hasPrev = self.__page > 0
        hasNext = self.__page < pages - 1
        for btn, on in ((self.__prev, hasPrev), (self.__next, hasNext)):
            btn.setEnabled(on)
            btn.setStyleSheet(
                f"color: {color('text') if on else color('textMuted')}; "
                f"background: {color('hover') if on else 'transparent'}; "
                f"border: 1px solid {color('line')};")
