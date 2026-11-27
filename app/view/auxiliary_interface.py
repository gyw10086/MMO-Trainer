import asyncio
import os
import stat
import threading

from PyQt5.QtCore import Qt, pyqtSignal, QEvent, QSize, QObject
from PyQt5.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QFrame, QSpacerItem, QSizePolicy, QCompleter, QApplication)
from qasync import asyncSlot
from qfluentwidgets import EditableComboBox

from app.common import win_window
from app.common.config import cfg
from app.common.icons import Icon
from app.common.logger import logger
from app.common.qfluentwidgets import (SettingCardGroup, SwitchSettingCard, ExpandLayout,
                                       SettingCard, LineEdit, PushButton,
                                       setCustomStyleSheet, ComboBox, SwitchButton, ConfigItem,
                                       qconfig, IndicatorPosition, InfoBar, InfoBarPosition,
                                       SpinBox, ExpandGroupSettingCard, TransparentToolButton,
                                       FluentIcon, Flyout, FlyoutAnimationType, MessageBox, ToolTipFilter,
                                       ToolTipPosition)
from app.common.style_sheet import StyleSheet
from app.components.champion_icon_widget import RoundIcon, SummonerSpellButton
from app.components.message_box import MultiChampionSelectMsgBox
from app.components.multi_champion_select import ChampionSelectFlyout, SplashesFlyout
from app.components.page_head import PageHead
from app.components.seraphine_interface import SeraphineInterface
from app.components.summoner_spell_widget import SummonerSpellSelectFlyout
from app.lol.connector import connector
from app.lol.exceptions import *
from app.lol.tools import fixLCUWindow

TAG = "AuxiliaryInterface"


class AuxiliaryInterface(SeraphineInterface):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        # 设计稿的 .page-head：总览 › 其他功能  SEC-05
        self.pageHead = PageHead(
            self.tr('其他功能'), crumbs=(self.tr('总览'),),
            sec='SEC-05', parent=self)
        self.pageHead.crumbClicked.connect(self.__onCrumb)
        self.pageHead.move(36, 28)

        self.profileGroup = SettingCardGroup(self.tr("资料"),
                                             self.scrollWidget)
        self.gameGroup = SettingCardGroup(self.tr("对局"), self.scrollWidget)
        self.bpGroup = SettingCardGroup(
            self.tr("禁选"), self.scrollWidget)
        self.clientGroup = SettingCardGroup(
            self.tr("客户端"), self.scrollWidget)

        self.onlineStatusCard = OnlineStatusCard(
            title=self.tr("在线状态"),
            content=self.tr("设置个人资料的在线状态"),
            parent=self.profileGroup)
        self.profileBackgroundCard = ProfileBackgroundCard(
            self.tr("个人背景"),
            self.tr("设置个人资料背景皮肤"), self.profileGroup)
        self.profileTierCard = ProfileTierCard(
            self.tr("段位展示"),
            self.tr("设置个人资料卡展示的段位"),
            self.profileGroup)
        self.onlineAvailabilityCard = OnlineAvailabilityCard(
            self.tr("在线可用性"),
            self.tr("设置在线可用状态"), self.profileGroup)
        self.removeTokensCard = RemoveTokensCard(
            self.tr("移除成就代币"),
            self.tr("移除个人资料上的全部成就代币"),
            self.profileGroup)
        self.removePrestigeCrestCard = RemovePrestigeCrestCard(
            self.tr("移除荣耀徽章"),
            self.tr("移除头像上的荣耀徽章（需要召唤师等级 ≥ 525）"),
            self.profileGroup)
        self.lockConfigCard = LockConfigCard(
            self.tr("锁定配置"),
            self.tr("让游戏配置不可被修改"),
            self.gameGroup)

        self.fixDpiCard = FixClientDpiCard(
            self.tr("修复客户端窗口"),
            self.tr("修复 DirectX 9 导致的客户端窗口尺寸异常（需要管理员权限）"),
            self.clientGroup
        )
        self.restartClientCard = RestartClientCard(
            self.tr("重启客户端"),
            self.tr("重启客户端且不重新排队"),
            self.clientGroup
        )

        # self.createPracticeLobbyCard = CreatePracticeLobbyCard(
        #     self.tr("创建 5v5 训练房"),
        #     self.tr("只能添加人机到房间"),
        #     self.gameGroup)

        self.autoReconnectCard = SwitchSettingCard(
            Icon.CONNECTION,
            self.tr("自动重连"),
            self.tr("断线后自动重连"),
            cfg.enableAutoReconnect, self.gameGroup)
        self.autoHonorCard = AutoHonorCard(
            self.tr("自动点赞"),
            self.tr("对局结束后自动给队友点赞"),
            cfg.enableAutoHonor, cfg.autoHonorCategory, self.gameGroup)
        self.autoPlayAgainCard = AutoPlayAgainCard(
            self.tr("自动回房"),
            self.tr("对局结束后自动回到房间"),
            cfg.enableAutoPlayAgain, cfg.autoPlayAgainDelay, self.gameGroup)
        self.spectateCard = SpectateCard(
            self.tr("观战"),
            self.tr("观战同环境召唤师的对局"),
            self.gameGroup
        )

        self.autoAcceptMatchingCard = AutoAcceptMatchingCard(
            self.tr("自动接受"),
            self.tr("匹配成功后按设定的秒数自动接受"),
            cfg.enableAutoAcceptMatching, cfg.autoAcceptMatchingDelay,
            self.bpGroup)
        self.autoAcceptSwapingCard = AutoAcceptSwapingCard(
            self.tr("自动接受交换"),
            self.tr("在 BP 中自动接受换位 / 换英雄请求"),
            cfg.autoAcceptCeilSwap, cfg.autoAcceptChampTrade,
            self.bpGroup)
        self.autoSelectChampionCard = AutoSelectChampionCard(
            self.tr("自动选英雄"),
            self.tr("轮到你选人时自动锁定"),
            cfg.enableAutoSelectChampion,
            cfg.autoSelectChampion,
            cfg.autoSelectChampionTop,
            cfg.autoSelectChampionJug,
            cfg.autoSelectChampionMid,
            cfg.autoSelectChampionBot,
            cfg.autoSelectChampionSup,
            cfg.enableAutoSelectTimeoutCompleted,
            self.bpGroup)
        self.autoBanChampionsCard = AutoBanChampionCard(
            self.tr("自动禁英雄"),
            self.tr("轮到你禁人时自动禁用"),
            cfg.enableAutoBanChampion,
            cfg.autoBanChampion,
            cfg.autoBanChampionTop,
            cfg.autoBanChampionJug,
            cfg.autoBanChampionMid,
            cfg.autoBanChampionBot,
            cfg.autoBanChampionSup,
            cfg.pretentBan,
            cfg.autoBanDelay,
            self.bpGroup)
        self.autoSetSpellCard = AutoSetSummonerSpellCard(
            self.tr("自动带召唤师技能"),
            self.tr("进入选人时自动设置召唤师技能"),
            cfg.enableAutoSetSpells,
            cfg.autoSetSummonerSpell,
            cfg.autoSetSummonerSpellTop,
            cfg.autoSetSummonerSpellJug,
            cfg.autoSetSummonerSpellMid,
            cfg.autoSetSummonerSpellBot,
            cfg.autoSetSummonerSpellSup,
            self.bpGroup
        )

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.scrollWidget.setObjectName('scrollWidget')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 90, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        StyleSheet.AUXILIARY_INTERFACE.apply(self)

    def showEvent(self, event):
        super().showEvent(event)

        if not getattr(self, '_AuxiliaryInterface__switchesLocalized', False):
            self.__localizeSwitches()
            self.__switchesLocalized = True

    def __localizeSwitches(self):
        """开关上的 On / Off 是库里的硬编码英文，统一换成中文"""
        for switch in self.findChildren(SwitchButton):
            switch.setOnText('开')
            switch.setOffText('关')
            switch.setText('开' if switch.isChecked() else '关')

    def __onCrumb(self, name: str):
        window = self.window()
        target = getattr(window, 'overviewInterface', None)

        if target is not None and hasattr(window, 'switchTo'):
            window.switchTo(target)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        head = getattr(self, 'pageHead', None)

        if head is not None:
            head.resize(max(200, self.width() - 60), 32)

    def __initLayout(self):
        # 个人主页
        self.profileGroup.addSettingCard(self.onlineStatusCard)
        self.profileGroup.addSettingCard(self.profileBackgroundCard)
        self.profileGroup.addSettingCard(self.profileTierCard)
        self.profileGroup.addSettingCard(self.onlineAvailabilityCard)
        self.profileGroup.addSettingCard(self.removeTokensCard)
        self.profileGroup.addSettingCard(self.removePrestigeCrestCard)

        # BP
        self.bpGroup.addSettingCard(self.autoAcceptMatchingCard)
        self.bpGroup.addSettingCard(self.autoAcceptSwapingCard)
        self.bpGroup.addSettingCard(self.autoSelectChampionCard)
        self.bpGroup.addSettingCard(self.autoBanChampionsCard)
        self.bpGroup.addSettingCard(self.autoSetSpellCard)

        # 游戏
        self.gameGroup.addSettingCard(self.autoReconnectCard)
        self.gameGroup.addSettingCard(self.autoHonorCard)
        self.gameGroup.addSettingCard(self.autoPlayAgainCard)
        # self.gameGroup.addSettingCard(self.createPracticeLobbyCard)
        self.gameGroup.addSettingCard(self.spectateCard)
        self.gameGroup.addSettingCard(self.lockConfigCard)

        # 客户端修复
        self.clientGroup.addSettingCard(self.fixDpiCard)
        self.clientGroup.addSettingCard(self.restartClientCard)

        # 组头编号按视觉顺序，和设置页保持同一套写法
        self.bpGroup.setSerial('01')
        self.gameGroup.setSerial('02')
        self.clientGroup.setSerial('03')
        self.profileGroup.setSerial('04')

        self.expandLayout.setSpacing(16)
        self.expandLayout.setContentsMargins(36, 0, 36, 0)
        self.expandLayout.addWidget(self.bpGroup)
        self.expandLayout.addWidget(self.gameGroup)
        self.expandLayout.addWidget(self.clientGroup)
        self.expandLayout.addWidget(self.profileGroup)

    async def initChampionList(self):
        async def initChampions():
            champions = await self.autoSelectChampionCard.initChampionList()
            await self.autoBanChampionsCard.initChampionList(champions)
            await self.profileBackgroundCard.initChampionList(champions)

        async def initSummonerSpell():
            await self.autoSetSpellCard.initSummonerSpells()

        await asyncio.gather(initChampions(), initSummonerSpell())


class OnlineStatusCard(ExpandGroupSettingCard):
    def __init__(self, title, content, parent=None):
        super().__init__(Icon.COMMENT, title, content, parent)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QHBoxLayout(self.inputWidget)
        self.statusLabel = QLabel(
            self.tr("要切换的在线状态："))
        self.lineEdit = LineEdit()

        self.buttonWidget = QWidget()
        self.buttonLayout = QHBoxLayout(self.buttonWidget)
        self.pushButton = PushButton(self.tr("应用"), self)

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.inputLayout.setSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(
            self.statusLabel, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(self.lineEdit, alignment=Qt.AlignRight)
        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonLayout.addWidget(self.pushButton, 0, Qt.AlignRight)
        self.buttonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.buttonWidget)

    def __initWidget(self):
        self.lineEdit.setMinimumWidth(250)
        self.lineEdit.setPlaceholderText(self.tr("请输入状态文本"))

        self.pushButton.setMinimumWidth(100)
        self.pushButton.clicked.connect(self.__onPushButtonClicked)

    @asyncSlot()
    async def __onPushButtonClicked(self):
        msg = self.lineEdit.text()
        await connector.setOnlineStatus(msg)

    def clear(self):
        self.lineEdit.clear()


class ProfileBackgroundCard(ExpandGroupSettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.VIDEO_PERSON, title, content, parent)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QGridLayout(self.inputWidget)

        self.championLabel = QLabel(self.tr("英雄名称："))
        self.championButton = PushButton(self.tr("选择英雄"), self)

        self.skinLabel = QLabel(self.tr("皮肤名称："))
        self.skinButton = PushButton(self.tr("选择皮肤"), self)

        self.buttonWidget = QWidget(self.view)
        self.buttonLayout = QHBoxLayout(self.buttonWidget)
        self.pushButton = PushButton(self.tr("应用"))

        self.completer = None

        self.chosenSkinId = None
        self.skins = None

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.inputLayout.setVerticalSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(
            self.championLabel, 0, 0, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(
            self.championButton, 0, 1, alignment=Qt.AlignRight)

        self.inputLayout.addWidget(
            self.skinLabel, 1, 0, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(
            self.skinButton, 1, 1, alignment=Qt.AlignRight)

        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonLayout.addWidget(self.pushButton, 0, Qt.AlignRight)
        self.buttonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.buttonWidget)

    def __initWidget(self):
        self.championButton.setMinimumWidth(100)
        self.championButton.clicked.connect(self.__onSelectButtonClicked)

        self.skinButton.setMinimumWidth(100)
        self.skinButton.setEnabled(False)
        self.skinButton.clicked.connect(self.__onSkinButtonClicked)

        self.pushButton.setMinimumWidth(100)
        self.pushButton.setEnabled(False)
        self.pushButton.clicked.connect(self.__onApplyButtonClicked)

    def __onSelectButtonClicked(self):
        view = ChampionSelectFlyout(self.champions)
        view.championSelected.connect(self.__onChampionSelected)

        self.w = Flyout.make(view, self.championButton,
                             self, FlyoutAnimationType.SLIDE_RIGHT, True)

    def __onSkinButtonClicked(self):
        view = SplashesFlyout(self.skins, self.chosenSkinId)
        view.skinWidget.selectedChanged.connect(self.__onSkinSelectedChanged)

        Flyout.make(view, self.skinButton, self,
                    FlyoutAnimationType.SLIDE_RIGHT, True)

    def __onSkinSelectedChanged(self, skinId, name):
        self.chosenSkinId = skinId
        self.skinLabel.setText(self.tr("皮肤名称：") + name)

    async def initChampionList(self, champions: dict = None):
        if champions:
            self.champions = champions
        else:
            self.champions = {
                i: [name, await connector.getChampionIcon(i)]
                for i, name in connector.manager.getChampions().items()
                if i != -1
            }

        return self.champions

    def __onChampionSelected(self, championId):
        self.w.fadeOut()
        self.championLabel.setText(self.tr(
            "Champion's name: ") + connector.manager.getChampionNameById(championId))
        self.skinLabel.setText(self.tr("皮肤名称："))
        self.chosenSkinId = None

        name = self.champions[championId][0]
        self.skins = connector.manager.getSkinListByChampionName(name)

        self.skinButton.clicked.emit()

        self.skinButton.setEnabled(True)
        self.pushButton.setEnabled(True)

    @asyncSlot()
    async def __onApplyButtonClicked(self):
        contentId = connector.manager.getSkinAugments(self.chosenSkinId)

        if contentId == None:
            await connector.setProfileBackground(self.chosenSkinId)
            return

        self.skinId = self.chosenSkinId
        self.contentId = contentId

        msg = MessageBox(
            self.tr("这款皮肤有签名版"),
            self.tr("切换到签名版会重启客户端"),
            self.window())

        msg.accepted.connect(self.__onMsgBoxYesButtonClicked)
        msg.rejected.connect(self.__onMsgBoxNoButtonClicked)

        msg.yesButton.setText(self.tr("签名版"))
        msg.cancelButton.setText(self.tr("普通版"))

        msg.exec_()

        InfoBar.success(title=self.tr("应用"), content=self.tr("操作成功"),
                        orient=Qt.Vertical, isClosable=True,
                        position=InfoBarPosition.TOP_RIGHT, duration=5000,
                        parent=self.window().auxiliaryFuncInterface)

    @asyncSlot()
    async def __onMsgBoxYesButtonClicked(self):
        await connector.setProfileBackground(self.skinId)
        await connector.setProfileBackgroundAugments(self.contentId)
        await connector.restartClient()

    @asyncSlot()
    async def __onMsgBoxNoButtonClicked(self):
        await connector.setProfileBackground(self.skinId)


class ProfileTierCard(ExpandGroupSettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.CERTIFICATE, title, content, parent)
        self.inputWidget = QWidget(self.view)
        self.inputLayout = QGridLayout(self.inputWidget)

        self.rankModeLabel = QLabel(self.tr("模式："))
        self.rankModeBox = ComboBox()
        self.tierLabel = QLabel(self.tr("段位："))
        self.tierBox = ComboBox()
        self.divisionLabel = QLabel(self.tr("小段："))
        self.divisionBox = ComboBox()

        self.buttonWidget = QWidget(self.view)
        self.buttonLayout = QHBoxLayout(self.buttonWidget)
        self.pushButton = PushButton(self.tr("应用"))

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.inputLayout.setVerticalSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(
            self.rankModeLabel, 0, 0, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(
            self.rankModeBox, 0, 1, alignment=Qt.AlignRight)

        self.inputLayout.addWidget(
            self.tierLabel, 1, 0, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(
            self.tierBox, 1, 1, alignment=Qt.AlignRight)

        self.inputLayout.addWidget(
            self.divisionLabel, 2, 0, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(
            self.divisionBox, 2, 1, alignment=Qt.AlignRight)

        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonLayout.addWidget(self.pushButton, 0, Qt.AlignRight)
        self.buttonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.buttonWidget)

    def __initWidget(self):
        self.rankModeBox.addItems([
            self.tr("云顶之弈"),
            self.tr("单双排"),
            self.tr("灵活组排")
        ])
        self.tierBox.addItems([
            self.tr("未定级"),
            self.tr("黑铁"),
            self.tr("青铜"),
            self.tr("白银"),
            self.tr("黄金"),
            self.tr("铂金"),
            self.tr("翡翠"),
            self.tr("钻石"),
            self.tr("大师"),
            self.tr("宗师"),
            self.tr("王者")
        ])
        self.divisionBox.addItems(['I', 'II', 'III', 'IV'])

        self.rankModeBox.setPlaceholderText(self.tr("请选择模式"))
        self.tierBox.setPlaceholderText(self.tr("请选择段位"))
        self.divisionBox.setPlaceholderText(self.tr("请选择小段"))

        self.pushButton.setEnabled(False)

        self.rankModeBox.setMinimumWidth(250)
        self.tierBox.setMinimumWidth(250)
        self.divisionBox.setMinimumWidth(250)
        self.pushButton.setMinimumWidth(100)

        self.rankModeBox.currentTextChanged.connect(
            self.__onRankModeTextChanged)
        self.tierBox.currentTextChanged.connect(self.__onTierTextChanged)
        self.divisionBox.currentTextChanged.connect(
            self.__setPushButtonAvailability)
        self.pushButton.clicked.connect(self.__onPushButtonClicked)

    def clear(self):
        self.rankModeBox.setCurrentIndex(0)
        self.tierBox.setCurrentIndex(0)
        self.divisionBox.setCurrentIndex(0)

        self.rankModeBox.setPlaceholderText(self.tr("模式"))
        self.tierBox.setPlaceholderText(self.tr("段位"))
        self.divisionBox.setPlaceholderText(self.tr("小段"))

    def __onRankModeTextChanged(self):
        currentText = self.tierBox.currentText()
        self.tierBox.clear()
        if self.rankModeBox.currentIndex() == 0:
            self.tierBox.addItems([
                self.tr("未定级"),
                self.tr("黑铁"),
                self.tr("青铜"),
                self.tr("白银"),
                self.tr("黄金"),
                self.tr("铂金"),
                self.tr("钻石"),
                self.tr("大师"),
                self.tr("宗师"),
                self.tr("王者")
            ])

            if currentText != self.tr("翡翠"):
                self.tierBox.setCurrentText(currentText)
            else:
                self.tierBox.setPlaceholderText(self.tr("段位"))
        else:
            self.tierBox.addItems([
                self.tr("未定级"),
                self.tr("黑铁"),
                self.tr("青铜"),
                self.tr("白银"),
                self.tr("黄金"),
                self.tr("铂金"),
                self.tr("翡翠"),
                self.tr("钻石"),
                self.tr("大师"),
                self.tr("宗师"),
                self.tr("王者")
            ])

            self.tierBox.setCurrentText(currentText)

        self.__setPushButtonAvailability()

    def __onTierTextChanged(self):
        currentTier = self.tierBox.currentText()
        currentDivision = self.divisionBox.currentText()
        self.divisionBox.clear()
        if currentTier in [
            self.tr("未定级"),
            self.tr("大师"),
            self.tr("宗师"),
            self.tr("王者")
        ]:
            self.divisionBox.addItems(['--'])
            self.divisionBox.setCurrentText('--')
        else:
            self.divisionBox.addItems(['I', 'II', 'III', 'IV'])
            if currentDivision != '--':
                self.divisionBox.setCurrentText(currentDivision)
            else:
                self.divisionBox.setPlaceholderText("Division")

        self.__setPushButtonAvailability()

    def __setPushButtonAvailability(self):
        rankMode = self.rankModeBox.currentText()
        tier = self.tierBox.currentText()
        division = self.divisionBox.currentText()

        enable = rankMode != '' and tier != '' and division != ''
        self.pushButton.setEnabled(enable)

    @asyncSlot()
    async def __onPushButtonClicked(self):
        queue = {
            self.tr("云顶之弈"): "RANKED_TFT",
            self.tr("单双排"): "RANKED_SOLO_5x5",
            self.tr("灵活组排"): 'RANKED_FLEX_SR'
        }[self.rankModeBox.currentText()]

        tier = {
            self.tr("未定级"): 'UNRANKED',
            self.tr("黑铁"): 'IRON',
            self.tr("青铜"): 'BRONZE',
            self.tr("白银"): 'SILVER',
            self.tr("黄金"): 'GOLD',
            self.tr("铂金"): 'PLATINUM',
            self.tr("翡翠"): 'EMERALD',
            self.tr("钻石"): 'DIAMOND',
            self.tr("大师"): 'MASTER',
            self.tr("宗师"): 'GRANDMASTER',
            self.tr("王者"): 'CHALLENGER'
        }[self.tierBox.currentText()]

        currentDivision = self.divisionBox.currentText()
        division = currentDivision if currentDivision != '--' else "NA"

        await connector.setTierShowed(queue, tier, division)


class OnlineAvailabilityCard(ExpandGroupSettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.PERSONAVAILABLE, title, content, parent)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QHBoxLayout(self.inputWidget)

        self.availabilityLabel = QLabel(
            self.tr("在线状态将显示为："))
        self.comboBox = ComboBox()

        self.buttonWidget = QWidget(self.view)
        self.buttonLayout = QHBoxLayout(self.buttonWidget)
        self.pushButton = PushButton(self.tr("应用"))

        self.__initLayout()
        self.__initWidget()

    def __initWidget(self):
        self.comboBox.setMinimumWidth(130)
        self.pushButton.setMinimumWidth(100)

        self.comboBox.addItems(
            [self.tr("在线"),
             self.tr("离开"),
             self.tr("离线")])

        self.comboBox.setPlaceholderText(self.tr("可用状态"))
        self.pushButton.setEnabled(False)

        self.comboBox.currentTextChanged.connect(self.__onComboBoxTextChanged)
        self.pushButton.clicked.connect(self.__onPushButttonClicked)

    def __initLayout(self):
        self.inputLayout.setSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(
            self.availabilityLabel, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(self.comboBox, alignment=Qt.AlignRight)
        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonLayout.addWidget(self.pushButton, 0, Qt.AlignRight)
        self.buttonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.buttonWidget)

    def clear(self):
        self.comboBox.setPlaceholderText(self.tr("可用状态"))
        self.comboBox.setCurrentIndex(0)

    @asyncSlot()
    async def __onPushButttonClicked(self):
        availability = {
            self.tr("在线"): "chat",
            self.tr("离开"): "away",
            self.tr("离线"): "offline"
        }[self.comboBox.currentText()]

        await connector.setOnlineAvailability(availability)

    def __onComboBoxTextChanged(self):
        if self.comboBox.currentIndex == -1:
            return

        self.pushButton.setEnabled(True)


class RemoveTokensCard(SettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.STAROFF, title, content, parent)
        self.pushButton = PushButton(self.tr("移除"))
        self.pushButton.setMinimumWidth(100)

        self.hBoxLayout.addWidget(self.pushButton)
        self.hBoxLayout.addSpacing(16)

        self.pushButton.clicked.connect(self.__onButtonClicked)

    @asyncSlot()
    async def __onButtonClicked(self):
        await connector.removeTokens()


class RemovePrestigeCrestCard(SettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.CIRCLELINE, title, content, parent)
        self.pushButton = PushButton(self.tr("移除"))
        self.pushButton.setMinimumWidth(100)

        self.hBoxLayout.addWidget(self.pushButton)
        self.hBoxLayout.addSpacing(16)

        self.pushButton.clicked.connect(self.__onButtonClicked)

    @asyncSlot()
    async def __onButtonClicked(self):
        await connector.removePrestigeCrest()


class FixClientDpiCard(SettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.SCALEFIT, title, content, parent)
        self.pushButton = PushButton(self.tr("修复"))
        self.pushButton.setMinimumWidth(100)

        self.hBoxLayout.addWidget(self.pushButton)
        self.hBoxLayout.addSpacing(16)

        self.pushButton.clicked.connect(self.__onButtonClicked)

    @asyncSlot()
    async def __onButtonClicked(self):
        if await fixLCUWindow():
            InfoBar.success(
                title=self.tr("已修复客户端窗口"),
                content=self.tr("客户端窗口尺寸已按当前缩放重设"),
                orient=Qt.Vertical,
                isClosable=True,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000,
                parent=self.window(),
            )
            return

        self.__onFixFailed()

    def __onFixFailed(self):
        """修复失败后的分支

        唯一值得「再抢救一次」的情况是权限不够：客户端以管理员身份运行时，
        UIPI 会拦住低完整性进程发来的 SetWindowPos（错误码 5），这时只能让
        本程序也提权。已经是管理员就绝不询问 —— 否则会变成重启循环。
        """
        if win_window.lastErrorCode == win_window.ERROR_ACCESS_DENIED \
                and not win_window.isAdmin():
            self.__askRestartAsAdmin()
            return

        InfoBar.warning(
            title=self.tr("无法调整客户端窗口"),
            content=self.tr("请确认客户端没有最小化；若客户端以管理员身份运行，"
                            "本程序也需要以管理员身份启动"),
            orient=Qt.Vertical,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self.window(),
        )

    def __askRestartAsAdmin(self):
        """问一次是否提权重启，用户点了「是」才重启（UAC 取消则原地不动）"""
        box = MessageBox(
            self.tr("需要管理员权限"),
            self.tr("客户端以管理员身份运行，调整窗口尺寸需要同等权限。"
                    "是否以管理员身份重启本程序？"),
            self.window())
        box.yesButton.setText(self.tr("以管理员身份重启"))
        box.cancelButton.setText(self.tr("取消"))

        accepted = box.exec() == box.Accepted

        # 用户点了「是」才去拉 UAC；restartAsAdmin 返回 False 说明 UAC 被取消
        if accepted and win_window.restartAsAdmin(win_window.buildRelaunchArgs()):
            logger.info('restarting as administrator for client window fix', TAG)
            QApplication.instance().quit()
            return

        InfoBar.warning(
            title=self.tr("已取消"),
            content=self.tr("未获得管理员权限，客户端窗口未调整"),
            orient=Qt.Vertical,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self.window(),
        )


class RestartClientCard(SettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.ARROWREPEAT, title, content, parent)
        self.pushButton = PushButton(self.tr("重启"))
        self.pushButton.setMinimumWidth(100)

        self.hBoxLayout.addWidget(self.pushButton)
        self.hBoxLayout.addSpacing(16)

        self.pushButton.clicked.connect(self.__onButtonClicked)

    @asyncSlot()
    async def __onButtonClicked(self):
        await connector.restartClient()


class CreatePracticeLobbyCard(ExpandGroupSettingCard):

    def __init__(self, title, content, parent):
        super().__init__(Icon.TEXTEDIT, title, content, parent)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QVBoxLayout(self.inputWidget)

        self.nameLayout = QHBoxLayout()
        self.nameLabel = QLabel(self.tr("房间名（不能为空）："))
        self.nameLineEdit = LineEdit()

        self.passwordLayout = QHBoxLayout()
        self.passwordLabel = QLabel(
            self.tr("密码（留空则不设密码）"))
        self.passwordLineEdit = LineEdit()

        self.pushButtonWidget = QWidget(self.view)
        self.pushButtonLayout = QHBoxLayout(self.pushButtonWidget)

        self.pushButton = PushButton(self.tr("创建"))

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.inputLayout.setSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.nameLayout.setContentsMargins(0, 0, 0, 0)
        self.nameLayout.addWidget(self.nameLabel, alignment=Qt.AlignLeft)
        self.nameLayout.addWidget(self.nameLineEdit, alignment=Qt.AlignRight)

        self.passwordLayout.setContentsMargins(0, 0, 0, 0)
        self.passwordLayout.addWidget(
            self.passwordLabel, alignment=Qt.AlignLeft)
        self.passwordLayout.addWidget(
            self.passwordLineEdit, alignment=Qt.AlignRight)

        self.inputLayout.addLayout(self.nameLayout)
        self.inputLayout.addLayout(self.passwordLayout)
        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.pushButtonLayout.setContentsMargins(48, 18, 44, 18)
        self.pushButtonLayout.addWidget(self.pushButton, 0, Qt.AlignRight)
        self.pushButtonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.pushButtonWidget)

    def __initWidget(self):
        self.nameLineEdit.setMinimumWidth(250)
        self.nameLineEdit.setClearButtonEnabled(True)
        self.nameLineEdit.setPlaceholderText(
            self.tr("请输入房间名"))

        self.passwordLineEdit.setMinimumWidth(250)
        self.passwordLineEdit.setClearButtonEnabled(True)
        self.passwordLineEdit.setPlaceholderText(
            self.tr("请输入密码"))

        self.pushButton.setMinimumWidth(100)
        self.pushButton.setEnabled(False)

        self.nameLineEdit.textChanged.connect(self.__onNameLineEditTextChanged)
        self.pushButton.clicked.connect(self.__onPushButtonClicked)

    def clear(self):
        self.nameLineEdit.clear()
        self.passwordLineEdit.clear()

    def __onNameLineEditTextChanged(self):
        enable = self.nameLineEdit.text() != ""
        self.pushButton.setEnabled(enable)

    @asyncSlot()
    async def __onPushButtonClicked(self):
        name = self.nameLineEdit.text()
        password = self.passwordLineEdit.text()

        await connector.create5v5PracticeLobby(name, password)


class SpectateCard(ExpandGroupSettingCard):
    def __init__(self, title, content=None, parent=None):
        super().__init__(Icon.EYES, title, content, parent)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QGridLayout(self.inputWidget)

        self.summonerNameLabel = QLabel(
            self.tr("要观战的召唤师："))
        self.spectateNameComboBox = EditableComboBox()

        self.spectateTypeLabel = QLabel(self.tr("方式："))
        self.spectateTypeComboBox = ComboBox()

        self.buttonWidget = QWidget(self.view)
        self.buttonLayout = QHBoxLayout(self.buttonWidget)
        self.button = PushButton(self.tr("观战"))

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.inputLayout.setVerticalSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(
            self.summonerNameLabel, 0, 0, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(
            self.spectateNameComboBox, 0, 1, alignment=Qt.AlignRight)
        self.inputLayout.addWidget(
            self.spectateTypeLabel, 1, 0, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(
            self.spectateTypeComboBox, 1, 1, alignment=Qt.AlignRight)

        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonLayout.addWidget(self.button, 0, Qt.AlignRight)
        self.buttonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.buttonWidget)

    def __initWidget(self):
        self.spectateNameComboBox.setPlaceholderText(
            self.tr("请输入召唤师名称"))
        self.spectateNameComboBox.setMinimumWidth(250)
        self.spectateNameComboBox.setClearButtonEnabled(True)

        self.button.setMinimumWidth(100)
        self.button.setEnabled(False)

        self.spectateTypeComboBox.addItem("LCU API", userData="LCU")
        self.spectateTypeComboBox.addItem(self.tr("命令行"), userData="CMD")
        self.spectateTypeComboBox.setMinimumWidth(100)

        self.spectateNameComboBox.currentTextChanged.connect(self.__onSpectateNameTextChanged)
        self.button.clicked.connect(self.__onButtonClicked)

    def __onSpectateNameTextChanged(self):
        enable = self.spectateNameComboBox.text() != ""
        self.button.setEnabled(enable)
        # FIXME: 点输入框的x号会清除文本，应该监听该事件将按钮禁用

    def setExpand(self, isExpand: bool):
        super().setExpand(isExpand)
        if isExpand:
            asyncio.create_task(self.__initFriendList())

    async def __initFriendList(self):
        res = await connector.getFriends()
        self.spectateNameComboBox.clear()
        items = [f"{i['gameName']}#{i['gameTag']}" for i in res if i.get('availability') != 'offline']
        if len(items) == 0:
            return
        self.spectateNameComboBox.addItems(items)
        self.spectateNameComboBox.setCurrentIndex(-1)
        completer = QCompleter(items, self.spectateNameComboBox)
        self.spectateNameComboBox.setCompleter(completer)

    @asyncSlot()
    async def __onButtonClicked(self):
        def info(type, title, content):
            f = InfoBar.error if type == 'error' else InfoBar.success

            f(title=title, content=content, orient=Qt.Vertical, isClosable=True,
              position=InfoBarPosition.TOP_RIGHT, duration=5000,
              parent=self.window().auxiliaryFuncInterface)

        try:
            text = self.spectateNameComboBox.text()
            text = text.replace('\u2066', '').replace('\u2069', '')

            if self.spectateTypeComboBox.currentData() == 'LCU':
                await connector.spectate(text)
            else:
                await connector.spectateDirectly(text)

        except SummonerNotFound:
            info('error', self.tr("找不到该召唤师"),
                 self.tr("请检查召唤师名称后重试"))
        except SummonerNotInGame:
            info('error', self.tr("该召唤师不在对局中"), "")
        else:
            info('success', self.tr("已开始观战"),
                 self.tr("请稍候"), )


class AutoAcceptMatchingCard(ExpandGroupSettingCard):
    def __init__(self, title, content, enableConfigItem: ConfigItem = None,
                 delayConfigItem: ConfigItem = None, parent=None):
        super().__init__(Icon.CIRCLEMARK, title, content, parent)

        self.statusLabel = QLabel(self)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QHBoxLayout(self.inputWidget)

        self.secondsLabel = QLabel(self.tr("匹配成功后的延迟秒数："))
        self.lineEdit = SpinBox()

        self.switchButtonWidget = QWidget(self.view)
        self.switchButtonLayout = QHBoxLayout(self.switchButtonWidget)

        self.switchButton = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)

        self.enableConfigItem = enableConfigItem
        self.delayConfigItem = delayConfigItem

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.addWidget(self.statusLabel)

        self.inputLayout.setSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(self.secondsLabel, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(self.lineEdit, alignment=Qt.AlignRight)
        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.switchButtonLayout.setContentsMargins(48, 18, 44, 18)
        self.switchButtonLayout.addWidget(self.switchButton, 0, Qt.AlignRight)
        self.switchButtonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.switchButtonWidget)

    def __initWidget(self):
        self.lineEdit.setRange(0, 11)
        self.lineEdit.setValue(cfg.get(self.delayConfigItem))
        self.lineEdit.setSingleStep(1)
        self.lineEdit.setMinimumWidth(250)

        self.switchButton.setChecked(cfg.get(self.enableConfigItem))

        self.lineEdit.valueChanged.connect(self.__onLineEditValueChanged)
        self.switchButton.checkedChanged.connect(
            self.__onSwitchButtonCheckedChanged)

        value, isChecked = self.lineEdit.value(), self.switchButton.isChecked()
        self.__setStatusLableText(value, isChecked)

    def setValue(self, delay: int, isChecked: bool):
        qconfig.set(self.delayConfigItem, delay)
        qconfig.set(self.enableConfigItem, isChecked)

        self.__setStatusLableText(delay, isChecked)

    def __onSwitchButtonCheckedChanged(self, isChecked: bool):
        self.setValue(self.lineEdit.value(), isChecked)

    def __onLineEditValueChanged(self, value):
        self.setValue(value, self.switchButton.isChecked())

    def __setStatusLableText(self, delay, isChecked):
        if isChecked:
            self.statusLabel.setText(self.tr("已开启，延迟 ") + str(delay) +
                                     self.tr(" 秒"))
        else:
            self.statusLabel.setText(self.tr("已关闭"))


class AutoHonorCard(ExpandGroupSettingCard):
    """自动点赞: 开关 + 点赞类型

    点赞名单由客户端在对局结束后给出, 这里只决定"点不点"和"点哪种"。
    """

    def __init__(self, title, content, enableConfigItem: ConfigItem = None,
                 categoryConfigItem: ConfigItem = None, parent=None):
        super().__init__(Icon.TEAM, title, content, parent)

        self.statusLabel = QLabel(self)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QHBoxLayout(self.inputWidget)

        self.categoryLabel = QLabel(self.tr("点赞类型："))
        self.comboBox = ComboBox()

        self.switchButtonWidget = QWidget(self.view)
        self.switchButtonLayout = QHBoxLayout(self.switchButtonWidget)

        self.switchButton = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)

        self.enableConfigItem = enableConfigItem
        self.categoryConfigItem = categoryConfigItem

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.addWidget(self.statusLabel)

        self.inputLayout.setSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(self.categoryLabel, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(self.comboBox, alignment=Qt.AlignRight)
        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.switchButtonLayout.setContentsMargins(48, 18, 44, 18)
        self.switchButtonLayout.addWidget(self.switchButton, 0, Qt.AlignRight)
        self.switchButtonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.switchButtonWidget)

    def __initWidget(self):
        self.comboBox.addItems(['随机', '心态良好', '指挥有方', '操作亮眼'])
        self.comboBox.setMinimumWidth(250)

        current = cfg.get(self.categoryConfigItem)
        if current in ['随机', '心态良好', '指挥有方', '操作亮眼']:
            self.comboBox.setCurrentText(current)

        self.switchButton.setChecked(cfg.get(self.enableConfigItem))

        self.comboBox.currentTextChanged.connect(self.__onCategoryChanged)
        self.switchButton.checkedChanged.connect(
            self.__onSwitchButtonCheckedChanged)

        self.__setStatusLableText()

    def __onCategoryChanged(self, text):
        cfg.set(self.categoryConfigItem, text)
        self.__setStatusLableText()

    def __onSwitchButtonCheckedChanged(self, isChecked: bool):
        cfg.set(self.enableConfigItem, isChecked)
        self.__setStatusLableText()

    def __setStatusLableText(self):
        if self.switchButton.isChecked():
            self.statusLabel.setText(
                self.tr("已开启，类型：") + self.comboBox.currentText())
        else:
            self.statusLabel.setText(self.tr("已关闭"))


class AutoPlayAgainCard(ExpandGroupSettingCard):
    """自动回房间: 开关 + 结算后延迟

    延迟的意义是留出看战绩的时间; 0 秒表示结算页一出现就回。
    """

    def __init__(self, title, content, enableConfigItem: ConfigItem = None,
                 delayConfigItem: ConfigItem = None, parent=None):
        super().__init__(Icon.ARROWREPEAT, title, content, parent)

        self.statusLabel = QLabel(self)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QHBoxLayout(self.inputWidget)

        self.secondsLabel = QLabel(self.tr("对局结束后的延迟秒数："))
        self.lineEdit = SpinBox()

        self.switchButtonWidget = QWidget(self.view)
        self.switchButtonLayout = QHBoxLayout(self.switchButtonWidget)

        self.switchButton = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)

        self.enableConfigItem = enableConfigItem
        self.delayConfigItem = delayConfigItem

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.addWidget(self.statusLabel)

        self.inputLayout.setSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(self.secondsLabel, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(self.lineEdit, alignment=Qt.AlignRight)
        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.switchButtonLayout.setContentsMargins(48, 18, 44, 18)
        self.switchButtonLayout.addWidget(self.switchButton, 0, Qt.AlignRight)
        self.switchButtonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.switchButtonWidget)

    def __initWidget(self):
        self.lineEdit.setRange(0, 30)
        self.lineEdit.setValue(cfg.get(self.delayConfigItem))
        self.lineEdit.setSingleStep(1)
        self.lineEdit.setMinimumWidth(250)

        self.switchButton.setChecked(cfg.get(self.enableConfigItem))

        self.lineEdit.valueChanged.connect(self.__onLineEditValueChanged)
        self.switchButton.checkedChanged.connect(
            self.__onSwitchButtonCheckedChanged)

        self.__setStatusLableText()

    def __onLineEditValueChanged(self, value):
        cfg.set(self.delayConfigItem, value)
        self.__setStatusLableText()

    def __onSwitchButtonCheckedChanged(self, isChecked: bool):
        cfg.set(self.enableConfigItem, isChecked)
        self.__setStatusLableText()

    def __setStatusLableText(self):
        if self.switchButton.isChecked():
            self.statusLabel.setText(
                self.tr("已开启，延迟 ") + str(self.lineEdit.value()) +
                self.tr(" 秒"))
        else:
            self.statusLabel.setText(self.tr("已关闭"))


class AutoAcceptSwapingCard(ExpandGroupSettingCard):
    def __init__(self, title, content, enableCeilSwapItem: ConfigItem = None,
                 enableChampSwapItem: ConfigItem = None, parent=None):
        super().__init__(Icon.TEXTCHECK, title, content, parent)

        self.statusLabel = QLabel(self)

        self.switchButtonWidget = QWidget(self.view)
        self.switchButtonLayout = QGridLayout(self.switchButtonWidget)

        self.label1 = QLabel(self.tr("自动接受交换请求："))
        self.label2 = QLabel(
            self.tr("自动接受英雄交换请求："))

        self.switchButton1 = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
        self.switchButton2 = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)

        self.enableCeilSwapItem = enableCeilSwapItem
        self.enableChampSwapItem = enableChampSwapItem

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.addWidget(self.statusLabel)

        self.switchButtonLayout.setVerticalSpacing(19)
        self.switchButtonLayout.addWidget(self.label1, 0, 0, Qt.AlignLeft)
        self.switchButtonLayout.addWidget(
            self.switchButton1, 0, 1, Qt.AlignRight)
        self.switchButtonLayout.addWidget(
            self.label2, 1, 0, Qt.AlignLeft)
        self.switchButtonLayout.addWidget(
            self.switchButton2, 1, 1, Qt.AlignRight)

        self.switchButtonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)
        self.switchButtonLayout.setContentsMargins(48, 24, 44, 28)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.switchButtonWidget)

    def __initWidget(self):
        ceilSwap = cfg.get(cfg.autoAcceptCeilSwap)
        champTrade = cfg.get(cfg.autoAcceptChampTrade)

        self.switchButton1.setChecked(ceilSwap)
        self.switchButton2.setChecked(champTrade)

        self.__setStatusLableText()

        self.switchButton1.checkedChanged.connect(
            self.__onSwichButton1CheckedChanged)
        self.switchButton2.checkedChanged.connect(
            self.__onSwichButton2CheckedChanged)

    def __onSwichButton1CheckedChanged(self, isChecked: bool):
        cfg.set(cfg.autoAcceptCeilSwap, isChecked)
        self.__setStatusLableText()

    def __onSwichButton2CheckedChanged(self, isChecked: bool):
        cfg.set(cfg.autoAcceptChampTrade, isChecked)
        self.__setStatusLableText()

    def __setStatusLableText(self):
        ceilSwap = self.switchButton1.isChecked()
        champTrade = self.switchButton2.isChecked()

        if any([ceilSwap, champTrade]):
            self.statusLabel.setText(self.tr("已开启"))
        else:
            self.statusLabel.setText(self.tr("已关闭"))


class DodgeCard(SettingCard):
    def __init__(self, title, content, parent):
        super().__init__(Icon.EXIT, title, content, parent)
        self.pushButton = PushButton(self.tr("秒退"))
        self.pushButton.setMinimumWidth(100)
        self.pushButton.setEnabled(False)

        self.hBoxLayout.addWidget(self.pushButton)
        self.hBoxLayout.addSpacing(16)

        self.pushButton.clicked.connect(lambda: threading.Thread(
            target=lambda: connector.dodge()).start())


class LockConfigCard(SettingCard):
    def __init__(self, title, content, parent):
        super().__init__(Icon.LOCK, title, content, parent)

        self.switchButton = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)

        self.hBoxLayout.addWidget(self.switchButton)
        self.hBoxLayout.addSpacing(16)

        self.switchButton.checkedChanged.connect(self.__onCheckedChanged)

    def __getSettingsPath(self):
        """PersistedSettings.json 的绝对路径

        没选客户端目录（首次使用）时返回 None —— 以前这里直接
        `cfg.get(cfg.lolFolder)[0]`，空列表就是一条 IndexError。
        """
        folders = cfg.get(cfg.lolFolder) or []

        # 老配置里 lolFolder 可能是裸字符串（主窗口启动时才归一成列表，
        # 这张卡有可能读得更早），字符串要整体当成一个路径，而不是取第一个字符
        if isinstance(folders, str):
            folders = [folders]

        folder = folders[0] if folders else ''

        # 空列表 / [''] / '' 都算「还没选客户端目录」
        if not folder:
            return None

        return f"{folder}/../Game/Config/PersistedSettings.json"

    def loadNowMode(self):
        path = self.__getSettingsPath()

        if not path or not os.path.exists(path):
            self.switchButton.setChecked(False)
            self.switchButton.setEnabled(False)

            return

        try:
            currentMode = stat.S_IMODE(os.lstat(path).st_mode)
            if currentMode == 0o444:
                self.switchButton.setChecked(True)
        except:
            self.switchButton.setEnabled(False)
            pass

    def __onCheckedChanged(self, isChecked: bool):
        if not self.setConfigFileReadOnlyEnabled(isChecked):
            InfoBar.error(
                title=self.tr("错误"),
                content=self.tr("设置文件权限失败"),
                orient=Qt.Vertical,
                isClosable=True,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000,
                parent=self.window(),
            )

            self.switchButton.checkedChanged.disconnect()
            self.switchButton.setChecked(not isChecked)
            self.switchButton.checkedChanged.connect(self.__onCheckedChanged)

    def setConfigFileReadOnlyEnabled(self, enable):
        path = self.__getSettingsPath()

        if not path:
            logger.warning(
                'lolFolder is not configured, skip locking config file', TAG)
            return False

        if not os.path.exists(path):
            return False

        mode = 0o444 if enable else 0o666
        try:
            os.chmod(path, mode)
            currentMode = stat.S_IMODE(os.lstat(path).st_mode)

            if currentMode != mode:
                return False
        except:
            self.switchButton.setEnabled(False)
            return False

        return True


class FriendRequestCard(ExpandGroupSettingCard):
    def __init__(self, title, content=None, parent=None):
        super().__init__(Icon.EYES, title, content, parent)

        self.inputWidget = QWidget(self.view)
        self.inputLayout = QHBoxLayout(self.inputWidget)

        self.summonerNameLabel = QLabel(
            self.tr("要添加好友的召唤师："))
        self.lineEdit = LineEdit()

        self.buttonWidget = QWidget(self.view)
        self.buttonLayout = QHBoxLayout(self.buttonWidget)
        self.button = PushButton(self.tr("发送"))

        self.__initLayout()
        self.__initWidget()

    def __initLayout(self):
        self.inputLayout.setSpacing(19)
        self.inputLayout.setAlignment(Qt.AlignTop)
        self.inputLayout.setContentsMargins(48, 18, 44, 18)

        self.inputLayout.addWidget(
            self.summonerNameLabel, alignment=Qt.AlignLeft)
        self.inputLayout.addWidget(self.lineEdit, alignment=Qt.AlignRight)
        self.inputLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonLayout.addWidget(self.button, 0, Qt.AlignRight)
        self.buttonLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.addGroupWidget(self.inputWidget)
        self.addGroupWidget(self.buttonWidget)

    def __initWidget(self):
        self.lineEdit.setPlaceholderText(
            self.tr("请输入召唤师名称"))
        self.lineEdit.setMinimumWidth(250)
        self.lineEdit.setClearButtonEnabled(True)

        self.button.setMinimumWidth(100)
        self.button.setEnabled(False)

        self.lineEdit.textChanged.connect(self.__onLineEditTextChanged)
        self.button.clicked.connect(self.__onButtonClicked)

    def __onLineEditTextChanged(self):
        enable = self.lineEdit.text() != ""
        self.button.setEnabled(enable)

    @asyncSlot()
    async def __onButtonClicked(self):
        def info(type, title, content=None):
            f = InfoBar.error if type == 'error' else InfoBar.success

            f(title=title, content=content, orient=Qt.Vertical, isClosable=True,
              position=InfoBarPosition.TOP_RIGHT, duration=5000,
              parent=self.window().auxiliaryFuncInterface)

        try:
            await connector.sendFriendRequest(self.lineEdit.text())
        except:
            info('error', self.tr("找不到该召唤师"),
                 self.tr("请检查召唤师名称后重试"))
        else:
            info('success', self.tr("好友请求已发送"))


class AutoSelectChampionCard(ExpandGroupSettingCard):
    def __init__(self, title, content=None,
                 enableConfigItem: ConfigItem = None,
                 championsConfigItem: ConfigItem = None,
                 topChampionsConfigItem: ConfigItem = None,
                 jugChampionsConfigItem: ConfigItem = None,
                 midChampionsConfigItem: ConfigItem = None,
                 botChampionsConfigItem: ConfigItem = None,
                 supChampionsConfigItem: ConfigItem = None,
                 enableTimeoutCompleteCfgItem: ConfigItem = None,
                 parent=None):
        super().__init__(Icon.CHECK, title, content, parent)

        self.champions = {}

        self.enableConfigItem = enableConfigItem
        self.defaultChampionsConfigItem = championsConfigItem
        self.topChampionsConfigItem = topChampionsConfigItem
        self.jugChampionsConfigItem = jugChampionsConfigItem
        self.midChampionsConfigItem = midChampionsConfigItem
        self.botChampionsConfigItem = botChampionsConfigItem
        self.supChampionsConfigItem = supChampionsConfigItem
        self.enableTimeoutCompleteCfgItem = enableTimeoutCompleteCfgItem

        self.statusLabel = QLabel()

        self.defaultCfgWidget = QWidget(self.view)
        self.defaultCfgLayout = QGridLayout(self.defaultCfgWidget)
        self.defaultHintLabel = QLabel(self.tr("默认配置"))
        self.helpLayout = QHBoxLayout()
        self.helpButotn = TransparentToolButton(Icon.QUESTION_CIRCLE)

        self.defaultLabel = QLabel(self.tr("默认英雄："))
        self.defaultChampions = ChampionsCard()
        self.defaultSelectButton = PushButton(self.tr("选择"))

        self.rankCfgWidget = QWidget(self.view)
        self.rankCfgLayout = QGridLayout(self.rankCfgWidget)
        self.rankLabel = QLabel(self.tr("分路配置"))

        self.topLabel = QLabel(self.tr("上单："))
        self.jugLabel = QLabel(self.tr("打野："))
        self.midLabel = QLabel(self.tr("中单："))
        self.botLabel = QLabel(self.tr("下路："))
        self.supLabel = QLabel(self.tr("辅助："))
        self.topChampions = ChampionsCard()
        self.jugChampions = ChampionsCard()
        self.midChampions = ChampionsCard()
        self.botChampions = ChampionsCard()
        self.supChampions = ChampionsCard()
        self.topSelectButton = PushButton(self.tr("选择"))
        self.jugSelectButton = PushButton(self.tr("选择"))
        self.midSelectButton = PushButton(self.tr("选择"))
        self.botSelectButton = PushButton(self.tr("选择"))
        self.supSelectButton = PushButton(self.tr("选择"))

        self.buttonsWidget = QWidget(self.view)
        self.buttonsLayout = QGridLayout(self.buttonsWidget)
        self.enableLabel = QLabel(self.tr("启用："))
        self.enableSwitchButton = SwitchButton(
            indicatorPos=IndicatorPosition.RIGHT)
        self.enableTimeoutCompleteLabel = QLabel(
            self.tr("超时前完成："))
        self.enableTimeoutSwtichButton = SwitchButton(
            indicatorPos=IndicatorPosition.RIGHT)
        self.resetButton = PushButton(self.tr("重置"))

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.defaultHintLabel.setStyleSheet("font: bold")
        self.rankLabel.setStyleSheet("font: bold")

        self.helpButotn.setFixedSize(QSize(26, 26))
        self.helpButotn.setIconSize(QSize(16, 16))

        self.helpButotn.setToolTip(self.tr("需要先设置默认英雄。\n\n按分路设置的英雄不可用时，会改用默认设置。"))
        self.helpButotn.installEventFilter(ToolTipFilter(
            self.helpButotn, 0, ToolTipPosition.RIGHT))

        # 逻辑是，必须要设置默认，才能设置具体分路和启动功能
        selected = qconfig.get(self.defaultChampionsConfigItem) != []
        checked = qconfig.get(self.enableConfigItem)
        timeoutChecked = qconfig.get(self.enableTimeoutCompleteCfgItem)

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            button: PushButton = getattr(self, f"{ty}SelectButton")
            button.setMinimumWidth(100)
            button.clicked.connect(lambda _, t=ty: self.__onButtonClicked(t))

            if ty != 'default':
                button.setEnabled(selected)

        self.enableSwitchButton.checkedChanged.connect(
            self.__onEnableSelectChanged)
        self.enableSwitchButton.setEnabled(selected)
        self.enableSwitchButton.setChecked(checked)

        self.enableTimeoutSwtichButton.checkedChanged.connect(
            self.__onEnableTimeoutCompleteChanged)
        self.enableTimeoutSwtichButton.setEnabled(checked)
        self.enableTimeoutSwtichButton.setChecked(timeoutChecked)

        self.resetButton.clicked.connect(self.__onResetButtonClicked)
        self.resetButton.setMinimumWidth(100)

        self.__updateStatusLabel()

    def __initLayout(self):
        self.addWidget(self.statusLabel)

        self.defaultCfgLayout.setVerticalSpacing(19)
        self.defaultCfgLayout.setContentsMargins(48, 18, 44, 18)
        self.defaultCfgLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.helpLayout.setContentsMargins(0, 0, 0, 0)
        self.helpLayout.setSpacing(10)
        self.helpLayout.addWidget(self.defaultHintLabel)
        self.helpLayout.addWidget(self.helpButotn)

        self.defaultCfgLayout.addLayout(
            self.helpLayout, 0, 0, Qt.AlignLeft)

        self.defaultCfgLayout.addWidget(
            self.defaultLabel, 1, 0, Qt.AlignLeft)
        self.defaultCfgLayout.addWidget(
            self.defaultChampions, 1, 1, Qt.AlignHCenter)
        self.defaultCfgLayout.addWidget(
            self.defaultSelectButton, 1, 2, Qt.AlignRight)

        self.rankCfgLayout.setVerticalSpacing(19)
        self.rankCfgLayout.setContentsMargins(48, 18, 44, 18)
        self.rankCfgLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.rankCfgLayout.addWidget(self.rankLabel, 0, 0, Qt.AlignLeft)

        for i, ty in enumerate(['top', 'jug', 'mid', 'bot', 'sup']):
            label = getattr(self, f"{ty}Label")
            champions = getattr(self, f"{ty}Champions")
            button = getattr(self, f"{ty}SelectButton")

            self.rankCfgLayout.addWidget(label, i + 1, 0, Qt.AlignLeft)
            self.rankCfgLayout.addWidget(champions, i + 1, 1, Qt.AlignHCenter)
            self.rankCfgLayout.addWidget(button, i + 1, 2, Qt.AlignRight)

        self.buttonsLayout.setVerticalSpacing(19)
        self.buttonsLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonsLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonsLayout.addWidget(
            self.enableLabel, 0, 0, Qt.AlignLeft)
        self.buttonsLayout.addWidget(
            self.enableSwitchButton, 0, 1, Qt.AlignRight)
        self.buttonsLayout.addWidget(
            self.enableTimeoutCompleteLabel, 1, 0, Qt.AlignLeft)
        self.buttonsLayout.addWidget(
            self.enableTimeoutSwtichButton, 1, 1, Qt.AlignRight)
        self.buttonsLayout.addWidget(
            self.resetButton, 2, 1, Qt.AlignRight)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)

        self.addGroupWidget(self.defaultCfgWidget)
        self.addGroupWidget(self.rankCfgWidget)
        self.addGroupWidget(self.buttonsWidget)

    async def initChampionList(self, champions: dict = None):
        if champions:
            self.champions = champions
        else:
            self.champions = {
                i: [name, await connector.getChampionIcon(i)]
                for i, name in connector.manager.getChampions().items()
                if i != -1
            }

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            configItem = getattr(self, f"{ty}ChampionsConfigItem")
            champions: ChampionsCard = getattr(self, f"{ty}Champions")
            selected = qconfig.get(configItem)

            champions.clearRequested.connect(
                lambda t=ty: self.__onChampionsChanged([], t))

            if not (type(selected) is list and all(type(s) is int for s in selected)):
                selected = []
                qconfig.set(configItem, selected)

            if len(selected) == 0:
                continue

            champions.updateChampions(
                [self.champions[id][1] for id in selected])

        return self.champions

    def __onButtonClicked(self, type: str):
        configItem: ConfigItem = getattr(self, f"{type}ChampionsConfigItem")
        selected = qconfig.get(configItem)

        box = MultiChampionSelectMsgBox(
            self.champions, selected, self.window())
        box.completed.connect(
            lambda champions, t=type: self.__onChampionsChanged(champions, t))
        box.exec()

    def __onChampionsChanged(self, champions: list, type: str):
        configItem = getattr(self, f"{type}ChampionsConfigItem")
        qconfig.set(configItem, champions)

        card: ChampionsCard = getattr(self, f"{type}Champions")
        card.updateChampions(
            [self.champions[id][1] for id in champions])

        if type != 'default':
            return

        if len(champions) == 0:
            self.enableSwitchButton.setChecked(False)
            self.enableSwitchButton.setEnabled(False)
            self.enableTimeoutSwtichButton.setChecked(False)
            self.enableTimeoutSwtichButton.setEnabled(False)
            buttonEnable = False
        else:
            self.enableSwitchButton.setEnabled(True)
            buttonEnable = True

        for ty in ['top', 'jug', 'mid', 'bot', 'sup']:
            button: PushButton = getattr(self, f"{ty}SelectButton")
            button.setEnabled(buttonEnable)

    def __onEnableSelectChanged(self, checked):
        qconfig.set(self.enableConfigItem, checked)

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            button: PushButton = getattr(self, f"{ty}SelectButton")
            button.setEnabled(not checked)

        self.enableTimeoutSwtichButton.setEnabled(checked)

        if not checked:
            self.enableTimeoutSwtichButton.setChecked(False)

        self.__updateStatusLabel()

    def __onEnableTimeoutCompleteChanged(self, checked):
        qconfig.set(self.enableTimeoutCompleteCfgItem, checked)

    def __updateStatusLabel(self):
        checked = self.enableSwitchButton.isChecked()

        text = self.tr("已开启") if checked else self.tr("已关闭")
        self.statusLabel.setText(text)

    def __onResetButtonClicked(self):
        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            self.__onChampionsChanged([], ty)


class AutoBanChampionCard(ExpandGroupSettingCard):
    def __init__(self, title, content=None,
                 enableConfigItem: ConfigItem = None,
                 championsConfigItem: ConfigItem = None,
                 topChampionsConfigItem: ConfigItem = None,
                 jugChampionsConfigItem: ConfigItem = None,
                 midChampionsConfigItem: ConfigItem = None,
                 botChampionsConfigItem: ConfigItem = None,
                 supChampionsConfigItem: ConfigItem = None,
                 friendlyConfigItem: ConfigItem = None,
                 delayTimeConfigItem: ConfigItem = None, parent=None):
        super().__init__(Icon.SQUARECROSS, title, content, parent)

        self.champions = {}

        self.enableConfigItem = enableConfigItem
        self.defaultChampionsConfigItem = championsConfigItem
        self.topChampionsConfigItem = topChampionsConfigItem
        self.jugChampionsConfigItem = jugChampionsConfigItem
        self.midChampionsConfigItem = midChampionsConfigItem
        self.botChampionsConfigItem = botChampionsConfigItem
        self.supChampionsConfigItem = supChampionsConfigItem

        self.friendlyConfigItem = friendlyConfigItem
        self.delayTimeConfigItem = delayTimeConfigItem

        self.statusLabel = QLabel()

        self.defaultCfgWidget = QWidget(self.view)
        self.defaultCfgLayout = QGridLayout(self.defaultCfgWidget)
        self.defaultHintLabel = QLabel(self.tr("默认配置"))
        self.helpLayout = QHBoxLayout()
        self.helpButotn = TransparentToolButton(Icon.QUESTION_CIRCLE)

        self.defaultLabel = QLabel(self.tr("默认英雄："))
        self.defaultChampions = ChampionsCard()
        self.defaultSelectButton = PushButton(self.tr("选择"))

        self.rankCfgWidget = QWidget(self.view)
        self.rankCfgLayout = QGridLayout(self.rankCfgWidget)
        self.rankLabel = QLabel(self.tr("分路配置"))

        self.topLabel = QLabel(self.tr("上单："))
        self.jugLabel = QLabel(self.tr("打野："))
        self.midLabel = QLabel(self.tr("中单："))
        self.botLabel = QLabel(self.tr("下路："))
        self.supLabel = QLabel(self.tr("辅助："))
        self.topChampions = ChampionsCard()
        self.jugChampions = ChampionsCard()
        self.midChampions = ChampionsCard()
        self.botChampions = ChampionsCard()
        self.supChampions = ChampionsCard()
        self.topSelectButton = PushButton(self.tr("选择"))
        self.jugSelectButton = PushButton(self.tr("选择"))
        self.midSelectButton = PushButton(self.tr("选择"))
        self.botSelectButton = PushButton(self.tr("选择"))
        self.supSelectButton = PushButton(self.tr("选择"))

        self.buttonsCfgWidget = QWidget(self.view)
        self.buttonsCfgLayout = QGridLayout(self.buttonsCfgWidget)
        self.delayLabel = QLabel(self.tr("禁人延迟秒数："))
        self.delaySpinBox = SpinBox()
        self.enableLabel = QLabel(self.tr("启用："))
        self.enableSwitchButton = SwitchButton(
            indicatorPos=IndicatorPosition.RIGHT)
        self.friendlyLabel = QLabel(
            self.tr("不禁队友已选英雄："))
        self.friendlySwitchButton = SwitchButton(
            indicatorPos=IndicatorPosition.RIGHT)

        self.resetButton = PushButton(self.tr("重置"))

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.defaultHintLabel.setStyleSheet("font: bold")
        self.rankLabel.setStyleSheet("font: bold")

        haveDefault = qconfig.get(self.defaultChampionsConfigItem) != []
        enabled = qconfig.get(self.enableConfigItem)
        delayTime = qconfig.get(self.delayTimeConfigItem)
        friendlyEnabled = qconfig.get(self.friendlyConfigItem)

        self.helpButotn.setFixedSize(QSize(26, 26))
        self.helpButotn.setIconSize(QSize(16, 16))

        self.helpButotn.setToolTip(self.tr("需要先设置默认英雄。\n\n按分路设置的英雄不可用时，会改用默认设置。"))
        self.helpButotn.installEventFilter(ToolTipFilter(
            self.helpButotn, 0, ToolTipPosition.RIGHT))

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            button: PushButton = getattr(self, f"{ty}SelectButton")
            button.setMinimumWidth(100)
            button.clicked.connect(lambda _, t=ty: self.__onButtonClicked(t))

            if ty != 'default':
                button.setEnabled(haveDefault)

        self.enableSwitchButton.checkedChanged.connect(
            self.__onEnableSwitchButtonClicked)
        self.delaySpinBox.valueChanged.connect(
            self.__onDelaySpinBoxValueChanged)
        self.friendlySwitchButton.checkedChanged.connect(
            self.__onFriendlySwitchButtonClicked)
        self.resetButton.clicked.connect(self.__onResetButtonClicked)

        self.delaySpinBox.setMinimumWidth(250)
        self.delaySpinBox.setSingleStep(1)
        self.delaySpinBox.setRange(0, 25)
        self.delaySpinBox.setEnabled(haveDefault and not enabled)
        self.delaySpinBox.setValue(delayTime)
        self.enableSwitchButton.setEnabled(haveDefault)
        self.enableSwitchButton.setChecked(enabled)
        self.friendlySwitchButton.setEnabled(enabled)
        self.friendlySwitchButton.setChecked(friendlyEnabled)
        self.resetButton.setMinimumWidth(100)

        self.__updateStatusLabel()
        self.__fixStyleSheetOfSpinBox()

    def __initLayout(self):
        self.addWidget(self.statusLabel)

        self.defaultCfgLayout.setVerticalSpacing(19)
        self.defaultCfgLayout.setContentsMargins(48, 18, 44, 18)
        self.defaultCfgLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.helpLayout.setContentsMargins(0, 0, 0, 0)
        self.helpLayout.setSpacing(10)
        self.helpLayout.addWidget(self.defaultHintLabel)
        self.helpLayout.addWidget(self.helpButotn)

        self.defaultCfgLayout.addLayout(
            self.helpLayout, 0, 0, Qt.AlignLeft)

        self.defaultCfgLayout.addWidget(
            self.defaultLabel, 1, 0, Qt.AlignLeft)
        self.defaultCfgLayout.addWidget(
            self.defaultChampions, 1, 1, Qt.AlignHCenter)
        self.defaultCfgLayout.addWidget(
            self.defaultSelectButton, 1, 2, Qt.AlignRight)

        self.rankCfgLayout.setVerticalSpacing(19)
        self.rankCfgLayout.setContentsMargins(48, 18, 44, 18)
        self.rankCfgLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.rankCfgLayout.addWidget(self.rankLabel, 0, 0, Qt.AlignLeft)

        for i, ty in enumerate(['top', 'jug', 'mid', 'bot', 'sup']):
            label = getattr(self, f"{ty}Label")
            champions = getattr(self, f"{ty}Champions")
            button = getattr(self, f"{ty}SelectButton")

            self.rankCfgLayout.addWidget(label, i + 1, 0, Qt.AlignLeft)
            self.rankCfgLayout.addWidget(champions, i + 1, 1, Qt.AlignHCenter)
            self.rankCfgLayout.addWidget(button, i + 1, 2, Qt.AlignRight)

        self.buttonsCfgLayout.setVerticalSpacing(19)
        self.buttonsCfgLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonsCfgLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonsCfgLayout.addWidget(
            self.delayLabel, 0, 0, Qt.AlignLeft)
        self.buttonsCfgLayout.addWidget(
            self.delaySpinBox, 0, 1, Qt.AlignRight)
        self.buttonsCfgLayout.addWidget(
            self.enableLabel, 1, 0, Qt.AlignLeft)
        self.buttonsCfgLayout.addWidget(
            self.enableSwitchButton, 1, 1, Qt.AlignRight)
        self.buttonsCfgLayout.addWidget(
            self.friendlyLabel, 2, 0, Qt.AlignLeft)
        self.buttonsCfgLayout.addWidget(
            self.friendlySwitchButton, 2, 1, Qt.AlignRight)
        self.buttonsCfgLayout.addWidget(
            self.resetButton, 3, 1, Qt.AlignRight)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)

        self.addGroupWidget(self.defaultCfgWidget)
        self.addGroupWidget(self.rankCfgWidget)
        self.addGroupWidget(self.buttonsCfgWidget)

    async def initChampionList(self, champions: dict = None):
        if champions:
            self.champions = champions
        else:
            self.champions = {
                i: [name, await connector.getChampionIcon(i)]
                for i, name in connector.manager.getChampions().items()
                if i != -1
            }

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            configItem = getattr(self, f"{ty}ChampionsConfigItem")
            champions: ChampionsCard = getattr(self, f"{ty}Champions")
            selected = qconfig.get(configItem)

            champions.clearRequested.connect(
                lambda t=ty: self.__onChampionsChanged([], t))

            # 原来的配置项里储存字符串，使用 ',' 分隔
            # 现在储存的是 list 类型，其中是 championId
            # 为了兼容老版本的配置文件，这里手动对配置文件进行一下验证 / 重置
            if not (type(selected) is list and all(type(s) is int for s in selected)):
                selected = []
                qconfig.set(configItem, selected)

            if len(selected) == 0:
                continue

            champions.updateChampions(
                [self.champions[id][1] for id in selected])

        return self.champions

    def __onButtonClicked(self, type: str):
        configItem: ConfigItem = getattr(self, f"{type}ChampionsConfigItem")
        selected = qconfig.get(configItem)

        box = MultiChampionSelectMsgBox(
            self.champions, selected, self.window())
        box.completed.connect(
            lambda champions, t=type: self.__onChampionsChanged(champions, t))
        box.exec()

    def __onChampionsChanged(self, champions: list, type: str):
        configItem = getattr(self, f"{type}ChampionsConfigItem")
        qconfig.set(configItem, champions)

        card: ChampionsCard = getattr(self, f"{type}Champions")
        card.updateChampions(
            [self.champions[id][1] for id in champions])

        if type != 'default':
            return

        if len(champions) == 0:
            self.enableSwitchButton.setChecked(False)
            self.enableSwitchButton.setEnabled(False)
            self.friendlySwitchButton.setChecked(False)
            self.friendlySwitchButton.setEnabled(False)
            self.delaySpinBox.setEnabled(False)
            buttonEnable = False
        else:
            self.enableSwitchButton.setEnabled(True)
            self.delaySpinBox.setEnabled(True)
            buttonEnable = True

        for ty in ['top', 'jug', 'mid', 'bot', 'sup']:
            button: PushButton = getattr(self, f"{ty}SelectButton")
            button.setEnabled(buttonEnable)

    def __onEnableSwitchButtonClicked(self, checked):
        qconfig.set(self.enableConfigItem, checked)

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            button: PushButton = getattr(self, f"{ty}SelectButton")
            button.setEnabled(not checked)

        self.friendlySwitchButton.setEnabled(checked)
        self.delaySpinBox.setEnabled(not checked)

        if not checked:
            self.friendlySwitchButton.setChecked(False)

        self.__updateStatusLabel()

    def __onDelaySpinBoxValueChanged(self, value):
        qconfig.set(self.delayTimeConfigItem, value)

    def __onFriendlySwitchButtonClicked(self, checked):
        qconfig.set(self.friendlyConfigItem, checked)

    def __onResetButtonClicked(self):
        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            self.__onChampionsChanged([], ty)

        self.delaySpinBox.setValue(0)

    def __updateStatusLabel(self):
        checked = self.enableSwitchButton.isChecked()

        text = self.tr("已开启") if checked else self.tr("已关闭")
        self.statusLabel.setText(text)

    def __fixStyleSheetOfSpinBox(self):
        # 这玩意在深色 + Enabled 为 False 的时候看起来怪怪的，手动改一下
        light = """
            SpinBox:disabled {
                color: rgba(0, 0, 0, 150);
                background-color: rgba(249, 249, 249, 0.3);
                border: 1px solid rgba(0, 0, 0, 13);
                border-bottom: 1px solid rgba(0, 0, 0, 13);
            }
        """

        dark = """
            SpinBox:disabled {    
                color: rgba(255, 255, 255, 150);
                background-color: rgba(255, 255, 255, 0.0419);
                border: 1px solid rgba(255, 255, 255, 0.0698);
            }
        """

        setCustomStyleSheet(self.delaySpinBox, light, dark)


class ChampionsCard(QFrame):
    clearRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(2, 0, 4, 0)
        self.hBoxLayout.setAlignment(Qt.AlignCenter)

        self.iconLayout = QHBoxLayout()
        self.iconLayout.setContentsMargins(6, 6, 0, 6)
        self.clearButton = TransparentToolButton(FluentIcon.CLOSE)
        self.clearButton.setFixedSize(28, 28)
        self.clearButton.setIconSize(QSize(15, 15))
        self.clearButton.setVisible(False)
        self.clearButton.clicked.connect(self.clearRequested)

        self.hBoxLayout.addLayout(self.iconLayout)
        self.hBoxLayout.addItem(QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed))
        self.hBoxLayout.addWidget(self.clearButton, alignment=Qt.AlignVCenter)

        self.setFixedWidth(250)
        self.setFixedHeight(42)

    def updateChampions(self, champions):
        self.clear()

        for icon in champions:
            icon = RoundIcon(icon, 28, 2, 2)
            self.iconLayout.addWidget(icon, alignment=Qt.AlignVCenter)

    def clear(self):
        for i in reversed(range(self.iconLayout.count())):
            item = self.iconLayout.itemAt(i)
            self.iconLayout.removeItem(item)

            if item.widget():
                item.widget().deleteLater()

    def enterEvent(self, a0: QEvent) -> None:
        self.clearButton.setVisible(True)
        return super().enterEvent(a0)

    def leaveEvent(self, a0: QEvent) -> None:
        self.clearButton.setVisible(False)
        return super().leaveEvent(a0)


class AutoSetSummonerSpellCard(ExpandGroupSettingCard):
    def __init__(self, title, content=None,
                 enableConfigItem: ConfigItem = None,
                 spellConfigItem: ConfigItem = None,
                 topSpellConfigItem: ConfigItem = None,
                 jugSpellConfigItem: ConfigItem = None,
                 midSpellConfigItem: ConfigItem = None,
                 botSpellConfigItem: ConfigItem = None,
                 supSpellConfigItem: ConfigItem = None,
                 parent=None):
        super().__init__(Icon.CHECKBOXFILL, title, content, parent)

        self.spells = {}

        self.enableConfigItem = enableConfigItem
        self.defaultSpellConfigItem = spellConfigItem
        self.topSpellConfigItem = topSpellConfigItem
        self.jugSpellConfigItem = jugSpellConfigItem
        self.midSpellConfigItem = midSpellConfigItem
        self.botSpellConfigItem = botSpellConfigItem
        self.supSpellConfigItem = supSpellConfigItem

        self.statusLabel = QLabel()

        self.defaultCfgWidget = QWidget(self.view)
        self.defaultCfgLayout = QGridLayout(self.defaultCfgWidget)
        self.defaultHintLabel = QLabel(self.tr("默认配置"))

        self.defaultLabel = QLabel(self.tr("默认召唤师技能："))
        self.defaultButtonLayout = QHBoxLayout()
        self.defaultSelectButton1 = SummonerSpellButton()
        self.defaultSelectButton2 = SummonerSpellButton()

        self.rankCfgWidget = QWidget(self.view)
        self.rankCfgLayout = QGridLayout(self.rankCfgWidget)
        self.rankLabel = QLabel(self.tr("分路配置"))

        self.topLabel = QLabel(self.tr("上单："))
        self.jugLabel = QLabel(self.tr("打野："))
        self.midLabel = QLabel(self.tr("中单："))
        self.botLabel = QLabel(self.tr("下路："))
        self.supLabel = QLabel(self.tr("辅助："))

        self.topButtonLayout = QHBoxLayout()
        self.topSelectButton1 = SummonerSpellButton()
        self.topSelectButton2 = SummonerSpellButton()
        self.jugButtonLayout = QHBoxLayout()
        self.jugSelectButton1 = SummonerSpellButton()
        self.jugSelectButton2 = SummonerSpellButton()
        self.midButtonLayout = QHBoxLayout()
        self.midSelectButton1 = SummonerSpellButton()
        self.midSelectButton2 = SummonerSpellButton()
        self.botButtonLayout = QHBoxLayout()
        self.botSelectButton1 = SummonerSpellButton()
        self.botSelectButton2 = SummonerSpellButton()
        self.supButtonLayout = QHBoxLayout()
        self.supSelectButton1 = SummonerSpellButton()
        self.supSelectButton2 = SummonerSpellButton()

        self.buttonsWidget = QWidget(self.view)
        self.buttonsLayout = QGridLayout(self.buttonsWidget)
        self.enableHintLabel = QLabel(self.tr("启用："))
        self.enableSwitchButton = SwitchButton(
            indicatorPos=IndicatorPosition.RIGHT)
        self.resetButton = PushButton(self.tr("重置"))

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        # 逻辑是，必须要设置默认，才能设置具体分路和启动功能
        self.defaultHintLabel.setStyleSheet("font: bold")
        self.rankLabel.setStyleSheet("font: bold")

        # 54 是占位用的空图标
        selected = 54 not in qconfig.get(self.defaultSpellConfigItem)
        checked = qconfig.get(self.enableConfigItem)

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            for index in [1, 2]:
                button: SummonerSpellButton = getattr(
                    self, f"{ty}SelectButton{index}")
                button.setFixedSize(40, 40)
                button.clicked.connect(
                    lambda _, t=ty, i=index: self.__onButtonClicked(t, i))

                if ty != 'default':
                    button.setEnabled(selected)

        self.enableSwitchButton.checkedChanged.connect(
            self.__onEnableSelectChanged)
        self.enableSwitchButton.setEnabled(selected)
        self.enableSwitchButton.setChecked(checked)
        self.resetButton.setMinimumWidth(100)
        self.resetButton.clicked.connect(self.__onResetButtonClicked)

        self.__updateStatusLabel()

    def __initLayout(self):
        self.addWidget(self.statusLabel)

        self.defaultCfgLayout.setVerticalSpacing(19)
        self.defaultCfgLayout.setContentsMargins(48, 18, 44, 18)
        self.defaultCfgLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.defaultButtonLayout.addWidget(self.defaultSelectButton1)
        self.defaultButtonLayout.addWidget(self.defaultSelectButton2)
        self.defaultButtonLayout.setSpacing(10)
        self.defaultButtonLayout.setContentsMargins(0, 0, 0, 0)
        self.defaultCfgLayout.addWidget(
            self.defaultHintLabel, 0, 0, Qt.AlignLeft)
        self.defaultCfgLayout.addWidget(
            self.defaultLabel, 1, 0, Qt.AlignLeft)
        self.defaultCfgLayout.addLayout(
            self.defaultButtonLayout, 1, 1, Qt.AlignRight)

        self.rankCfgLayout.setVerticalSpacing(19)
        self.rankCfgLayout.setContentsMargins(48, 18, 44, 18)
        self.rankCfgLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.rankCfgLayout.addWidget(self.rankLabel, 0, 0, Qt.AlignLeft)

        for i, ty in enumerate(['top', 'jug', 'mid', 'bot', 'sup']):
            label = getattr(self, f"{ty}Label")
            button1 = getattr(self, f"{ty}SelectButton1")
            button2 = getattr(self, f"{ty}SelectButton2")
            layout: QHBoxLayout = getattr(self, f"{ty}ButtonLayout")

            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            layout.addWidget(button1)
            layout.addWidget(button2)

            self.rankCfgLayout.addWidget(label, i + 1, 0, Qt.AlignLeft)
            self.rankCfgLayout.addLayout(layout, i + 1, 1, Qt.AlignRight)

        self.buttonsLayout.setVerticalSpacing(19)
        self.buttonsLayout.setContentsMargins(48, 18, 44, 18)
        self.buttonsLayout.setSizeConstraint(QHBoxLayout.SetMinimumSize)

        self.buttonsLayout.addWidget(
            self.enableHintLabel, 0, 0, Qt.AlignLeft)
        self.buttonsLayout.addWidget(
            self.enableSwitchButton, 0, 1, Qt.AlignRight)

        self.buttonsLayout.addWidget(
            self.resetButton, 1, 1, Qt.AlignRight)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)

        self.addGroupWidget(self.defaultCfgWidget)
        self.addGroupWidget(self.rankCfgWidget)
        self.addGroupWidget(self.buttonsWidget)

    async def initSummonerSpells(self):
        self.spells = {
            i: await connector.getSummonerSpellIcon(i)
            for i in connector.manager.getSummonerSpellList()
        }

        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            configItem = getattr(self, f"{ty}SpellConfigItem")
            selected = qconfig.get(configItem)

            for i in [1, 2]:
                spellId = selected[i - 1]

                button = f"{ty}SelectButton{i}"
                button: SummonerSpellButton = getattr(self, button)
                button.setPicture(self.spells[spellId])
                button.setSpellId(spellId)

    def __onButtonClicked(self, type: str, index: int):
        view = SummonerSpellSelectFlyout(self.spells)
        view.selectWidget.spellClicked.connect(
            lambda i, ty=type, ind=index: self.__onSpellSelected(ty, ind, i))

        button = QObject.sender(self)

        if index == 1:
            position = FlyoutAnimationType.SLIDE_LEFT
        else:
            position = FlyoutAnimationType.SLIDE_RIGHT

        self.w = Flyout.make(view, button, self, position, True)
        view.selectWidget.spellClicked.connect(self.w.fadeOut)

    def __onSpellSelected(self, type: str, index: int, id):

        button = f"{type}SelectButton{index}"
        button: SummonerSpellButton = getattr(self, button)

        if id != 54:
            anotherButton = f"{type}SelectButton{2 if index == 1 else 1}"
            anotherButton: SummonerSpellButton = getattr(self, anotherButton)
            anotherSpellId = anotherButton.getSpellId()

            # 选的技能和另一个已经选好的一样，认为是想要交换位置
            if id == anotherSpellId:
                currentSpellId = button.getSpellId()
                anotherButton.setPicture(self.spells[currentSpellId])
                anotherButton.setSpellId(currentSpellId)
                anotherButton.repaint()

        button.setPicture(self.spells[id])
        button.setSpellId(id)
        button.repaint()

        button1 = f"{type}SelectButton1"
        button1: SummonerSpellButton = getattr(self, button1)
        button2 = f"{type}SelectButton2"
        button2: SummonerSpellButton = getattr(self, button2)

        spells = [button1.getSpellId(), button2.getSpellId()]

        configItem = getattr(self, f"{type}SpellConfigItem")
        cfg.set(configItem, spells)

        if type != 'default':
            return

        buttonEnabled = False
        if id == 54:
            self.enableSwitchButton.setChecked(False)
            self.enableSwitchButton.setEnabled(False)
        elif 54 not in spells:
            self.enableSwitchButton.setEnabled(True)
            buttonEnabled = True

        for ty in ['top', 'jug', 'mid', 'bot', 'sup']:
            for i in [1, 2]:
                button = f"{ty}SelectButton{i}"
                button: SummonerSpellButton = getattr(self, button)
                button.setEnabled(buttonEnabled)

    def __onEnableSelectChanged(self, checked):
        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            for i in [1, 2]:
                button = f"{ty}SelectButton{i}"
                button: SummonerSpellButton = getattr(self, button)
                button.setEnabled(not checked)

        cfg.set(self.enableConfigItem, checked)

        self.__updateStatusLabel()

    def __onResetButtonClicked(self):
        for ty in ['default', 'top', 'jug', 'mid', 'bot', 'sup']:
            for i in [1, 2]:
                self.__onSpellSelected(ty, i, 54)

    def __updateStatusLabel(self):
        checked = self.enableSwitchButton.isChecked()

        text = self.tr("已开启") if checked else self.tr("已关闭")
        self.statusLabel.setText(text)
