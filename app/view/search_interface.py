import re
import time
from asyncio import LifoQueue
from collections import OrderedDict

from qasync import asyncSlot
import asyncio
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QFrame, QPushButton,
                             QSpacerItem, QSizePolicy, QLabel, QStackedWidget,
                             QWidget)
from PyQt5.QtCore import Qt, pyqtSignal

from ..common.logger import logger
from ..common.qfluentwidgets import (PushButton, ToolButton, InfoBar, InfoBarPosition,
                                     IndeterminateProgressRing, ComboBox)

from app.common.design_tokens import color, font, mono
from app.common import theme
from app.common.style_sheet import StyleSheet
from app.common.icons import Icon
from app.common.config import cfg
from app.common.signals import signalBus
from app.components.match_detail_view import MatchDetailView
from app.components.champion_icon_widget import RoundIcon
from app.components.search_line_edit import SearchLineEdit
from app.components.animation_frame import ColorAnimationFrame
from app.components.color_label import DeathsLabel
from app.lol.connector import connector
from app.lol.exceptions import SummonerGamesNotFound, SummonerNotFound
from app.lol.positions import resolve as resolvePositions
from app.lol.tools import parseGameData, parseGameDetailData, parseGamesDataConcurrently
from ..components.seraphine_interface import SeraphineInterface


TAG = "SearchInterface"


def asyncLockDecorator(lockName):
    """
    给任何一个方法加锁, return时释放
    args[0]是self, 通过getattr找到lockName
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            lock = getattr(args[0], lockName)
            async with lock:
                return await func(*args, **kwargs)
        return wrapper
    return decorator


class GamesTab(QFrame):
    tabClicked = pyqtSignal(str)
    gameDetailReady = pyqtSignal(dict)
    loadFinish = pyqtSignal()

    def __init__(self, parnet=None):
        super().__init__(parnet)
        self.setFixedWidth(160)
        self.vBoxLayout = QVBoxLayout(self)

        self.stackWidget = QStackedWidget()
        self.buttonsLayout = QHBoxLayout()

        self.prevButton = ToolButton(Icon.CHEVRONLEFT)
        self.pageLabel = QLabel(" ")
        self.nextButton = ToolButton(Icon.CHEVRONRIGHT)

        # 从生涯界面里调过来，需要被选中但还没被选中的 gameId
        self.waitingForSelect = None

        # 当前选中的 queueId，-1 对应所有对局
        self.queueId = -1

        # 当前所在的页码
        self.currentPageNum = 0

        # 目前已经绘制好的最大页码
        self.maxPageNum = 0

        # 目前选中的 tab
        self.currentTabSelected = None

        # 所有的对局记录
        self.games = LifoQueue()  # LifoQueue 保证线程安全 -- By Hpero4

        # queueId 到对应 self.games 下标数组的映射
        # OrderedDict 保证线程安全 -- By Hpero4
        self.queueIdMap = OrderedDict({-1: []})

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.pageLabel.setAlignment(Qt.AlignCenter)
        self.prevButton.setVisible(False)
        self.prevButton.setEnabled(False)
        self.nextButton.setVisible(False)
        self.nextButton.setEnabled(False)

    def __initLayout(self):
        defaultWidget = QWidget()
        layout = QVBoxLayout(defaultWidget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stackWidget.addWidget(defaultWidget)
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.buttonsLayout.addWidget(self.prevButton)
        self.buttonsLayout.addWidget(self.pageLabel)
        self.buttonsLayout.addWidget(self.nextButton)

        self.vBoxLayout.addWidget(self.stackWidget)
        self.vBoxLayout.addSpacing(10)
        self.vBoxLayout.addLayout(self.buttonsLayout)

    def __connectSignalToSlot(self):
        self.prevButton.clicked.connect(self.__onPrevButtonClicked)
        self.nextButton.clicked.connect(self.__onNextButtonClicked)

    def updateQueueIdMap(self, games):
        for game in games:
            index = len(self.games)
            self.games.append(game)
            queueId = game['queueId']

            l: list = self.queueIdMap.get(queueId)

            if not l:
                self.queueIdMap[queueId] = [index]
            else:
                l.append(index)

            self.queueIdMap[-1].append(index)

    @asyncSlot()
    async def __onPrevButtonClicked(self):
        self.currentPageNum -= 1
        self.pageLabel.setText(f"{self.currentPageNum}")
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.resetButtonEnabled()

    @asyncSlot()
    async def __onNextButtonClicked(self):
        self.currentPageNum += 1

        if self.currentPageNum > self.maxPageNum:
            # self.games 一定足够绘制下一页，因为否则这个按钮本身就点不了
            self.prepareNextPage()

        self.pageLabel.setText(f"{self.currentPageNum}")
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.resetButtonEnabled()

    def showTheFirstPage(self):
        self.prepareNextPage()

        self.stackWidget.setCurrentIndex(1)
        self.currentPageNum = 1
        self.pageLabel.setText("1")

        self.nextButton.setVisible(True)
        self.prevButton.setVisible(True)
        self.pageLabel.setVisible(True)

        self.resetButtonEnabled()

    def resetButtonEnabled(self):
        prevEnable = not self.currentPageNum in [0, 1]
        self.prevButton.setEnabled(prevEnable)

        nextEnable = len(
            self.queueIdMap.get(self.queueId, [])) > self.currentPageNum * 10
        self.nextButton.setEnabled(nextEnable)

    def prepareNextPage(self):
        ''' 满足
        - 当前所在页码 == 已经绘制好的最大页码时
        - 在内存中的对局记录数量大于已经绘制好的数量时

        调用该函数，绘制下一页，加入 stackedWidget
        '''

        # 游戏数据在 self.games 数组中对应的下标
        indices = self.queueIdMap[self.queueId]

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        begin = self.maxPageNum * 10

        # 防止能画上去的不够 10 个导致越界
        end = min(10 + begin, len(indices))

        for i in range(begin, end):
            tab = GameTab(self.games[indices[i]])
            waiting = self.waitingForSelect

            if waiting and tab.gameId == int(self.waitingForSelect):
                tab.setProperty('selected', True)
                tab.style().polish(tab)
                self.currentTabSelected = tab
                self.waitingForSelect = None

            layout.addWidget(tab, stretch=1)

        # 如果不够十个就给它填充一下
        if end - begin < 10:
            layout.addStretch(10 - (end - begin))
            spacing = layout.spacing()
            layout.addSpacing((10 - (end - begin)) * spacing)

        self.stackWidget.addWidget(widget)
        self.maxPageNum += 1

    def clearTabs(self):
        self.currentPageNum = 0
        self.maxPageNum = 0
        self.currentTabSelected = None

        for i in reversed(range(len(self.stackWidget))):
            if i == 0:
                continue

            widget = self.stackWidget.widget(i)
            self.stackWidget.removeWidget(widget)

        self.stackWidget.setCurrentIndex(0)

    def clear(self):
        self.pageLabel.setText(" ")

        self.prevButton.setVisible(False)
        self.nextButton.setVisible(False)

        self.queueId = -1
        self.games = []
        self.queueIdMap = {-1: []}

        self.clearTabs()

    def clickFirstTab(self):
        widget = self.stackWidget.widget(1)
        tab = widget.layout().itemAt(0).widget()

        tab.setProperty("pressed", False)
        tab.setProperty("selected", True)
        tab.style().polish(tab)
        signalBus.gameTabClicked.emit(tab)


class GamesView(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.stackWidget = QStackedWidget()

        self.emptyPage = SearchEmptyState()
        self.loadingPage = QWidget()
        self.infoPage = QWidget()

        self.infoLayout = QHBoxLayout(self.infoPage)
        self.gamesTab = GamesTab()
        self.gameDetailView = MatchDetailView()

        self.loadingLayout = QHBoxLayout(self.loadingPage)
        self.processRing = IndeterminateProgressRing()

        self.__initLayout()

    def __initLayout(self):
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.infoLayout.setSpacing(0)

        self.infoLayout.addWidget(self.gamesTab)
        self.infoLayout.addWidget(self.gameDetailView)

        self.loadingLayout.addWidget(self.processRing)

        # 0 = 空态, 1 = 加载中, 2 = 结果
        self.stackWidget.addWidget(self.emptyPage)
        self.stackWidget.addWidget(self.loadingPage)
        self.stackWidget.addWidget(self.infoPage)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addWidget(self.stackWidget)
        self.stackWidget.setCurrentIndex(0)

    def setLoadingPageEnable(self, enable):
        self.stackWidget.setCurrentIndex(1 if enable else 2)

    def showEmpty(self):
        self.stackWidget.setCurrentIndex(0)
        self.emptyPage.refreshHistory()


class GameTab(ColorAnimationFrame):
    def __init__(self, game=None, parent=None):
        if game['remake']:
            type = 'remake'
        elif game['win']:
            type = 'win'
        else:
            type = 'lose'

        super().__init__(type=type, parent=parent)

        self.setFixedHeight(54)
        self.setFixedWidth(141)

        self.setProperty("selected", False)

        self.vBoxLayout = QHBoxLayout(self)
        self.nameTimeKdaLayout = QVBoxLayout()
        self.infoLayout = QHBoxLayout()

        self.gameId = game["gameId"]
        self.championIcon = RoundIcon(game["championIcon"], 32, 2, 2)

        self.modeName = QLabel(game["name"].replace("排位赛 ", ""))

        self.kills = QLabel(str(game['kills']))
        self.slash1 = QLabel("/")
        self.deaths = DeathsLabel(str(game['deaths']))
        self.slash2 = QLabel("/")
        self.assists = QLabel(str(game['assists']))

        self.time = QLabel(game['shortTime'])

        self.resultLabel = QLabel()

        if game["remake"]:
            self.resultLabel.setText(self.tr("重开"))
        elif game["win"]:
            self.resultLabel.setText(self.tr("胜"))
        else:
            self.resultLabel.setText(self.tr("负"))

        self.remake = game['remake']
        self.win = game['win']

        self.__initWidget()
        self.__initLayout()

        self.clicked.connect(lambda: signalBus.gameTabClicked.emit(self))

    def __initWidget(self):
        self.kills.setObjectName("kills")
        self.slash1.setObjectName("slash1")
        self.deaths.setObjectName("deaths")
        self.slash2.setObjectName("slash2")
        self.assists.setObjectName("assists")
        self.kills.setAlignment(Qt.AlignCenter)
        self.slash1.setAlignment(Qt.AlignCenter)
        self.deaths.setAlignment(Qt.AlignCenter)
        self.slash2.setAlignment(Qt.AlignCenter)
        self.assists.setAlignment(Qt.AlignCenter)

        self.time.setObjectName("time")

        self.kills.setFixedWidth(12)
        self.slash1.setFixedWidth(6)
        self.deaths.setFixedWidth(12)
        self.slash2.setFixedWidth(6)
        self.assists.setFixedWidth(12)

    def __initLayout(self):
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.infoLayout.setSpacing(0)

        self.infoLayout.addWidget(self.time)
        self.infoLayout.addSpacing(5)
        self.infoLayout.addWidget(self.kills)
        self.infoLayout.addWidget(self.slash1)
        self.infoLayout.addWidget(self.deaths)
        self.infoLayout.addWidget(self.slash2)
        self.infoLayout.addWidget(self.assists)

        self.nameTimeKdaLayout.addWidget(self.modeName)
        self.nameTimeKdaLayout.addLayout(self.infoLayout)

        self.vBoxLayout.setContentsMargins(7, 7, 7, 7)
        self.vBoxLayout.addWidget(self.championIcon)
        self.vBoxLayout.addLayout(self.nameTimeKdaLayout)

        self.vBoxLayout.addSpacerItem(QSpacerItem(
            1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))


def hintStyle(hint):
    """搜索空态的提示文字

    这段是 RichText，中间加粗的那半句自带颜色 —— 颜色写在文本里，
    光重设样式改不到它，所以文本和样式必须一起重算。
    """
    hint.setText(
        '输入召唤师名字或「名字#编号」后回车；也可以在'
        f'<b style="color:{color("textSub")}">对局信息页右键任意玩家卡</b>，<br>'
        '直接跳到这个人的生涯 —— 不用先把名字抄下来。')

    return f"color: {color('textMuted')}; " + font(11)


class SearchEmptyState(QWidget):
    """搜索页空态（设计稿 S6）

    没查过人时页面只剩一个输入框，用户不知道这里能干什么。
    空态一次说清「查谁 / 怎么查 / 快捷键」，并把历史查询做成可点的胶囊。
    """

    picked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('searchEmpty')
        # QWidget 默认不画 QSS 的边框，得开 WA_StyledBackground
        self.setAttribute(Qt.WA_StyledBackground, True)

        glyph = QLabel('⌕', self)
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedSize(40, 40)
        theme.styled(glyph, lambda: (
            f"color: {color('textMuted')}; border: 1px solid {color('line')}; "
            + mono(20)))

        title = QLabel('查谁？', self)
        title.setAlignment(Qt.AlignCenter)
        theme.styled(title,
                     lambda: f"color: {color('textSub')}; " + font(14))

        # 文本和样式都由 hintStyle 一起生成：中间加粗那半句的颜色写在 RichText 里，
        # 只重设样式改不到它
        hint = QLabel(self)
        hint.setTextFormat(Qt.RichText)
        hint.setAlignment(Qt.AlignCenter)
        theme.styled(hint, lambda: hintStyle(hint))

        keys = QHBoxLayout()
        keys.setContentsMargins(0, 0, 0, 0)
        keys.setSpacing(8)
        keys.addStretch(1)
        for text in ('Ctrl + V 粘贴 ID', 'Enter 查询', 'Esc 清空'):
            keys.addWidget(self.__kbd(text))
        keys.addStretch(1)

        self.__history = QHBoxLayout()
        self.__history.setContentsMargins(0, 12, 0, 0)
        self.__history.setSpacing(8)
        self.__historyBox = QWidget(self)
        self.__historyBox.setLayout(self.__history)
        theme.styled(self.__historyBox, lambda: (
            f'border-top: 1px solid {color("line")}; padding-top: 12px;'))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addStretch(1)
        layout.addWidget(glyph, 0, Qt.AlignHCenter)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(keys)
        layout.addSpacing(2)
        layout.addWidget(self.__historyBox, 0, Qt.AlignHCenter)
        layout.addStretch(1)

        self.refreshHistory()

    def __kbd(self, text: str) -> QLabel:
        label = QLabel(text)
        theme.styled(label, lambda: (
            f"color: {color('textMuted')}; border: 1px solid {color('lineStrong')}; "
            f"padding: 2px 7px; " + mono(9)))
        return label

    def refreshHistory(self):
        """历史查询来自 cfg.searchHistory，和输入框的候选条同源"""
        while self.__history.count():
            item = self.__history.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        raw = cfg.get(cfg.searchHistory) or ''
        names = [name for name in raw.split(',') if name.strip()][:4]

        if not names:
            self.__historyBox.setVisible(False)
            return

        self.__historyBox.setVisible(True)
        head = QLabel('最近查询')
        theme.styled(head,
                     lambda: f"color: {color('textMuted')}; " + mono(9))
        self.__history.addWidget(head)

        for name in names:
            btn = QPushButton(name)
            btn.setFixedHeight(22)
            btn.setCursor(Qt.PointingHandCursor)
            theme.styled(btn, lambda: (
                f"QPushButton {{ color: {color('textSub')}; background: transparent;"
                f"  border: 1px solid {color('lineStrong')}; padding: 0 8px; {font(10)} }}"
                f"QPushButton:hover {{ color: {color('brand')};"
                f"  border-color: {color('brand')}; }}"))
            btn.clicked.connect(lambda _, n=name: self.picked.emit(n))
            self.__history.addWidget(btn)

        clear = QPushButton('清空')
        clear.setFixedHeight(22)
        clear.setCursor(Qt.PointingHandCursor)
        theme.styled(clear, lambda: (
            f"QPushButton {{ color: {color('textMuted')}; background: transparent;"
            f"  border: 1px dashed {color('lineStrong')}; padding: 0 8px; {font(10)} }}"
            f"QPushButton:hover {{ color: {color('red')};"
            f"  border-color: {color('red')}; }}"))
        clear.clicked.connect(self.__clearHistory)
        self.__history.addWidget(clear)

    def __clearHistory(self):
        cfg.set(cfg.searchHistory, '')
        self.refreshHistory()


class SearchInterface(SeraphineInterface):
    summonerPuuidGetted = pyqtSignal(str)
    gamesNotFound = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.puuid = 0
        self.gameLoadingTask: asyncio.Task = None

        self.vBoxLayout = QVBoxLayout(self)

        self.searchLayout = QHBoxLayout()
        self.searchLineEdit = SearchLineEdit()
        self.careerButton = PushButton(self.tr("生涯"))
        self.filterComboBox = ComboBox()

        self.gamesView = GamesView()
        self.currentSummonerName = None

        self.detailViewLoadTask = None
        self.loadingGameId = 0

        self.loadFirstPageLock = asyncio.Lock()

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.searchLineEdit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.searchLineEdit.setClearButtonEnabled(True)
        self.searchLineEdit.setPlaceholderText('输入召唤师名称')
        self.searchLineEdit.setFixedHeight(40)
        # 输入框本身透明，边框交给外层 searchBar 画，整条看起来是一个控件
        theme.styled(self.searchLineEdit, lambda: (
            "SearchLineEdit { background: transparent; border: none; "
            f"color: {color('text')}; " + font(13) + " }"))

        self.searchHint = QLabel('支持「名字#编号」或纯名字')
        theme.styled(self.searchHint,
                     lambda: f"color: {color('textMuted')}; " + font(10))

        self.searchButton = QPushButton('查询')
        self.searchButton.setFixedSize(72, 40)
        self.searchButton.setCursor(Qt.PointingHandCursor)
        theme.styled(self.searchButton, lambda: (
            f"QPushButton {{ color: {color('bg0')}; background: {color('brand')};"
            f"  border: 1px solid {color('brand')}; {font(12, bold=True)} }}"
            f"QPushButton:hover {{ background: {color('gold')};"
            f"  border-color: {color('gold')}; }}"
            f"QPushButton:pressed {{ background: {color('gold')}; }}"))
        self.searchButton.clicked.connect(self.searchLineEdit.searchButton.click)
        # 输入框自带一个放大镜按钮，我们已经在右侧放了「查询」，两个会打架
        self.searchLineEdit.searchButton.setVisible(False)

        self.careerButton.setText('生涯')
        self.careerButton.setFixedHeight(40)
        self.careerButton.setEnabled(False)
        self.careerButton.setCursor(Qt.PointingHandCursor)
        theme.styled(self.careerButton, lambda: (
            f"QPushButton {{ color: {color('textSub')}; background: transparent;"
            f"  border: 1px solid {color('lineStrong')}; padding: 0 12px; {font(11)} }}"
            f"QPushButton:hover {{ color: {color('brand')}; border-color: {color('brand')}; }}"
            f"QPushButton:disabled {{ color: {color('textMuted')};"
            f"  border-color: {color('line')}; }}"))

        self.filterComboBox.setFixedSize(96, 40)
        self.filterComboBox.setEnabled(False)

        StyleSheet.SEARCH_INTERFACE.apply(self)

        self.filterComboBox.addItems(
            ['全部', '匹配', '大乱斗', '单双排', '灵活组排'])
        self.filterComboBox.setCurrentIndex(0)

        self.gamesView.emptyPage.picked.connect(self.__onHistoryPicked)

    def __buildPageHead(self) -> QWidget:
        """面包屑 + 页面标题 + 右侧快捷键提示（设计稿 S6）"""
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        crumb = QLabel('总览')
        theme.styled(crumb,
                     lambda: f"color: {color('textMuted')}; " + font(11))
        sep = QLabel('›')
        theme.styled(sep,
                     lambda: f"color: {color('textMuted')}; " + font(11))
        title = QLabel('战绩查询')
        theme.styled(title,
                     lambda: f"color: {color('text')}; " + font(18, bold=True))

        row.addWidget(crumb)
        row.addWidget(sep)
        row.addWidget(title)
        row.addStretch(1)

        tip = QLabel('任意页面按')
        theme.styled(tip,
                     lambda: f"color: {color('textMuted')}; " + font(11))
        kbd = QLabel('Ctrl + F')
        theme.styled(kbd, lambda: (
            f"color: {color('textSub')}; border: 1px solid {color('lineStrong')};"
            f" padding: 2px 6px; " + mono(9)))
        arrive = QLabel('可直达')
        theme.styled(arrive,
                     lambda: f"color: {color('textMuted')}; " + font(11))

        row.addWidget(tip)
        row.addWidget(kbd)
        row.addWidget(arrive)
        return head

    def __buildSearchBar(self) -> QWidget:
        """整条搜索框：放大镜 + 输入 + 提示 + 查询 + 生涯 + 模式筛选"""
        bar = QFrame()
        bar.setObjectName('searchBar')
        bar.setFixedHeight(40)
        theme.styled(bar, lambda: (
            f"QFrame#searchBar {{ background: {color('bg1')};"
            f"  border: 1px solid {color('line')}; }}"))

        glyph = QLabel('⌕')
        glyph.setFixedWidth(16)
        glyph.setAlignment(Qt.AlignCenter)
        theme.styled(glyph,
                     lambda: f"color: {color('textMuted')}; " + mono(16))

        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 6, 0)
        row.setSpacing(8)
        row.addWidget(glyph)
        row.addWidget(self.searchLineEdit, 1)
        row.addWidget(self.searchHint)
        row.addWidget(self.searchButton)
        row.addWidget(self.careerButton)

        # 下拉框跟着 40px 的条走，单独包一层免得被 QSS 的圆角/内边距带偏
        self.filterComboBox.move(0, 0)
        row.addWidget(self.filterComboBox)
        return bar

    def __initLayout(self):
        self.vBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.addWidget(self.__buildPageHead())
        self.vBoxLayout.addWidget(self.__buildSearchBar())
        self.vBoxLayout.addWidget(self.gamesView, 1)

    def __onHistoryPicked(self, name: str):
        self.searchLineEdit.setText(name)
        self.searchButton.click()

    def setEnabled(self, a0: bool) -> None:
        self.gamesView.gameDetailView.clear()
        self.gamesView.gamesTab.clear()
        self.searchLineEdit.clear()
        # 断开客户端时退回空态，别留一个空白的结果页
        self.gamesView.showEmpty()

        self.searchLineEdit.setEnabled(a0)
        self.searchLineEdit.searchButton.setEnabled(a0)

        if not a0:
            self.filterComboBox.setEnabled(a0)

        return super().setEnabled(a0)

    def __connectSignalToSlot(self):
        # 回车挂在可见的「查询」上：输入框内置按钮已经藏起来，
        # 快捷键挂在隐藏控件上不会触发
        self.searchButton.setShortcut(Qt.Key.Key_Return)
        self.searchLineEdit.searchButton.clicked.connect(
            self.onSearchButtonClicked)

        self.careerButton.clicked.connect(
            lambda: signalBus.toCareerInterface.emit(self.puuid))
        self.filterComboBox.currentIndexChanged.connect(
            self.__onFilterComboxChanged)

        signalBus.gameTabClicked.connect(self.__onGameTabClicked)

    def __showSummonerNotFoundMsg(self):
        InfoBar.error(
            title=self.tr("找不到该召唤师"),
            content=self.tr("请检查召唤师名称后重试"),
            orient=Qt.Vertical,
            duration=5000,
            position=InfoBarPosition.BOTTOM_RIGHT,
            parent=self
        )

    # Fix: 超快速的在候选栏选中两次同样puuid会起两个task加载战绩, 100%干掉客户端 -- By Hpero4
    @asyncLockDecorator('loadFirstPageLock')
    async def searchAndShowFirstPage(self, puuid=None):
        name = self.searchLineEdit.text()
        if name == "":
            return False

        if re.match(r"\S+-\S+-\S+-\S+-\S+", name):
            summoner = await connector.getSummonerByPuuid(name)
        else:
            summoner = await connector.getSummonerByName(name)

        if 'errorCode' in summoner:
            self.__showSummonerNotFoundMsg()
            return False

        self.gamesView.setLoadingPageEnable(True)

        self.careerButton.setEnabled(True)
        self.filterComboBox.setEnabled(True)

        self.gamesView.gameDetailView.clear()

        # NOTE 如果是生涯和搜索反复横跳, 就不重新启 loadgames 任务了
        if puuid != self.puuid:
            if self.gameLoadingTask:
                self.gameLoadingTask.cancel()

                while not self.gameLoadingTask.cancelled() \
                        and not self.gameLoadingTask.done():
                    await asyncio.sleep(.3)

            self.puuid = summoner['puuid']
            self.gamesView.gamesTab.clear()

            # 先加载两页，让用户看着
            try:
                games = await connector.getSummonerGamesByPuuid(self.puuid, 0, 19)
            except SummonerGamesNotFound:
                games = []
            else:
                # 分路单独取（SGP），缓存命中后就是一次字典查询
                positions = await resolvePositions(self.puuid, games['games'])
                games = await parseGamesDataConcurrently(games['games'], positions)

            if len(games) == 0:
                self.gamesView.gamesTab.nextButton.setVisible(False)
                self.gamesView.gamesTab.prevButton.setVisible(False)
                self.filterComboBox.setEnabled(False)
                self.gamesView.setLoadingPageEnable(False)
                return False

            self.gamesView.gamesTab.updateQueueIdMap(games)

            # 启动任务，往 gamesTab 里丢数据
            # NOTE 既然创建新任务, 并且刷新了self.puuid 就应该用self的, 否则就违背了loadGames判断的初衷

            self.gameLoadingTask = asyncio.create_task(
                self.__loadGames(self.puuid))

        self.gamesView.gamesTab.showTheFirstPage()
        self.gamesView.setLoadingPageEnable(False)

        self.__addSearchHistroy(name)

        return True

    def waitingForDrawSelect(self, gameId):
        '''
        从生涯界面点下面进来的时候，绘制选中的提示框
        '''
        tabs = self.gamesView.gamesTab

        # 遍历一下目前已经画好的第一页有没有这个 tab，有的话就直接画上
        # FIXME -- By Hpero4
        #  如果在绘制前Tabs的StackWidget改变, 会导致画错框甚至找不到绘制对象 AttributeError
        #  必须保证绘制时界面没有被改变; (目前暂时是将耗时操作移到画框后面)
        # FIXME -- By Hpero4
        #  如果选中的对局不在第一页中(> 11), 将不会画上选择框
        layout = tabs.stackWidget.widget(1).layout()
        for i in range(layout.count()):
            item = layout.itemAt(i)

            if not item.widget():
                continue

            widget: GameTab = item.widget()

            if widget.gameId == int(gameId):
                tabs.currentTabSelected = widget
                widget.setProperty("selected", True)
                widget.style().polish(widget)
                widget.repaint()
            else:
                widget.setProperty("selected", False)
                widget.style().polish(widget)

        if not tabs.currentTabSelected:
            tabs.waitingForSelect = gameId

    @asyncSlot()
    async def onSearchButtonClicked(self):
        if not await self.searchAndShowFirstPage():
            return

        self.filterComboBox.setCurrentIndex(0)  # 将筛选条件回调至"全部" -- By Hpero4
        self.gamesView.gamesTab.clickFirstTab()

    async def __loadGames(self, puuid):
        begIdx = 20
        endIdx = 29

        logger.debug(f"welcome load games task: {puuid}", TAG)

        # NOTE 换了查询目标, 若之前正在查, 先等 task 被 release 掉 -- By Hpero4
        while self.gameLoadingTask \
                and not self.gameLoadingTask.done() \
                and puuid != self.puuid:
            await asyncio.sleep(.2)

        logger.debug(f"start load {puuid}", TAG)
        # 连续查多个人时, 将前面正在查的task给release掉
        while self.puuid == puuid:
            # 为加载战绩详情让行
            while (
                (self.detailViewLoadTask and not self.detailViewLoadTask.done())
                or
                (self.window().careerInterface.loadGamesTask and not self.window(
                ).careerInterface.loadGamesTask.done())
            ):
                await asyncio.sleep(.2)

            t1 = time.time()
            try:
                games = await connector.getSummonerGamesByPuuidSlowly(
                    puuid, begIdx, endIdx)
            except SummonerGamesNotFound:
                # TODO 这里可以弹个窗  -- By Zzaphkiel
                # NOTE 触发 SummonerGamesNotFound 时, 异常信息会通过 connector 下发到 main_window 的 __onShowLcuConnectError
                #  理论上会有弹框提示  -- By Hpero4
                return
            t2 = time.time()

            logger.debug(
                f"load games {self.puuid} [{begIdx}-{endIdx}] finish {t2-t1}s", TAG)
            # 1000 局搜完了，或者正好上一次就是最后
            # 在切换了puuid时, 就不要再把数据刷到Games上了 -- By Hpero4
            if games['gameCount'] == 0 or self.puuid != puuid:
                return

            # 处理数据，交给 gamesTab，更新其 games 成员以及 queueIdMap
            positions = await resolvePositions(puuid, games['games'])
            games = await parseGamesDataConcurrently(games['games'], positions)

            if self.puuid != puuid:
                return

            self.gamesView.gamesTab.updateQueueIdMap(games)

            # 如果用户下一页点得太猛，在还没加载完的时候点到了能绘制的最后一页
            # 由于 __onNextButtonClicked 的逻辑，在用户进入最后一页时，nextButton 会被设置为不可用。
            # 而现在，由于新的两页对局数据加载好了，可以绘制上去了，要让 button 变得可用
            self.gamesView.gamesTab.resetButtonEnabled()

            # 如果长度小于 10，也说明搜完了已经
            if len(games) < 10:
                return

            begIdx = endIdx + 1
            endIdx = begIdx + 9

            # 睡不睡都行
            await asyncio.sleep(.1)

    @asyncSlot(QWidget)
    async def __onGameTabClicked(self, tab: GameTab):
        tabs: GamesTab = self.gamesView.gamesTab
        cur: GameTab = tabs.currentTabSelected

        if tab is cur:
            return

        self.gamesView.gameDetailView.clear()

        tab.setProperty("selected", True)
        tab.style().polish(tab)

        if cur:
            cur.setProperty("selected", False)
            cur.style().polish(cur)

        tabs.currentTabSelected = tab
        self.loadingGameId = tab.gameId
        await self.updateGameDetailView(tab.gameId, self.puuid)

    async def updateGameDetailView(self, gameId, puuid):
        # if cfg.get(cfg.showTierInGameInfo):
        if True:
            self.gamesView.gameDetailView.setLoadingPageEnabled(True)

        # NOTE self.detailViewLoadTask 用于标记详情正在加载, self.loadGame会为其让行 -- By Hpero4
        self.detailViewLoadTask = asyncio.create_task(
            connector.getGameDetailByGameId(gameId))
        game = await self.detailViewLoadTask

        # 加载GameDetail的过程中, 切换了搜索对象(self.puuid变更), 将后续任务pass -- By Hpero4
        if puuid == self.puuid:
            self.detailViewLoadTask = asyncio.create_task(
                parseGameDetailData(puuid, game))
            game = await self.detailViewLoadTask
            self.gamesView.gameDetailView.updateGame(game)

        # if cfg.get(cfg.showTierInGameInfo):
        if True:
            self.gamesView.gameDetailView.setLoadingPageEnabled(False)

    @asyncSlot(int)
    async def __onFilterComboxChanged(self, index):
        self.gamesView.gameDetailView.clear()
        tabs = self.gamesView.gamesTab
        tabs.clearTabs()

        ids = (-1, 430, 450, 420, 440)
        tabs.queueId = ids[index]

        self.gamesView.setLoadingPageEnable(True)

        while len(self.gamesView.gamesTab.queueIdMap.get(tabs.queueId, [])) < 10:
            await asyncio.sleep(.2)

        enable = tabs.queueIdMap.get(tabs.queueId) is not None
        tabs.prevButton.setVisible(enable)
        tabs.nextButton.setVisible(enable)
        tabs.nextButton.setVisible(enable)

        self.gamesView.setLoadingPageEnable(False)

        if enable:
            self.gamesView.gamesTab.showTheFirstPage()

    def __addSearchHistroy(self, name):
        history: list = cfg.get(cfg.searchHistory).split(',')

        if name in history:
            history.remove(name)

        history.insert(0, name)
        cfg.set(cfg.searchHistory, ",".join(
            [t for t in history if t][:10]), True)
