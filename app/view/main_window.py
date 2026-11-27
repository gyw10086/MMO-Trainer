import os
import sys
import traceback
import time
import copy
import win32api
from pathlib import Path

import pyperclip

import asyncio
from aiohttp.client_exceptions import ClientConnectorError
from qasync import asyncClose, asyncSlot
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QEvent, QTimer, QPoint
from PyQt5.QtGui import QIcon, QImage, QKeySequence
from PyQt5.QtWidgets import QApplication, QLabel, QSystemTrayIcon, QShortcut

from app.common.qfluentwidgets import (NavigationItemPosition, InfoBar, InfoBarPosition, Action,
                                       FluentWindow, SplashScreen, MessageBox, SmoothScrollArea,
                                       ToolTipFilter, FluentIcon, ToolTipPosition, FluentWindowBase)

from app.view.start_interface import StartInterface
from app.view.setting_interface import SettingInterface
from app.view.career_interface import CareerInterface
from app.view.overview_interface import OverviewInterface
from app.view.match_detail_dialog import MatchDetailDialog
from app.view.search_interface import SearchInterface
from app.view.game_info_interface import GameInfoInterface
from app.view.auxiliary_interface import AuxiliaryInterface
from app.view.opgg_window import OpggWindow
from app.view.bp_overlay_controller import BpOverlayController
from app.view.spectator_window import SpectatorWindow
from app.common.util import (github, getLolClientPid, getTasklistPath,
                             getLolClientPidSlowly, getLoLPathByRegistry)
from app.components.avatar_widget import NavigationAvatarWidget
from app.components.temp_system_tray_menu import TmpSystemTrayMenu
from app.common.icons import Icon
from app.common.config import cfg, VERSION, BETA
from app.common.design_tokens import color, mono
from app.common import autostart
from app.common import offline_update
from app.common import theme
from app.common import nav_skin
from app.common import titlebar_skin
from app.common.update import runUpdater
from app.common.logger import logger
from app.common.signals import signalBus
from app.components.message_box import (UpdateMessageBox, NoticeMessageBox,
                                        WaitingForLolMessageBox, ExceptionMessageBox,
                                        ChangeDpiMessageBox)
from app.lol.exceptions import (SummonerGamesNotFound, RetryMaximumAttempts,
                                SummonerNotFound, SummonerNotInGame, SummonerRankInfoNotFound)
from app.lol.listener import (LolProcessExistenceListener, StoppableThread)
from app.lol.connector import connector
from app.lol.tools import (parseAllyGameInfo, parseEnemyGameInfo,
                           parseGameInfoByGameflowSession,
                           getAllyOrderByGameRole, getTeamColor, autoBan, autoPick,
                           autoComplete, autoSwap, autoTrade, ChampionSelection,
                           SERVERS_NAME, SERVERS_SUBSET, showOpggBuild, autoShow,
                           autoSetSummonerSpell, separateTeams,
                           collectExcludedChampions)
from app.lol.post_game import PostGameAutomation
from app.lol.player_db import playerDB
from app.lol.aram import AramBuff
from app.lol.champions import ChampionAlias
from app.lol.cn_strategy import cnStrategy
from app.lol.opgg import opgg
from app.lol.live_client import liveClient
from app.lol.spectator import LiveGameWatcher

import threading

TAG = "MainWindow"

# 设计稿 .titlebar 的高度；库默认 48，两处（标题栏本身、内容区上边距）都要用
TITLE_BAR_H = 40


class MainWindow(FluentWindow):
    mainWindowHide = pyqtSignal(bool)
    showUpdateMessageBox = pyqtSignal(dict)
    showNoticeMessageBox = pyqtSignal(str)
    checkUpdateFailed = pyqtSignal()
    checkUpdateNoUpdate = pyqtSignal()
    fetchNoticeFailed = pyqtSignal()

    def __init__(self):
        super().__init__()

        logger.critical(f"Seraphine started, version: {BETA or VERSION}", TAG)

        self.windowSize = cfg.get(cfg.windowSize)

        self.__initConfig()
        self.__initWindow()
        self.__initSystemTray()

        # create sub interface
        self.startInterface = StartInterface(self)
        self.overviewInterface = OverviewInterface(self)
        self.careerInterface = CareerInterface(self)
        self.searchInterface = SearchInterface(self)
        self.gameInfoInterface = GameInfoInterface(self)
        self.auxiliaryFuncInterface = AuxiliaryInterface(self)
        self.settingInterface = SettingInterface(self)

        logger.critical("Seraphine interfaces initialized", TAG)

        # create listener
        self.isClientProcessRunning = False
        # 单场详情浮层：同一时间只留一个，切页前要能收起来
        self.__matchDetailDialog = None
        self.processListener = LolProcessExistenceListener(self)
        self.checkUpdateThread = StoppableThread(
            target=self.checkUpdate, parent=self)
        self.checkNoticeThread = StoppableThread(
            target=lambda: self.checkNotice(False), parent=self)

        logger.critical("Seraphine listerners started", TAG)

        self.currentSummoner = None

        self.isGaming = False
        # 当前对局信息页承载的是哪一局；换局时先清空，否则上一把的卡片会留着
        self.currentGameId = None
        self.isTrayExit = False
        self.tasklistEnabled = True
        self.championSelection = ChampionSelection()

        # 观战 / 对局中的实时数据
        self.liveWatcher = None
        self.spectatorWindow = None
        self.lastLiveStopTime = 0.0

        # 遇见记录去重
        self.lastRecordedGameId = None
        self.lastRecordTs = 0.0

        # 结算页自动化 (自动点赞 / 自动回房间)
        self.postGame = PostGameAutomation()

        self.lastTipsTime = time.time()
        self.lastTipsType = None

        self.__initInterface()
        self.__initNavigation()
        self.__initShortcuts()
        self.__initListener()
        self.__conncetSignalToSlot()
        self.__autoStartLolClient()

        self.splashScreen.finish()

        self.opggWindow = OpggWindow()

        # BP 对位浮窗：跟着选人会话走，默认开
        self.bpOverlayController = BpOverlayController(self)

        logger.critical("Seraphine initialized", TAG)

        self.__silentStart()

        # 开机自启对账: 程序可能被用户挪过位置, 按配置重写一次注册表项
        autostart.sync(cfg.get(cfg.enableAutoStart))

        # 观战检测: 观战时 LCU 的 gameflow 往往读不到, 只能主动探 2999
        QTimer.singleShot(0, lambda: asyncio.create_task(self.__probeSpectate()))
        QTimer.singleShot(0, self.__expandNavigationIfWide)

        # 首启安全说明: 放最后, 等窗口画完第一帧再弹, 免得遮罩盖在未完成的主界面上
        QTimer.singleShot(0, self.__maybeShowSafetyNotice)

    def __maybeShowSafetyNotice(self):
        """首启弹一次安全说明（同版本只弹一次）

        弹窗本身绝不能影响主窗口启动: 任何异常都只记日志。
        """
        try:
            from app.view.safety_notice import showSafetyNotice

            showSafetyNotice(self)
        except Exception as e:
            logger.error(f'failed to show safety notice: {e}', TAG)

    def __initConfig(self):
        folder = cfg.get(cfg.lolFolder)

        isEmptyList = folder == []
        isEmptyStr = folder == str(Path("").absolute()).replace(
            "\\", "/") or folder == ""

        if isEmptyList or isEmptyStr:
            path = getLoLPathByRegistry()

            if not path:
                return

            cfg.set(cfg.lolFolder, [path])
            return

        if type(folder) is str:
            cfg.set(cfg.lolFolder, [folder])

    def __initInterface(self):
        self.__lockInterface()

        self.startInterface.setObjectName("startInterface")
        self.overviewInterface.setObjectName("overviewInterface")
        self.careerInterface.setObjectName("careerInterface")
        self.searchInterface.setObjectName("searchInterface")
        self.gameInfoInterface.setObjectName("gameInfoInterface")
        self.auxiliaryFuncInterface.setObjectName("auxiliaryFuncInterface")
        self.settingInterface.setObjectName("settingInterface")

        # 把更新包 zip 拖到窗口上就能更新
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls()

        if any(url.toLocalFile().lower().endswith('.zip') for url in urls):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()

            if path.lower().endswith('.zip'):
                e.acceptProposedAction()
                self.applyUpdatePackage(path)
                return

    def applyUpdatePackage(self, path):
        """校验 + 确认 + 解压, 然后退出让替换脚本接手

        拖拽、设置页「选择更新包」、启动参数 --update 三条路都走这里
        """
        # 独立版走不了这条路：替换脚本只会把包里的 app/*.py 铺到 exe 旁边，
        # 而真正运行的代码在 exe 内部的 PYZ 里 —— 覆盖完还是旧版本，
        # 目录里还多出一份永远不会被加载的源码。只能整包替换。
        if getattr(sys, 'frozen', False):
            InfoBar.warning(
                self.tr("独立版不支持这样更新"),
                self.tr("请关掉程序，用新包解压出的文件夹整体覆盖当前目录"),
                duration=8000,
                orient=Qt.Vertical,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT)
            return

        try:
            info = offline_update.inspect(path)
        except offline_update.PackageError as err:
            InfoBar.error(
                self.tr("更新包不可用"),
                str(err),
                duration=6000,
                orient=Qt.Vertical,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT)
            return

        newer = info['isNewer']
        title = (f'更新到 v{info["version"]}' if newer
                 else f'覆盖安装 v{info["version"]}')

        lines = [
            f'当前版本  v{VERSION}',
            f'包内版本  v{info["version"]}（{info["files"]} 个文件，{info["sizeMB"]:.1f} MB）',
            '',
            '安装时程序会退出，覆盖 app/ 等文件后自动重新启动。',
            '配置、玩家标签、遇见记录都在 %APPDATA%\\Seraphine，不受影响。',
        ]

        if info.get('changelog'):
            lines.append('')
            lines.append('本版更新：')

            for item in info['changelog']:
                lines.append(f'  · {item}')

        if not newer:
            lines.insert(0, '注意：包内版本不比当前新，继续会覆盖现有文件。\n')

        if not MessageBox(title, '\n'.join(lines), self).exec_():
            return

        try:
            offline_update.extract(path)
        except Exception as err:
            logger.exception(f'解压更新包失败: {err}', TAG)
            InfoBar.error(
                self.tr("解压失败"),
                str(err),
                duration=6000,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT)
            return

        InfoBar.success(
            self.tr("正在更新"),
            self.tr("程序将退出并在 2 秒后重新启动"),
            duration=1500,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT)

        QTimer.singleShot(1200, self.__restartForUpdate)

    def __restartForUpdate(self):
        runUpdater()
        signalBus.terminateListeners.emit()
        QApplication.instance().quit()

    def __initNavigation(self):
        # 展开宽度必须赶在第一次 expand() 之前设置：库的默认值是 322，
        # 建窗口期间任何一次展开都会把面板定格成 322px（比导航位宽 136px，
        # 会盖住内容区左边一块），之后再改 expandWidth 不会自动缩回去
        self.navigationInterface.setExpandWidth(186)
        pos = NavigationItemPosition.SCROLL

        self.navigationInterface.addSeparator(NavigationItemPosition.TOP)

        # 连接页只在「未连接」时自动切过去, 不占导航位置;
        # 仍然注册 routeKey, 否则 checkAndSwitchTo() 找不到对 NavWidget
        startItem = self.addSubInterface(
            self.startInterface, Icon.CONNECTION, self.tr("连接"), pos)
        startItem.setVisible(False)

        self.addSubInterface(
            self.overviewInterface, Icon.HOME, self.tr("总览"), pos)
        self.addSubInterface(
            self.gameInfoInterface, Icon.GAME, self.tr("对局信息"), pos)
        self.addSubInterface(
            self.careerInterface, Icon.PERSON, self.tr("生涯"), pos)
        self.addSubInterface(
            self.searchInterface, Icon.SEARCH, self.tr("战绩查询"), pos)
        self.addSubInterface(
            self.auxiliaryFuncInterface, Icon.WRENCH,
            self.tr("其他功能"), pos)

        pos = NavigationItemPosition.BOTTOM

        self.navigationInterface.addItem(
            routeKey='Opgg',
            icon=QIcon("app/resource/images/opgg.svg"),
            text="OP.GG",
            onClick=self.showOpggWindow,
            selectable=False,
            position=pos,
            tooltip="OP.GG"
        )

        self.navigationInterface.addItem(
            routeKey='Fix',
            icon=Icon.ARROWCIRCLE,
            text=self.tr("回到房间"),
            onClick=self.__onFixLCUButtonClicked,
            selectable=False,
            position=pos,
            tooltip=self.tr("回到房间"),
        )

        self.navigationInterface.addItem(
            routeKey='Notice',
            icon=Icon.ALERT,
            text=self.tr("公告"),
            onClick=lambda: threading.Thread(
                target=lambda: self.checkNotice(True)).start(),
            selectable=False,
            position=pos,
            tooltip=self.tr("公告"),
        )

        self.navigationInterface.insertSeparator(
            3, NavigationItemPosition.BOTTOM)

        self.avatarWidget = NavigationAvatarWidget(
            avatar="app/resource/images/game.png", name=self.tr("启动英雄联盟"))
        self.navigationInterface.addWidget(
            routeKey="avatar",
            widget=self.avatarWidget,
            onClick=self.__onAvatarWidgetClicked,
            position=pos,
        )

        # 设计稿的选中块是通栏 186px（左侧强调条贴 x=0），库默认左右各缩进
        # 4px，会画成一块浮在中间的小卡片；右侧留 1px 给容器的分割线，
        # 否则自绘底色会把那条 1px 线盖掉
        panel = self.navigationInterface.panel
        for layout in (panel.scrollLayout, panel.bottomLayout):
            layout.setContentsMargins(0, 0, 1, 0)

        self.addSubInterface(
            self.settingInterface, Icon.SETTING,
            self.tr("设置"), pos,
        )

        self.__fixNavigationPaint()

        # 1321 太苛刻: 1366 的笔记本全屏也刚好不够, 只能永远看图标
        # 这里给 0 是故意的: 库用这个值同时决定「窗口窄到多少就收成浮动菜单」,
        # 建窗口时尺寸还是 640x480, 任何正数都会让面板走 MENU 分支 ——
        # 被 reparent 到窗口上、变成 322px 宽 #202020 的浮层压在内容左侧，
        # 之后窗口变宽也不会回来。收/展由下面的 resizeEvent 自己管。
        self.navigationInterface.setMinimumExpandWidth(0)


    def __fixNavigationPaint(self):
        """给导航项铺一层不透明底色

        实现搬到 `nav_skin.fixItemBackground()` 了：那边每次切主题都要用同一段
        逻辑重铺调色板（`QPalette` 的深色快照活不过一次主题切换），两处各写一份
        早晚会走岔。
        """
        nav_skin.fixItemBackground(self.navigationInterface)

    def __initShortcuts(self):
        """Ctrl+1~5 切页, Ctrl+F 直接去搜人

        熟手不用离开键盘去点左侧导航; 这些都只在应用内生效,
        不会和游戏里的技能键冲突。
        """
        mapping = (
            ('1', self.overviewInterface),
            ('2', self.gameInfoInterface),
            ('3', self.careerInterface),
            ('4', self.searchInterface),
            ('5', self.auxiliaryFuncInterface),
        )

        for key, interface in mapping:
            shortcut = QShortcut(QKeySequence(f'Ctrl+{key}'), self)
            shortcut.activated.connect(
                lambda i=interface: self.switchTo(i))

        search = QShortcut(QKeySequence('Ctrl+F'), self)
        search.activated.connect(self.__focusSearch)

    def __focusSearch(self):
        self.switchTo(self.overviewInterface)
        self.searchInterface.searchLineEdit.setFocus()
        self.searchInterface.searchLineEdit.selectAll()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.__expandNavigationIfWide()

    def __expandNavigationIfWide(self):
        """按窗口宽度展开 / 收起左侧导航

        qfluentwidgets 只在初始化时判断一次，之后把窗口拉宽导航也不会自己出来，
        只能一直看图标。设计稿是常驻的 186px 文字导航，这里跟着 resize 同步。

        另外要防一手「浮动菜单」残留：库在窗口窄的时候会把面板 reparent 到
        窗口上、展开成 322px 的 #202020 浮层，窗口变宽后它不会自己回来。
        """
        from qfluentwidgets.components.navigation.navigation_panel import (
            NavigationDisplayMode)

        panel = getattr(self.navigationInterface, 'panel', None)
        if panel is None:
            return

        try:
            if self.width() >= 1240:
                if panel.parent() is not self.navigationInterface:
                    panel.setParent(self.navigationInterface)
                    panel.move(0, 0)
                    panel.show()

                if (panel.isCollapsed()
                        or panel.displayMode != NavigationDisplayMode.EXPAND
                        or panel.width() != panel.expandWidth):
                    panel.expand(useAni=False)
            elif self.width() < 1180 and not panel.isCollapsed():
                panel.collapse()
        except Exception:
            pass

    def __conncetSignalToSlot(self):
        # From listener:
        signalBus.tasklistNotFound.connect(self.__showWaitingMessageBox)

        # From overview_interface
        self.overviewInterface.refreshRequested.connect(self.__refreshOverview)
        self.overviewInterface.detailRequested.connect(self.__openOverviewDetail)
        self.overviewInterface.openSearch.connect(
            lambda: self.switchTo(self.searchInterface))
        self.overviewInterface.openGameInfo.connect(
            lambda: self.switchTo(self.gameInfoInterface))
        self.overviewInterface.openSetting.connect(
            lambda: self.switchTo(self.settingInterface))
        self.overviewInterface.openCareer.connect(self.__switchToCareerInterface)
        signalBus.lolClientStarted.connect(self.__onLolClientStarted)
        signalBus.lolClientEnded.connect(self.__onLolClientEnded)
        signalBus.lolClientChanged.connect(self.__onLolClientChanged)
        signalBus.terminateListeners.connect(self.__terminateListeners)

        # From connector
        signalBus.currentSummonerProfileChanged.connect(
            self.__onCurrentSummonerProfileChanged)
        signalBus.gameStatusChanged.connect(
            self.__onGameStatusChanged)
        signalBus.champSelectChanged.connect(
            self.__onChampSelectChanged)
        signalBus.lcuApiExceptionRaised.connect(
            self.__onShowLcuConnectError)
        signalBus.getCmdlineError.connect(
            self.__showNeedAdminMessageBox)

        # From career_interface
        signalBus.careerGameBarClicked.connect(self.__onCareerGameClicked)

        # From search_interface and gameinfo_interface
        signalBus.toSearchInterface.connect(self.__switchToSearchInterface)
        signalBus.toCareerInterface.connect(self.__switchToCareerInterface)

        # 切页兜底：总览是「我的」，没数据时补一次
        self.stackedWidget.currentChanged.connect(self.__onPageChanged)

        # From setting_interface
        self.settingInterface.careerGamesCount.pushButton.clicked.connect(
            self.__refreshCareerInterface)
        self.settingInterface.micaCard.checkedChanged.connect(
            self.__cascadeSetMicaEffect)

        # From main_window
        self.showUpdateMessageBox.connect(self.__onShowUpdateMessageBox)
        self.showNoticeMessageBox.connect(self.__onShowNoticeMessageBox)
        self.checkUpdateFailed.connect(self.__onCheckUpdateFailed)
        self.checkUpdateNoUpdate.connect(self.__onCheckUpdateNoUpdate)
        self.fetchNoticeFailed.connect(self.__onFetchNoticeFailed)
        self.stackedWidget.currentChanged.connect(
            self.__onCurrentStackedChanged)
        self.mainWindowHide.connect(self.__onWindowHide)

    def __initWindow(self):
        # 设计稿按 1240x840 出图, 最小档 1080x720 保证卡片还能退到紧凑高度
        self.setMinimumSize(1080, 720)
        self.setWindowIcon(QIcon("app/resource/images/logo.ico"))
        self.setWindowTitle("MMO战队训练助手")

        # 设计稿 .titlebar 是 40px; 库里 FluentTitleBar 写死 48, 顶部会多出
        # 一条 8px 的空带。内容区的上边距是 FluentWindow 建窗口时钉的 48,
        # 改标题栏高度必须同步改它, 否则页面会被标题栏压掉一截
        self.titleBar.setFixedHeight(TITLE_BAR_H)
        self.widgetLayout.setContentsMargins(0, TITLE_BAR_H, 0, 0)
        # 库把按钮布局按 AlignTop 摆，48 高时正好贴着顶；收到 40 之后
        # 最小化 / 关闭会贴到最上面，这里补一半的差值让它垂直居中
        self.titleBar.vBoxLayout.setContentsMargins(
            0, max(0, (TITLE_BAR_H - self.titleBar.closeBtn.height()) // 2), 0, 0)

        # 标题栏标题的颜色由 __applyWindowStyle 统一管 (要在主题切换时跟着变)
        self.titleBar.hBoxLayout.insertSpacing(0, 10)

        # 设计稿标题栏：应用名后面跟一枚 mono 小角标写版本号
        self.versionChip = QLabel(f'v{VERSION}', self.titleBar)
        self.versionChip.setStyleSheet(
            f"color: {color('textMuted')}; border: 1px solid {color('line')}; "
            f"padding: 1px 5px; " + mono(10))
        # 索引 0 是刚插进去的 10px 空隙，1 是图标，2 是标题，版本角标排在标题后面
        self.titleBar.hBoxLayout.insertWidget(
            3, self.versionChip, 0, Qt.AlignLeft | Qt.AlignVCenter)

        # 承载页面的是 PopUpAniStackedWidget，它没有 objectName，
        # 下面这条规则一直没命中；而它的调色板是系统浅色（白）——
        # 任何一页把背景设成 transparent，就会整屏发白（对局信息页踩过）。
        self.stackedWidget.setObjectName('stackedWidget')
        # 导航栏同样没有 objectName，导致 QWidget#navigationInterface 这条规则
        # 从来没命中过；它的 palette 是系统浅色，窗口一动就会露出灰白底
        self.navigationInterface.setObjectName('navigationInterface')
        # NavigationInterface 是裸 QWidget，而且库里给自己设了
        # WA_TranslucentBackground —— 这两个叠加的结果是 qss 里的
        # background-color 完全不上色，导航空白处透出窗口的 Mica 模糊底色
        # （实测 #1D2122，比设计稿的 #0A0B0D 亮一档，还会跟着壁纸变色）。
        # 库只在「浮动菜单」模式下真正需要透明，这里是常驻导航，关掉即可。
        self.navigationInterface.setAttribute(Qt.WA_TranslucentBackground, False)
        self.navigationInterface.setAttribute(Qt.WA_StyledBackground, True)

        # 机能风底色: 只钉住承载页面的容器, 不动子控件自己的样式
        # 导航容器的底色与右分割线由 nav_skin 自绘（它的 WA_TranslucentBackground
        # 让 qss 的 background-color 不生效），这里不用再写
        self.__applyWindowStyle()

        # 组件 qss（面板 / 战绩行 / 分页条）只挂页面容器这一棵子树:
        # 挂 QApplication 会让应用里每个 widget 重新 polish（195ms），挂容器上只要 40ms
        theme.registerQssRoot(self.stackedWidget, chrome=self.__stackedChrome)
        # 导航与标题栏是自绘的，没有主题信号，得单独补一次重绘
        nav_skin.register(self.navigationInterface)
        titlebar_skin.register(self.titleBar)

        theme.register(self)

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop = QApplication.desktop().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)

        self.show()

        QApplication.processEvents()

        self.oldHook = sys.excepthook
        sys.excepthook = self.exceptHook

    @asyncSlot(str, BaseException)
    async def __onShowLcuConnectError(self, api, obj):
        # 同类错误限制弹出频率(1.5秒每次)
        if time.time() - self.lastTipsTime < 1.5 and self.lastTipsType is type(obj):
            return
        else:
            self.lastTipsTime = time.time()
            self.lastTipsType = type(obj)

        if type(obj) in [SummonerGamesNotFound, SummonerRankInfoNotFound]:
            msg = self.tr("服务器返回了异常内容，可能正在维护")
        elif type(obj) is RetryMaximumAttempts:
            msg = self.tr("超过最大重试次数")
        elif type(obj) in [SummonerNotFound, SummonerNotInGame]:
            return
        else:
            msg = repr(obj)

        InfoBar.error(
            self.tr("LCU 请求失败"),
            self.tr(f"Connect API") + f" {api}: {msg}",
            duration=5000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__applyWindowStyle()

    def __stackedChrome(self) -> str:
        """页面容器的底色

        页面背景是透明的，这层透出来的就是它 —— 原来靠窗口级 qss 上色，
        而窗口级 `setStyleSheet` 会把整个窗口下的子控件（含 4 个隐藏页）
        重新 polish 一遍，实测 90ms，正好把分层挂载省下的还回去。
        现在作为「根自己的骨架样式」跟着组件 qss 一次刷完。
        """
        bg = color('bg0')

        return (f"PopUpAniStackedWidget{{background-color:{bg};}}"
                f"QWidget#stackedWidget{{background-color:{bg};}}")

    def __applyWindowStyle(self, *_):
        """标题栏底色 + 标题色

        原来两处都写死（#0A0B0D / #EAECEF），切到浅色后标题栏是白底白字。
        页面容器的底色见 `__stackedChrome`（跟着组件 qss 一起刷）。
        """
        bg = color('bg0')

        self.titleBar.setStyleSheet(f"QWidget#titleBar{{background-color:{bg};}}")
        self.titleBar.titleLabel.setStyleSheet(
            "QLabel {font: 13px 'Microsoft YaHei UI', 'Segoe UI';"
            f" color: {color('text')};}}")

    def __onWindowHide(self, hide):
        """

        @param hide: True -> 隐藏, False -> 显示
        @return:
        """
        if hide:
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()

    def checkUpdate(self, force=False):
        """检查更新

        force=True 时忽略"启动时自动检查"开关, 用于设置页的"立即检查"按钮
        """
        # 独立版更新不了自己：代码在 exe 内部的 PYZ 里，更新包里的 app/*.py
        # 覆盖到目录上不会生效，只会在旁边留一堆没用的源码。所以静默跳过，
        # 别在启动时弹一个点了也装不上的更新框（设置页里已写明要用整包替换）
        if getattr(sys, 'frozen', False):
            return

        if not force and not cfg.get(cfg.enableCheckUpdate):
            return

        try:
            releasesInfo = github.checkUpdate()
        except:
            self.checkUpdateFailed.emit()
            return

        if releasesInfo:
            self.showUpdateMessageBox.emit(releasesInfo)
        elif force:
            # 只有用户主动点了才提示"已是最新", 启动时自动检查保持安静
            self.checkUpdateNoUpdate.emit()

    def requestCheckUpdate(self):
        """设置页「立即检查」的入口

        两个源各 8~12 秒超时，直接在主线程跑会把界面冻住十几秒。
        启动时那条是 QThread 里跑的非一次性 target，不能复用，
        这里开一条一次性的守护线程；信号 emit 是跨线程安全的（排队投递）。
        """
        if getattr(self, '_checkingUpdate', False):
            return

        self._checkingUpdate = True

        def run():
            try:
                self.checkUpdate(force=True)
            except Exception as e:
                logger.error(f'手动检查更新异常: {e}', TAG)
                self.checkUpdateFailed.emit()
            finally:
                self._checkingUpdate = False

        threading.Thread(target=run, daemon=True).start()

    def checkNotice(self, triggerByUser):
        try:
            noticeInfo = github.getNotice()

            # 仓库没有公告文件: 静默跳过, 不弹"获取公告失败"
            if not noticeInfo:
                return

            sha = noticeInfo['sha']
            content = noticeInfo['content']
        except:
            self.fetchNoticeFailed.emit()
            return

        # 如果是开启软件时，并且该公告曾经已经展示过，就直接 return 了
        if not triggerByUser and sha == cfg.get(cfg.lastNoticeSha):
            return

        cfg.set(cfg.lastNoticeSha, sha)
        self.showNoticeMessageBox.emit(content)

    def __onCheckUpdateFailed(self):
        InfoBar.warning(
            self.tr("检查更新失败"),
            self.tr("检查更新失败，可能无法连接 GitHub"),
            duration=5000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __onCheckUpdateNoUpdate(self):
        InfoBar.success(
            self.tr("已是最新版本"),
            self.tr("当前已是最新版本") + f"  v{VERSION}",
            duration=3000,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __onFetchNoticeFailed(self):
        InfoBar.warning(
            self.tr("获取公告失败"),
            self.tr("获取公告失败，可能无法连接 GitHub"),
            duration=5000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __onShowUpdateMessageBox(self, info):
        msgBox = UpdateMessageBox(info, self.window())
        msgBox.exec()

    def __onShowNoticeMessageBox(self, msg):
        msgBox = NoticeMessageBox(msg, self.window())
        msgBox.exec()

    def __showWaitingMessageBox(self):
        self.tasklistEnabled = False

        msgBox = WaitingForLolMessageBox(self.window())

        if not msgBox.exec():
            signalBus.terminateListeners.emit()
            sys.exit()

    def __showNeedAdminMessageBox(self):
        msgBox = MessageBox(self.tr("获取启动参数失败"), self.tr(
            "请尝试以管理员身份运行 MMO战队训练助手"), self.window())
        msgBox.cancelButton.setVisible(False)
        msgBox.exec()

        signalBus.terminateListeners.emit()
        sys.exit()

    def __initSystemTray(self):
        self.trayIcon = QSystemTrayIcon(self)
        self.trayIcon.setToolTip("MMO战队训练助手")
        self.trayIcon.installEventFilter(ToolTipFilter(self.trayIcon))

        self.trayIcon.setIcon(QIcon("app/resource/images/logo.ico"))

        careerAction = Action(Icon.PERSON, self.tr("生涯"), self)
        searchAction = Action(Icon.SEARCH, self.tr("战绩查询"), self)
        gameInfoAction = Action(Icon.GAME, self.tr("对局信息"), self)
        auxiliaryAction = Action(
            Icon.WRENCH, self.tr("其他功能"), self)
        spectatorAction = Action(Icon.GAME, self.tr("观战面板"), self)
        settingsAction = Action(Icon.SETTING, self.tr("设置"), self)
        quitAction = Action(Icon.EXIT, self.tr('退出'), self)

        def showAndSwitch(interface):
            self.show()
            self.checkAndSwitchTo(interface)

        def quit():
            self.isTrayExit = True
            self.close()

        careerAction.triggered.connect(
            lambda: showAndSwitch(self.careerInterface))
        searchAction.triggered.connect(
            lambda: showAndSwitch(self.searchInterface))
        gameInfoAction.triggered.connect(
            lambda: showAndSwitch(self.gameInfoInterface))
        auxiliaryAction.triggered.connect(
            lambda: showAndSwitch(self.auxiliaryFuncInterface))
        spectatorAction.triggered.connect(self.showSpectatorWindow)
        settingsAction.triggered.connect(
            lambda: showAndSwitch(self.settingInterface))
        quitAction.triggered.connect(quit)

        self.trayMenu = TmpSystemTrayMenu(self)

        self.trayMenu.addAction(careerAction)
        self.trayMenu.addAction(searchAction)
        self.trayMenu.addAction(gameInfoAction)
        self.trayMenu.addAction(auxiliaryAction)
        self.trayMenu.addAction(spectatorAction)
        self.trayMenu.addSeparator()
        self.trayMenu.addAction(settingsAction)
        self.trayMenu.addAction(quitAction)

        self.trayIcon.setContextMenu(self.trayMenu)
        # 双击事件
        self.trayIcon.activated.connect(lambda reason: self.show(
        ) if reason == QSystemTrayIcon.DoubleClick else None)
        self.trayIcon.show()

    def show(self):
        self.activateWindow()
        self.setWindowState(self.windowState() & ~
                            Qt.WindowMinimized | Qt.WindowActive)
        self.showNormal()

    def __initListener(self):
        self.processListener.start()
        self.checkUpdateThread.start()
        self.checkNoticeThread.start()
        # self.minimizeThread.start()  # 该功能不再支持 -- By Hpero4

    async def __changeCareerToCurrentSummoner(self):
        summoner = await connector.getCurrentSummoner()
        self.currentSummoner = summoner
        name = summoner.get("gameName") or summoner['displayName']
        self.careerInterface.setLoginSummonerPuuid(summoner['puuid'])

        asyncio.create_task(self.careerInterface.updateInterface(
            summoner=summoner))

    @asyncSlot(int)
    async def __onLolClientStarted(self, pid):
        logger.info(f"League of Legends client started: {pid}", TAG)
        res = await self.__startConnector(pid)
        if not res:
            return

        await opgg.start()
        await cnStrategy.start()
        # 连上之后落在总览: 首屏先回答「我是谁 / 什么状态 / 有什么改动」
        self.checkAndSwitchTo(self.overviewInterface)
        self.isClientProcessRunning = True

        await self.__changeCareerToCurrentSummoner()
        await self.__updateAvatarIconName()

        self.startInterface.hideLoadingPage()

        folder, status = await asyncio.gather(connector.getInstallFolder(),
                                              connector.getGameStatus())

        self.__setLolInstallFolder(folder)

        asyncio.create_task(self.auxiliaryFuncInterface.initChampionList())

        self.auxiliaryFuncInterface.lockConfigCard.loadNowMode()

        # 加载大乱斗buff -- By Hpero4
        aramInitT = asyncio.create_task(AramBuff.checkAndUpdate())
        championsInit = asyncio.create_task(ChampionAlias.checkAndUpdate())

        asyncio.create_task(self.opggWindow.initWindow())
        self.opggWindow.setHomeInterfaceEnabled(False)

        # ---- 240413 ---- By Hpero4
        # 如果你希望 self.__onGameStatusChanged(status) 和 self.__unlockInterface() 并行执行, 可以这样使用:
        #     t = self.__onGameStatusChanged(status)
        #     self.__unlockInterface()
        #     await t
        #
        # 如果你希望等待 self.__onGameStatusChanged(status) 返回之后再执行 self.__unlockInterface() 可以这样使用:
        #     await self.__onGameStatusChanged(status)
        #     self.__unlockInterface()
        #
        # 而不是直接调用:
        #     self.__onGameStatusChanged(status)
        #     self.__unlockInterface()
        #
        # 此外 self.__onGameStatusChanged(status) 本身不是一个常规的异步函数, 它是使用 asyncSlot 装饰的槽函数,
        #   内部封装了task的新建过程, 并且会被立即加入到 QEventLoop 等待执行, 并返回一个Task实例;
        #
        # 如果 func a 是一个常规异步函数, func b 是一个常规的同步函数, 你应该这样使用它:
        #     t = asyncio.create_task(a())
        #     b()
        #     await t
        #
        # 项目中还有其他异步函数使用了await进行了额外的等待, 亦或是直接调用异步函数而没有使用await保证竞态的情况,
        # 这可能导致性能或是其他不可预期的问题, 这是只是一个例子;
        # ---- 240413 ---- By Hpero4

        self.__unlockInterface()
        await asyncio.gather(championsInit, aramInitT)
        await self.__onGameStatusChanged(status)

        # Note 如果你希望测试大乱斗的数据弹框, 参考这个 -- By Hpero4
        # self.careerInterface.icon.aramInfo = AramBuff.getInfoByChampionId(75)

    async def __startConnector(self, pid):
        try:
            await connector.start(pid)
            return True
        except RetryMaximumAttempts:
            # 若超出最大尝试次数, 则认为 lcu 未就绪 (如大区排队中),
            # 捕获到该异常时不抛出, 等待下一个 emit
            await connector.close()

            if self.processListener.isRunning():
                self.processListener.runningPid = 0
            else:
                signalBus.tasklistNotFound.emit()

            return False

    @asyncSlot(int)
    async def __onLolClientChanged(self, pid):
        logger.critical(f"League of Legends client changed: {pid}", TAG)
        await self.__onLolClientEnded()
        self.processListener.runningPid = pid
        await self.__onLolClientStarted(pid)

    @asyncSlot()
    async def __onLolClientEnded(self):
        logger.critical("League of Legends client ended", TAG)

        if self.searchInterface.gameLoadingTask:
            self.searchInterface.puuid = 0
            self.searchInterface.gameLoadingTask = None

        await connector.close()
        await opgg.close()
        await cnStrategy.close()

        self.isClientProcessRunning = False
        self.currentSummoner = None
        self.careerInterface.setLoginSummonerPuuid(None)

        await self.__updateAvatarIconName()

        self.startInterface.showLoadingPage()
        self.careerInterface.setLoadingPageEnabled(True)
        self.opggWindow.setHomeInterfaceEnabled(True)

        self.setWindowTitle("MMO战队训练助手")

        self.checkAndSwitchTo(self.startInterface)
        self.__lockInterface()

    async def __updateAvatarIconName(self):
        if self.currentSummoner:
            try:
                iconId = self.currentSummoner['profileIconId']
                icon = await connector.getProfileIcon(iconId)
                name = (self.currentSummoner.get("gameName")
                        or self.currentSummoner['displayName'])

                server = SERVERS_NAME.get(connector.server) or connector.server
                subset = SERVERS_SUBSET.get(connector.server)

                # 导航只有 186px，「名字（大区）」会被截成「示例召唤师（联盟三…」，
                # 所以名字只留 ID，大区与服务器明细挪进 tooltip
                if not self.avatarWidget.toolTip():
                    tooltip = self.tr(", ").join([server] + list(subset or []))
                    self.avatarWidget.setToolTip(tooltip)
                    self.avatarWidget.installEventFilter(
                        ToolTipFilter(self.avatarWidget, 0,
                                      ToolTipPosition.TOP)
                    )

            except:
                icon = "app/resource/images/game.png"
                name = self.tr("启动英雄联盟")
                self.avatarWidget.setToolTip("")
        else:
            icon = "app/resource/images/game.png"
            name = self.tr("启动英雄联盟")
            self.avatarWidget.setToolTip("")

        self.avatarWidget.setAvatar(icon)
        self.avatarWidget.setName(name)

        self.avatarWidget.repaint()

    def __setLolInstallFolder(self, folder: str):
        folder = folder.replace("\\", "/")
        folder = folder.replace("LeagueClient", "TCLS")
        folder = f"{folder[:1].upper()}{folder[1:]}"

        current: list = cfg.get(cfg.lolFolder)

        if folder.lower() not in [item.lower() for item in current]:
            new = copy.deepcopy(current)
            new.append(folder)
            cfg.set(cfg.lolFolder, new)

    @asyncSlot(dict)
    async def __onCurrentSummonerProfileChanged(self, data: dict):
        self.currentSummoner = data

        await asyncio.gather(self.__updateAvatarIconName(),
                             self.careerInterface.updateNameIconExp(data))

        logger.debug(f"Update Summoner Info : {data}", TAG)

    def __autoStartLolClient(self):
        if self.isClientProcessRunning:
            return

        if not cfg.get(cfg.enableStartLolWithApp):
            return

        if self.tasklistEnabled:
            path = getTasklistPath()
            pid = getLolClientPid(path)
        else:
            pid = getLolClientPidSlowly()

        if pid == 0:
            self.__startLolClient()

    def __startLolClient(self):
        for clientName in ("client.exe", "LeagueClient.exe"):
            path = f'{cfg.get(cfg.lolFolder)[0]}/{clientName}'
            if os.path.exists(path):
                # 走 ShellExecuteW, 路径里有中文也不会被 cmd.exe 的代码页吃掉
                try:
                    os.startfile(path)
                except OSError:
                    os.popen(f'"{path}"')
                self.__showStartLolSuccessInfo()
                break
        else:
            self.__showLolClientPathErrorInfo()

    def __onAvatarWidgetClicked(self):
        if not self.isClientProcessRunning:
            self.__startLolClient()
        else:
            self.careerInterface.backToMeButton.clicked.emit()
            self.checkAndSwitchTo(self.careerInterface)

    def __showStartLolSuccessInfo(self):
        InfoBar.success(
            title=self.tr("客户端已启动"),
            orient=Qt.Vertical,
            content="",
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self,
        )

    def __showLolClientPathErrorInfo(self):
        InfoBar.error(
            title=self.tr("路径无效"),
            content=self.tr("请在设置页填写正确的客户端目录"),
            orient=Qt.Vertical,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self,
        )

    def checkAndSwitchTo(self, interface):
        index = self.stackedWidget.indexOf(interface)

        if not self.stackedWidget.currentIndex() == index:
            self.navigationInterface.widget(interface.objectName()).click()

    def __unlockInterface(self):
        self.searchInterface.setEnabled(True)
        self.auxiliaryFuncInterface.setEnabled(True)
        # pass

    def __lockInterface(self):
        self.searchInterface.setEnabled(False)
        self.auxiliaryFuncInterface.setEnabled(False)

    def __terminateListeners(self):
        """优雅停止后台线程, 超时才强杀。

        QThread.terminate() 会直接掐断线程, 可能留下未释放的锁或半初始化的对象,
        因此只作为最后的兜底: 先置退出标志并等待, 2000ms 内没退才 terminate。
        """
        self.__stopLiveWatcher()

        threads = (
            ("processListener", self.processListener),
            ("checkUpdateThread", self.checkUpdateThread),
            ("checkNoticeThread", self.checkNoticeThread),
        )

        for _, thread in threads:
            thread.stop()

        for name, thread in threads:
            if not thread.isRunning():
                continue

            # 进程监听是 1.5s 轮询, 给足一个周期; 另两个可能在等网络请求
            if not thread.wait(2000):
                logger.warning(f"{name} did not stop in time, terminate it", TAG)
                thread.terminate()
                thread.wait(500)

        # 总览页的版本改动请求也可能还在等网络
        self.overviewInterface.stopLoader()

    @asyncClose
    async def closeEvent(self, a0) -> None:
        # 首次点击 关闭 按钮
        if cfg.get(cfg.enableCloseToTray) is None:
            msgBox = MessageBox(
                self.tr("确定要退出吗？"),
                self.tr("选择关闭按钮的行为（随时可在设置页修改）"),
                self
            )

            msgBox.yesButton.setText(self.tr("最小化"))
            msgBox.cancelButton.setText(self.tr("退出"))
            self.update()

            cfg.set(cfg.enableCloseToTray,
                    (True if 1 == msgBox.exec() else False))

        if not cfg.get(cfg.enableCloseToTray) or self.isTrayExit:
            self.postGame.abort()
            self.__terminateListeners()
            self.opggWindow.close()
            self.bpOverlayController.close()

            if self.spectatorWindow:
                self.spectatorWindow.close()

            cfg.set(cfg.windowSize, self.windowSize)

            return super().closeEvent(a0)
        else:
            a0.ignore()
            self.hide()

    def __silentStart(self):
        if not cfg.get(cfg.enableSilent):
            return

        QTimer.singleShot(0, self.hide)

    @asyncSlot(str)
    async def __switchToSearchInterface(self, name):
        self.searchInterface.searchLineEdit.setText(name)
        self.checkAndSwitchTo(self.searchInterface)

        await self.searchInterface.onSearchButtonClicked()

    @asyncSlot(str)
    async def __switchToCareerInterface(self, puuid):
        # 空 puuid = 「看我自己」：总览的「我的生涯」快捷项与 CTRL+3 就是这么发的。
        # 以前把空串直接传给 updateInterface，assert 当场失败，点了没反应。
        puuid = puuid or self.careerInterface.loginSummonerPuuid

        if not puuid or puuid == '00000000-0000-0000-0000-000000000000':
            return

        # 详情浮层是独立顶层窗口，切页发生在它下面：不先收起来，用户点了
        # 名字却看到浮层原样盖在上面，会以为点击没生效
        self.__dismissMatchDetail()

        try:
            self.careerInterface.w.close()
        except:
            pass

        self.checkAndSwitchTo(self.careerInterface)
        await self.careerInterface.updateInterface(puuid=puuid)

    def __onPageChanged(self, index):
        """切到总览时确保它有自己的数据

        总览只认「我的」那份（`infoLoaded` 也只在自己加载完时发），所以正常
        情况下不会空。这里兜的是「客户端刚连上、自己那份还没加载完就先去查
        了别人」这种顺序。
        """
        if self.stackedWidget.widget(index) is not self.overviewInterface:
            return

        if self.overviewInterface.hasInfo():
            return

        puuid = self.careerInterface.loginSummonerPuuid

        if puuid:
            asyncio.create_task(
                self.careerInterface.updateInterface(puuid=puuid))

    def __dismissMatchDetail(self):
        """收起当前的单场详情浮层（没有就什么都不做）"""
        dialog, self.__matchDetailDialog = self.__matchDetailDialog, None

        if dialog is None:
            return

        try:
            dialog.close()
        except RuntimeError:
            # WA_DeleteOnClose 已经把它销毁了，没什么可关的
            pass

    def __followGameInterface(self):
        """BP / 进游戏时自动切到对局信息页

        以前每次都要手点左侧导航, 对局开始时往往已经错过对面一楼选人;
        设置里可以关掉, 免得在搜索页查东西时被强行带走。
        """
        if not cfg.get(cfg.autoFollowGame):
            return

        if self.stackedWidget.currentWidget() is not self.gameInfoInterface:
            self.switchTo(self.gameInfoInterface)

    @asyncSlot(str)
    async def __onGameStatusChanged(self, status):
        """游戏阶段变化：标题、自动切页、对局结束刷新

        这个槽以前漏了 `@asyncSlot` —— PyQt5 调一个 `async def` 只会生成
        没人 await 的协程，方法体一行都不跑。于是「打完回大厅自动刷新生涯」
        从来没生效过，用户只能靠「回总览再进一次」手动触发。
        """
        title = None
        isGaming = False

        logger.critical(f"Client gameflow phase changed: {status}", TAG)

        # 离开结算阶段就撤掉挂起的点赞/回房间任务
        if status not in ('WaitingForStats', 'PreEndOfGame', 'EndOfGame'):
            self.postGame.abort()

        if status == 'None':
            # 主页不加后缀：设计稿标题栏只有应用名，状态在状态条里写着
            title = None
            await self.__onGameEnd()
        elif status == 'ChampSelect':
            title = self.tr("选人阶段")

            # 在标题添加所处队伍
            side = await connector.getMapSide()
            if side:
                if side == 'blue':
                    mapSide = self.tr("蓝色方")
                else:
                    mapSide = self.tr("红色方")

                title = title + " - " + mapSide

            await self.__onChampionSelectBegin()
            self.__followGameInterface()
        elif status == 'GameStart':
            title = self.tr("对局中")
            await self.__onGameStart()
            isGaming = True
            self.__followGameInterface()
        elif status == 'InProgress':
            title = self.tr("对局中")

            # 重连或正常进入游戏 (走 GameStart), 不需要更新数据
            if not self.isGaming:
                await self.__onGameStart()
            isGaming = True
            self.__followGameInterface()
        elif status == 'WaitingForStatus':
            title = self.tr("等待结算")
        elif status == 'PreEndOfGame':
            # 结算页: 点赞名单只有这时候拿得到
            title = self.tr("对局结算")
            self.postGame.onPreEndOfGame()
        elif status == 'EndOfGame':
            title = self.tr("对局结算")
            self.postGame.onEndOfGame()
        elif status == 'Lobby':
            title = self.tr("房间中")
            await self.__onGameEnd()
            # 刚打完一局，要刷的是自己的数据；生涯页可能还停在别人身上
            await self.__reloadMyCareer()

            if self.stackedWidget.currentWidget() is self.gameInfoInterface:
                self.switchTo(self.careerInterface)

        elif status == 'ReadyCheck':
            title = self.tr("确认对局")
            await self.__onMatchMade()
        elif status == 'Matchmaking':
            title = self.tr("匹配中")
            await self.__onGameEnd()
        elif status == "Reconnect":  # 等待重连
            title = self.tr("等待重连")
            await self.__onReconnect()

        self.isGaming = isGaming

        if status != 'ChampSelect':
            self.opggWindow.setStaysOnTopEnabled(False)

        if title != None:
            self.setWindowTitle("MMO战队训练助手 - " + title)

    async def __onMatchMade(self):
        if not cfg.get(cfg.enableAutoAcceptMatching):
            return

        async def accept():
            timeDelay = cfg.get(cfg.autoAcceptMatchingDelay)
            await asyncio.sleep(timeDelay)
            status = await connector.getReadyCheckStatus()

            if status.get("errorCode"):
                return

            if not status['playerResponse'] == 'Declined':
                await connector.acceptMatchMaking()

        asyncio.create_task(accept())

    async def __onReconnect(self):
        if not cfg.get(cfg.enableAutoReconnect):
            return

        async def reconnect():
            while await connector.getGameStatus() == "Reconnect":
                # 掉线立刻重连会无效
                await asyncio.sleep(.3)
                await connector.reconnect()

        asyncio.create_task(reconnect())

    # 进入英雄选择界面时触发
    async def __onChampionSelectBegin(self):
        self.championSelection.reset()
        self.gameInfoInterface.resetCounters()
        self.checkAndSwitchTo(self.gameInfoInterface)

        cSession, gSession = await asyncio.gather(connector.getChampSelectSession(),
                                                  connector.getGameflowSession())

        # 换局先清空：上一把没经过 Lobby（打完直接再来一局 / 重连）时，
        # 页面会一直挂着上一把的 10 张卡，新 session 填进来只能贴补丁。
        gameId = ((gSession or {}).get('gameData') or {}).get('gameId')

        if gameId and gameId != self.currentGameId:
            self.currentGameId = gameId
            self.gameInfoInterface.clear()

        try:
            queueId = gSession['gameData']['queue']['id']
        except:
            queueId = None

        self.championSelection.queueId = queueId

        if cfg.get(cfg.autoShowOpgg):
            self.opggWindow.show()

            if cfg.get(cfg.enableOpggOnTop):
                self.opggWindow.setStaysOnTopEnabled(True)

        # 敌方只有已锁英雄 (客户端不给身份和预选), 不查网络, 先画出来让右列
        # 在 BP 一开始就有内容, 而不是等到自己这边查完战绩才出现
        self.gameInfoInterface.updateEnemySummoners(
            await parseEnemyGameInfo(cSession))

        currentSummonerId = self.currentSummoner['summonerId']
        info = await parseAllyGameInfo(cSession, currentSummonerId, queueId, useSGP=True)
        self.gameInfoInterface.updateAllySummoners(info)

    # 英雄选择时，英雄改变 / 楼层改变时触发
    @asyncSlot(dict)
    async def __onChampSelectChanged(self, data):
        data = data['data']

        phase = {
            'PLANNING': [autoSetSummonerSpell, autoShow],
            'BAN_PICK': [autoSetSummonerSpell, autoBan, autoPick, autoComplete, autoSwap, showOpggBuild],
            'FINALIZATION': [autoSetSummonerSpell, autoTrade, showOpggBuild],
        }

        for func in phase.get(data['timer']['phase'], []):
            if await func(data, self.championSelection):
                break

        # 更新双方英雄: 我方含悬停预选, 敌方只有已锁定的
        await asyncio.gather(
            self.gameInfoInterface.updateAllyIcon(data.get('myTeam') or []),
            self.gameInfoInterface.updateEnemyIcon(data.get('theirTeam') or []))

        # 更新楼层顺序
        self.gameInfoInterface.updateAllySummonersOrder(data.get('myTeam') or [])

        # 克制英雄要请求 OP.GG, 首次可能几百毫秒; 放后台, 不让它拖慢 BP 刷新
        asyncio.create_task(self.gameInfoInterface.updateEnemyCounters(
            data.get('theirTeam') or [], collectExcludedChampions(data)))

    # 进入游戏后触发
    async def __onGameStart(self):
        session = await connector.getGameflowSession()
        currentSummonerId = self.currentSummoner['summonerId']

        queueId = session['gameData']['queue']['id']
        if queueId in (1700, 1090, 1100, 1110, 1130, 1160):  # 斗魂 云顶匹配 (排位)
            return

        # 新一局（gameId 变了）先把上一把的卡片清掉。
        # 下面 paintAllySummonersInfo 原本只要 allyChampions 满 5 个就直接
        # return，上一把的数据没清就会一直显示上一把。
        gameId = (session.get('gameData') or {}).get('gameId')
        newGame = bool(gameId and gameId != self.currentGameId)

        if not gameId:
            # 个别模式 gameData 里没有 gameId，退化成比英雄：五个人全换了就是新局
            sessionChamps = {m.get('championId')
                             for m in (session.get('gameData') or {}).get('teamOne') or []
                             if m.get('championId')}
            currentChamps = set(
                (self.gameInfoInterface.allyChampions or {}).values())

            newGame = bool(sessionChamps and currentChamps
                           and sessionChamps != currentChamps)

        if newGame:
            self.currentGameId = gameId
            self.gameInfoInterface.clear()

        # 观战 / 回放时 gameflow 一样会给 InProgress, 但那 10 人里没有自己。
        # 数据照样查 (段位/战绩对观战同样有用), 只是不记"遇见"、并且让 watcher 走观战态。
        inThisGame = self.__isInThisGame(session['gameData'],
                                         currentSummonerId)

        if inThisGame:
            self.__recordEncounters(session, currentSummonerId)
        else:
            logger.info("Not in this game, treat as spectate", TAG)

        # 如果是进游戏后开的软件，需要先把友方信息更新上去
        async def paintAllySummonersInfo():
            # TODO 自定义时, 若队伍成员<5, 会触发重新加载导致性能浪费
            if not newGame and self.gameInfoInterface.allyChampions \
                    and len(self.gameInfoInterface.allyChampions) >= 5:
                return

            info = await parseGameInfoByGameflowSession(
                session, currentSummonerId, "ally", useSGP=True)

            self.gameInfoInterface.allyChampions = {}
            self.gameInfoInterface.allyOrder = []

            self.gameInfoInterface.updateAllySummoners(info)

        # 将敌方的召唤师基本信息绘制上去
        async def paintEnemySummonersInfo():
            info = await parseGameInfoByGameflowSession(
                session, currentSummonerId, 'enemy', useSGP=True)

            # 这个 info 是已经按照游戏位置排序过的了（若排位）
            self.gameInfoInterface.updateEnemySummoners(info)

        # 更新己方召唤师楼层顺序至角色顺序
        async def sortAllySummonersByGameRole():
            order = getAllyOrderByGameRole(session, currentSummonerId)
            if order == None:
                return

            interface = self.gameInfoInterface

            if order == interface.allyOrder or len(order) != len(interface.allyOrder):
                return

            interface.blueBoard.reorder(order)
            interface.allyOrder = order

        # 绘制提示组队的颜色
        async def paintTeamColor():
            ally, enemy = getTeamColor(session, currentSummonerId)
            self.gameInfoInterface.updateTeamColor(ally, enemy)

        await paintAllySummonersInfo()
        await asyncio.gather(paintEnemySummonersInfo(),
                             sortAllySummonersByGameRole())
        await paintTeamColor()

        self.checkAndSwitchTo(self.gameInfoInterface)

        # 进入对局后改用 2999 的实时数据刷新卡片 (加载阶段接口还没起来, watcher 会等)
        self.__startLiveWatcher(not inThisGame)

    async def __onGameEnd(self):
        spectating = bool(self.liveWatcher and self.liveWatcher.spectating)

        # 观战的 watcher 由 onOffline 自己收尾, 这里不能误杀
        if self.liveWatcher and not spectating:
            self.__stopLiveWatcher()

        if not cfg.get(cfg.enableReserveGameinfo) and not spectating:
            self.gameInfoInterface.clear()

    # ------------------------------------------------------------------ 实时数据

    def __isInThisGame(self, data, currentSummonerId) -> bool:
        """自己是否在这局的 10 人名单里

        观战 / 回放时 gameflow 也会给完整的 teamOne / teamTwo, 但里面没有自己。
        优先用 puuid 比对: 新版本里 summonerId 会变, puuid 才是稳定的。
        """
        members = (data.get('teamOne') or []) + (data.get('teamTwo') or [])

        if not members:
            # 名单缺失 (加载中 / 特殊模式) 时按自家对局处理, 别把正常对局误判成观战
            return True

        myPuuid = (self.currentSummoner or {}).get('puuid')

        for member in members:
            if currentSummonerId and member.get('summonerId') == currentSummonerId:
                return True

            if myPuuid and member.get('puuid') == myPuuid:
                return True

        return False

    def __recordEncounters(self, session, currentSummonerId):
        """把本局双方记进本地遇见库 (同一 gameId 只记一次)"""
        try:
            data = session.get('gameData') or {}
            gameId = data.get('gameId')

            if gameId and gameId == self.lastRecordedGameId:
                return

            # 取不到 gameId 时用时间兜底, 防止重连流程把同一局记两遍
            if not gameId and time.time() - self.lastRecordTs < 600:
                return

            ally, enemy = separateTeams(data, currentSummonerId)

            playerDB.recordTeam(ally, 'ally', gameId)
            playerDB.recordTeam(enemy, 'enemy', gameId)

            self.lastRecordedGameId = gameId
            self.lastRecordTs = time.time()
        except Exception as e:
            logger.error(f"Record encounters failed: {e}", TAG)

    def showSpectatorWindow(self):
        """托盘菜单 / 手动打开双方悬浮面板"""
        if self.spectatorWindow is None:
            self.spectatorWindow = SpectatorWindow(self)

        self.spectatorWindow.userClosed = False
        self.spectatorWindow.show()
        self.spectatorWindow.raise_()
        self.spectatorWindow.activateWindow()

    def __startLiveWatcher(self, spectating: bool):
        self.__stopLiveWatcher()

        if self.spectatorWindow:
            self.spectatorWindow.userClosed = False

        self.liveWatcher = LiveGameWatcher(
            onSnapshot=self.__onLiveSnapshot,
            onOffline=self.__onLiveOffline,
            spectating=spectating)
        self.liveWatcher.start()

        # 观战不该弹"再来一局"
        self.postGame.spectating = spectating

        if spectating:
            self.postGame.abort()

        logger.info(f"Live watcher started, spectating={spectating}", TAG)

    def __stopLiveWatcher(self):
        if self.liveWatcher is None:
            return

        self.liveWatcher.stop()
        self.liveWatcher = None
        self.lastLiveStopTime = time.time()
        self.postGame.spectating = False

    def __onLiveSnapshot(self, snapshot: dict):
        self.gameInfoInterface.applyLive(snapshot)

        spectating = snapshot.get('spectating', False)

        if spectating and cfg.get(cfg.enableSpectatorPanel):
            if self.spectatorWindow is None:
                self.spectatorWindow = SpectatorWindow(self)

            self.spectatorWindow.applySnapshot(snapshot)

            if not self.spectatorWindow.isVisible() \
                    and not self.spectatorWindow.userClosed:
                self.spectatorWindow.show()
        elif self.spectatorWindow and self.spectatorWindow.isVisible():
            # 手动打开的面板在自家对局里也继续刷新
            self.spectatorWindow.applySnapshot(snapshot)

    def __onLiveOffline(self):
        self.liveWatcher = None
        self.lastLiveStopTime = time.time()

        if self.spectatorWindow:
            self.spectatorWindow.hide()

    async def __probeSpectate(self):
        """空闲时低频探测 2999, 用来识别"正在观战"

        观战 / 回放时 LCU 的 gameflow 常常是 None, 只靠 gameStatusChanged 永远不会
        触发, 所以反过来问游戏进程: 本地 2999 有响应但自己不在对局里, 就是观战。
        """
        while True:
            await asyncio.sleep(12)

            if not cfg.get(cfg.enableSpectatorPanel):
                continue

            if self.isGaming or not self.isClientProcessRunning:
                continue

            if self.liveWatcher and self.liveWatcher.running:
                continue

            # 刚打完一局 2999 还会活十几秒, 别把它当成观战
            if time.time() - self.lastLiveStopTime < 60:
                continue

            if await liveClient.isAvailable():
                logger.info("Spectate detected by live client data", TAG)
                self.__startLiveWatcher(spectating=True)

    def __checkWindowSize(self):
        if (dpi := self.devicePixelRatioF()) == 1.0:
            return

        w, h = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
        size: QSize = self.size() * dpi

        if size.width() < w and size.height() < h:
            return

        for scale in [2.0, 1.75, 1.5, 1.25, 1]:
            if scale * self.width() < w and scale * self.height() < h:
                cfg.set(cfg.dpiScale, scale)
                break

        self.splashScreen.finish()
        msg = ChangeDpiMessageBox(self.window())
        msg.exec()

    @asyncSlot(str)
    async def __onCareerGameClicked(self, gameId):
        name = self.careerInterface.getSummonerName()
        self.searchInterface.searchLineEdit.setText(name)

        # 从生涯页跳过来默认将筛选条件设置为全部 -- By Hpero4
        self.searchInterface.filterComboBox.setCurrentIndex(0)

        # 先加载完再切换, 避免加载过程中换搜索目标导致puuid出错 -- By Hpero4
        await self.searchInterface.searchAndShowFirstPage(self.careerInterface.puuid)
        self.checkAndSwitchTo(self.searchInterface)
        self.searchInterface.loadingGameId = gameId

        # 先画框再加载对局 否则快速切换(如筛选或换人)会导致找不到widget -- By Hpero4
        self.searchInterface.waitingForDrawSelect(gameId)
        await self.searchInterface.updateGameDetailView(gameId, self.careerInterface.puuid)

    def showOpggWindow(self):
        self.opggWindow.show()
        self.opggWindow.showNormal()

        self.opggWindow.raise_()

    def __refreshCareerInterface(self):
        """设置页改了「生涯对局数」之后：按新数量重拉自己的数据

        原来直接点生涯页的刷新按钮，刷的是「当前显示的那个人」；改数量后用户
        要看的是自己的数据，所以统一走 `__refreshMyCareer()`。
        """
        self.__refreshMyCareer()

    async def __reloadMyCareer(self):
        """刷新「我自己的」生涯数据

        总览、设置页、对局结束要的都是自己的数据，但生涯页可能停在别人身上 ——
        那时刷的是别人，而 `infoLoaded` 只在自己加载完时才发，总览一点变化都没有。
        """
        if not self.isClientProcessRunning:
            return

        puuid = self.careerInterface.loginSummonerPuuid

        if not puuid:
            return

        if self.careerInterface.isLoginSummoner():
            # 已经停在自己身上，走原来的刷新（会保留模式筛选）
            await self.careerInterface.refresh()
            return

        await self.careerInterface.updateInterface(puuid=puuid)

    def __refreshMyCareer(self, _=None):
        """`__reloadMyCareer` 的同步入口，给 clicked 信号用"""
        asyncio.create_task(self.__reloadMyCareer())

    def __refreshOverview(self):
        """总览页的刷新按钮：数据由生涯页统一拉取，这里只触发源头"""
        self.__refreshMyCareer()
        # 手动刷新就绕过 45 分钟缓存
        self.overviewInterface.reloadPatchNotes(force=True)

    def __openOverviewDetail(self, game: dict):
        self.openMatchDetail(game, self.overviewInterface.detailContext())

    def openMatchDetail(self, game: dict, context: list = None, puuid: str = ''):
        """单场详情浮层：本页浮起，关掉回到原位置，不切页

        context 是这一场所在的列表；给了浮层才显示「上一场 / 下一场」。
        puuid 是「这一局属于谁」：看好友生涯时传好友的，不传就按当前登录
        账号算 —— 少了它，别人单排的对局会因为「这局里没有我」解析失败。
        """
        if not game:
            return

        if not game.get('gameId'):
            # 刚打完的对局在 LCU 的历史里可能还没有 gameId（结算没写完），
            # 以前这里直接 return —— 用户点了一下什么都没发生，会以为界面卡死
            InfoBar.warning(
                self.tr("这局还打不开"),
                self.tr("对局数据还在入库，稍等片刻刷新后再看"),
                orient=Qt.Vertical, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000, parent=self)
            return

        # 连点两行时只留最后开的那一个
        self.__dismissMatchDetail()

        dialog = MatchDetailDialog(self)
        dialog.load(game, context, puuid)
        # 用 show() 而不是 exec_(): exec_ 会开嵌套事件循环,
        # qasync 的 asyncio 回调在里面不推进, 详情永远拉不回来
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.__matchDetailDialog = dialog

    @asyncSlot()
    async def __cascadeSetMicaEffect(self):
        isMicaEnabled = cfg.get(cfg.micaEnabled)
        self.setMicaEffectEnabled(isMicaEnabled)
        self.opggWindow.setMicaEffectEnabled(isMicaEnabled)

    @asyncSlot()
    async def __onFixLCUButtonClicked(self):
        if self.isClientProcessRunning:
            await connector.playAgain()

    def exceptHook(self, ty, value, tb):
        tracebackFormat = traceback.format_exception(ty, value, tb)
        title = self.tr("出现异常 😥")
        content = "".join(tracebackFormat)

        if ty in [ConnectionRefusedError, ClientConnectorError]:
            return

        logger.error(f"Exception occurred:\n{content}", "Crash")

        for call in connector.callStack:
            logger.error(call, "Crash")

        logger.error(str(self.searchInterface), "Crash")
        logger.error(str(self.gameInfoInterface), "Crash")
        logger.error(str(self.careerInterface), "Crash")
        logger.error(str(self.auxiliaryFuncInterface), "Crash")
        logger.error(str(self.settingInterface), "Crash")

        content = f"MMO战队训练助手 ver.{BETA or VERSION}\n{'-'*5}\n{content}"

        w = ExceptionMessageBox(title, content, self.window())

        if w.exec():
            pyperclip.copy(content)

        self.oldHook(ty, value, tb)
        signalBus.terminateListeners.emit()
        logger.error("Abnormal exit", "Crash")
        sys.exit()

    def __onCurrentStackedChanged(self, index):
        widget: SmoothScrollArea = self.stackedWidget.view.currentWidget()
        widget.delegate.vScrollBar.resetValue(0)

        # 切回总览页顺手看一眼公告（45 分钟缓存拦着，不会每次切页都打接口）。
        # 之前只在启动时拉一次，软件开一整天就永远是启动那版公告。
        if widget is self.overviewInterface:
            self.overviewInterface.reloadPatchNotes()

    def eventFilter(self, obj, e: QEvent):
        # Fix #553, #560
        if e.type() == QEvent.Type.Resize:
            self.windowSize = self.size()

        if e.type() == QEvent.Type.Move:
            self.resize(self.windowSize)

        return super().eventFilter(obj, e)
