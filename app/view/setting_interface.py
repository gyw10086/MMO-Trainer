# coding:utf-8
import os
import sys

from app.common.qfluentwidgets import (SettingCardGroup, SwitchSettingCard, ComboBoxSettingCard,
                                       PushSettingCard, InfoBar, InfoBarPosition,
                                       setThemeColor, PrimaryPushSettingCard, HyperlinkCard,
                                       TeachingTip, TeachingTipTailPosition, TeachingTipView, PushButton,
                                       SwitchButton)
from PyQt5.QtCore import Qt, QUrl, QTimer
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget, QLabel, QFileDialog, QHBoxLayout,
                             QPushButton, QStackedWidget, QVBoxLayout)

from app.common.design_tokens import color
from app.common.icons import Icon

# 设置卡片自己的 qss 不在 app 的枚举里, 换主题要手动重新 apply
from qfluentwidgets.common.style_sheet import FluentStyleSheet
from qfluentwidgets.components.settings.setting_card import SettingCard
from app.common import autostart
from app.common.config import (cfg, YEAR, AUTHOR, VERSION, FEEDBACK_URL, GITHUB_URL, isWin11,
                               BETA)
from app.common.logger import logger
from app.common import theme
from app.common.style_sheet import StyleSheet
from app.components.seraphine_interface import SeraphineInterface
from app.components.page_head import PageHead
from app.components.setting_cards import (LineEditSettingCard, GameTabColorSettingCard,
                                          LooseSwitchSettingCard, ProxySettingCard,
                                          DeathsNumberColorSettingCard, ThemeColorSettingCard,
                                          QueueFilterCard)
from app.components.message_box import MultiPathSettingMsgBox

TAG = "Setting"


class SettingInterface(SeraphineInterface):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.scrollWidget = QWidget()
        self.rootLayout = QVBoxLayout(self.scrollWidget)
        self.rootLayout.setContentsMargins(0, 0, 0, 0)
        self.rootLayout.setSpacing(0)

        # 设计稿 S4 的页头：总览 › 设置 ›  常规  SEC-01
        self.pageHead = PageHead(
            self.tr('功能'),
            crumbs=(self.tr('总览'), self.tr('设置')),
            sec='SEC-01', parent=self)
        self.pageHead.crumbClicked.connect(self.__onCrumb)
        self.pageHead.move(36, 28)

        self.functionGroup = SettingCardGroup(self.tr("功能"),
                                              self.scrollWidget)

        self.apiConcurrencyCount = LineEditSettingCard(
            cfg.apiConcurrencyNumber,
            self.tr("LCU 接口并发数"),
            self.tr("并发数量："),
            1, 1, 20,
            Icon.APPLIST,
            self.tr("同时请求 LCU 接口的最大数量，调高能加快加载，过高可能被客户端限流。"),
            self.functionGroup)

        self.careerGamesCount = LineEditSettingCard(
            cfg.careerGamesNumber,
            self.tr("默认对局数量"),
            self.tr("场次："),
            10, 10, 100,
            Icon.SLIDESEARCH,
            self.tr(
                "生涯页一次加载的对局场数上限。"),
            self.functionGroup)

        self.gameInfoFilterCard = QueueFilterCard(
            self.tr("对局信息模式过滤"),
            self.tr(
                "对局信息页只显示与当前模式一致的对局。"),
            cfg.queueFilter
        )

        self.gameInfoShowTierCard = SwitchSettingCard(
            Icon.TROPHY, self.tr("对局信息显示段位"),
            self.tr(
                "在对局信息里显示段位图标，开启后会略微增加请求量。"),
            cfg.showTierInGameInfo)

        self.autoClearGameinfoCard = SwitchSettingCard(
            Icon.ATTACHTEXT, self.tr("保留对局信息页"),
            self.tr(
                "直到下一次选人开始前，不自动离开对局信息页。"),
            cfg.enableReserveGameinfo
        )

        self.autoFollowGameCard = SwitchSettingCard(
            Icon.GAME, self.tr("自动跟随对局"),
            self.tr("BP / 进入对局时自动切到对局信息页，回到大厅再切回生涯"),
            cfg.autoFollowGame)

        self.autoDensityCard = SwitchSettingCard(
            Icon.ATTACHTEXT, self.tr("自动调整信息密度"),
            self.tr("窗口不够高时自动收起床均数据，保证一屏能看全 5 个人"),
            cfg.gameInfoAutoDensity)

        self.bpOverlayCard = SwitchSettingCard(
            Icon.DESKTOPRIGHT, self.tr("BP 对位浮窗"),
            self.tr("选人阶段在客户端旁显示对位英雄的克制与胜率，"
                    "可拖动位置、右键收起。选人过程中改这个开关会立刻生效。"),
            cfg.bpOverlayEnabled)

        # 克制数据源不再单独给开关：跟着 OP.GG 窗口的地区下拉走
        # （国服 -> 101，韩服/全部地区 -> OP.GG），两处设置容易打架，已经并掉

        self.opggGroup = SettingCardGroup(self.tr("OP.GG"),
                                          self.scrollWidget)

        self.autoShowOpggCard = SwitchSettingCard(
            Icon.WINDOW, self.tr("自动打开 OP.GG 窗口"),
            self.tr("进入选人阶段时自动弹出 OP.GG 窗口。"),
            cfg.autoShowOpgg, self.opggGroup)
        self.opggOnTopCard = SwitchSettingCard(
            Icon.PADDINGTOP, self.tr("OP.GG 窗口置顶"),
            self.tr(
                "选人期间让 OP.GG 窗口保持在最前面。"),
            cfg.enableOpggOnTop, self.opggGroup)
        self.opggProxyCard = ProxySettingCard(
            self.tr("OP.GG HTTP 代理"), self.tr(
                "连接 OP.GG 时走代理。"),
            cfg.enableOpggProxy, cfg.opggProxyAddr, self.opggGroup)

        self.generalGroup = SettingCardGroup(self.tr("通用"),
                                             self.scrollWidget)

        self.lolFolderCard = PushSettingCard(self.tr("选择目录"),
                                             Icon.FOLDER,
                                             self.tr("客户端路径"),
                                             self.tr("设置客户端路径与启动顺序"),
                                             self.generalGroup)
        self.lolFolderCard.button.setFixedWidth(100)
        self.lolFolderCard.button.setStyleSheet(
            "QPushButton {padding-left: 0; padding-right: 0;}")

        self.autoStartCard = SwitchSettingCard(
            Icon.ARROWREPEAT, self.tr("开机自启"),
            self.tr("开机自动启动 (默认关闭, 只写入当前用户启动项)"),
            cfg.enableAutoStart
        )

        self.silentCard = SwitchSettingCard(
            Icon.SNOOZE, self.tr("静默启动"),
            self.tr(
                "启动时最小化窗口"),
            cfg.enableSilent
        )

        self.logGroup = SettingCardGroup(self.tr("日志"), self.scrollWidget)

        self.logLevelCard = ComboBoxSettingCard(
            cfg.logLevel,
            Icon.LOG,
            self.tr('日志级别'),
            self.tr('日志级别（重启后生效）'),
            texts=["Debug", "Info", "Warning", "Error"],
            parent=self.logGroup)
        self.viewLogCard = PushSettingCard(
            self.tr("打开"), Icon.DOCUMENT, self.tr("日志文件"),
            self.
            tr("Open log directory"),
            self.logGroup)
        self.viewLogCard.button.setFixedWidth(100)

        # 这玩意左右 padding 大的离谱，手动给它改了
        self.viewLogCard.button.setStyleSheet(
            "QPushButton {padding-left: 0; padding-right: 0;}")

        self.enableStartLolWithApp = SwitchSettingCard(
            Icon.CIRCLERIGHT,
            self.tr("自动启动英雄联盟"),
            self.tr("打开 MMO战队训练助手 时自动启动英雄联盟客户端"),
            configItem=cfg.enableStartLolWithApp,
            parent=self.generalGroup)
        self.deleteResourceCard = PushSettingCard(
            self.tr("删除"), Icon.DELETE, self.tr("清理缓存"),
            self.
            tr("Delete all game resources (Apply it when game resources update)"
               ), self.generalGroup)

        self.deleteResourceCard.button.setFixedWidth(100)
        self.deleteResourceCard.button.setStyleSheet(
            "QPushButton {padding-left: 0; padding-right: 0;}")

        self.enableCloseToTray = LooseSwitchSettingCard(
            Icon.EXIT,
            self.tr("关闭时最小化到托盘"),
            self.tr("点击关闭按钮时收进系统托盘，不退出程序。"),
            configItem=cfg.enableCloseToTray,
            parent=self.generalGroup)

        self.personalizationGroup = SettingCardGroup(
            self.tr("个性化"), self.scrollWidget)

        self.micaCard = SwitchSettingCard(
            Icon.BLUR,
            self.tr('云母材质'),
            self.tr("窗口与界面半透明（仅 Win11 可用）"),
            cfg.micaEnabled,
            self.personalizationGroup
        )
        self.themeCard = ComboBoxSettingCard(
            cfg.themeMode,
            Icon.BRUSH,
            self.tr("应用主题"),
            self.tr("切换主题外观"),
            texts=[
                self.tr("浅色"),
                self.tr("深色"),
                self.tr("跟随系统")
            ],
            parent=self.personalizationGroup)
        # self.themeColorCard = CustomColorSettingCard(
        #     cfg.themeColor, Icon.PALETTE, self.tr("主题色"),
        #     self.tr("切换主题色"),
        #     self.personalizationGroup)
        self.themeColorCard = ThemeColorSettingCard(
            self.tr("主题色"), self.tr(
                "切换主题色"),
            cfg.themeColor, self.personalizationGroup)
        self.gameTabColorSettingCard = GameTabColorSettingCard(
            self.tr("战绩卡配色"),
            self.tr("自定义胜 / 负 / 重开卡片的颜色。"),
            cfg.winCardColor, cfg.loseCardColor, cfg.remakeCardColor,
            self.personalizationGroup
        )
        self.deathNumberColorSettingCard = DeathsNumberColorSettingCard(
            self.tr("阵亡数配色"),
            self.tr("自定义 KDA 里阵亡数的颜色。"),
            cfg.lightDeathsNumberColor, cfg.darkDeathsNumberColor,
            self.personalizationGroup
        )
        self.zoomCard = ComboBoxSettingCard(
            cfg.dpiScale,
            Icon.ZOOMFIT,
            self.tr("界面缩放"),
            self.tr("调整控件与字号大小。"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                self.tr("跟随系统")
            ],
            parent=self.personalizationGroup)
        self.languageCard = ComboBoxSettingCard(
            cfg.language,
            Icon.LANGUAGE,
            self.tr('界面语言'),
            self.tr('界面语言'),
            texts=['简体中文', 'English',
                   self.tr('跟随系统')],
            parent=self.personalizationGroup)

        self.updateGroup = SettingCardGroup(self.tr("更新"), self.scrollWidget)

        # 独立版（PyInstaller）的代码在 exe 内部的 PYZ 里，更新包里的
        # app/*.py 覆盖到目录上不生效，还会在旁边留一堆用不到的源码。
        # 所以这两种更新方式对它都不成立，直接说明白，别给一个点了没用的按钮。
        frozen = getattr(sys, 'frozen', False)

        # 在线更新: 直接从 GitHub 拉包并重启安装，不用等人发 zip
        self.updateCheckCard = PushSettingCard(
            self.tr('立即检查'), Icon.ARROWREPEAT,
            self.tr('在线更新') if not frozen else self.tr('更新'),
            self.tr('从 GitHub 检查新版本，有的话可以直接下载安装')
            if not frozen else
            self.tr('当前是独立版：程序打包在 exe 内，只能整包替换。'
                    '新版本请下载完整包后覆盖整个文件夹'),
            self.updateGroup)
        self.updateCheckCard.button.setFixedWidth(100)

        # 离线更新: 用户拿到 zip 就装, 不依赖网络
        self.updatePackageCard = PushSettingCard(
            self.tr('选择文件'), Icon.UPDATE,
            self.tr('离线更新包') if not frozen else self.tr('版本替换'),
            self.tr('把更新包 zip 拖进窗口任意位置，或点这里选择')
            if not frozen else
            self.tr('替换方法：关掉程序，用新包解压出的文件夹整体覆盖当前目录'),
            self.updateGroup)
        self.updatePackageCard.button.setFixedWidth(100)

        if frozen:
            self.updateCheckCard.button.setEnabled(False)
            self.updatePackageCard.button.setEnabled(False)

        self.aboutGroup = SettingCardGroup(self.tr("关于"), self.scrollWidget)

        self.aboutCard = HyperlinkCard(
            GITHUB_URL, self.tr("打开 GitHub"), Icon.INFO, self.tr('关于'),
            self.tr('版权所有') + ' © ' + f"{YEAR}, {AUTHOR}. " +
            self.tr('版本') + f" {BETA or VERSION}", self.aboutGroup)
        self.aboutCard.linkButton.setIcon(Icon.GITHUB)

        # 安全说明随时可查（首启也会自动弹一次），force=True 不写配置
        self.safetyNoticeCard = PushSettingCard(
            self.tr('查看'),
            Icon.INFO,
            self.tr('安全说明'),
            self.tr('查看本程序对游戏进程的访问范围'),
            self.aboutGroup)
        self.safetyNoticeCard.button.setFixedWidth(100)

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 90, 0, 20)
        # self.scrollDelagate.vScrollBar.setContentsMargins(0, 50, 0, 0)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        self.micaCard.switchButton.setEnabled(isWin11())

        self.__localizeSwitches()

        # initialize style sheet
        self.scrollWidget.setObjectName('scrollWidget')
        StyleSheet.SETTING_INTERFACE.apply(self)

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()

    def showEvent(self, event):
        """首帧再补一次开关文案

        部分卡片（代理、主题色这些自定义卡）的开关是构造时才建出来的，
        __initWidget 那一刻还找不到它们，结果界面上会混着 On / Off。
        """
        super().showEvent(event)
        if not getattr(self, '_SettingInterface__switchesLocalized', False):
            self.__localizeSwitches()
            self.__switchesLocalized = True

    def __localizeSwitches(self):
        """开关上的 On / Off 是库里的硬编码英文，统一换成中文

        setOnText / setOffText 只改属性，不会刷新已经画上去的那段文字，
        所以还要按当前状态 setText 一次。
        """
        for switch in self.findChildren(SwitchButton):
            switch.setOnText('开')
            switch.setOffText('关')
            switch.setText('开' if switch.isChecked() else '关')

    def __initLayout(self):
        # add cards to group
        self.functionGroup.addSettingCard(self.apiConcurrencyCount)
        self.functionGroup.addSettingCard(self.careerGamesCount)
        self.functionGroup.addSettingCard(self.gameInfoFilterCard)
        self.functionGroup.addSettingCard(self.autoClearGameinfoCard)
        self.functionGroup.addSettingCard(self.autoFollowGameCard)
        self.functionGroup.addSettingCard(self.bpOverlayCard)
        self.functionGroup.addSettingCard(self.autoDensityCard)
        self.functionGroup.addSettingCard(self.gameInfoShowTierCard)

        self.opggGroup.addSettingCard(self.autoShowOpggCard)
        self.opggGroup.addSettingCard(self.opggOnTopCard)
        self.opggGroup.addSettingCard(self.opggProxyCard)

        self.generalGroup.addSettingCard(self.lolFolderCard)
        self.generalGroup.addSettingCard(self.enableStartLolWithApp)
        self.generalGroup.addSettingCard(self.deleteResourceCard)
        self.generalGroup.addSettingCard(self.enableCloseToTray)
        self.generalGroup.addSettingCard(self.autoStartCard)
        self.generalGroup.addSettingCard(self.silentCard)

        self.logGroup.addSettingCard(self.logLevelCard)
        self.logGroup.addSettingCard(self.viewLogCard)

        self.personalizationGroup.addSettingCard(self.micaCard)
        self.personalizationGroup.addSettingCard(self.themeCard)
        self.personalizationGroup.addSettingCard(self.themeColorCard)
        self.personalizationGroup.addSettingCard(self.gameTabColorSettingCard)
        self.personalizationGroup.addSettingCard(
            self.deathNumberColorSettingCard)
        self.personalizationGroup.addSettingCard(self.zoomCard)
        self.personalizationGroup.addSettingCard(self.languageCard)

        self.updateGroup.addSettingCard(self.updateCheckCard)
        self.updateGroup.addSettingCard(self.updatePackageCard)

        self.aboutGroup.addSettingCard(self.aboutCard)
        self.aboutGroup.addSettingCard(self.safetyNoticeCard)

        # 七个分组一屏放不下，滚动条的解法是「越滚越不知道还有多少」；
        # 改成左侧二级导航，点哪个只显示哪一组（设计稿 S4）
        self.__groups = [
            ('01', '功能', self.functionGroup),
            ('02', 'OP.GG', self.opggGroup),
            ('03', '通用', self.generalGroup),
            ('04', '窗口与外观', self.personalizationGroup),
            ('05', '更新与关于', None),
            ('06', '日志', self.logGroup),
        ]
        groups = self.__groups

        self.stack = QStackedWidget(self.scrollWidget)
        self.navBox = QWidget(self.scrollWidget)
        self.navBox.setFixedWidth(148)

        navLayout = QVBoxLayout(self.navBox)
        navLayout.setContentsMargins(36, 0, 0, 0)
        navLayout.setSpacing(0)

        self.navButtons = []
        for i, (code, title, group) in enumerate(groups):
            if group is None:
                page, group = self.__updateAndAboutPage()
            else:
                page = self.__groupPage(group)
            self.stack.addWidget(page)

            # 组头也带编号，和左侧导航对得上
            group.setSerial(code)

            btn = self.__navButton(code, title, i)
            navLayout.addWidget(btn)
            self.navButtons.append(btn)

        navLayout.addStretch(1)

        # 换主题时按钮 / 卡片的 qss 都要重新铺一遍
        theme.register(self)

        host = QWidget(self.scrollWidget)
        hostLayout = QHBoxLayout(host)
        hostLayout.setContentsMargins(0, 0, 24, 0)
        hostLayout.setSpacing(20)
        hostLayout.addWidget(self.navBox)
        hostLayout.addWidget(self.stack, 1)

        self.rootLayout.addWidget(host, 1)
        self.__switchGroup(0)
        # 首次构建后立刻按当前主题铺一次, 否则启动在浅色时标题还是淡灰
        self.__refreshThemeStyles()

    def __groupPage(self, group) -> QWidget:
        page = QWidget(self.stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 20)
        layout.setSpacing(0)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def __updateAndAboutPage(self) -> tuple:
        """更新与关于合成一页：一个卡片 + 一个链接卡，单独占一页太空"""
        page = QWidget(self.stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 20)
        layout.setSpacing(18)
        layout.addWidget(self.updateGroup)
        layout.addWidget(self.aboutGroup)
        layout.addStretch(1)
        return page, self.updateGroup

    def __navButtonStyle(self) -> str:
        """分组按钮样式

        颜色全部走令牌。原来写死 #9BA3AD / #262C33 / #EAECEF —— 浅色主题下
        选中项就是一整块深灰底配白字，在白色页面里像贴了张黑纸。
        """
        return (
            "QPushButton {"
            f"  color: {color('textSub')}; background: transparent;"
            "  text-align: left; border: none;"
            "  border-left: 2px solid transparent; padding-left: 10px;"
            "  font-size: 12px;"
            "}"
            "QPushButton:hover {"
            f"  color: {color('text')}; background: {color('hover')};"
            "}"
            "QPushButton:checked {"
            f"  color: {color('text')};"
            f"  border-left: 2px solid {color('brand')};"
            f"  background: {color('bg3')};"
            "}")

    def __refreshNavStyles(self, *_):
        style = self.__navButtonStyle()
        for btn in self.navButtons:
            btn.setStyleSheet(style)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__refreshThemeStyles()

    def __refreshThemeStyles(self, *_):
        """换主题后把导航与设置卡片的样式重新铺一遍

        `SettingCard` 只在构造时 apply 一次 `SETTING_CARD`，库没有监听
        themeChanged —— 不重铺的话标题会留在旧主题的颜色上：深色切浅色时
        标题是浅灰白，在白卡片上几乎看不见。
        """
        self.__refreshNavStyles()

        for card in self.findChildren(SettingCard):
            FluentStyleSheet.SETTING_CARD.apply(card)

            # 库的 SETTING_CARD 在浅色主题下给标题的是 #8C93A3,
            # 白底上对比度只有 2.9:1, 读起来发飘。标题用正文色, 副标题保持次要色
            title = getattr(card, 'titleLabel', None)

            if title is not None:
                title.setStyleSheet(f"color: {color('text')};")

    def __navButton(self, code: str, title: str, index: int) -> QPushButton:
        # 设计稿 S4 的 .set-nav：32 高单行、选中 2px 黄条 + 灰底
        btn = QPushButton(title, self.navBox)
        btn.setCheckable(True)
        btn.setFixedHeight(32)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(self.__navButtonStyle())
        btn.clicked.connect(lambda _=False, i=index: self.__switchGroup(i))
        return btn

    def __switchGroup(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.navButtons):
            btn.setChecked(i == index)

        code, title, _ = self.__groups[index]
        self.pageHead.setTitle(self.tr(title))
        self.pageHead.setSec(f'SEC-{code}')

    def __onCrumb(self, name: str):
        """面包屑前级：总览 → 跳页面，设置 → 回到第一组"""
        window = self.window()

        if name == self.tr('总览'):
            target = getattr(window, 'overviewInterface', None)

            if target is not None and hasattr(window, 'switchTo'):
                window.switchTo(target)
            return

        if name == self.tr('设置'):
            self.__switchGroup(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 页头是 scrollArea 的直接子控件（不随内容滚动），宽度要跟着窗口变
        head = getattr(self, 'pageHead', None)

        if head is not None:
            head.resize(max(200, self.width() - 60), 32)

    def __connectSignalToSlot(self):
        self.lolFolderCard.clicked.connect(self.__onLolFolderCardClicked)
        self.safetyNoticeCard.clicked.connect(self.__onSafetyNoticeCardClicked)

        # 主题只留一条刷新链路：监听配置项本身。
        # 以前这里是 `comboBox.currentIndexChanged -> setTheme(...)`，而它会在
        # `ComboBoxSettingCard` 自己 `cfg.set(cfg.themeMode)` 的调用栈深处再跑一遍
        # 全量刷新（实测 394ms / 580ms），中途 Qt 还会插进来画图，于是出现
        # 「导航旧色 + 内容新色」。改成盯配置项 + 挪到下一拍：让下拉框先把自己
        # 画完，再整体切换。
        cfg.themeMode.valueChanged.connect(
            lambda *_: QTimer.singleShot(0, self.__applyThemeNow))

        cfg.appRestartSig.connect(self.__showRestartToolTip)
        self.updateCheckCard.clicked.connect(self.__onCheckUpdateOnline)
        self.updatePackageCard.clicked.connect(self.__onChooseUpdatePackage)
        cfg.enableAutoStart.valueChanged.connect(self.__onAutoStartChanged)
        self.careerGamesCount.pushButton.clicked.connect(
            self.__showUpdatedSuccessfullyToolTip)
        self.deleteResourceCard.clicked.connect(self.__showFlyout)
        self.viewLogCard.clicked.connect(
            lambda: os.system(f'explorer {os.getcwd()}\\log')
        )

    def __applyThemeNow(self):
        """真正切主题（延后一拍执行，已跳出 ComboBox 的调用栈）"""
        theme.applyTheme(cfg.get(cfg.themeMode))

    def __onLolFolderCardClicked(self):
        current = cfg.get(cfg.lolFolder)

        msgBox = MultiPathSettingMsgBox(current, self.window())
        msgBox.exec_()

    def __onSafetyNoticeCardClicked(self):
        """用户主动查看安全说明：每次弹，且不动 safetyNoticeVersion"""
        from app.view.safety_notice import showSafetyNotice

        try:
            showSafetyNotice(self.window(), force=True)
        except Exception as e:
            # 弹窗失败不能连累设置页
            logger.error(f'failed to show safety notice: {e}', TAG)

    def __onCheckUpdateOnline(self):
        """在线检查更新

        实际拉取在主窗口的一次性线程里跑（两个源各 8~12 秒超时，同步跑会把
        设置页冻住）。这里只负责给个「正在检查」的即时反馈 —— 否则用户点完
        盯着没变化的按钮，不知道到底有没有反应。
        """
        window = self.window()

        if not hasattr(window, 'requestCheckUpdate'):
            return

        self.updateCheckCard.button.setEnabled(False)
        InfoBar.info(
            self.tr('正在检查更新'),
            self.tr('正在从 GitHub 获取版本信息…'),
            orient=Qt.Vertical, isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=3000, parent=self)

        # 3 秒后放开按钮：检查本身可能更久，但按钮不该一直灰着
        QTimer.singleShot(3000, lambda: self.updateCheckCard.button.setEnabled(True))
        window.requestCheckUpdate()

    def __onChooseUpdatePackage(self):
        """选一个 zip; 真正的校验和确认交给主窗口, 和拖拽走同一条路"""
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr('选择更新包'), '', 'Zip (*.zip)')

        if not path:
            return

        window = self.window()

        if hasattr(window, 'applyUpdatePackage'):
            window.applyUpdatePackage(path)

    def __onAutoStartChanged(self, enabled):
        """写注册表; 失败要把开关拨回去, 否则界面显示"开"但实际没生效"""
        if autostart.apply(enabled):
            return

        InfoBar.error(
            self.tr('设置失败'),
            self.tr('无法写入注册表启动项，可能被安全软件拦截'),
            duration=4000,
            parent=self)

        # 回滚配置 (会再触发一次本函数, 那次 apply 走的是取消, 不会再弹错)
        cfg.set(cfg.enableAutoStart, not enabled)

    def __showRestartToolTip(self):
        InfoBar.success(self.tr("更新成功"),
                        self.tr("修改后需要重启程序才会生效"),
                        duration=2000,
                        parent=self)

    def __showUpdatedSuccessfullyToolTip(self):
        InfoBar.success(self.tr("更新成功"),
                        self.tr("设置已应用"),
                        duration=2000,
                        parent=self)

    def __onDeleteButtonClicked(self):
        folders = [
            'champion icons', 'item icons', 'profile icons', 'rune icons',
            'summoner spell icons', "augment icons"
        ]

        for folder in folders:
            path = f'app/resource/game/{folder}'

            if not os.path.exists(path):
                continue

            for file in os.listdir(path):
                filePath = f"{path}/{file}"

                if not os.path.exists(filePath):
                    continue

                os.remove(filePath)

    def __showFlyout(self):
        view = TeachingTipView(
            title=self.tr("确定要这么做吗？"),
            content=self.
            tr("资源会在下次使用时重新下载，由 MMO战队训练助手 下载会多花一点时间"
               ),
            isClosable=True,
            tailPosition=TeachingTipTailPosition.RIGHT)

        applyButton = PushButton(self.tr('确认清理'))

        view.widgetLayout.insertSpacing(1, 10)
        view.widgetLayout.addSpacing(10)
        view.addWidget(applyButton, align=Qt.AlignRight)

        t = TeachingTip.make(
            view,
            self.deleteResourceCard.button,
            -1,
            TeachingTipTailPosition.RIGHT,
            self,
        )

        applyButton.clicked.connect(self.__onDeleteButtonClicked)
        view.closed.connect(t.close)
