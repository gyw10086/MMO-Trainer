import sys
import win32api
import traceback

from qasync import asyncSlot, asyncClose
from PyQt5.QtGui import QColor, QPainter, QIcon, QShowEvent
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QRect, QEvent
from PyQt5.QtWidgets import (QHBoxLayout, QStackedWidget, QWidget, QLabel,
                             QFrame, QVBoxLayout, QSpacerItem, QSizePolicy,
                             QApplication)


from app.common.icons import Icon
from app.lol.connector import connector
from app.lol.opgg import opgg
from app.lol.cn_data import cnData
from app.lol.champions import ChampionAlias
from app.common.logger import logger
from app.common.config import qconfig, cfg
from app.common import theme
from app.common import titlebar_skin
from app.common.style_sheet import StyleSheet
from app.common.signals import signalBus
from app.common.util import getLolClientWindowPos
from app.common.qfluentwidgets import (FramelessWindow, isDarkTheme, BackgroundAnimationWidget,
                                       FluentTitleBar,  ComboBox, BodyLabel, ToolTipFilter,
                                       ToolTipPosition, IndeterminateProgressRing, setTheme,
                                       Theme, PushButton, SearchLineEdit, ToolButton,
                                       FlyoutViewBase, Flyout, FlyoutAnimationType)
from app.components.transparent_button import TransparentToggleButton
from app.components.multi_champion_select import ChampionSelectFlyout
from app.components.champion_icon_widget import RoundIcon
from app.common.design_tokens import color, font
from app.view.opgg_tier_interface import TierInterface
from app.view.opgg_build_interface import BuildInterface

TAG = 'OpggWindow'

# 搜索结果里的分路文案（和顶部下拉保持一致）
POSITION_NAMES = {
    'TOP': '上单',
    'JUNGLE': '打野',
    'MID': '中单',
    'ADC': '下路',
    'SUPPORT': '辅助',
}

# 搜索结果最多列几条，再多也选不过来
MAX_SEARCH_RESULTS = 6


class OpggWindowBase(BackgroundAnimationWidget, FramelessWindow):
    def __init__(self, parent=None):
        self._isMicaEnabled = cfg.get(cfg.micaEnabled)
        self._lightBackgroundColor = QColor(244, 245, 247)
        # 和主窗口同一套 bg0。原来是 32,32,32，Mica 一开还会变全透明，
        # 露出来的模糊壁纸让这个窗口的底色跟着壁纸跑
        self._darkBackgroundColor = QColor(10, 11, 13)

        super().__init__(parent=parent)

        self.setTitleBar(FluentTitleBar(self))
        self.setMicaEffectEnabled(self._isMicaEnabled)
        self.setContentsMargins(0, 36, 0, 0)

        self.titleBar.hBoxLayout.setContentsMargins(14, 0, 0, 0)
        self.titleBar.maxBtn.setVisible(False)

        qconfig.themeChangedFinished.connect(self._onThemeChangedFinished)

        # 这是独立顶层窗口，不在主窗口子树里 —— 组件 qss（面板 / 战绩格）必须
        # 自己挂一份，否则切主题后这块区域一直是旧色
        theme.registerQssRoot(self)
        titlebar_skin.register(self.titleBar)

    def setCustomBackgroundColor(self, light, dark):
        self._lightBackgroundColor = QColor(light)
        self._darkBackgroundColor = QColor(dark)
        self._updateBackgroundColor()

    def _normalBackgroundColor(self):
        # 不再跟随 Mica 透明：设计稿的机能风底是纯色 #0A0B0D，
        # 透出壁纸会变成一块随壁纸变色的灰
        return self._darkBackgroundColor if isDarkTheme() else self._lightBackgroundColor

    def _onThemeChangedFinished(self):
        if self.isMicaEffectEnabled():
            self.windowEffect.setMicaEffect(self.winId(), isDarkTheme())

    def paintEvent(self, e):
        super().paintEvent(e)
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.backgroundColor)
        painter.drawRect(self.rect())

    def setMicaEffectEnabled(self, isEnabled: bool):
        """ set whether the mica effect is enabled, only available on Win11 """
        if sys.platform != 'win32' or sys.getwindowsversion().build < 22000:
            return

        self._isMicaEnabled = isEnabled

        if isEnabled:
            self.windowEffect.setMicaEffect(self.winId(), isDarkTheme())
        else:
            self.windowEffect.removeBackgroundEffect(self.winId())

        self.setBackgroundColor(self._normalBackgroundColor())

    def isMicaEffectEnabled(self):
        return self._isMicaEnabled

    def setStaysOnTopEnabled(self, enable: bool):
        isMinimized = self.isMinimized()
        isVisiable = self.isVisible()

        if enable:
            flag = self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        else:
            flag = self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint

        self.setWindowFlags(flag)

        self.windowEffect.enableBlurBehindWindow(self.winId())
        self.windowEffect.addWindowAnimation(self.winId())

        if sys.platform == 'win32' and sys.getwindowsversion().build >= 22000:
            self.windowEffect.addShadowEffect(self.winId())

        if not isMinimized and isVisiable:
            self.show()
        elif isVisiable and isMinimized:
            self.showMinimized()
            self.setWindowState(self.windowState() | Qt.WindowMinimized)

    def show(self):
        self.activateWindow()
        self.setWindowState(self.windowState() & ~
                            Qt.WindowMinimized | Qt.WindowActive)
        self.showNormal()


class OpggWindow(OpggWindowBase):
    def __init__(self, parent=None):
        super().__init__()

        # setTheme(Theme.LIGHT)
        self.vBoxLayout = QVBoxLayout(self)

        self.filterLayout = QHBoxLayout()
        self.searchButton = ToolButton(Icon.SEARCH)
        self.toggleButton = TransparentToggleButton(Icon.APPLIST, Icon.PERSON)
        self.modeComboBox = ComboBox()
        self.regionComboBox = ComboBox()
        self.tierComboBox = ComboBox()
        self.positionComboBox = ComboBox()

        self.debugButton = PushButton()
        self.debugButton.setFixedSize(33, 33)
        self.debugButton.clicked.connect(self.__onDebugButtonClicked)

        self.versionLabel = BodyLabel()

        self.stackedWidget = QStackedWidget()
        self.tierInterface = TierInterface()
        self.buildInterface = BuildInterface()
        self.waitingInterface = WaitingInterface()
        self.errorInterface = ErrorInterface()
        self.homeInterface = HomeInterface()

        # 缓存一个召唤师峡谷的梯队数据，切换位置的时候不重新调 opgg 了
        self.cachedTier = None
        self.cachedRegion = None
        self.cachedRankedTierList = None

        self.filterLock = False

        # 搜索弹层（Flyout.make 创建后由它自己 deleteLater 回收）
        self.searchFlyout: SearchLineEditFlyout = None

        self.__initWindow()
        self.__initLayout()

        # self.debugButton.click()
        self.debugButton.setVisible(False)
        self.setHomeInterfaceEnabled(True)

    def __initWindow(self):
        self.setFixedSize(640, 826)
        self.setWindowIcon(QIcon("app/resource/images/opgg.svg"))
        self.setWindowTitle("OP.GG")

        self.toggleButton.setToolTip(self.tr("显示梯队 / 出装"))
        self.toggleButton.installEventFilter(ToolTipFilter(
            self.toggleButton, 500, ToolTipPosition.TOP))

        self.modeComboBox.addItem(
            self.tr("排位"), icon="app/resource/images/sr-victory.png", userData='ranked')
        self.modeComboBox.addItem(
            self.tr("大乱斗"), icon="app/resource/images/ha-victory.png", userData='aram')
        self.modeComboBox.addItem(
            self.tr("斗魂"), icon="app/resource/images/arena-victory.png", userData='arena')
        self.modeComboBox.addItem(
            self.tr("无限火力"), icon="app/resource/images/other-victory.png", userData='urf')
        self.modeComboBox.addItem(
            self.tr("极限闪击"), icon="app/resource/images/other-victory.png", userData='nexus_blitz')

        # 数据源：国服 101 放在最前，BP 时从外服切过去只要点一下
        self.regionComboBox.addItem(
            self.tr("国服 101"), icon="app/resource/images/cn.svg", userData="cn")
        self.regionComboBox.addItem(
            self.tr("全部地区"), icon="app/resource/images/global.svg", userData="global")
        self.regionComboBox.addItem(
            self.tr("韩服"), icon="app/resource/images/kr.svg", userData="kr")

        self.tierComboBox.addItem(
            self.tr("全部"), icon="app/resource/images/UNRANKED.svg", userData="all")
        self.tierComboBox.addItem(
            self.tr("黄金以下"), icon="app/resource/images/GOLD.svg", userData="ibsg")
        self.tierComboBox.addItem(
            self.tr("黄金以上"), icon="app/resource/images/GOLD.svg", userData="gold_plus")
        self.tierComboBox.addItem(
            self.tr("铂金以上"), icon="app/resource/images/PLATINUM.svg", userData="platinum_plus")
        self.tierComboBox.addItem(
            self.tr("翡翠以上"), icon="app/resource/images/EMERALD.svg", userData="emerald_plus")
        self.tierComboBox.addItem(
            self.tr("钻石以上"), icon="app/resource/images/DIAMOND.svg", userData="diamond_plus")
        self.tierComboBox.addItem(
            self.tr("大师"), icon="app/resource/images/MASTER.svg", userData="master")
        self.tierComboBox.addItem(self.tr("大师以上"), icon="app/resource/images/MASTER.svg", userData="master_plus")
        self.tierComboBox.addItem(
            self.tr("宗师"), icon="app/resource/images/GRANDMASTER.svg", userData="grandmaster")
        self.tierComboBox.addItem(self.tr("王者"), icon="app/resource/images/CHALLENGER.svg", userData="challenger")

        self.positionComboBox.addItem(
            self.tr("上单"), "app/resource/images/icon-position-top.svg", "TOP")
        self.positionComboBox.addItem(
            self.tr("打野"), "app/resource/images/icon-position-jng.svg", "JUNGLE")
        self.positionComboBox.addItem(
            self.tr("中单"), "app/resource/images/icon-position-mid.svg", "MID")
        self.positionComboBox.addItem(
            self.tr("下路"), "app/resource/images/icon-position-bot.svg", "ADC")
        self.positionComboBox.addItem(
            self.tr("辅助"), "app/resource/images/icon-position-sup.svg", "SUPPORT")

        self.__setComboBoxCurrentData(
            self.tierComboBox, cfg.get(cfg.opggTier))
        self.__setComboBoxCurrentData(
            self.regionComboBox, cfg.get(cfg.opggRegion))
        self.__setComboBoxCurrentData(
            self.positionComboBox, cfg.get(cfg.opggPosition))

        self.stackedWidget.currentChanged.connect(
            self.__onStackedWidgetCurrentChanged)
        self.modeComboBox.currentIndexChanged.connect(
            self.__onFilterTextChanged)
        self.regionComboBox.currentIndexChanged.connect(
            self.__onRegionChanged)
        self.tierComboBox.currentIndexChanged.connect(
            self.__onFilterTextChanged)
        self.positionComboBox.currentIndexChanged.connect(
            self.__onFilterTextChanged)

        self.toggleButton.changed.connect(self.__onToggleButtonClicked)
        self.searchButton.clicked.connect(self.__onSearchButtonClicked)

        signalBus.toOpggBuildInterface.connect(
            self.__toChampionBuildInterface)

        # 上次退出时看的是国服，启动就按国服的状态锁一遍控件
        self.__applySourceState()

    def __setComboBoxCurrentData(self, comboBox: ComboBox, data) -> int:
        """
        这 `ComboBox` 居然没提供通过 `userData` 设置当前项的函数，我帮它实现一个

        虽然这函数是 $O(n)$ 的，但 `ComboBox` 提供的 `setCurrentText()` 也是 $O(n)$ 的 ^^
        """

        index = comboBox.findData(data)
        comboBox.setCurrentIndex(index)

    def setPositionByLane(self, lane):
        """BP 浮窗识别到分路后同步过来（LCU 写法 -> OP.GG 写法）

        只在值真的变了的时候才切，避免每次选人推送都触发一轮榜单请求。
        """
        mapping = {
            'TOP': 'TOP',
            'JUNGLE': 'JUNGLE',
            'MIDDLE': 'MID',
            'BOTTOM': 'ADC',
            'UTILITY': 'SUPPORT',
        }

        data = mapping.get(str(lane or '').strip().upper())

        if not data:
            return

        index = self.positionComboBox.findData(data)

        if index < 0 or index == self.positionComboBox.currentIndex():
            return

        self.positionComboBox.setCurrentIndex(index)

    def isCnSource(self) -> bool:
        """当前看的是不是国服（101）数据"""
        return self.regionComboBox.currentData() == 'cn'

    def __applySourceState(self):
        """切换数据源后调一下控件状态

        101 只有召唤师峡谷排位，没有大乱斗 / 斗魂 / 无限火力，所以国服态下
        把模式锁在「排位」；外服态再把模式放开。
        """
        cn = self.isCnSource()

        self.modeComboBox.setEnabled(not cn)
        self.modeComboBox.setToolTip(
            self.tr("101 数据站只有排位数据") if cn else "")

        if cn and self.modeComboBox.currentData() != 'ranked':
            self.setAutoRefreshEnabled(False)
            self.__setComboBoxCurrentData(self.modeComboBox, 'ranked')
            self.setAutoRefreshEnabled(True)

    def __onRegionChanged(self, index):
        # 地区下拉同时也是 BP 浮窗的数据源开关，改了就必须立刻落配置。
        #
        # 以前只在 __updateTierInterface 里 cfg.set，于是「停在英雄详情页切地区」
        # 不会更新配置 —— 详情页走的是 __updateBuildInterface，它只读 region 不写，
        # 结果榜单页的数据源变了、浮窗还挂在旧源上。放在这里就不依赖后续页面
        # 加载成功与否，网络失败也照样切。
        cfg.set(cfg.opggRegion, self.regionComboBox.currentData())

        self.__applySourceState()
        self.__onFilterTextChanged(index)

    def __initLayout(self):
        self.filterLayout.addWidget(self.toggleButton)
        self.filterLayout.addWidget(self.searchButton)
        self.filterLayout.addWidget(self.modeComboBox)
        self.filterLayout.addWidget(self.regionComboBox)
        self.filterLayout.addWidget(self.tierComboBox)
        self.filterLayout.addWidget(self.positionComboBox)
        self.filterLayout.addWidget(self.debugButton)
        self.filterLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding,  QSizePolicy.Fixed))
        self.filterLayout.addWidget(self.versionLabel)
        self.filterLayout.addSpacing(4)

        self.stackedWidget.addWidget(self.tierInterface)
        self.stackedWidget.addWidget(self.buildInterface)
        self.stackedWidget.addWidget(self.waitingInterface)
        self.stackedWidget.addWidget(self.errorInterface)
        self.stackedWidget.addWidget(self.homeInterface)

        self.vBoxLayout.setAlignment(Qt.AlignTop)
        self.vBoxLayout.addLayout(self.filterLayout)
        self.vBoxLayout.addWidget(self.stackedWidget)

    def __onToggleButtonClicked(self, index):
        self.stackedWidget.setCurrentIndex(index)

    def __onStackedWidgetCurrentChanged(self):
        widget = self.stackedWidget.currentWidget()
        self.setComboBoxesEnabled(True)

        if widget in [self.waitingInterface, self.homeInterface]:
            self.setComboBoxesEnabled(False)
        elif widget in [self.buildInterface, self.errorInterface]:
            self.searchButton.setEnabled(False)

        if widget is self.tierInterface \
                and not self.buildInterface.getCurrentChampionId():
            self.toggleButton.setEnabled(False)

        if (index := self.stackedWidget.currentIndex()) in [0, 1]:
            self.toggleButton.setCurrentIcon(index)

    def __onSearchButtonClicked(self):
        # 点击之后弹出的搜索框是空白的，让下方的所有英雄重新显示出来比较符合直觉
        self.tierInterface.tierList.showAllChampions()

        view = SearchLineEditFlyout()
        self.searchFlyout = view
        view.destroyed.connect(self.__onSearchFlyoutDestroyed)

        Flyout.make(view, self.searchButton, self, isDeleteOnClose=True)
        view.textChanged.connect(self.__onSearchLineTextChanged)
        view.championActivated.connect(self.__onSearchResultActivated)

        # 点一下搜索按钮之后，自动让弹出的搜索框获得焦点，可以少点一次鼠标
        view.searchLineEdit.setFocus()

    def __onSearchFlyoutDestroyed(self, *_):
        # flyout 关掉后 deleteLater 会把它销毁，这里同步清掉引用，
        # 否则后续输入会往一个已析构的 C++ 对象上塞结果
        self.searchFlyout = None

    @asyncSlot(str)
    async def __onSearchLineTextChanged(self, text):
        # 先按老规矩过滤当前分路的列表
        if text == '':
            self.tierInterface.tierList.showAllChampions()
        elif ChampionAlias.isAvailable():
            ids = ChampionAlias.getChampionIdsByAliasFuzzily(text)
            self.tierInterface.tierList.filterChampions('championId', ids)
        else:
            self.tierInterface.tierList.filterChampions('name', text)

        # 再补一份「跨分路」的结果 —— 当前分路搜不到人时靠它直达
        if self.searchFlyout is None:
            return

        try:
            self.searchFlyout.setResults(await self.__searchChampions(text))
        except RuntimeError:
            # 弹层刚被点掉、C++ 对象已经销毁，忽略这次刷新即可
            self.searchFlyout = None

    async def __searchChampions(self, text) -> list:
        """把输入串变成搜索结果行

        国服态用 101 的全分路索引：只列 101 真有数据的英雄，并且带上它的
        主分路，点进去不会撞到「国服暂无该英雄在这个分路的数据」。
        外服态没有这份索引，分路留空，交给 opgg 自己纠正到该英雄的主分路。
        """
        text = (text or '').strip()

        if not text:
            return []

        ids = self.__matchChampionIds(text)

        if not ids:
            return []

        rows = []

        if self.isCnSource():
            tier = self.tierComboBox.currentData() or 'emerald_plus'
            index = await cnData.getChampionLanes(tier)

            for championId in ids:
                positions = index.get(championId)

                if not positions:
                    continue

                main = positions[0]

                rows.append({
                    'championId': championId,
                    'name': connector.manager.getChampionNameById(championId),
                    'icon': await connector.getChampionIcon(championId),
                    'position': main['position'],
                    'winRate': main['winRate'],
                })

                if len(rows) >= MAX_SEARCH_RESULTS:
                    break

            return rows

        for championId in ids[:MAX_SEARCH_RESULTS]:
            rows.append({
                'championId': championId,
                'name': connector.manager.getChampionNameById(championId),
                'icon': await connector.getChampionIcon(championId),
                'position': '',
                'winRate': None,
            })

        return rows

    @staticmethod
    def __matchChampionIds(text) -> list:
        """别名库优先（「卡特」「Katarina」都能命中），没下载好就退回名字包含"""
        if ChampionAlias.isAvailable():
            ids = ChampionAlias.getChampionIdsByAliasFuzzily(text)

            # 别名表里存的是小写，英文名带着大写直接查会落空（Katarina）
            if not ids and text.lower() != text:
                ids = ChampionAlias.getChampionIdsByAliasFuzzily(text.lower())

            return ids

        return [cid for cid, name in (connector.manager.getChampions() or {}).items()
                if text in str(name)]

    def __onSearchResultActivated(self, championId, position):
        if not championId:
            return

        if self.searchFlyout is not None:
            # 先关掉弹层再切页，不然它会浮在出装页上面
            if (window := self.searchFlyout.parent()) is not None:
                window.close()

        # 复用 BP 浮窗那条链路：它会设好分路、再切到出装页
        signalBus.toOpggBuildInterface.emit(championId, "", position or "")

    def setComboBoxesEnabled(self, enabled):
        self.toggleButton.setEnabled(enabled)
        self.searchButton.setEnabled(enabled)
        # 国服（101）只有排位，模式下拉不给点，否则切页时又会被这里打开
        self.modeComboBox.setEnabled(enabled and not self.isCnSource())
        self.regionComboBox.setEnabled(enabled)
        self.tierComboBox.setEnabled(enabled)
        self.positionComboBox.setEnabled(enabled)

    def setCurrentInterface(self, widget: QWidget):
        self.stackedWidget.setCurrentWidget(widget)

    def setAutoRefreshEnabled(self, enabled):
        """
        设置界面是否随着 Combo Box 的改变而自动刷新

        用于想要一次性设置多个 Combo Box 的值之后再刷新的场景
        """

        self.filterLock = not enabled

    @asyncSlot(int)
    async def __onFilterTextChanged(self, _):
        # 给函数加个互斥锁，防止在该函数内修改了 combo box 的值，导致无限递归
        if self.filterLock:
            return

        # 上方 Combo box 改变的时候，相当于从自己跳转到自己
        current = self.stackedWidget.currentWidget()

        self.setAutoRefreshEnabled(False)
        await self.updateAndSwitchTo(current, current)

    @asyncSlot(int, str, str)
    async def __toChampionBuildInterface(self, championId, mode, pos):
        if championId == self.buildInterface.getCurrentChampionId() and \
                (mode == "" or mode == self.modeComboBox.currentData()):
            self.setCurrentInterface(self.buildInterface)
            return

        self.setAutoRefreshEnabled(False)
        self.buildInterface.setCurrentChampionId(championId)

        if mode:
            self.__setComboBoxCurrentData(self.modeComboBox, mode)

        if pos:
            self.__setComboBoxCurrentData(self.positionComboBox, pos)

        current = self.stackedWidget.currentWidget()
        await self.updateAndSwitchTo(current, self.buildInterface)

    async def updateAndSwitchTo(self, current, to):
        """
        这个函数做三件事情：

        1. 显示转圈界面，并锁住上方的 combo box
        2. 尝试刷新 `to` 界面
        3. 解锁上方的 combo box
        4. - 若更新成功，则转到 `to` 界面
           - 若更新失败，则转到错误界面
        """

        # 显示转圈圈界面，并且锁住上方的 combo box
        self.setCurrentInterface(self.waitingInterface)

        # 如果是在出错的界面请求的更新，则需要知道是因为刷新了啥才进入到的出错界面
        if current is self.errorInterface:
            # 将目标界面置为进入错误界面之前的界面
            to = self.errorInterface.getFromInterface()

        # 还停在「等待客户端启动」/ 转圈页时点筛选（例如刚切数据源），
        # 没有对应的刷新函数，直接落到梯队页
        if to in (self.homeInterface, self.waitingInterface):
            to = self.tierInterface

        try:
            # 尝试刷新当前的界面
            await self.__updateInterface(to)

            # 让转圈消失，显示界面
            self.setCurrentInterface(to)
        except Exception as e:
            stack = traceback.format_exc()
            logger.error(f"Get OPGG data failed, {stack}\n{e}", TAG)

            # 记录一下是想要进入到哪个界面时加载出错了
            self.errorInterface.setFromInterface(to)

            # 显示出错的界面
            self.setCurrentInterface(self.errorInterface)
        finally:
            self.setAutoRefreshEnabled(True)

    async def __updateInterface(self, interface: QWidget):
        # 窗口可能比主窗口先被打开（托盘 / 快捷键），session 没建就补一次
        if not self.isCnSource():
            await opgg.start()

        map = {
            self.tierInterface: self.__updateTierInterface,
            self.buildInterface: self.__updateBuildInterface
        }

        await map[interface]()

    async def __updateTierInterface(self):
        mode = self.modeComboBox.currentData()
        region = self.regionComboBox.currentData()
        tier = self.tierComboBox.currentData()
        position = self.positionComboBox.currentData()

        cfg.set(cfg.opggRegion, region)
        cfg.set(cfg.opggTier, tier)
        cfg.set(cfg.opggPosition, position)

        logger.info(
            f"Get tier list: {mode}, {region}, {tier}, {position}", TAG)

        # 国服（101）只有召唤师峡谷排位，接口也只接受单个分路
        if self.isCnSource():
            self.positionComboBox.setVisible(True)
            self.tierComboBox.setVisible(True)

            data = await cnData.getTierList(tier, position)

            self.versionLabel.setText(
                self.tr("国服 101 · 版本 ") + data['version'])
            self.tierInterface.tierList.updateList(data['data'][position])
            return

        # 只有在排位模式下，可以选择对应的分路
        if mode != 'ranked':
            position = 'none'
            self.positionComboBox.setVisible(False)
        else:
            self.positionComboBox.setVisible(True)

        # 斗魂竞技场的段位选择只能是 "all"
        if mode == 'arena':
            tier = 'all'
            self.tierComboBox.setVisible(False)
        else:
            self.tierComboBox.setVisible(True)

        if mode == 'ranked':
            # rank 模式下，如果是切换了位置选项，会命中 cache，不用重新请求了
            if tier == self.cachedTier and \
                    region == self.cachedRegion and \
                    self.cachedRankedTierList != None:
                res = self.cachedRankedTierList['data'][position]
                data = self.cachedRankedTierList

            # 否则是第一次请求 rank 模式数据，记录一下 cache
            else:
                data = await opgg.getTierList(region, mode, tier)
                self.cachedTier = tier
                self.cachedRegion = region
                self.cachedRankedTierList = data

                res = data['data'][position]

        # 除了 rank 以外的其他模式，该咋整咋整吧
        else:
            data = await opgg.getTierList(region, mode, tier)
            res = data['data']

        version = data['version']
        self.versionLabel.setText(self.tr("版本：") + version)
        self.tierInterface.tierList.updateList(res)

    async def __updateBuildInterface(self):
        mode = self.modeComboBox.currentData()
        region = self.regionComboBox.currentData()
        tier = self.tierComboBox.currentData()
        position = self.positionComboBox.currentData()
        championId = self.buildInterface.getCurrentChampionId()

        # 国服：101 的英雄详情（符文 / 加点 / 装备 / 对位）
        if self.isCnSource():
            logger.info(
                f"Get cn champion build, {tier}, {position}, {championId}", TAG)

            data = await cnData.getChampionBuild(championId, position, tier)

            self.buildInterface.updateInterface(data['data'])
            self.versionLabel.setText(
                self.tr("国服 101 · 版本 ") + data['version'])
            return

        # 只有在排位模式下，可以选择对应的分路
        if mode != 'ranked':
            position = 'none'
            self.positionComboBox.setVisible(False)
        else:
            self.positionComboBox.setVisible(True)

        # 斗魂竞技场的段位选择只能是 "all"
        if mode == 'arena':
            tier = 'all'
            self.tierComboBox.setVisible(False)
        else:
            self.tierComboBox.setVisible(True)

        logger.info(
            f"Get champion build, {mode}, {region}, {tier}, {position}, {championId}", TAG)

        data = await opgg.getChampionBuild(region, mode, championId, position, tier)

        self.buildInterface.updateInterface(data['data'])

        # 若英雄没有在特定位置下的数据，则根据得到的数据重新设置一下位置的 combo box
        if (pos := data['data']['summary']['position']) != position \
                and mode == 'ranked':

            # 在设置之前需要锁住 combo box changed 的槽函数，防止它自动刷新
            self.setAutoRefreshEnabled(False)
            self.__setComboBoxCurrentData(self.positionComboBox, pos)
            self.setAutoRefreshEnabled(True)

        self.versionLabel.setText(self.tr("版本：") + data['version'])

    @asyncSlot(bool)
    async def __onDebugButtonClicked(self, _):
        await opgg.start()
        await connector.autoStart()
        await ChampionAlias.checkAndUpdate()

        await self.initWindow()

    async def initWindow(self):
        self.__onFilterTextChanged(1)

    def showEvent(self, a0: QShowEvent) -> None:
        """在显示的时候，自动显示在客户端正右侧"""

        size: QSize = self.size()
        pos = getLolClientWindowPos()

        if not pos:
            self.__moveRightCenter()
            return super().showEvent(a0)

        # 别问为什么要这么算，我也不知道，反正它能跑
        dpi = self.devicePixelRatioF()
        x = pos.right()
        y = pos.center().y() - size.height() * dpi / 2
        rect = QRect(int(x / dpi), int(y / dpi), size.width(), size.height())

        # 如果超出右边界，则直接 return 了
        screenWidth = win32api.GetSystemMetrics(0)
        if (rect.left() + size.width()) * dpi > screenWidth:
            self.__moveRightCenter()
            return super().showEvent(a0)

        self.setGeometry(rect)
        return super().showEvent(a0)

    def __moveRightCenter(self):
        """
        将窗口移动到屏幕最右侧的中心
        """
        desktop = QApplication.desktop().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w - self.width(), h // 2 - self.height() // 2)

    def setHomeInterfaceEnabled(self, enabeld):
        interface = self.homeInterface if enabeld else self.tierInterface
        self.stackedWidget.setCurrentWidget(interface)

    def eventFilter(self, obj, e: QEvent):
        # Fix #553
        if e.type() == QEvent.Type.MouseButtonRelease:
            self.adjustSize()

        return super().eventFilter(obj, e)

    def closeEvent(self, e):
        # Fix #555
        e.ignore()
        self.hide()


class WaitingInterface(QFrame):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.processRing = IndeterminateProgressRing()

        self.__initWidget()
        self.__initLayout()

        StyleSheet.OPGG_WAITING_INTERFACE.apply(self)

    def __initWidget(self):
        pass

    def __initLayout(self):
        self.vBoxLayout.setAlignment(Qt.AlignCenter)
        self.vBoxLayout.addWidget(self.processRing, alignment=Qt.AlignCenter)


class ErrorInterface(QFrame):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.title = QLabel(self.tr("获取数据失败 😭"))
        self.content = QLabel(self.tr("请稍后再试"))

        self.fromInterface: QWidget = None

        self.__initWidget()
        self.__initLayout()

        StyleSheet.OPGG_ERROR_INTERFACE.apply(self)

    def setFromInterface(self, interface: QWidget):
        self.fromInterface = interface

    def getFromInterface(self):
        return self.fromInterface

    def __initWidget(self):
        self.title.setObjectName("titleLabel")
        self.content.setObjectName("contentLabel")

    def __initLayout(self):
        self.vBoxLayout.setAlignment(Qt.AlignCenter)
        self.vBoxLayout.addWidget(self.title, alignment=Qt.AlignCenter)
        self.vBoxLayout.addWidget(self.content, alignment=Qt.AlignCenter)


class HomeInterface(QFrame):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.title = QLabel(self.tr("等待客户端启动"))

        self.__initWidget()
        self.__initLayout()

        StyleSheet.OPGG_HOME_INTERFACE.apply(self)

    def setFromInterface(self, interface: QWidget):
        self.fromInterface = interface

    def getFromInterface(self):
        return self.fromInterface

    def __initWidget(self):
        self.title.setObjectName("titleLabel")

    def __initLayout(self):
        self.vBoxLayout.setAlignment(Qt.AlignCenter)
        self.vBoxLayout.addWidget(self.title, alignment=Qt.AlignCenter)


class SearchResultItem(QFrame):
    """搜索结果的一行：头像 + 英雄名 + 分路 + 胜率

    点一下直接进这个英雄的出装页。分路由数据源给：国服态来自 101 的
    全分路索引（保证点进去一定有数据），外服态留空、由 opgg 自己纠正
    到该英雄的主分路。
    """

    activated = pyqtSignal(int, str)

    def __init__(self, data: dict, parent: QWidget = None):
        super().__init__(parent)

        self.data = data

        self.setObjectName('searchResultItem')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)
        self.setStyleSheet(
            "QFrame#searchResultItem { border-radius: 4px; background: transparent; }"
            f"QFrame#searchResultItem:hover {{ background: {color('hover')}; }}")

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(6, 0, 8, 0)
        self.hBoxLayout.setSpacing(6)

        self.icon = RoundIcon(data.get('icon'), 22, 2, 1)
        self.nameLabel = QLabel(data.get('name') or '')
        self.nameLabel.setStyleSheet(f"color: {color('text')}; {font(12)}")

        self.positionLabel = QLabel(
            POSITION_NAMES.get(data.get('position') or '', ''))
        self.positionLabel.setFixedWidth(28)
        self.positionLabel.setStyleSheet(
            f"color: {color('textMuted')}; {font(10)}")

        self.rateLabel = QLabel()
        self.rateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        if data.get('winRate'):
            # 高过 50% 用品牌黄、低于则压成次级灰，一眼看出这个英雄强不强
            winRate = data['winRate']
            rateColor = color('gold') if winRate >= 0.5 else color('textMuted')

            self.rateLabel.setText(f"{winRate * 100:.1f}%")
            self.rateLabel.setStyleSheet(f"color: {rateColor}; {font(11)}")

        self.hBoxLayout.addWidget(self.icon)
        self.hBoxLayout.addWidget(self.nameLabel)
        self.hBoxLayout.addSpacing(2)
        self.hBoxLayout.addWidget(self.positionLabel)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.rateLabel)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.activated.emit(self.data.get('championId') or 0,
                                self.data.get('position') or '')

        super().mouseReleaseEvent(e)


class SearchLineEditFlyout(FlyoutViewBase):
    textChanged = pyqtSignal(str)
    championActivated = pyqtSignal(int, str)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(4)

        self.searchLineEdit = SearchLineEdit()
        self.searchLineEdit.setPlaceholderText(self.tr("搜索英雄，点击直达"))
        self.searchLineEdit.setMinimumWidth(248)

        self.resultLayout = QVBoxLayout()
        self.resultLayout.setContentsMargins(0, 0, 0, 0)
        self.resultLayout.setSpacing(1)

        self.resultBox = QWidget()
        self.resultBox.setLayout(self.resultLayout)
        self.resultBox.setVisible(False)

        self.items: list[SearchResultItem] = []

        self.vBoxLayout.addWidget(self.searchLineEdit)
        self.vBoxLayout.addWidget(self.resultBox)

        self.searchLineEdit.textChanged.connect(self.textChanged)
        self.searchLineEdit.returnPressed.connect(self.__activateFirst)

    def setResults(self, results: list):
        self.__clearItems()

        for data in (results or [])[:MAX_SEARCH_RESULTS]:
            item = SearchResultItem(data)
            item.activated.connect(self.championActivated)
            self.resultLayout.addWidget(item)
            self.items.append(item)

        self.resultBox.setVisible(bool(self.items))

        # flyout 是 Popup，长高之后位置不动、向下展开刚好，
        # 但必须手动 adjustSize，否则新加的行会被裁掉
        self.adjustSize()

        if (flyout := self.parent()) is not None:
            flyout.adjustSize()

    def __clearItems(self):
        for item in self.items:
            # deleteLater 要等下一轮事件循环，这期间旧行还挂在原位，
            # 连续输入会看到上一批结果垫在下面
            item.setParent(None)
            item.deleteLater()

        self.items = []

    def __activateFirst(self):
        if self.items:
            self.championActivated.emit(
                self.items[0].data.get('championId') or 0,
                self.items[0].data.get('position') or '')
