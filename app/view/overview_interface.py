# coding:utf-8
"""总览页

首屏要回答一个问题：「我是谁、现在什么状态、有什么新改动」。

版面（对应设计稿 S0）：
    ┌ 状态条：客户端 / 账号 / 对局 ──────────── 自动刷新 · 查战绩 · 设置 ┐
    ├ 左列 62% ───────────────────────┬ 右列 38% ──────────────┤
    │ [01] 我的生涯                  │ [04] 我的战绩          │
    │ [02] 近期改动                  │      模式分段 + 列表   │
    │ [03] 今日对战                  │ [05] 常用英雄          │
    │      快捷入口                  │                        │
    └────────────────────────────────┴────────────────────────┘

数据不自己拉：生涯页 `repaintInterface()` 里已经算好一份 `info`
（段位 / 近 N 场 / 英雄统计），通过 `infoLoaded` 信号送过来。
两处各自请求会让 LCU 同一份数据拉两遍，还会出现两边数字不一致。
"""
import datetime
import webbrowser

from PyQt5.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from app.common.config import cfg
from app.common.design_tokens import color, font, mono
from app.common import theme
from app.common.signals import signalBus
from app.common.style_sheet import StyleSheet
from app.components.game_row import GameRow
from app.components.pager import Pager
from app.components.patch_news import MicroBadge, PatchNewsRow
from app.components.segment import SegmentControl
from app.components.panel import Panel
from app.components.status_dot import StatusDot
from app.components.rank_card import RankLine, splitRank
from app.components.seraphine_interface import SeraphineInterface
from app.lol.patch_notes import (NEWS_HOME, PatchNotesUnavailable,
                                 loadPatchNotes, readCache)

# 队列筛选：0=全部，其余按 queueId 归类
MODES = [
    ('全部', None),
    ('单双', (420,)),
    ('灵活', (440,)),
    ('乱斗', (450,)),
]

# 段位 -> 估算的隐藏分基数（不是官方 MMR，仅用于横向比较）
_MMR_BASE = {
    '黑铁': 400, '黄铜': 800, '白银': 1200, '黄金': 1550,
    '铂金': 1850, '翡翠': 2050, '钻石': 2250, '大师': 2500,
    '宗师': 2800, '王者': 3000,
}
_MMR_DIV = {'Ⅳ': 0, 'Ⅲ': 60, 'Ⅱ': 120, 'Ⅰ': 180, '★': 0}


def estimateMmr(rankRow: list, winRate: float) -> int:
    """用段位 + LP + 近期胜率推一个近似分

    LCU 不返回真实 MMR，所以这个数只能当「大概什么水平」看。
    界面上必须标「估算」，否则用户会拿它跟 OP.GG 对，对不上就是 bug。
    """
    if not rankRow or len(rankRow) < 7:
        return 0

    tier, division = splitRank(rankRow[5])
    if tier not in _MMR_BASE:
        return 0

    lp = rankRow[6]
    lp = int(lp) if str(lp).isdigit() else 0

    base = _MMR_BASE[tier] + _MMR_DIV.get(division, 0) + lp * 0.8
    # 胜率高于 50% 加一点，低于则减，最多 ±120
    base += max(-120, min(120, (winRate - 50) * 6))
    return int(base)


def sameDay(game: dict, day: datetime.date) -> bool:
    stamp = game.get('timeStamp') or 0
    if not stamp:
        return False
    return datetime.date.fromtimestamp(stamp / 1000) == day


class _StatCell(QWidget):
    """生涯块里的一个小指标

    设计稿 `.metric`：**标签在上**（等宽 10px 弱灰）、数字在下（等宽 18px 加粗）。
    之前写反了 —— 数字在上、标签在下，和稿子是镜像的，一眼就能看出不对劲。
    """

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.__label = QLabel(label, self)
        self.__label.setObjectName('statLabel')
        self.__label.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))

        self.__value = QLabel('--', self)
        self.__value.setObjectName('statValue')
        self.__value.setTextFormat(Qt.RichText)
        self.__value.setStyleSheet(f"color: {color('text')}; " + mono(18, bold=True))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        # 上下各留一根弹簧：整组在单元格里垂直居中，
        # 不加的话 QLabel 会被拉长，标签贴着顶、数字贴着底，两行隔了 70px
        layout.addStretch(1)
        layout.addWidget(self.__label)
        layout.addWidget(self.__value)
        layout.addStretch(1)

    def setValue(self, text: str, tone: str = ''):
        self.__value.setText(text)
        self.__value.setStyleSheet(
            f"color: {color(tone) if tone else color('text')}; " + mono(18, bold=True))

    def setHint(self, text: str):
        self.__label.setText(text)


class _QuickItem(QFrame):
    """左列底部的快捷入口

    设计稿是 56px 两行：主标题 13px + 快捷键 10px 等宽。
    用 QFrame 而不是 QPushButton —— 按钮里塞两行 label 要对齐两次，反而更绕。
    """

    clicked = pyqtSignal()

    def __init__(self, title: str, hint: str, parent=None):
        super().__init__(parent)
        self.setObjectName('quickItem')
        self.setFixedHeight(56)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.titleLabel = QLabel(title, self)
        self.hintLabel = QLabel(hint, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(3)
        layout.addStretch(1)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.hintLabel)
        layout.addStretch(1)

        self.__applyStyle()
        theme.register(self)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__applyStyle()

    def __applyStyle(self, *_):
        # 直接写样式而不用属性选择器：QFrame 的默认 palette 是浅色，
        # 属性没匹配上就是一块白底，很难看出是哪一层挂了
        self.setStyleSheet(
            f"QFrame#quickItem {{ background-color: {color('bg1')};"
            f" border: 1px solid {color('line')}; }}"
            f"QFrame#quickItem:hover {{ background-color: {color('bg2')};"
            f" border-color: {color('lineStrong')}; }}")

        self.titleLabel.setStyleSheet(f"color: {color('text')}; " + font(13))
        self.hintLabel.setStyleSheet(
            f"color: {color('textMuted')}; " + mono(10))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class PatchNotesLoader(QThread):
    """官方公告是外网请求，不能压在 UI 线程上

    最快也要 300ms 往返，慢的时候十几秒；同步拉会让整个窗口在启动时白屏。
    """

    loaded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, force: bool = False, parent=None):
        super().__init__(parent)
        self.__force = force

    def run(self):
        try:
            self.loaded.emit(loadPatchNotes(self.__force))
        except PatchNotesUnavailable as error:
            self.failed.emit(str(error) or '获取官方公告失败')
        except Exception as error:  # 解析结构变了也要落回可读文案
            self.failed.emit(f'获取官方公告失败：{error}')


class OverviewInterface(SeraphineInterface):
    """总览"""

    refreshRequested = pyqtSignal()
    detailRequested = pyqtSignal(dict)
    openCareer = pyqtSignal(str)
    openSearch = pyqtSignal()
    openGameInfo = pyqtSignal()
    openSetting = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('overviewInterface')

        self.__info = None
        self.__games = []
        self.__champions = []
        self.__today = []
        self.__records = []
        self.__mode = 0
        self.__clientOnline = False
        self.__gameStatus = 'None'
        self.__todayRows = []
        self.__recordRows = []
        self.__noticeRows = 5
        self.__compact = False
        self.__recentRecord = None
        self.__colHeaders = {}

        # [02] 近期改动：官方版本公告（英雄 / 装备 / 系统），不是软件更新日志
        self.__patchNotes = None
        self.__patchLoader = None
        self.__patchError = ''
        self.__patchRequested = False
        self.__careerUpdatedAt = None

        self.view = QWidget()
        self.view.setObjectName('overviewView')
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setViewportMargins(0, 0, 4, 0)

        self.__initLayout()
        self.__connectSignalToSlot()
        StyleSheet.OVERVIEW_INTERFACE.apply(self)
        self.__loadPatchNotes()

        # 「N 分钟前更新」每分钟自己走一格，不用等下一次刷新
        self.__metaTimer = QTimer(self)
        self.__metaTimer.setInterval(60_000)
        self.__metaTimer.timeout.connect(self.__refreshCareerMeta)
        self.__metaTimer.start()

    # ---------------------------------------------------------------- 布局
    def __initLayout(self):
        self.__statusBar = self.__buildStatusBar()

        self.__careerPanel = self.__buildCareerPanel()
        self.__noticePanel = self.__buildNoticePanel()
        self.__todayPanel = self.__buildTodayPanel()
        self.__recordsPanel = self.__buildRecordsPanel()
        self.__champPanel = self.__buildChampionsPanel()

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        left.addWidget(self.__careerPanel)
        left.addWidget(self.__noticePanel)
        left.addWidget(self.__todayPanel)
        # 上面的高度是算出来的，取整误差留几像素；这根弹簧把余量吃掉，
        # 顺带把快捷入口钉在列底，不会因为面板矮了就往上飘
        left.addStretch(1)
        left.addLayout(self.__buildQuickRow())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        # 设计稿 [04] flex:2.1 / [05] flex:1，约 68:32
        right.addWidget(self.__recordsPanel, 7)
        right.addWidget(self.__champPanel, 3)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(14)
        columns.addLayout(left, 62)
        columns.addLayout(right, 38)

        # 设计稿 .content{padding:16px 20px; gap:12px}
        root = QVBoxLayout(self.view)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        root.addWidget(self.__statusBar)
        root.addLayout(columns, 1)

    def __buildStatusBar(self) -> QWidget:
        """状态条：客户端 / 账号 / 对局 —— 右侧自动刷新提示与刷新区

        设计稿 `.statusbar`：34px 高、项间距 18px，左三项之间是 1x12 细线；
        每项内部是「等宽 10px 弱灰的键 + 12px 的值」，间距 7px。
        「查询战绩」「设置」两个文字按钮不在稿子里，收回导航 ——
        「自动跟随」角标点一下就能进设置，比原来的按钮更省位置。
        """
        bar = QFrame()
        bar.setObjectName('statusBar')
        bar.setFixedHeight(34)

        self.__clientDot = StatusDot('red', 6, bar)
        self.__clientText = QLabel('未连接')
        self.__clientText.setStyleSheet(f"color: {color('red')}; " + font(12))

        self.__accountText = QLabel('--')
        self.__accountText.setStyleSheet(f"color: {color('text')}; " + font(12))
        self.__accountTag = QLabel('')
        self.__accountTag.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))

        self.__gameText = QLabel('\u25cb 空闲')
        self.__gameText.setStyleSheet(f"color: {color('textMuted')}; " + font(12))

        self.__autoDot = StatusDot('win', 6, bar)
        self.__autoLabel = QLabel('对局结束自动刷新')
        self.__autoLabel.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))

        self.__autoBadge = MicroBadge('自动跟随 已开启', 'win')
        self.__autoBadge.setToolTip('在「设置 → 功能」里可以关掉')
        self.__autoBadge.clicked.connect(self.openSetting)

        self.__refreshBtn = self.__iconButton('\u21bb', '立即刷新 (F5)')
        self.__refreshBtn.clicked.connect(self.refreshRequested)

        account = QWidget()
        accountRow = QHBoxLayout(account)
        accountRow.setContentsMargins(0, 0, 0, 0)
        accountRow.setSpacing(3)
        accountRow.addWidget(self.__accountText)
        accountRow.addWidget(self.__accountTag)

        auto = QWidget()
        autoRow = QHBoxLayout(auto)
        autoRow.setContentsMargins(0, 0, 0, 0)
        autoRow.setSpacing(6)
        autoRow.addWidget(self.__autoDot)
        autoRow.addWidget(self.__autoLabel)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(18)
        layout.addWidget(self.__statusItem('客户端', self.__clientDot, self.__clientText))
        layout.addWidget(self.__sep())
        layout.addWidget(self.__statusItem('账号', account))
        layout.addWidget(self.__sep())
        layout.addWidget(self.__statusItem('对局', self.__gameText))
        layout.addStretch(1)
        layout.addWidget(auto)
        layout.addWidget(self.__autoBadge)
        layout.addSpacing(2)
        layout.addWidget(self.__refreshBtn)

        self.__syncAutoFollow()
        try:
            cfg.autoFollowGame.valueChanged.connect(self.__syncAutoFollow)
        except Exception:
            pass
        return bar

    def __syncAutoFollow(self, *_):
        """「自动跟随」角标跟着设置项走，别写死一个「已开启」骗人"""
        try:
            enabled = bool(cfg.get(cfg.autoFollowGame))
        except Exception:
            enabled = True

        self.__autoBadge.setText(f"自动跟随 {'已开启' if enabled else '已关闭'}")
        self.__autoBadge.applyTone('win' if enabled else 'textMuted')
        self.__autoDot.setTone('win' if enabled else 'textMuted')

    def __statusItem(self, key: str, *widgets) -> QWidget:
        """状态条里的一项：键名 + 值（值可能是多个控件），项内间距 7px"""
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        row.addWidget(self.__tag(key))
        for widget in widgets:
            row.addWidget(widget)
        return box

    def __tag(self, text: str) -> QLabel:
        """设计稿 `.statusbar .k`：等宽 10px、弱灰"""
        label = QLabel(text)
        label.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))
        return label

    def __sep(self) -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 12)
        line.setStyleSheet(f"background: {color('line')}; border: none;")
        return line

    def __iconButton(self, glyph: str, tip: str) -> QPushButton:
        """22x22 方角图标按钮（设计稿 `.refresh-btn`）"""
        btn = QPushButton(glyph)
        btn.setFixedSize(22, 22)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        btn.setStyleSheet(
            f"QPushButton {{ color: {color('textMuted')}; background: transparent; "
            f"border: 1px solid {color('line')}; {mono(13)} }}"
            f"QPushButton:hover {{ color: {color('brand')}; border-color: {color('brand')}; "
            f"background: {color('bg2')}; }}")
        return btn

    def __buildCareerPanel(self) -> Panel:
        # 设计稿 S0 里这块是 .panel.primary：底色抬一档 + 四角标
        panel = Panel('01', '我的生涯', primary=True)
        panel.setFixedHeight(125)

        # 设计稿右上角：「3 分钟前更新」+ 只刷这一块的按钮
        self.__careerMeta = QLabel('')
        self.__careerMeta.setStyleSheet(f"color: {color('textMuted')}; " + mono(9))
        panel.addActionSpacing(4)
        panel.addAction(self.__careerMeta)

        self.__careerRefresh = QLabel('↻')
        self.__careerRefresh.setFixedSize(16, 16)
        self.__careerRefresh.setAlignment(Qt.AlignCenter)
        self.__careerRefresh.setCursor(Qt.PointingHandCursor)
        self.__careerRefresh.setStyleSheet(
            f"color: {color('textMuted')}; " + font(12))
        self.__careerRefresh.setToolTip('只刷新生涯数据')
        self.__careerRefresh.mouseReleaseEvent = (
            lambda event: self.refreshRequested.emit())
        panel.addAction(self.__careerRefresh)

        # 单双排 + 灵活组排都要在首屏能看见，各占一行
        self.__rankSolo = RankLine()
        self.__rankFlex = RankLine()
        self.__careerRankBox = QVBoxLayout()
        self.__careerRankBox.setContentsMargins(0, 0, 0, 0)
        self.__careerRankBox.setSpacing(8)
        self.__careerRankBox.addStretch(1)
        self.__careerRankBox.addWidget(self.__rankSolo)
        self.__careerRankBox.addWidget(self.__rankFlex)
        self.__careerRankBox.addStretch(1)

        # 同上：标签在渲染时按实际场次改写
        self.__statRecent = _StatCell('近况')
        self.__statWinRate = _StatCell('胜率')
        self.__statKda = _StatCell('KDA')
        self.__statMmr = _StatCell('隐藏分（估算）')

        statsRow = QHBoxLayout()
        statsRow.setContentsMargins(0, 0, 0, 0)
        # 四个指标之间留口气, 窄窗口下「4 胜 5 负」和「44%」会粘在一起
        statsRow.setSpacing(12)
        for cell in (self.__statRecent, self.__statWinRate,
                     self.__statKda, self.__statMmr):
            statsRow.addWidget(cell, 1)

        body = QHBoxLayout()
        # 设计稿 panel-body 左右各 14px，段位块自己不再留内边距
        body.setContentsMargins(14, 0, 14, 0)
        body.setSpacing(28)
        # 段位块按内容定宽，四个指标均分剩下的宽度 —— 段位块给固定比例的话，
        # 「单双排 · 41 LP」右边会空一大块，看着像两块内容被硬拆开
        body.addLayout(self.__careerRankBox, 0)
        body.addLayout(statsRow, 1)

        panel.body.addLayout(body)
        return panel

    def __buildNoticePanel(self) -> Panel:
        """[02] 近期改动 = 官方版本改动

        这里显示的是 **游戏** 的平衡性调整（英雄 / 装备 / 系统），
        不是本软件的更新日志 —— 软件更新在设置页「关于」里看。
        """
        panel = Panel('02', '近期改动')
        # 5 行 × 44 + 表头 30 + 上下补白；固定高度，否则左列余量全灌进这里
        panel.setFixedHeight(254)

        self.__patchVersion = MicroBadge('— 版本', 'brand')
        self.__patchVersion.setToolTip('当前显示的官方版本')

        self.__patchSource = MicroBadge('官方公告', 'accent')
        self.__patchSource.setCursor(Qt.ArrowCursor)
        self.__patchSource.setToolTip('数据来源：英雄联盟官方版本更新公告')

        # 一版公告通常 20 条左右改动，面板只放得下几行，这里进全量列表
        self.__patchMore = QPushButton('全部 ›')
        self.__patchMore.setFixedHeight(18)
        self.__patchMore.setCursor(Qt.PointingHandCursor)
        self.__patchMore.setStyleSheet(
            f"QPushButton {{ color: {color('textSub')}; background: transparent;"
            f" border: none; padding: 0 2px; {font(10)} }}"
            f"QPushButton:hover {{ color: {color('brand')}; }}")
        self.__patchMore.setToolTip('查看这个版本的全部改动')
        self.__patchMore.setVisible(False)
        self.__patchMore.clicked.connect(self.__openPatchDialog)

        # 成功态也给一个手动刷新：官网刚发版本时不用等 45 分钟缓存过期
        self.__patchRefresh = QPushButton('⟳')
        self.__patchRefresh.setFixedSize(18, 18)
        self.__patchRefresh.setCursor(Qt.PointingHandCursor)
        self.__patchRefresh.setStyleSheet(
            f"QPushButton {{ color: {color('textSub')}; background: transparent;"
            f" border: none; {font(11)} }}"
            f"QPushButton:hover {{ color: {color('brand')}; }}")
        self.__patchRefresh.setToolTip('重新拉取官方版本公告')
        self.__patchRefresh.clicked.connect(
            lambda: self.__loadPatchNotes(force=True))

        panel.addAction(self.__patchVersion)
        panel.addAction(self.__patchSource)
        panel.addAction(self.__patchRefresh)
        panel.addAction(self.__patchMore)

        self.__noticeBox = QVBoxLayout()
        self.__noticeBox.setContentsMargins(0, 4, 0, 0)
        self.__noticeBox.setSpacing(0)
        panel.body.addLayout(self.__noticeBox)
        panel.body.addStretch(1)
        return panel

    def __buildTodayPanel(self) -> Panel:
        panel = Panel('03', '今日对战')
        panel.setMinimumHeight(150)

        # 设计稿 [03] 右上角是「2 胜 / 1 负」两个角标，比「11-26 · 1 局」有用
        self.__todayWin = MicroBadge('0 胜', 'win')
        self.__todayLose = MicroBadge('0 负', 'red')
        for badge in (self.__todayWin, self.__todayLose):
            badge.setCursor(Qt.ArrowCursor)
            badge.setVisible(False)
            panel.addAction(badge)

        self.__todayBox = QVBoxLayout()
        self.__todayBox.setContentsMargins(0, 0, 0, 0)
        self.__todayBox.setSpacing(0)
        panel.body.addLayout(self.__todayBox, 1)

        self.__todayPager = Pager(4, '场')
        self.__todayPager.pageChanged.connect(lambda _: self.__renderToday())
        panel.body.addWidget(self.__todayPager)
        return panel

    def __buildQuickRow(self) -> QHBoxLayout:
        """设计稿 .quick-item：56px 两行，主标题 13px + 快捷键 10px 等宽"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        for title, hint, signal in (
                ('查战绩', 'CTRL + 4', self.openSearch),
                ('对局信息', 'CTRL + 2', self.openGameInfo),
                ('我的生涯', 'CTRL + 3', lambda: self.openCareer.emit(''))):
            item = _QuickItem(title, hint)
            item.clicked.connect(signal)
            row.addWidget(item, 1)
        return row

    def __buildRecordsPanel(self) -> Panel:
        panel = Panel('04', '我的战绩')
        panel.setMinimumHeight(260)

        self.__modeSegment = SegmentControl(MODES)
        self.__modeSegment.changed.connect(self.__onModeChanged)
        panel.addAction(self.__modeSegment)

        # 列表头
        header = QWidget()
        header.setObjectName('rowHeader')
        header.setFixedHeight(20)
        headLayout = QHBoxLayout(header)
        headLayout.setContentsMargins(8, 0, 8, 0)
        headLayout.setSpacing(8)
        headLayout.addSpacing(2)
        self.__colHeaders = {}
        for text, width, align in (('英雄 / 分路', 0, Qt.AlignLeft),
                                   ('KDA', 58, Qt.AlignRight),
                                   ('补刀', 36, Qt.AlignRight),
                                   ('时长', 38, Qt.AlignRight),
                                   ('结果', 20, Qt.AlignCenter)):
            label = QLabel(text)
            label.setStyleSheet(f"color: {color('textMuted')}; " + font(10))
            if width:
                label.setFixedWidth(width)
            label.setAlignment(align | Qt.AlignVCenter)
            self.__colHeaders[text] = label
            if width == 0:
                headLayout.addWidget(label, 1)
            else:
                headLayout.addWidget(label)
        panel.body.addWidget(header)

        self.__recordsBox = QVBoxLayout()
        self.__recordsBox.setContentsMargins(0, 0, 0, 0)
        self.__recordsBox.setSpacing(0)
        panel.body.addLayout(self.__recordsBox, 1)

        self.__recordsPager = Pager(10, '场')
        self.__recordsPager.pageChanged.connect(lambda _: self.__renderRecords())
        panel.body.addWidget(self.__recordsPager)

        # 面板高度是布局算出来的，比窗口 resizeEvent 晚一帧；
        # 直接监听面板自己的 Resize，行数才不会按旧高度算
        panel.installEventFilter(self)
        return panel

    def __buildChampionsPanel(self) -> Panel:
        panel = Panel('05', '常用英雄')
        panel.setMinimumHeight(170)

        self.__champCount = QLabel('')
        self.__champCount.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))
        panel.addAction(self.__champCount)

        self.__champBox = QVBoxLayout()
        self.__champBox.setContentsMargins(0, 0, 0, 0)
        self.__champBox.setSpacing(0)
        panel.body.addLayout(self.__champBox, 1)

        self.__champPager = Pager(4, '个')
        self.__champPager.pageChanged.connect(lambda _: self.__renderChampions())
        panel.body.addWidget(self.__champPager)
        return panel

    # ---------------------------------------------------------------- 信号
    def __connectSignalToSlot(self):
        signalBus.infoLoaded.connect(self.updateInfo)
        signalBus.gameStatusChanged.connect(self.__onGameStatus)

    def __onGameStatus(self, status: str):
        self.__gameStatus = status or 'None'
        mapping = {
            'None': ('空闲', 'textMuted'),
            'Lobby': ('房间中', 'textSub'),
            'Matchmaking': ('匹配中', 'brand'),
            'ChampSelect': ('选人中', 'brand'),
            'GameStart': ('对局加载', 'accent'),
            'InProgress': ('对局中', 'accent'),
            'WaitingForStats': ('结算中', 'textSub'),
            'PreEndOfGame': ('结算中', 'textSub'),
            'EndOfGame': ('结算中', 'textSub'),
        }
        text, tone = mapping.get(self.__gameStatus, (self.__gameStatus, 'textSub'))
        self.__setGameText(text, tone if tone != 'textSub' else 'textSub')

    def setClientOnline(self, online: bool, name: str = ''):
        self.__clientOnline = online
        if online:
            self.__clientDot.setTone('win')
            self.__clientText.setText('已连接')
            self.__clientText.setStyleSheet(f"color: {color('win')}; " + font(12))
            self.__renderAccount(name)
        else:
            self.__clientDot.setTone('red')
            self.__clientText.setText('未连接')
            self.__clientText.setStyleSheet(f"color: {color('red')}; " + font(12))
            self.__renderAccount('')
            self.__setGameText('空闲', 'textMuted')

    def __renderAccount(self, name: str):
        """`念尘词 #78063` 拆成「名字 + 弱灰 tag」：tag 是等宽小一号"""
        name = (name or '').strip()
        if '#' in name:
            head, _, tag = name.rpartition('#')
            if head.strip() and tag.strip():
                self.__accountText.setText(head.strip())
                self.__accountTag.setText(f'#{tag.strip()}')
                return
        self.__accountText.setText(name or '--')
        self.__accountTag.setText('')

    def __setGameText(self, text: str, tone: str):
        """`\u25cf 对局中` / `\u25cb 空闲`：圆点用文字画，省一个子控件"""
        glyph = '\u25cb' if tone == 'textMuted' else '\u25cf'
        self.__gameText.setText(f'{glyph} {text}')
        self.__gameText.setStyleSheet(
            f"color: {color(tone)}; " + font(12))

    # ---------------------------------------------------------------- 数据
    def hasInfo(self) -> bool:
        """是否已经拿到过「我自己的」那份数据

        切到总览时用它兜底：客户端刚连上就先去查了别人的话，自己这份还没
        加载过，总览会是一片空白。
        """
        return bool(self.__info)

    def updateInfo(self, info: dict):
        if not info:
            return

        self.__info = info
        self.__games = (info.get('games') or {}).get('games') or []
        self.__champions = info.get('champions') or []

        self.__updateCareer(info)
        self.__updateToday()
        self.__updateRecords()
        self.__updateChampions()

    def __updateCareer(self, info: dict):
        rankTable = info.get('rankTable') or []
        self.__rankSolo.update(rankTable[0] if rankTable else None)

        # 灵活组排是第二行；没打过的号也要占位显示「未定级」
        flex = rankTable[1] if len(rankTable) > 1 else None
        self.__rankFlex.update(flex)
        self.__rankFlex.setVisible(bool(flex))

        games = [g for g in self.__games if g.get('queueId') != 0]
        # 全部取回的场次，不再截前 10 场 —— 生涯页统计的是全部（默认 20 场），
        # 两处口径不一致会出现「总览 2 胜 8 负 / 生涯 2 胜 18 负」这种对不上的数字
        recent = games
        wins = sum(1 for g in recent if g.get('win') and not g.get('remake'))
        losses = sum(1 for g in recent if not g.get('win') and not g.get('remake'))
        winRate = wins * 100 / max(1, wins + losses)

        kills = sum(g.get('kills', 0) for g in recent)
        deaths = sum(g.get('deaths', 0) for g in recent)
        assists = sum(g.get('assists', 0) for g in recent)
        kda = (kills + assists) / max(1, deaths)

        self.__recentRecord = (wins, losses) if recent else None

        if recent:
            self.__statRecent.setHint(f'近 {len(recent)} 场')
            self.__statRecent.setValue(self.__recentText(wins, losses))
        else:
            self.__statRecent.setHint('近况')
            self.__statRecent.setValue('--')

        # 胜率用机能黄（--accent），KDA 用终端青（--highlight）
        self.__statWinRate.setValue(f'{winRate:.0f}%' if recent else '--', 'brand')
        self.__statKda.setValue(f'{kda:.1f}' if recent else '--', 'accent')

        solo = rankTable[0] if rankTable else None
        mmr = estimateMmr(solo, winRate) if solo else 0
        if mmr:
            self.__statMmr.setValue(f'{mmr:,}')
        else:
            self.__statMmr.setValue('--')

        account = info.get('name') or ''
        tag = info.get('tagLine') or ''
        self.setClientOnline(True, f'{account} #{tag}' if tag else account)

        self.__careerUpdatedAt = datetime.datetime.now()
        self.__refreshCareerMeta()

    def __refreshCareerMeta(self):
        """右上角的时间戳：刚刷过显示「刚刚更新」，之后按分钟走"""
        if not getattr(self, '_OverviewInterface__careerMeta', None):
            return

        if not self.__careerUpdatedAt:
            self.__careerMeta.setText('')
            return

        minutes = int((datetime.datetime.now() - self.__careerUpdatedAt)
                      .total_seconds() // 60)
        self.__careerMeta.setText('刚刚更新' if minutes < 1 else f'{minutes} 分钟前更新')

    def __updateToday(self):
        today = datetime.date.today()
        self.__today = [g for g in self.__games if sameDay(g, today)]
        wins = sum(1 for game in self.__today if game.get('win'))
        self.__todayWin.setText(f'{wins} 胜')
        self.__todayLose.setText(f'{len(self.__today) - wins} 负')
        for badge in (self.__todayWin, self.__todayLose):
            badge.setVisible(bool(self.__today))
        self.__todayPager.setTotal(len(self.__today))

    def __onModeChanged(self, index: int):
        self.__mode = index
        self.__updateRecords()

    def __updateRecords(self):
        queueIds = MODES[self.__mode][1]
        if queueIds is None:
            self.__records = list(self.__games)
        else:
            self.__records = [g for g in self.__games if g.get('queueId') in queueIds]
        self.__recordsPager.setTotal(len(self.__records))

    def __updateChampions(self):
        # 设计稿这一格只写「近 20 场」，英雄个数在下面的分页条里
        self.__champCount.setText(f'近 {len(self.__games)} 场')
        self.__champPager.setTotal(len(self.__champions))

    # ---------------------------------------------------------------- 渲染
    def __clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self.__clear(item.layout())

    def __emptyRow(self, text: str, height: int = 38) -> QLabel:
        label = QLabel(text)
        label.setFixedHeight(height)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color: {color('textMuted')}; " + font(10))
        return label

    def __renderToday(self):
        self.__clear(self.__todayBox)

        if not self.__today:
            self.__todayBox.addWidget(
                self.__emptyRow('今天还没有对局。打一把，这里会自动出现。', 60))
            return

        page = self.__todayPager.currentPage()
        size = self.__todayPager.perPage()
        self.__todayRows = []
        for game in self.__today[page * size:(page + 1) * size]:
            row = GameRow(game, 'today')
            row.clicked.connect(self.detailRequested)
            row.setCompact(self.__compact)
            self.__todayBox.addWidget(row)
            self.__todayRows.append(row)

        # 不加这根弹簧, QVBoxLayout 会把剩余高度平均摊到每行之间, 行距忽大忽小
        self.__todayBox.addStretch(1)

    def __renderRecords(self):
        self.__clear(self.__recordsBox)

        if not self.__records:
            self.__recordsBox.addWidget(
                self.__emptyRow('这个模式下还没有对局记录。', 60))
            return

        page = self.__recordsPager.currentPage()
        size = self.__recordsPager.perPage()
        self.__recordRows = []
        for game in self.__records[page * size:(page + 1) * size]:
            row = GameRow(game, 'full')
            row.clicked.connect(self.detailRequested)
            row.setCompact(self.__compact)
            self.__recordsBox.addWidget(row)
            self.__recordRows.append(row)

        self.__recordsBox.addStretch(1)

    def __renderChampions(self):
        self.__clear(self.__champBox)

        if not self.__champions:
            self.__champBox.addWidget(self.__emptyRow('还没有英雄记录。', 60))
            return

        page = self.__champPager.currentPage()
        size = self.__champPager.perPage()
        for champ in self.__champions[page * size:(page + 1) * size]:
            self.__champBox.addWidget(self.__champRow(champ))

        self.__champBox.addStretch(1)

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
        # 设计稿三档：≥60% 绿、50~59% 青、<50% 红
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

    # ---------------------------------------------------------------- 版本改动
    def __loadPatchNotes(self, force: bool = False):
        """先用本地缓存铺满，再去官方拉新的

        公告在外部网络，同步请求会把首屏卡住。缓存里通常已经有上一个版本，
        先把它渲染出来，用户看到的是有内容的列表而不是转圈。
        """
        if self.__patchNotes is None:
            cached = readCache()
            if cached:
                cached['stale'] = True
                self.__patchNotes = cached
                self.__renderPatchNotes()

        self.__startPatchLoader(force)

    def __startPatchLoader(self, force: bool = False):
        if self.__patchLoader and self.__patchLoader.isRunning():
            return

        self.__patchLoader = PatchNotesLoader(force, self)
        self.__patchLoader.loaded.connect(self.__onPatchLoaded)
        self.__patchLoader.failed.connect(self.__onPatchFailed)
        self.__patchLoader.finished.connect(self.__onPatchLoaderFinished)
        self.__patchLoader.start()

    def __onPatchLoaderFinished(self):
        # 线程对象必须活到 run() 结束；但如果期间又起了新线程，
        # 老线程的 finished 不能把新引用清掉
        if self.sender() is self.__patchLoader:
            self.__patchLoader = None

    def __onPatchLoaded(self, data: dict):
        if not data:
            return
        self.__patchError = ''
        self.__patchNotes = data
        self.__renderPatchNotes()

    def __onPatchFailed(self, message: str):
        self.__patchError = message
        if not self.__patchNotes:
            self.__renderPatchNotes()

    def __renderPatchNotes(self):
        self.__clear(self.__noticeBox)
        data = self.__patchNotes

        if not data:
            self.__patchVersion.setText('暂无版本')
            self.__patchMore.setVisible(False)
            if self.__patchError:
                self.__noticeBox.addWidget(self.__patchState(
                    '暂时连不上官方公告',
                    '公告页需要联网获取，恢复网络后会自动更新',
                    retry=True), 1)
            else:
                self.__noticeBox.addWidget(self.__patchState(
                    '正在获取官方版本改动…',
                    '首次读取会慢一点，之后走本地缓存'), 1)
            return

        version = data.get('version') or ''
        reason = data.get('staleReason') or ('offline' if data.get('stale') else '')
        suffix = '· 陈旧' if reason == 'outdated' else ('· 缓存' if reason else '')

        if version and suffix:
            self.__patchVersion.setText(f'{version} 版本 {suffix}')
        else:
            self.__patchVersion.setText(f'{version} 版本' if version else '官方版本')

        # 过期的数据不能还挂着「当前版本」的牌子，角标转警示色
        self.__patchVersion.applyTone('red' if reason else 'brand')
        self.__patchVersion.setToolTip(
            '远端数据已多日未更新，官网结构可能变了；这里是最后一次成功拿到的版本'
            if reason == 'outdated'
            else '本地缓存，网络恢复后会自动更新' if reason
            else '当前官方版本')

        total = len(data.get('items') or [])
        self.__patchMore.setText(f'全部 {total} 条 ›' if total else '全部 ›')
        self.__patchMore.setVisible(bool(total))

        items = (data.get('items') or [])[:self.__noticeRows]
        for index, item in enumerate(items):
            row = PatchNewsRow(item, last=(index == len(items) - 1))
            row.clicked.connect(self.__onPatchRowClicked)
            self.__noticeBox.addWidget(row)

        self.__noticeBox.addStretch(1)

    def __patchState(self, title: str, hint: str, retry: bool = False) -> QWidget:
        """面板内的加载 / 失败占位

        不用 components/states.py：那套是按整页留白设计的（120px 起），
        塞进 300px 的面板里会偏上；这里自己排，文案颜色也显式写死，
        避免 QLabel 落到 Qt 默认调色板（浅色字）在深底上隐形。
        """
        box = QWidget()
        # 占满列表区高度，文案才会落在面板正中，而不是挤在顶部
        box.setMinimumHeight(max(120, self.__noticeRows * 44))

        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(4)
        layout.addStretch(1)

        titleLabel = QLabel(title, box)
        titleLabel.setAlignment(Qt.AlignCenter)
        titleLabel.setStyleSheet(f"color: {color('text')}; " + font(12))

        hintLabel = QLabel(hint, box)
        hintLabel.setAlignment(Qt.AlignCenter)
        hintLabel.setStyleSheet(f"color: {color('textMuted')}; " + font(10))

        layout.addWidget(titleLabel)
        layout.addWidget(hintLabel)

        if retry:
            button = QPushButton('重试', box)
            button.setFixedSize(64, 24)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {color('text')}; background: transparent;"
                f" border: 1px solid {color('lineStrong')}; {font(10)} }}"
                f"QPushButton:hover {{ color: {color('brand')};"
                f" border-color: {color('brand')}; }}")
            button.clicked.connect(lambda: self.__loadPatchNotes(force=True))

            row = QHBoxLayout()
            row.setContentsMargins(0, 6, 0, 0)
            row.addStretch(1)
            row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)

        layout.addStretch(1)
        return box

    def __onPatchRowClicked(self, item: dict):
        self.__openUrl((item or {}).get('url') or NEWS_HOME)

    def __openPatchDialog(self):
        if not self.__patchNotes:
            return

        from app.view.dialogs.patch_notes import PatchNotesDialog

        dialog = PatchNotesDialog(self.__patchNotes, self.window())
        dialog.exec_()

    @staticmethod
    def __openUrl(url: str):
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # ---------------------------------------------------------------- 响应式
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.__applyResponsive()

    @staticmethod
    def __clamp(value: int, low: int, high: int) -> int:
        return max(low, min(high, value))

    def __applyResponsive(self):
        """按视口尺寸重排

        高度：面板高度不再写死像素，一律取视口高度的百分比再夹上下限 ——
        1080 高的屏上生涯块 124px 才不显空，728 高上 92px 才不挤。
        宽度：先砍「值得知道」的列（补刀 / 评分 / 统计文案），再砍说明文字。
        """
        if not hasattr(self, '_OverviewInterface__careerPanel'):
            return

        h = self.viewport().height()
        w = self.viewport().width()
        if h < 120 or w < 200:
            return

        # ── 高度 ──
        # 设计稿 840 高的画布上：状态条 34 / 生涯 125 / 改动 256（5 行）/ 今日 258 / 快捷 56
        # 比例换算成当前视口高度，保证任何窗口下都和稿子同一个观感
        career = self.__clamp(int(h * 0.148), 112, 132)
        self.__careerPanel.setFixedHeight(career)

        # 改动面板：44px 一行，840 高时 5 行；1080 高时 6 行
        rows = self.__clamp(int(h / 168 + 0.5), 4, 7)
        if rows != self.__noticeRows:
            self.__noticeRows = rows
            self.__renderPatchNotes()
        noticeH = 34 + rows * 44 + 2
        self.__noticePanel.setFixedHeight(noticeH)

        # 今日对战吃掉剩余（设计稿里它是 flex:1）
        todayH = h - 32 - 34 - 12 - career - 12 - noticeH - 12 - 56
        self.__todayPanel.setFixedHeight(max(150, todayH))

        # 右列：矮屏收一点最小高，免得两栏互相挤
        self.__recordsPanel.setMinimumHeight(210 if h < 700 else 260)
        self.__champPanel.setMinimumHeight(140 if h < 700 else 170)

        # [04] / [05] 的行数都要等布局落定才知道面板多高，排到下一轮事件循环再算。
        # 之前用「视口高 * 0.4」估常用英雄面板，估出来比实际高一截，
        # 最后两行和分页条会被裁掉
        QTimer.singleShot(0, self.__fitRecordRows)
        QTimer.singleShot(0, self.__fitChampRows)

        # ── 宽度 ──
        # viewport 宽度是逻辑像素, 窗口最小 1080, 所以断点贴着它定
        narrow = w < 1090
        veryNarrow = w < 1000

        for row in self.__todayRows + self.__recordRows:
            row.setCompact(narrow)

        self.__recordsPager.setCompact(veryNarrow)
        self.__todayPager.setCompact(veryNarrow)
        self.__champPager.setCompact(veryNarrow)
        # 窄窗口先收起「对局结束自动刷新」；圆点要跟着一起走，
        # 只藏文字会剩一颗孤零零的绿点
        self.__autoLabel.setVisible(not narrow)
        self.__autoDot.setVisible(not narrow)

        self.__compact = narrow

        # 表头和数据列同进同退（窄屏要藏两列：补刀 + 时长）
        for name in ('补刀', '时长'):
            col = self.__colHeaders.get(name)
            if col is not None:
                col.setVisible(not narrow)

        # 窄屏把「5 胜 4 负」压成「5-4」，四个指标才不打架
        if getattr(self, '_OverviewInterface__recentRecord', None):
            wins, losses = self.__recentRecord
            self.__statRecent.setValue(self.__recentText(wins, losses))

    def __recentText(self, wins: int, losses: int) -> str:
        """「6胜 4负」：数字 18px，单位压到 12px 弱灰（设计稿 .metric 的写法）

        数字和单位之间不留空格 —— 1080 窗口下每个指标只有 72px，
        「4 胜 5 负」要多花 18px，会把「胜」字挤掉。
        """
        unit = f"font-size:12px; color:{color('textMuted')}"
        return (f"{wins}<span style='{unit}'>胜</span> "
                f"{losses}<span style='{unit}'>负</span>")

    def eventFilter(self, obj, event):
        if (obj is getattr(self, '_OverviewInterface__recordsPanel', None)
                and event.type() == QEvent.Resize):
            self.__fitRecordRows()
        return super().eventFilter(obj, event)

    def __fitRecordRows(self):
        """按 [04] 面板的实际高度决定一屏放几行

        固定 10 行时，1080 以上的窗口每页下面会空出一条（36px 起）；
        这里按「面板高 - 表头 - 列表头 - 分页条」反推，能放几行放几行。
        """
        panel = getattr(self, '_OverviewInterface__recordsPanel', None)
        pager = getattr(self, '_OverviewInterface__recordsPager', None)
        if panel is None or pager is None:
            return

        body = panel.height() - 30 - 1          # 标题行 30 + 底边 1
        available = body - 20 - pager.height()
        rows = self.__clamp(available // 38, 6, 14)
        if rows != pager.perPage():
            pager.setPerPage(rows)
            self.__renderRecords()

    def __fitChampRows(self):
        """按 [05] 面板的实际高度决定一屏放几行

        面板高度是 7:3 分出来的，跟视口高不是固定比例，只能等布局算完再反推：
        标题行 30 + 底边 1 + 分页条，剩下的按 38px 一行。
        """
        panel = getattr(self, '_OverviewInterface__champPanel', None)
        pager = getattr(self, '_OverviewInterface__champPager', None)
        if panel is None or pager is None:
            return

        body = panel.height() - 30 - 1
        available = body - pager.height()
        rows = self.__clamp(available // 38, 3, 8)
        if rows != pager.perPage():
            pager.setPerPage(rows)
            self.__renderChampions()

    # ---------------------------------------------------------------- 其它
    def setLoadingPageEnabled(self, enable: bool):
        """保留接口名，总览页用骨架文案代替转圈，不做整页遮挡"""
        pass

    def refresh(self):
        self.refreshRequested.emit()

    def detailContext(self) -> list:
        """「我的战绩」当前筛选出的全部场次，给详情浮层做上一场 / 下一场"""
        return list(self.__records)

    def reloadPatchNotes(self, force: bool = False):
        """外部（主窗口刷新、切回总览页）要求更新版本改动

        默认走缓存优先（45 分钟内直接命中）；`force=True` 时绕过缓存，
        用于用户主动点刷新。
        """
        self.__loadPatchNotes(force)

    def stopLoader(self):
        """退出时收掉公告线程

        requests 的阻塞读没法优雅中断，所以先等一个网络超时周期；
        还没退才 terminate —— 和主窗口处理其它监听线程的策略保持一致。
        """
        loader = self.__patchLoader
        if loader is None or not loader.isRunning():
            return

        if not loader.wait(1500):
            loader.terminate()
            loader.wait(300)
