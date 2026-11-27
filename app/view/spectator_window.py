"""观战 / 对局中的悬浮面板

复用对局信息页的 `TeamBoard`, 所以卡片样式、紧凑密度、点击看战绩都是同一套。

观战和普通对局的差别只有两点:

- 观战没有"我方", 列头固定为蓝方 / 红方
- 数据慢 3 分钟左右, 表头需要提示, 避免用户以为卡了

窗口本身不做轮询, 数据由 `main_window` 里的 `LiveGameWatcher` 推过来。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QVBoxLayout)

from app.common.design_tokens import color, font
from app.common.qfluentwidgets import FluentTitleBar, FramelessWindow
from app.common import theme
from app.common import titlebar_skin
from app.components.player_card import Chip
from app.view.game_info_interface import TeamBoard


class SpectatorWindow(FramelessWindow):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.phase = 'spectate'
        self.__positioned = False
        # 用户手动关掉之后, 本轮对局就不要再自动弹了
        self.userClosed = False

        self.setTitleBar(FluentTitleBar(self))
        self.setWindowTitle('对局双方')
        self.setWindowIcon(QIcon("app/resource/images/logo.png"))

        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        # 不设 contentsMargins: 无边框窗口的 QSS 背景在 margin 区域不绘制,
        # 会让标题栏那一条露成透明, 所以改成让 container 铺满整个窗口
        self.setContentsMargins(0, 0, 0, 0)
        self.resize(940, 560)

        # QWidget 子类默认不画 QSS 背景, 无边框窗口必须显式打开
        self.setObjectName("SpectatorWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.__initWidget()
        self.__initLayout()
        self.__applyStyle()

        # 容器铺满整个窗口, 会把标题栏盖住, 必须重新抬到最上层
        self.titleBar.raise_()

        # 独立顶层窗口: 组件 qss（球员卡 / 面板）要自己挂一份
        theme.registerQssRoot(self)
        titlebar_skin.register(self.titleBar)
        theme.register(self)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__applyStyle()

    def __styleTitleBar(self):
        """标题栏交给 qframelesswindow 画, 但要让它透明, 否则会盖出一条黑边"""
        self.titleBar.setStyleSheet(
            "FluentTitleBar { background-color: transparent; border: none; }")

    # ------------------------------------------------------------------ 构建

    def __initWidget(self):
        self.titleBar.maxBtn.setVisible(False)
        self.titleBar.hBoxLayout.setContentsMargins(14, 0, 0, 0)

        self.container = QFrame(self)
        self.container.setObjectName("SpectatorContainer")

        self.phaseChip = Chip('观战', 'gold', self.container)

        self.clockLabel = QLabel('00:00', self.container)
        self.hintLabel = QLabel('观战数据存在约 3 分钟延迟', self.container)
        self.hintLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.blueBoard = TeamBoard('blue', '蓝方', self.container)
        self.redBoard = TeamBoard('red', '红方', self.container)

    def __initLayout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.container)

        # 顶部 48 是标题栏高度, 内容从标题栏下方开始
        outer = QVBoxLayout(self.container)
        outer.setContentsMargins(16, 56, 16, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.phaseChip)
        header.addWidget(self.clockLabel)
        header.addStretch(1)
        header.addWidget(self.hintLabel)

        board = QHBoxLayout()
        board.setSpacing(14)
        board.addWidget(self.blueBoard, 1)
        board.addWidget(self.redBoard, 1)

        outer.addLayout(header)
        outer.addLayout(board, 1)

    # ------------------------------------------------------------------ 样式

    def __applyStyle(self):
        self.__styleTitleBar()

        self.setStyleSheet(
            f"#SpectatorWindow {{"
            f"  background-color: {color('bg1')};"
            f"  border: 1px solid {color('line')};"
            f"}}"
        )

        self.container.setStyleSheet(
            f"QFrame#SpectatorContainer {{"
            f"  background-color: {color('bg1')};"
            f"  border: 1px solid {color('line')};"
            f"}}"
        )

        self.clockLabel.setStyleSheet(
            f"QLabel {{ {font(13, True)} color: {color('text')}; }}")
        self.hintLabel.setStyleSheet(
            f"QLabel {{ {font(10)} color: {color('textMuted')}; }}")

        self.phaseChip.applyStyle()

    # ------------------------------------------------------------------ 数据

    def applySnapshot(self, snapshot: dict):
        spectating = snapshot.get('spectating', False)
        self.phase = 'spectate' if spectating else 'game'

        self.phaseChip.setText('观战' if spectating else '对局中')
        self.phaseChip.setTone('gold' if spectating else 'brand')

        self.hintLabel.setText('观战数据存在约 3 分钟延迟' if spectating else
                               '实时数据来自本机 2999 接口')

        gameTime = int(snapshot.get('gameTime') or 0)
        self.clockLabel.setText(f"{gameTime // 60:02d}:{gameTime % 60:02d}")

        self.blueBoard.setCards(snapshot.get('blue') or [], self.phase)
        self.redBoard.setCards(snapshot.get('red') or [], self.phase)

    # ------------------------------------------------------------------ 窗口

    def closeEvent(self, e):
        e.ignore()
        self.userClosed = True
        self.hide()

    def showEvent(self, e):
        super().showEvent(e)

        if not self.__positioned:
            self.__moveToCorner()
            self.__positioned = True

    def __moveToCorner(self):
        """放到屏幕右上角, 避开游戏画面中央的团战区域"""
        screen = QApplication.primaryScreen()

        if screen is None:
            return

        rect = screen.availableGeometry()
        self.move(rect.right() - self.width() - 24, rect.top() + 40)
