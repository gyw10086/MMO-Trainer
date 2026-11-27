# coding:utf-8
"""单场详情浮层

设计稿 S10 的落地形态：在本页浮起，关掉回到原来的滚动位置，
不写页面历史，不进面包屑。

内容直接复用 `MatchDetailView`（与战绩查询页内嵌的是同一个类）—— 一份实现两处套壳。
标题行多出「上一场 / 下一场」：调用方把列表一起传进来才有，连着翻不用关掉再点下一行。
"""
import asyncio

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (QPushButton, QDialog, QFrame, QHBoxLayout, QLabel,
                             QVBoxLayout, QWidget)

from app.common.design_tokens import color, font, mono
from app.common.logger import logger
from app.common import theme
from app.common.qfluentwidgets import IndeterminateProgressRing, isDarkTheme
from app.common.style_sheet import StyleSheet
from app.components.match_detail_view import MatchDetailView
from app.lol.connector import connector
from app.lol.tools import parseGameDetailData

TAG = 'MatchDetail'

# 浮层尺寸：大屏可以到 1240×840，小屏按父窗口留 96px 余量
SHELL_W = 1240
SHELL_H = 840
MARGIN = 48

# 遮罩：压暗底层，让「这是浮层不是页面」一眼可辨
SCRIM = QColor(0, 0, 0, 150)


def panelBg() -> str:
    """面板底色

    深色下 bg1(#101215) 和遮罩后的背景几乎分不开，要用 bg2(#171A1E) 才浮得起来；
    浅色的 bg1 就是纯白，正好和页面 #F4F5F7 拉开一档。
    """
    return color('bg2') if isDarkTheme() else color('bg1')

buttonStyle = (
    f"QPushButton {{ color: {color('textSub')}; background: transparent;"
    f"  border: 1px solid {color('lineStrong')}; {font(11)} }}"
    f"QPushButton:hover {{ color: {color('brand')}; border-color: {color('brand')}; }}"
    f"QPushButton:disabled {{ color: {color('textMuted')}; }}")


class _TitleBar(QWidget):
    """自绘标题行，兼做拖动把手"""

    def __init__(self, dialog: QDialog, parent=None):
        super().__init__(parent)
        self.__dialog = dialog
        self.__dragFrom = None
        # QWidget 子类默认不画 qss 的 background / border，不加这个
        # 标题栏那条分隔线永远不显示
        self.setAttribute(Qt.WA_StyledBackground, True)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.__dragFrom = e.globalPos() - self.__dialog.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.__dragFrom is not None and e.buttons() & Qt.LeftButton:
            self.__dialog.move(e.globalPos() - self.__dragFrom)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.__dragFrom = None
        super().mouseReleaseEvent(e)


class _CloseButton(QLabel):
    def __init__(self, parent=None):
        super().__init__('✕', parent)
        self.setFixedSize(22, 22)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.__idle = (
            f"color: {color('textSub')}; {font(11)}"
            f"border: 1px solid transparent;")
        self.setStyleSheet(self.__idle)
        self.setToolTip('关闭 (Esc)')

    def enterEvent(self, e):
        self.setStyleSheet(
            f"color: {color('red')}; {font(11)}"
            f"border: 1px solid {color('red')}66; background: {color('red')}14;")
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setStyleSheet(self.__idle)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.parent():
            self.parent().window().close()
        super().mouseReleaseEvent(e)


class MatchDetailDialog(QDialog):
    """铺满父窗口的对局详情浮层，中间是 1240×840 的面板

    以前是「一个 1120×760 的无边框窗口直接浮在页面上」：没有遮罩、没有阴影、
    边框和页面卡片几乎同色，看起来就像又开了一个页面 —— 于是里面的 8~9px
    辅助字被当成正文来读，显得格外小。

    现在改成标准浮层形态：
    - 铺满父窗口 + 半透明遮罩，底下的页面明确「退到后面去」
    - 面板四周留白，点遮罩任意处关闭（和主流弹窗一致）
    - 标题 12 → 15px，标题栏固定 44px 高并带分隔线
    """

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        # 不给下限的话, 布局会按内容 sizeHint 把窗口缩回去
        self.setMinimumSize(980, 660)

        self.__shellW = SHELL_W
        self.__shellH = SHELL_H

        # 这一场所在的列表与下标：给了才有「上一场 / 下一场」
        self.__context = []
        self.__index = -1

        self.__shell = QFrame(self)
        self.__shell.setObjectName('matchDetailShell')

        self.__title = QLabel('对局详情', self.__shell)
        self.__title.setStyleSheet(
            f"color: {color('text')}; " + font(15, bold=True))

        self.__subtitle = QLabel('', self.__shell)
        self.__subtitle.setStyleSheet(
            f"color: {color('textMuted')}; " + mono(11))

        self.__close = _CloseButton(self.__shell)

        self.__prev = QPushButton('‹ 上一场', self.__shell)
        self.__next = QPushButton('下一场 ›', self.__shell)
        for btn in (self.__prev, self.__next):
            btn.setFixedSize(78, 26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(buttonStyle)
            btn.setVisible(False)
        self.__prev.clicked.connect(lambda: self.__step(-1))
        self.__next.clicked.connect(lambda: self.__step(1))

        bar = _TitleBar(self, self.__shell)
        bar.setObjectName('matchDetailTitleBar')
        bar.setFixedHeight(44)
        barLayout = QHBoxLayout(bar)
        barLayout.setContentsMargins(16, 0, 10, 0)
        barLayout.setSpacing(10)
        barLayout.addWidget(self.__title)
        barLayout.addWidget(self.__subtitle)
        barLayout.addStretch(1)
        barLayout.addWidget(self.__prev)
        barLayout.addWidget(self.__next)
        barLayout.addSpacing(8)
        barLayout.addWidget(self.__close)

        self.__bar = bar

        self.view = MatchDetailView(self.__shell)

        shellLayout = QVBoxLayout(self.__shell)
        shellLayout.setContentsMargins(1, 0, 1, 1)
        shellLayout.setSpacing(0)
        shellLayout.addWidget(bar)
        shellLayout.addWidget(self.view, 1)

        # 面板用绝对定位居中：窗口要铺满父窗口才能画遮罩，
        # 放进 layout 会被拉伸成整屏，那就又是「页面」了
        self.__shell.raise_()

        # 加载指示：数据没回来之前先盖一层
        self.__loading = QWidget(self.__shell)
        self.__loading.setObjectName('matchDetailLoading')
        loadingLayout = QVBoxLayout(self.__loading)
        loadingLayout.addStretch(1)
        ring = IndeterminateProgressRing()
        ring.setFixedSize(40, 40)
        loadingLayout.addWidget(ring, 0, Qt.AlignHCenter)
        tip = QLabel('正在读取对局详情…', self.__loading)
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet(f"color: {color('textSub')}; " + font(12))
        loadingLayout.addWidget(tip)
        loadingLayout.addStretch(1)
        self.__loading.hide()

        self.__task = None
        self.__game = None
        # 这一局属于谁（看好友生涯时是好友的 puuid）；空 = 当前登录账号
        self.__puuid = ''
        # 详情拉失败后点「重试」直接重拉这一场，不用关掉再点一次
        self.view.retry.connect(self.__reload)

        # 浮层是顶层窗口, 不会继承页面的样式表, 得自己挂一份壳的样式;
        # 内容的样式在 components.qss 里 —— 以前是挂在 QApplication 上的,
        # 现在组件 qss 只挂页面容器, 这个浮层也要自己登记一份, 否则内容区
        # （战损条 / 双方阵容 / 装备格）切主题后不变色
        StyleSheet.MATCH_DETAIL.apply(self)
        theme.registerQssRoot(self)

        # 壳和内部容器都要显式刷成 bg1：
        # 1) `#matchDetailShell` 那条 qss 规则会被组件 qss 的 `QWidget` 规则盖掉
        # 2) 内容区是 SmoothScrollArea，skin.solid() 会把它的透明刷成页面底色 bg0，
        #    于是面板和遮罩后面的页面同色，看起来还是一块页面
        theme.styled(self.__shell, lambda: (
            f"QFrame#matchDetailShell {{"
            f" background: {panelBg()};"
            f" border: 1px solid {color('textMuted')}55; }}"
            f"QFrame#matchDetailShell QWidget {{"
            f" background: {panelBg()}; }}"
            f"QFrame#matchDetailShell QScrollArea {{"
            f" background: {panelBg()}; }}"))
        theme.styled(bar, lambda: (
            f"#matchDetailTitleBar {{"
            f" background: {panelBg()};"
            f" border-bottom: 1px solid {color('line')}; }}"))

        self.resize(SHELL_W, SHELL_H)
        self.__placeShell()

    # ---- 遮罩与定位 ----
    def paintEvent(self, e):
        """整块窗口画成半透明黑，面板浮在上面"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), SCRIM)

    def mousePressEvent(self, e):
        """点面板外面任意处关闭 —— 让「这是浮层」变成可交互的常识"""
        if (e.button() == Qt.LeftButton
                and not self.__shell.geometry().contains(e.pos())):
            self.close()
            return

        super().mousePressEvent(e)

    def __placeShell(self):
        width = min(self.__shellW, max(980, self.width() - MARGIN * 2))
        height = min(self.__shellH, max(660, self.height() - MARGIN * 2))

        self.__shell.setFixedSize(width, height)
        self.__shell.move((self.width() - width) // 2,
                          (self.height() - height) // 2)
        self.__loading.setGeometry(self.__shell.rect())

    # ---- 对外 ----
    def load(self, game: dict, context: list = None, puuid: str = ''):
        """game 是列表里的那条（含 gameId），详情另行请求

        context 是它所在的那个列表；给了才显示「上一场 / 下一场」。
        puuid 是「这一局属于谁」：看好友生涯时传好友的 puuid，不传才退回
        当前登录账号。少了它，看别人单排的对局会因为「这局里没有我」
        解析失败，只能显示错误页。
        """
        game = game or {}
        gameId = game.get('gameId')
        if not gameId:
            return

        self.__puuid = puuid or ''
        self.__game = game
        self.__context = [g for g in (context or []) if g.get('gameId')]
        ids = [g['gameId'] for g in self.__context]
        self.__index = ids.index(gameId) if gameId in ids else -1
        self.__syncStepButtons()

        self.__subtitle.setText(
            f"#{gameId} · {game.get('name', '')} · {game.get('time', '')}")
        self.__loading.setGeometry(self.__shell.rect())
        self.__loading.show()
        self.__loading.raise_()

        self.view.setLoadingPageEnabled(True)

        async def run():
            detail = None
            try:
                raw = await connector.getGameDetailByGameId(gameId)
                # 原始返回里 teams 是 list, MatchDetailView 要的是按 teamId 索引的 dict,
                # 这一步转换原来在战绩查询页里做, 浮层也得走同一套解析
                # 「这局属于谁」优先用调用方给的（看好友生涯时是好友的 puuid），
                # 没人给才去问当前登录账号 —— 省掉一次请求，也不会把别人的局
                # 按「我不在场」判死
                owner = self.__puuid

                if not owner:
                    owner = (await connector.getCurrentSummoner())['puuid']

                detail = await parseGameDetailData(owner, raw)
            except Exception as e:
                # 拉不到就停在空态, 但原因得留痕, 否则只有一个白框无从排查
                logger.exception(f'get game detail failed: {gameId}', e, TAG)

            self.__loading.hide()
            if detail:
                self.view.updateGame(detail)
                self.view.setLoadingPageEnabled(False)
            else:
                self.view.setLoadingPageEnabled(False)
                self.view.setError('对局详情读取失败', '客户端可能没响应，或这场记录已过期')

        self.__task = asyncio.ensure_future(run())

    def __reload(self):
        if self.__game:
            self.load(self.__game, self.__context, self.__puuid)

    # ---- 翻场 ----
    def __syncStepButtons(self):
        has = self.__index >= 0
        self.__prev.setVisible(has)
        self.__next.setVisible(has)
        self.__prev.setEnabled(has and self.__index > 0)
        self.__next.setEnabled(has and self.__index < len(self.__context) - 1)

    def __step(self, delta: int):
        target = self.__index + delta

        if 0 <= target < len(self.__context):
            # 翻场要带上同一个归属人，否则第二场又退回「当前账号」
            self.load(self.__context[target], self.__context, self.__puuid)

    def closeEvent(self, e):
        if self.__task and not self.__task.done():
            self.__task.cancel()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        # 左右方向键翻场
        if e.key() == Qt.Key_Left and self.__prev.isEnabled():
            self.__step(-1)
            return
        if e.key() == Qt.Key_Right and self.__next.isEnabled():
            self.__step(1)
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)

        # 构造期 resize 会先于 __shell 触发一次，跳过即可
        if getattr(self, '_MatchDetailDialog__shellW', None) is not None:
            self.__placeShell()

    def showEvent(self, e):
        super().showEvent(e)
        # 用 singleShot 而不是当场 setGeometry: showEvent 里窗口还没拿到最终
        # 尺寸, 算出来的遮罩会少一圈
        QTimer.singleShot(0, self.__fitToParent)

    def __fitToParent(self):
        """铺满父窗口，面板再按可用空间居中

        铺满是为了画遮罩 —— 只盖住中间一块的话，四周的页面还亮着，
        看起来就像面板本身是个页面。
        """
        parent = self.parentWidget()

        if not parent:
            return

        self.setGeometry(parent.window().frameGeometry())
        self.__placeShell()
