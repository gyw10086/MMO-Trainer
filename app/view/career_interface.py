# coding:utf-8
"""生涯页

设计稿 S8/S9。旧版的问题：
  1. 段位是 2 行 × 9 列的表格 —— 9 列里有 4 列是同一段位的时间切片
     （当前 / 最高 / 上季 / LP），横着扫完才拼出「他什么水平」；
  2. 逐场是 86px 的三行卡片，一屏只能看 6 场；
  3. 头像名字被两侧 Expanding 顶到正中，视线起点每次都在跳；
  4. 最近队友要点「按钮 → 浮层」才知道里面有什么。

这一版：
  身份条左对齐 → 两张段位卡（官方色）+ 近况带 → 逐场 38px 行 + 五段筛选 + 分页
  → 右栏常驻「常用英雄 / 最近队友」。
"""
import asyncio

import pyperclip
from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)
from qasync import asyncSlot

from app.common.config import cfg
from app.common.logger import logger
from app.common.design_tokens import color, font, mono
from app.common.icons import Icon
from app.common.qfluentwidgets import (IndeterminateProgressRing,
                                       ToolButton, InfoBar, InfoBarPosition)
from app.common import theme
from app.common.signals import signalBus
from app.common.style_sheet import StyleSheet
from app.components.career_row import CareerRow
from app.components.pager import Pager
from app.components.page_head import PageHead
from app.components.panel import Panel, CORNER
from app.view.dialogs.champions import ChampionPoolDialog
from app.view.dialogs.teammates import TeammatesDialog
from app.components.rank_card import RankCard
from app.components.segment import SegmentControl
from app.components.seraphine_interface import SeraphineInterface
from app.lol.connector import connector
from app.lol.tools import (getRecentTeammates, parseDetailRankInfo,
                           parseSummonerData)

TAG = 'Career'


class NameLabel(QLabel):
    def text(self) -> str:
        return super().text().replace("🔒", '')


class TagLineLabel(QLabel):
    def text(self) -> str:
        return super().text().replace(" ", '')


class ResultStrip(QWidget):
    """胜负序列：一格一场，从左到右由旧到新

    格数跟着实际场次走 —— 原来是写死 20 格 + `[-20:]`，配置把对局数量改成
    30 场时就会出现「近 30 场」配 20 个格子的矛盾。
    """

    CELL = 8
    GAP = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.__results = []
        self.setFixedHeight(self.CELL)
        # 布局里加了 stretch，sizeHint 会被压成 0，宽度必须自己钉住
        self.__fitWidth()

    def __fitWidth(self):
        count = max(1, len(self.__results))
        self.setFixedWidth(count * (self.CELL + self.GAP) - self.GAP)

    def setResults(self, results):
        self.__results = list(results or [])
        self.__fitWidth()
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        win = QColor(color('win'))
        lose = QColor(color('red'))
        muted = QColor(color('textMuted'))
        track = QColor(color('bg3'))

        for i in range(20):
            x = i * (self.CELL + self.GAP)
            if i < len(self.__results):
                result = self.__results[i]
                fill = muted if result is None else (win if result else lose)
            else:
                fill = track

            painter.fillRect(x, 0, self.CELL, self.CELL, fill)


class _Stat(QWidget):
    """近况带的一格：大数字 + 标签"""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.value = QLabel('--', self)
        self.value.setStyleSheet(mono(14, bold=True))
        self.label = QLabel(label, self)
        self.__tone = ''
        self.__applyStyle()
        theme.register(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.value)
        layout.addWidget(self.label)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__applyStyle()

    def __applyStyle(self, *_):
        # 颜色是拼进 qss 的，换主题要重拼一次，否则浅色下标签是淡灰、数字是近白
        self.label.setStyleSheet(f"color: {color('textMuted')}; " + font(9))
        self.value.setStyleSheet(
            f"color: {self.__tone or color('text')}; " + mono(14, bold=True))

    def setHint(self, text: str):
        """改标签文案

        场次是变化的（默认 20 场，配置可改），写死在构造时就会出现
        「2 胜 18 负」配「近 10 场」这种自相矛盾的显示。
        """
        self.label.setText(text)

    def setValue(self, text, tone=''):
        self.__tone = tone
        self.value.setText(text)
        self.value.setStyleSheet(
            f"color: {tone or color('text')}; " + mono(14, bold=True))


class CareerAvatar(QFrame):
    """身份条头像：58×58 方框 + 右下角等级徽章（设计稿 .id-avatar / .lv）

    原来用的是 RoundLevelAvatar —— 那个控件是按「圆形头像 + 外圈经验进度」
    设计的：直径默认 100，塞进 72 高的身份条直接上下裁掉；改成 58 之后，
    它自己又留了 30% 给进度环，头像圆只剩 40px，等级数字还压在圆底外面
    （被身份条裁一半）。设计稿要的是方角头像 + 独立小徽章，重画一个。
    """

    SIZE = 58

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('careerAvatar')
        self.setFixedSize(self.SIZE, self.SIZE)

        self.image = QLabel(self)
        self.image.setGeometry(1, 1, self.SIZE - 2, self.SIZE - 2)
        self.image.setScaledContents(True)
        self.image.setStyleSheet('background: transparent;')

        self.badge = QLabel('', self)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedHeight(16)
        self.badge.setMinimumWidth(28)
        self.badge.setStyleSheet(
            f"color: {color('brand')}; background: {color('bg0')};"
            f" border: 1px solid {color('lineStrong')}; " + mono(9))
        self.badge.hide()

    def updateIcon(self, icon, xpSinceLastLevel=None, xpUntilNextLevel=None,
                   text=''):
        pix = QPixmap(icon or '')

        if not pix.isNull():
            self.image.setPixmap(pix)

        text = str(text or '').strip()
        self.badge.setText(text)

        if not text or text == 'None':
            self.badge.hide()
            return

        self.badge.adjustSize()
        width = max(28, self.badge.width())
        self.badge.resize(width, 16)
        self.badge.move(self.SIZE - width - 1, self.SIZE - 17)
        self.badge.show()


class _PrimaryCard(QFrame):
    """无标题的 `.panel.primary`：抬一档底色 + 左上/右下 9px L 角标

    身份条是生涯页「这屏最重要的一块」，但用 Panel 会多出 30px 标题行，
    所以单独写一个只有角标的版本。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('primary', True)

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setPen(QPen(QColor(color('brandMuted')), 1))
        painter.setBrush(Qt.NoBrush)

        w, h = self.width(), self.height()
        painter.drawLine(0, 0, CORNER, 0)
        painter.drawLine(0, 0, 0, CORNER)
        painter.drawLine(w - 1 - CORNER, h - 1, w - 1, h - 1)
        painter.drawLine(w - 1, h - 1, w - 1, h - 1 - CORNER)


class CareerInterface(SeraphineInterface):

    # (标题, 宽度, 对齐) —— 与 CareerRow 的列一一对应
    COLUMNS = (
        ('英雄', 104, Qt.AlignLeft),
        ('分路', 34, Qt.AlignLeft),
        ('模式', 48, Qt.AlignLeft),
        ('KDA', 78, Qt.AlignRight),
        ('补刀', 44, Qt.AlignRight),
        ('伤害', 48, Qt.AlignRight),
        ('评分', 36, Qt.AlignRight),
        ('时长', 42, Qt.AlignRight),
        ('时间', 50, Qt.AlignRight),
    )
    gameInfoBarClicked = pyqtSignal(str)
    iconLevelExpChanged = pyqtSignal(dict)

    GAMES_PER_PAGE = 9
    MODES = [('全部', None), ('单双', (420,)), ('灵活', (440,)),
             ('大乱斗', (450,))]

    def __init__(self, parent=None):
        super().__init__(parent)

        self.loginSummonerPuuid = None
        self.puuid = None
        self.showTagLine = False
        self.games = {}
        self.rankInfo = None
        self.champions = []
        # 身份条副行里要显示等级，repaintInterface 里填
        self.__level = None
        self.allGames = []
        self.filtered = []
        self.recentTeammatesInfo = None
        self.loadGamesTask = None
        self.w = None
        self.__compact = False
        self.__rows = []
        self.__champRows = 5
        # 从「常用英雄」全部弹窗点进来的筛选；None = 不筛
        self.__champFilter = None

        self.view = QWidget()
        self.view.setObjectName('careerView')
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setViewportMargins(0, 0, 4, 0)

        self.progressRing = IndeterminateProgressRing(self)
        self.progressRing.setFixedSize(40, 40)

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

        StyleSheet.CAREER_INTERFACE.apply(self)
        self.setLoadingPageEnabled(True)

    # ------------------------------------------------------------------ 构建
    def __initWidget(self):
        self.icon = CareerAvatar(self)
        self.icon.updateIcon('app/resource/images/champion-0.png')

        self.name = NameLabel('连接中…')
        self.name.setObjectName('careerName')
        self.tagLineLabel = TagLineLabel()
        self.tagLineLabel.setVisible(False)

        self.copyButton = ToolButton(Icon.COPY)
        self.copyButton.setFixedSize(22, 22)
        self.copyButton.setEnabled(False)
        self.copyButton.setToolTip('复制召唤师名')

        self.backToMeButton = QPushButton('回到我自己')
        self.refreshButton = QPushButton('刷新')
        self.searchButton = QPushButton('查战绩')

        for btn in (self.backToMeButton, self.refreshButton, self.searchButton):
            btn.setFixedHeight(24)
            btn.setCursor(Qt.PointingHandCursor)

        # 刷新按钮上要轮流显示「刷新中 / +2 场 / 无新对局」，
        # 宽度必须固定，否则每次改文案 header 都会抖一下
        self.refreshButton.setFixedWidth(66)

        self.backToMeButton.setEnabled(False)
        self.__applyButtonStyle()
        # 按钮颜色是拼进 qss 的, 换主题要重拼; 统一由 theme 回调, 不再各自 connect
        theme.register(self)

        self.modeSegment = SegmentControl(self.MODES)
        self.modeSegment.changed.connect(self.__onModeChanged)

        # 从「常用英雄」全部弹窗点进来后的筛选提示，点一下清除
        self.champChip = QPushButton('')
        self.champChip.setFixedHeight(22)
        self.champChip.setCursor(Qt.PointingHandCursor)
        self.champChip.setVisible(False)
        self.champChip.setStyleSheet(
            f"QPushButton {{ color: {color('brand')}; background: {color('brandSoft')};"
            f"  border: 1px solid {color('brand')}; padding: 0 8px; {font(10)} }}"
            f"QPushButton:hover {{ color: {color('red')}; border-color: {color('red')}; }}")
        self.champChip.setToolTip('点一下清除英雄筛选')
        self.champChip.clicked.connect(self.__clearChampFilter)

        self.pager = Pager(self.GAMES_PER_PAGE, '场')
        self.pager.pageChanged.connect(lambda _: self.__renderGames())

        # 设计稿 S8 页头：总览 › 生涯 [本人] .......... 数据来自客户端本地缓存
        self.pageHead = PageHead(
            self.tr('生涯'), crumbs=(self.tr('总览'),), sec='', parent=self)
        self.pageHead.crumbClicked.connect(self.__onCrumb)

        self.headHint = QLabel(self.tr('数据来自客户端本地缓存'))
        self.headHint.setStyleSheet(f"color: {color('textMuted')}; " + font(10))
        self.pageHead.addAction(self.headHint)

    def __initLayout(self):
        root = QVBoxLayout(self.view)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        root.addWidget(self.pageHead)
        root.addWidget(self.__buildIdentity())
        root.addWidget(self.__buildRankRow())
        root.addWidget(self.__buildRecent())
        root.addLayout(self.__buildMain(), 1)

    def __buildIdentity(self) -> QWidget:
        bar = _PrimaryCard()
        bar.setObjectName('careerIdentity')
        # 58 头像 + 上下 11 内边距 = 80，与设计稿 .idcard 一致
        bar.setFixedHeight(80)

        nameBox = QVBoxLayout()
        nameBox.setContentsMargins(0, 0, 0, 0)
        nameBox.setSpacing(2)

        nameRow = QHBoxLayout()
        nameRow.setContentsMargins(0, 0, 0, 0)
        nameRow.setSpacing(6)
        self.name.setStyleSheet(font(20, bold=True))
        nameRow.addWidget(self.name)
        nameRow.addWidget(self.tagLineLabel)
        nameRow.addWidget(self.copyButton)
        nameRow.addStretch(1)

        self.identitySub = QLabel('--')
        self.identitySub.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))

        nameBox.addLayout(nameRow)
        nameBox.addWidget(self.identitySub)
        nameBox.addStretch(1)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(14)
        layout.addWidget(self.icon)
        layout.addLayout(nameBox, 1)
        layout.addWidget(self.backToMeButton)
        layout.addWidget(self.refreshButton)
        layout.addWidget(self.searchButton)
        return bar

    def __buildRankRow(self) -> QWidget:
        """段位区：满宽两列，一段一卡（设计稿 .rank-grid）

        原来和近况带挤在同一行（5 : 6），卡片只剩一半宽度，meta 行被压到
        换行；改成满宽两列后，卡里那行「模式 · 场次 · 胜率 · 胜负 · 最高 ·
        上季」才放得下。
        """
        box = QWidget()
        # 96 是「内容刚好 + 一点余量」: 再高卡片中间就会出现一条空带
        box.setFixedHeight(96)

        self.soloCard = RankCard()
        self.flexCard = RankCard()

        cards = QHBoxLayout(box)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(8)
        cards.addWidget(self.soloCard, 1)
        cards.addWidget(self.flexCard, 1)
        return box

    def __buildRecent(self) -> QWidget:
        """近况带：五个指标 + 最近 20 场胜负序列，单独一整行"""
        # 标签在 __renderSummary 里按实际场次改写，这里只是没数据时的占位
        self.statRecent = _Stat('近况')
        self.statWinRate = _Stat('胜率')
        self.statKda = _Stat('KDA')
        self.statDamage = _Stat('场均伤害')
        self.statScore = _Stat('场均评分')

        self.strip = ResultStrip()

        box = QFrame()
        box.setObjectName('careerRecent')
        box.setFixedHeight(52)

        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(18)

        for stat in (self.statRecent, self.statWinRate, self.statKda,
                     self.statDamage, self.statScore):
            layout.addWidget(stat)

        layout.addStretch(1)

        self.__stripLabel = QLabel('最近 -- 场')
        theme.styled(self.__stripLabel,
                     lambda: f"color: {color('textMuted')}; " + font(10))
        layout.addWidget(self.__stripLabel, 0, Qt.AlignVCenter)
        layout.addWidget(self.strip, 0, Qt.AlignVCenter)
        return box

    def __buildMain(self) -> QHBoxLayout:
        self.gamesPanel = Panel('01', '逐场记录')
        self.gamesPanel.addAction(self.champChip)
        self.gamesPanel.addAction(self.modeSegment)

        self.rowHeader = QWidget()
        self.rowHeader.setObjectName('rowHeader')
        self.rowHeader.setFixedHeight(22)
        headLayout = QHBoxLayout(self.rowHeader)
        # 边距和列宽必须和 CareerRow 完全一致，否则表头会和内容错位
        headLayout.setContentsMargins(4, 0, 6, 0)
        headLayout.setSpacing(9)
        # 行首是 2px 色条 + 9 + 26px 头像 + 9，表头留空跳过这 46px
        headLayout.addSpacing(2 + 9 + 26 + 9)
        self.__colHeaders = {}
        for text, width, align in self.COLUMNS:
            label = QLabel(text)
            label.setStyleSheet(f"color: {color('textMuted')}; " + font(11))
            label.setFixedWidth(width)
            label.setAlignment(align | Qt.AlignVCenter)
            self.__colHeaders[text] = label
            headLayout.addWidget(label)
        headLayout.addStretch(1)
        headLayout.addSpacing(12)

        self.gamesPanel.body.addWidget(self.rowHeader)

        self.gamesBox = QVBoxLayout()
        self.gamesBox.setContentsMargins(0, 0, 0, 0)
        self.gamesBox.setSpacing(0)
        self.gamesPanel.body.addLayout(self.gamesBox, 1)
        self.gamesPanel.body.addWidget(self.pager)
        self.gamesPanel.installEventFilter(self)

        self.champPanel = Panel('02', '常用英雄')
        self.champCount = QLabel('')
        self.champCount.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))
        self.champPanel.addAction(self.champCount)
        self.champPanel.addActionSpacing(6)
        self.__champMore = self.__moreButton(self.__openChampionPool)
        self.champPanel.addAction(self.__champMore)

        self.champBox = QVBoxLayout()
        self.champBox.setContentsMargins(0, 0, 0, 0)
        self.champBox.setSpacing(0)
        self.champPanel.body.addLayout(self.champBox, 1)

        self.teamPanel = Panel('03', '最近队友')
        self.teamCount = QLabel('')
        self.teamCount.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))
        self.teamPanel.addAction(self.teamCount)
        self.teamPanel.addActionSpacing(6)
        self.__teamMore = self.__moreButton(self.__openTeammates)
        self.teamPanel.addAction(self.__teamMore)

        self.teamBox = QVBoxLayout()
        self.teamBox.setContentsMargins(0, 0, 0, 0)
        self.teamBox.setSpacing(0)
        self.teamPanel.body.addLayout(self.teamBox, 1)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        side.addWidget(self.champPanel, 1)
        side.addWidget(self.teamPanel, 1)

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(8)
        main.addWidget(self.gamesPanel, 62)
        main.addLayout(side, 38)
        return main

    def __connectSignalToSlot(self):
        self.backToMeButton.clicked.connect(self.__changeToCurrentSummoner)
        self.refreshButton.clicked.connect(self.refresh)
        self.searchButton.clicked.connect(
            lambda: signalBus.toSearchInterface.emit(self.getSummonerName()))
        self.copyButton.clicked.connect(
            lambda: pyperclip.copy(self.getSummonerName()))

    def __onCrumb(self, name: str):
        """面包屑前级：总览 → 跳回总览页"""
        if name != self.tr('总览'):
            return

        window = self.window()
        target = getattr(window, 'overviewInterface', None)

        if target is not None and hasattr(window, 'switchTo'):
            window.switchTo(target)

    # ------------------------------------------------------------------ 状态
    def setLoadingPageEnabled(self, enable: bool):
        self.view.setVisible(not enable)
        self.progressRing.setVisible(enable)
        if enable:
            self.__centerRing()

    def __centerRing(self):
        self.progressRing.move(
            (self.viewport().width() - self.progressRing.width()) // 2,
            (self.viewport().height() - self.progressRing.height()) // 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.progressRing.isVisible():
            self.__centerRing()
        self.__applyResponsive()

    def __applyResponsive(self):
        w = self.viewport().width()
        h = self.viewport().height()
        if w < 200 or h < 200:
            return

        # 列是按设计稿定死的，窄了就按「伤害 → 时长 → 补刀/时间」的顺序收
        width = self.view.width()
        level = 0 if width >= 1100 else (1 if width >= 1010 else
                                         (2 if width >= 900 else 3))
        self.__compact = level
        for row in self.__rows:
            row.setCompact(level)

        for key, threshold in (('伤害', 1), ('时长', 2), ('补刀', 3), ('时间', 3)):
            label = self.__colHeaders.get(key)
            if label is not None:
                label.setVisible(level < threshold)

        self.pager.setCompact(w < 1000)

        self.__fitRows()

        # 右栏两个面板各占一半, 高度够就多放几行
        sideH = self.champPanel.height()
        if sideH < 150:
            sideH = max(150, (h - 208 - 8) // 2)
        champRows = max(3, min(9, (sideH - 39) // 38))
        if champRows != self.__champRows:
            self.__champRows = champRows
            self.__renderChampions()

    def __fitRows(self):
        """每页行数按面板实际高度算

        之前固定 12 行：881 高的窗口底部会空出 160px，看着像列表没加载完。
        面板高度是布局分配的，比窗口 resize 晚一帧，所以除了 resize 还要听
        面板自己的 QEvent.Resize，否则第一次会按默认 480 算出个偏小的值。
        """
        panelH = self.gamesPanel.height()
        if panelH < 200:
            return

        # 面板高 - 标题 31 - 表头 22 - 分页条 25 - 余量 6
        rows = max(5, min(18, (panelH - 84) // CareerRow.HEIGHT))
        if rows != self.pager.perPage():
            self.pager.setPerPage(rows)
            self.__renderGames()

    def eventFilter(self, obj, event):
        if (obj is getattr(self, 'gamesPanel', None)
                and event.type() == QEvent.Resize):
            self.__fitRows()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ 数据
    def setLoginSummonerPuuid(self, puuid):
        self.loginSummonerPuuid = puuid

    def getSummonerName(self) -> str:
        """带 `#编号` 的完整名字

        只给名字的话，战绩查询那边按名字搜会查不到 —— 同一大区重名的人很多，
        LCU 的按名查询需要 `名字#编号` 才能定位到具体这个号。
        """
        name = self.name.text()
        tag = ''

        if self.showTagLine:
            tag = self.tagLineLabel.text().lstrip('#').strip()

        return f'{name}#{tag}' if tag else name

    def isLoginSummoner(self):
        return self.loginSummonerPuuid == self.puuid

    async def updateNameIconExp(self, info):
        if not self.isLoginSummoner():
            return

        name = info.get('gameName') or info['displayName']
        name = name if info['privacy'] == 'PUBLIC' else f'{name}🔒'
        icon = await connector.getProfileIcon(info['profileIconId'])

        self.name.setText(name)
        level = info['summonerLevel']
        levelStr = str(level) if level != -1 else 'None'
        self.icon.updateIcon(icon, info['xpSinceLastLevel'],
                             info['xpUntilNextLevel'], levelStr)

    @asyncSlot()
    async def __changeToCurrentSummoner(self):
        self.setLoadingPageEnabled(True)
        summoner = await connector.getCurrentSummoner()
        await self.updateInterface(summoner=summoner)
        self.setLoadingPageEnabled(False)

    @asyncSlot()
    async def refresh(self):
        """重新拉一次当前这个人的战绩

        以前点完按钮什么都不变：拉到了、没拉到、报错了，用户都看不出来 ——
        表现就是「点了没反应」。现在三种结果都写在按钮上。

        对局刚结束时 LCU 那边的历史可能还没写全（新场次的 `gameId` 是 0），
        这种情况会显示「无新对局」，隔几秒再点一次就有了。
        """
        if not self.puuid:
            return

        index = self.modeSegment.index()
        before = {g.get('gameId') for g in self.allGames}
        self.__setRefreshState('busy')

        try:
            await self.updateInterface(puuid=self.puuid)
            self.modeSegment.setIndex(index)
        except Exception as e:
            logger.error(f'刷新生涯失败: {e}', TAG)
            self.setLoadingPageEnabled(False)
            self.__setRefreshState('fail')
            return

        added = len({g.get('gameId') for g in self.allGames} - before)
        self.__setRefreshState('done' if added else 'same', added)

    def __setRefreshState(self, state: str, count: int = 0):
        labels = {'busy': '刷新中', 'done': f'+{count} 场',
                  'same': '无新对局', 'fail': '刷新失败'}

        self.refreshButton.setText(labels.get(state, '刷新'))
        self.refreshButton.setEnabled(state != 'busy')

        if state != 'busy':
            QTimer.singleShot(1600, self.__resetRefreshState)

    def __resetRefreshState(self):
        self.refreshButton.setText('刷新')
        self.refreshButton.setEnabled(True)

    async def updateInterface(self, puuid=None, summoner=None):
        """通过 `puuid` 或 `summoner` 更新界面"""
        assert summoner or puuid

        self.setLoadingPageEnabled(True)
        self.recentTeammatesInfo = None

        # 任何一步抛异常都要把 loading 页收掉：卡在转圈上比报错更像"点了没反应"；
        # 同时弹一条提示 —— 静默变空白比报错更难查（用户只会说「数据全没了」）
        try:
            if summoner is None:
                summoner = await connector.getSummonerByPuuid(puuid)

            if 'errorCode' in summoner:
                return

            self.loadGamesTask = asyncio.create_task(
                connector.getSummonerGamesByPuuid(
                    summoner['puuid'], 0, cfg.get(cfg.careerGamesNumber) - 1))
            rankTask = asyncio.create_task(
                connector.getRankedStatsByPuuid(summoner['puuid']))

            info = await parseSummonerData(summoner, rankTask,
                                           self.loadGamesTask)
            await self.repaintInterface(info)
        except Exception as e:
            logger.exception('加载生涯失败', e, TAG)
            self.__showLoadError(e)
            raise
        finally:
            self.setLoadingPageEnabled(False)

    def __showLoadError(self, e: Exception):
        """加载失败时给一句人话，而不是留一个空白页"""
        detail = f'{type(e).__name__}: {e}'
        hint = '可以点右上角「刷新」重试'

        try:
            InfoBar.error(
                '生涯数据加载失败', f'{detail}\n{hint}',
                orient=Qt.Vertical, isClosable=True,
                position=InfoBarPosition.TOP, duration=6000, parent=self)
        except Exception:
            pass

    async def repaintInterface(self, info):
        name = info['name'] if info['isPublic'] else f"{info['name']}🔒"
        self.name.setText(name)
        self.puuid = info['puuid']

        # 「本人」标签只在自己的生涯页出现；看别人时不挂，免得误认
        self.pageHead.setBadge(
            self.tr('本人') if self.isLoginSummoner() else '')

        self.__level = info['level'] if info['level'] != -1 else None

        self.icon.updateIcon(info['icon'], info['xpSinceLastLevel'],
                             info['xpUntilNextLevel'],
                             str(info['level']) if info['level'] != -1 else 'None')

        if info.get('tagLine'):
            self.showTagLine = True
            self.tagLineLabel.setText(f"# {info['tagLine']}")
            self.tagLineLabel.setVisible(True)
            self.tagLineLabel.setStyleSheet(
                f"color: {color('textMuted')}; " + font(11))
        else:
            self.showTagLine = False
            self.tagLineLabel.setVisible(False)

        rankInfo = info.get('rankInfo') or {}
        if 'queueMap' in rankInfo:
            self.rankInfo = parseDetailRankInfo(rankInfo)
            self.copyButton.setEnabled(True)
        else:
            self.rankInfo = None
            self.copyButton.setEnabled(False)

        # 看别人时只保留段位名与 LP, 场次 / 胜率属于隐私
        if not self.isLoginSummoner() and self.rankInfo:
            for i in range(2):
                for j in (1, 2, 3, 4):
                    self.rankInfo[i][j] = '--'

        self.soloCard.update(self.rankInfo[0] if self.rankInfo else None)
        self.flexCard.update(self.rankInfo[1] if self.rankInfo else None)

        games = info.get('games') or {}
        self.games = games
        # 换了个人还留着上一个人的英雄筛选, 会得到一个空列表
        self.__champFilter = None
        self.allGames = games.get('games') or []
        self.champions = info.get('champions') or []

        self.__renderRecent()
        self.__onModeChanged(self.modeSegment.index())
        self.__renderChampions()
        self.__renderSummary()

        self.backToMeButton.setEnabled(not self.isLoginSummoner())
        self.setLoadingPageEnabled(False)

        # 总览页展示的是「我的」那套数据（段位 / 近期战绩 / 常用英雄），
        # 查别人时不能拿别人的去覆盖它 —— 否则点回总览看到的还是别人。
        # 上面几处渲染本来就是给「当前查看的人」用的，不受影响。
        if self.isLoginSummoner():
            signalBus.infoLoaded.emit({**info, 'rankTable': self.rankInfo})

        if self.allGames:
            asyncio.create_task(self.__updateRecentTeammates())

    # ------------------------------------------------------------------ 渲染
    def __identitySubText(self, total: int, wins: int, losses: int) -> str:
        """身份条第二行：等级 · 场次 · 胜负（设计稿的 .id-sub）

        等级要等 `repaintInterface` 拿到，场次要等战绩加载完，谁先到谁先写；
        两段都缺就退回「还没有对局记录」。
        """
        parts = []

        if self.__level:
            parts.append(f'等级 {self.__level}')

        if total:
            parts.append(f'{total} 场')
            parts.append(f'{wins} 胜 {losses} 负')
        elif not parts:
            parts.append('还没有对局记录')

        return '  ·  '.join(parts)

    def __renderSummary(self):
        games = self.allGames
        wins = sum(1 for g in games if g.get('win') and not g.get('remake'))
        losses = sum(1 for g in games if not g.get('win') and not g.get('remake'))

        self.identitySub.setText(self.__identitySubText(len(games), wins, losses))

        # 场次文案必须跟着真实数据走：以前写死「近 10 场」，
        # 实际统计的是取回来的 20 场，2 胜 18 负配「近 10 场」看着就是错的
        self.statRecent.setHint(f'近 {len(games)} 场' if games else '近况')
        self.statRecent.setValue(f'{wins} 胜 {losses} 负' if games else '--')
        self.statWinRate.setValue(
            f'{wins * 100 // max(1, wins + losses)}%' if games else '--')

        kills = sum(g.get('kills', 0) for g in games)
        deaths = sum(g.get('deaths', 0) for g in games)
        assists = sum(g.get('assists', 0) for g in games)
        self.statKda.setValue(
            f'{(kills + assists) / max(1, deaths):.1f}' if games else '--')

        # 场均伤害 / 评分只统计非重开, 否则 0 伤的重开局会把均值拉垮
        valid = [g for g in games if not g.get('remake')]
        if valid:
            avgDamage = sum(g.get('damage', 0) for g in valid) / len(valid)
            avgScore = sum(g.get('score', 0) for g in valid) / len(valid)
            self.statDamage.setValue(f'{avgDamage / 1000:.1f}k')
            self.statScore.setValue(f'{avgScore:.1f}')
        else:
            self.statDamage.setValue('--')
            self.statScore.setValue('--')

    def __renderRecent(self):
        results = []
        for game in self.allGames:
            results.append(None if game.get('remake') else bool(game.get('win')))
        self.strip.setResults(results)

        if results:
            self.__stripLabel.setText(f'最近 {len(results)} 场')

    def __onModeChanged(self, index: int):
        queueIds = self.MODES[index][1] if 0 <= index < len(self.MODES) else None

        if queueIds is None:
            self.filtered = list(self.allGames)
        else:
            self.filtered = [g for g in self.allGames
                             if g.get('queueId') in queueIds]

        if self.__champFilter:
            self.filtered = [g for g in self.filtered
                             if (g.get('championName') or '') == self.__champFilter]

        self.champChip.setVisible(bool(self.__champFilter))
        if self.__champFilter:
            self.champChip.setText(f'{self.__champFilter} ✕')

        self.pager.setTotal(len(self.filtered))
        self.__renderGames()

    def __renderGames(self):
        self.__clear(self.gamesBox)
        self.__rows = []

        if not self.filtered:
            self.gamesBox.addWidget(self.__emptyRow('这个模式下还没有对局记录', 80))
            return

        page = self.pager.currentPage()
        size = self.pager.perPage()
        for game in self.filtered[page * size:(page + 1) * size]:
            row = CareerRow(game)
            row.setCompact(self.__compact)
            row.clicked.connect(self.__onGameClicked)
            self.gamesBox.addWidget(row)
            self.__rows.append(row)

        self.gamesBox.addStretch(1)

    def __renderChampions(self):
        self.__clear(self.champBox)

        self.__champMore.setEnabled(bool(self.champions))
        if not self.champions:
            self.champBox.addWidget(self.__emptyRow('还没有英雄记录', 60))
            return

        self.champCount.setText(f'{len(self.champions)} 个')
        for champ in self.champions[:self.__champRows]:
            self.champBox.addWidget(self.__champRow(champ))
        self.champBox.addStretch(1)

    def __champRow(self, champ: dict) -> QWidget:
        row = QWidget()
        row.setFixedHeight(38)

        icon = QLabel(row)
        icon.setFixedSize(24, 24)
        icon.setScaledContents(True)
        pix = QPixmap(champ.get('icon') or '')
        if not pix.isNull():
            icon.setPixmap(pix)

        name = QLabel(champ.get('name') or '--', row)
        name.setStyleSheet(f"color: {color('text')}; " + font(12))

        games = champ.get('total', 0)
        wins = champ.get('wins', 0)
        rate = wins * 100 // max(1, games)

        meta = QLabel(f'{games} 场', row)
        meta.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))

        rateLabel = QLabel(f'{rate}%', row)
        rateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rateLabel.setFixedWidth(38)
        # 与总览一致的三档：≥60% 绿、50~59% 青、<50% 红
        if rate >= 60:
            tone = color('win')
        elif rate >= 50:
            tone = color('accent')
        else:
            tone = color('red')
        rateLabel.setStyleSheet(f"color: {tone}; " + mono(11, bold=True))

        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)
        layout.addWidget(icon)
        layout.addWidget(name, 1)
        layout.addWidget(meta)
        layout.addWidget(rateLabel)
        return row

    def __renderTeammates(self):
        self.__clear(self.teamBox)
        info = self.recentTeammatesInfo or {}
        summoners = info.get('summoners') or []

        if not summoners:
            self.teamBox.addWidget(self.__emptyRow(
                f'最近 {len(self.allGames)} 场没有重复队友', 60))
            self.teamCount.setText('')
            self.__teamMore.setEnabled(False)
            return

        self.teamCount.setText(f'{len(summoners)} 人')
        self.__teamMore.setEnabled(True)
        for summoner in summoners[:5]:
            self.teamBox.addWidget(self.__teamRow(summoner))
        self.teamBox.addStretch(1)

    def __teamRow(self, summoner: dict) -> QWidget:
        row = _TeamRow(summoner, self)
        row.opened.connect(self.__openCareer)
        return row

    # ------------------------------------------------------------------ 交互

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调

        页面自己的 qss 由库的样式管理器重刷, 这里只负责拼在字符串里的按钮颜色。
        """
        self.__applyButtonStyle()

    def __applyButtonStyle(self, *_):
        """页头三个按钮 + 英雄筛选 chip

        颜色原来是构造时拼死的: 深色切浅色后按钮只剩边框, 文字是白的 —
        白底上等于消失。
        """
        style = (
            f"QPushButton {{ color: {color('text')}; background: transparent;"
            f"  border: 1px solid {color('lineStrong')}; padding: 0 12px;"
            f"  {font(10)} }}"
            f"QPushButton:hover {{ border-color: {color('brand')};"
            f"  color: {color('brand')}; }}"
            f"QPushButton:disabled {{ color: {color('textMuted')};"
            f"  border-color: {color('line')}; }}")

        for btn in (self.backToMeButton, self.refreshButton, self.searchButton):
            btn.setStyleSheet(style)

        chip = getattr(self, 'champChip', None)

        if chip is not None:
            chip.setStyleSheet(
                f"QPushButton {{ color: {color('brand')};"
                f"  background: {color('brandSoft')};"
                f"  border: 1px solid {color('brand')}; padding: 0 8px;"
                f"  {font(10)} }}"
                f"QPushButton:hover {{ color: {color('red')};"
                f"  border-color: {color('red')}; }}")

    def __moreButton(self, slot) -> QPushButton:
        btn = QPushButton('全部 ›')
        btn.setFixedHeight(22)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ color: {color('textSub')}; background: transparent;"
            f"  border: 1px solid transparent; padding: 0 4px; {font(10)} }}"
            f"QPushButton:hover {{ color: {color('brand')}; }}"
            f"QPushButton:disabled {{ color: {color('textMuted')}; }}")
        btn.clicked.connect(slot)
        return btn

    def __openChampionPool(self):
        if not self.champions:
            return

        dialog = ChampionPoolDialog(self.champions, len(self.allGames), self.window())
        dialog.picked.connect(self.__filterByChampion)
        dialog.exec_()

    def __openTeammates(self):
        summoners = (self.recentTeammatesInfo or {}).get('summoners') or []
        if not summoners:
            return

        dialog = TeammatesDialog(summoners, len(self.allGames),
                                  self.window())
        dialog.picked.connect(self.__openCareer)
        dialog.exec_()

    def __filterByChampion(self, name: str):
        if not name:
            return
        self.__champFilter = name
        self.__onModeChanged(self.modeSegment.index())

    def __clearChampFilter(self):
        self.__champFilter = None
        self.__onModeChanged(self.modeSegment.index())

    def __onGameClicked(self, game: dict):
        """本页浮层看详情, 不再跳去战绩查询页 —— 那边没有回程"""
        window = self.window()
        if hasattr(window, 'openMatchDetail'):
            # 把当前筛选后的整个列表带过去, 浮层里才能直接翻上一场 / 下一场;
            # 同时把「这页是谁的生涯」传下去 —— 看好友单排的对局时,
            # 详情必须按好友的 puuid 解析, 按当前登录账号会一个人都对不上
            window.openMatchDetail(
                game, list(self.filtered), getattr(self, 'puuid', '') or '')

    def __openCareer(self, puuid):
        signalBus.toCareerInterface.emit(puuid)

    async def __updateRecentTeammates(self):
        try:
            self.recentTeammatesInfo = await getRecentTeammates(
                self.allGames, self.puuid)
        except Exception:
            self.recentTeammatesInfo = None

        self.__renderTeammates()

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def __clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    @staticmethod
    def __emptyRow(text: str, height: int = 60) -> QLabel:
        label = QLabel(text)
        label.setFixedHeight(height)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color: {color('textMuted')}; " + font(10))
        return label


class _TeamRow(QWidget):
    """最近队友的一行：头像 + 名字 + 同队场次 + 共同胜率

    只写「同队 5 场」的话，半年前打的 5 场和昨天打的 5 场看起来一样，
    所以共同胜率也一起给出来。
    """

    opened = pyqtSignal(str)

    def __init__(self, summoner: dict, parent=None):
        super().__init__(parent)
        self.puuid = summoner.get('puuid') or ''
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)

        icon = QLabel(self)
        icon.setFixedSize(24, 24)
        icon.setScaledContents(True)
        pix = QPixmap(summoner.get('icon') or '')
        if not pix.isNull():
            icon.setPixmap(pix)

        total = summoner.get('total', 0)
        wins = summoner.get('wins', 0)
        rate = wins * 100 // max(1, total)

        self.nameLabel = QLabel(summoner.get('name') or '--', self)
        self.metaLabel = QLabel(f'同队 {total} 场', self)
        self.rateLabel = QLabel(f'{rate}%', self)
        self.rateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.rateLabel.setFixedWidth(38)
        self.__rate = rate

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)
        layout.addWidget(icon)
        layout.addWidget(self.nameLabel, 1)
        layout.addWidget(self.metaLabel)
        layout.addWidget(self.rateLabel)

        self.__applyStyle()
        theme.register(self)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__applyStyle()

    def __applyStyle(self, *_):
        # 这行原来把颜色拼死在构造里, 浅色主题下名字是近白色 —— 白底上看不见
        self.nameLabel.setStyleSheet(f"color: {color('text')}; " + font(12))
        self.metaLabel.setStyleSheet(
            f"color: {color('textMuted')}; " + mono(11))

        if self.__rate >= 60:
            tone = color('win')
        elif self.__rate >= 50:
            tone = color('accent')
        else:
            tone = color('red')

        self.rateLabel.setStyleSheet(f"color: {tone}; " + mono(11, bold=True))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.puuid:
            self.opened.emit(self.puuid)
        super().mouseReleaseEvent(e)
