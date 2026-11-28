from enum import Enum
from copy import deepcopy
import os
import sys

from PyQt5.QtCore import QLocale, QSize


from .qfluentwidgets import (qconfig, QConfig, ConfigItem, FolderValidator, BoolValidator,
                             OptionsConfigItem, OptionsValidator, ConfigSerializer,
                             RangeConfigItem, RangeValidator, EnumSerializer, ColorConfigItem, ConfigValidator,
                             Theme)


class Language(Enum):
    CHINESE_SIMPLIFIED = QLocale(QLocale.Chinese, QLocale.China)
    ENGLISH = QLocale(QLocale.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    def serialize(self, language: Language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != 'Auto' else Language.AUTO


class QSizeSerializer(ConfigSerializer):
    def serialize(self, size: QSize) -> str:
        return f"{size.width()}, {size.height()}"

    def deserialize(self, value: str) -> QSize:
        width, height = map(int, value.split(', '))
        return QSize(width, height)


def isWin11():
    return sys.platform == 'win32' and sys.getwindowsversion().build >= 22000


class QueueFilterValidator(ConfigValidator):
    def validate(self, value):
        keys = ["420", "430", "440", "450", "480"]

        for key in keys:
            if key not in value:
                return False

        return True

    def correct(self, value):
        keys = ["420", "430", "440", "450", "480"]
        new = deepcopy(value)

        for key in keys:
            if key not in value:
                new[key] = []

        return new


class Config(QConfig):
    lolFolder = ConfigItem("General", "LolPath", [])
    enableStartLolWithApp = ConfigItem("General", "EnableStartLolWithApp",
                                       False, BoolValidator())

    micaEnabled = ConfigItem(
        "Personalization", "MicaEnabled", isWin11(), BoolValidator())
    dpiScale = OptionsConfigItem("Personalization",
                                 "DpiScale",
                                 "Auto",
                                 OptionsValidator(
                                     [1, 1.25, 1.5, 1.75, 2, "Auto"]),
                                 restart=True)

    language = OptionsConfigItem("Personalization",
                                 "Language",
                                 Language.AUTO,
                                 OptionsValidator(Language),
                                 LanguageSerializer(),
                                 restart=True)

    winCardColor = ColorConfigItem(
        "Personalization", "WinCardColor", '#2839b01b')
    loseCardColor = ColorConfigItem(
        "Personalization", "LoseCardColor", '#28d3190c')
    remakeCardColor = ColorConfigItem(
        "Personalization", "RemakeCardColor", '#28a2a2a2')

    lightDeathsNumberColor = ColorConfigItem(
        "Personalization", "LightDeathsNumberColor", "#ffb60000")
    darkDeathsNumberColor = ColorConfigItem(
        "Personalization", "DarkDeathsNumberColor", "#ffff8d8d")

    careerGamesNumber = RangeConfigItem("Functions", "CareerGamesNumber", 20,
                                        RangeValidator(10, 100))
    apiConcurrencyNumber = RangeConfigItem("Functions", "ApiConcurrencyNumber", 1,
                                           RangeValidator(1, 100), restart=True)

    gameInfoFilter = ConfigItem(
        "Functions", "GameInfoFilter", False, BoolValidator())

    # 对局信息页: 排序方式 (order / winrate / kda) 与紧凑密度
    gameInfoSort = ConfigItem("Functions", "GameInfoSort", "order")
    gameInfoCompact = ConfigItem(
        "Functions", "GameInfoCompact", False, BoolValidator())

    # 进入 BP / 对局时自动切到"对局信息"页, 回大厅再切回生涯
    autoFollowGame = ConfigItem(
        "Functions", "AutoFollowGame", True, BoolValidator())
    # 窗口不够高时自动收掉场均数据行, 保证一屏能看全 5 个人
    gameInfoAutoDensity = ConfigItem(
        "Functions", "GameInfoAutoDensity", True, BoolValidator())

    # BP 阶段在敌方卡上显示"谁克制他"
    enableChampionCounter = ConfigItem(
        "Functions", "EnableChampionCounter", True, BoolValidator())

    # 已废弃：克制数据源统一跟随 opggRegion（国服 -> 101，其它 -> OP.GG）。
    # 定义保留只是为了老配置还能读出来，代码里不再使用
    counterSource = OptionsConfigItem(
        "Functions", "CounterSource", "cn", OptionsValidator(["cn", "kr"]))

    # 观战: 检测到 2999 时自动弹出双方面板
    enableSpectatorPanel = ConfigItem(
        "Functions", "EnableSpectatorPanel", True, BoolValidator())

    showTierInGameInfo = ConfigItem("Functions", "ShowTierInGameInfo", False,
                                    BoolValidator())
    autoShowOpgg = ConfigItem("Functions", "AutoShowOpgg", True,
                              BoolValidator())
    enableOpggOnTop = ConfigItem(
        "Functions", "EnableOpggOnTop", False, BoolValidator())
    enableAutoAcceptMatching = ConfigItem("Functions",
                                          "EnableAutoAcceptMatching", False,
                                          BoolValidator())
    enableAutoReconnect = ConfigItem("Functions",
                                     "EnableAutoReconnect", False,
                                     BoolValidator())

    autoAcceptMatchingDelay = RangeConfigItem(
        "Functions", "AutoAcceptMatchingDelay", 0, RangeValidator(0, 11))

    enableAutoSelectTimeoutCompleted = ConfigItem("Functions",
                                                  "EnableAutoSelectTimeoutCompleted", False,
                                                  BoolValidator())

    enableRandomSkin = ConfigItem(
        "Functions", "enableRandomSkin", False, BoolValidator())

    enableAutoSelectChampion = ConfigItem(
        "Functions", "EnableAutoSelectChampion", False, BoolValidator())
    autoSelectChampion = ConfigItem(
        "Functions", "AutoSelectChampion", [])
    autoSelectChampionTop = ConfigItem(
        "Functions", "AutoSelectChampionTop", [])
    autoSelectChampionJug = ConfigItem(
        "Functions", "AutoSelectChampionJug", [])
    autoSelectChampionMid = ConfigItem(
        "Functions", "AutoSelectChampionMid", [])
    autoSelectChampionBot = ConfigItem(
        "Functions", "AutoSelectChampionBot", [])
    autoSelectChampionSup = ConfigItem(
        "Functions", "AutoSelectChampionSup", [])

    enableAutoBanChampion = ConfigItem(
        "Functions", "EnableAutoBanChampion", False, BoolValidator())
    autoBanChampion = ConfigItem(
        "Functions", "AutoBanChampion", [])
    autoBanChampionTop = ConfigItem(
        "Functions", "AutoBanChampionTop", [])
    autoBanChampionJug = ConfigItem(
        "Functions", "AutoBanChampionJug", [])
    autoBanChampionMid = ConfigItem(
        "Functions", "AutoBanChampionMid", [])
    autoBanChampionBot = ConfigItem(
        "Functions", "AutoBanChampionBot", [])
    autoBanChampionSup = ConfigItem(
        "Functions", "AutoBanChampionSup", [])

    autoBanDelay = RangeConfigItem(
        "Functions", "AutoBanDelay", 0, RangeValidator(0, 25))
    pretentBan = ConfigItem("Functions", "PrententBan", False, BoolValidator())

    autoAcceptCeilSwap = ConfigItem(
        "Functions", "AutoAcceptCeilSwap", False, BoolValidator())
    autoAcceptChampTrade = ConfigItem(
        "Functions", "AutoAcceptChampTrade", False, BoolValidator())

    lastNoticeSha = ConfigItem("Other", "LastNoticeSha", "")

    # 首启安全说明已经弹到第几版（和 SAFETY_NOTICE_VERSION 比大小决定要不要弹）。
    # 不加 validator：老配置里没这个键时读出来是 0，正好等于「从没弹过」
    safetyNoticeVersion = ConfigItem("Other", "SafetyNoticeVersion", 0)

    enableCloseToTray = ConfigItem(
        "General", "EnableCloseToTray", None, OptionsValidator([None, True, False]))

    searchHistory = ConfigItem(
        "Other", "SearchHistory", ""
    )

    enableGameStartMinimize = ConfigItem("General",
                                         "EnableGameStartMinimize", False,
                                         BoolValidator())

    # 在线检查 GitHub Releases。现在改为"离线更新包"为主, 默认关掉,
    # 免得每次启动都去请求一个不存在的仓库
    enableCheckUpdate = ConfigItem("General",
                                   "EnableCheckUpdate", False,
                                   BoolValidator())

    # 独立版（打包成 exe 发出去）默认记 INFO：测试用户遇到问题只会说「用不了」，
    # 日志里得先有东西可看。源码开发保持 ERROR，免得本地日志刷得飞快
    logLevel = OptionsConfigItem(
        "General", "LogLevel", 20 if getattr(sys, 'frozen', False) else 40,
        OptionsValidator([10, 20, 30, 40]), restart=True)

    enableGithubProxy = ConfigItem(
        "General", "EnableGithubProxy", False, BoolValidator())
    githubProxyAddr = ConfigItem(
        "General", "GithubProxyAddr", "127.0.0.1:10809")

    enableOpggProxy = ConfigItem(
        "General", "EnableOpggProxy", False, BoolValidator(), restart=True)
    opggProxyAddr = ConfigItem(
        "General", "OpggProxyAddr", "127.0.0.1:10809")

    # 默认看国服数据（101 数据站）；老配置里存过 kr/global 的保持原样。
    # 不加 restart=True：国服走 101、外服走 OP.GG，只是换个接口，改完立刻生效。
    # 带上它会在每次切地区时弹「重启后生效」，而 BP 期间切数据源恰恰是最常见的操作
    opggRegion = ConfigItem("Functions", "OpggRegion", "cn",
                            OptionsValidator(["cn", "kr", "global"]))
    opggTier = ConfigItem("Functions", "OpggTier", "emerald_plus", OptionsValidator([
        "ibsg", "gold_plus", "platinum_plus", "emerald_plus", "diamond_plus", "master", "master_plus", "grandmaster", "challenger"
    ]))
    opggPosition = ConfigItem("Functions", "OpggPosition", "MID", OptionsValidator([
        "TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]))

    # BP 对位浮窗：默认开；位置存相对客户端左上角的偏移（基准像素）
    bpOverlayEnabled = ConfigItem(
        "Functions", "BpOverlayEnabled", True, BoolValidator())
    bpOverlayCollapsed = ConfigItem(
        "Functions", "BpOverlayCollapsed", False, BoolValidator())
    bpOverlayOffset = ConfigItem("Functions", "BpOverlayOffset", "")
    # 2.2.44 把浮窗从「一块面板」换成了「5 张卡」，宽度从 360 变成 1314。
    # 旧偏移是按老尺寸记的，照搬会把窗口甩到客户端外面，所以要能识别并丢弃。
    bpOverlayOffsetSchema = ConfigItem(
        "Functions", "BpOverlayOffsetSchema", 0, RangeValidator(0, 99))

    enableReserveGameinfo = ConfigItem(
        "Functions", "EnableReserveGameinfo", False, BoolValidator())

    enableSilent = ConfigItem(
        "General", "EnableSilent", False, BoolValidator())

    # 开机自启: 默认关。只写 HKCU 的 Run 项, 不需要管理员权限
    enableAutoStart = ConfigItem(
        "General", "EnableAutoStart", False, BoolValidator())

    windowSize = ConfigItem("Personalization", "WindowSize", QSize(
        1134, 826), None, QSizeSerializer())

    enableAutoHonor = ConfigItem(
        "Functions", "EnableAutoHonor", False, BoolValidator())

    autoHonorCategory = ConfigItem(
        "Functions", "AutoHonorCategory", "随机",
        OptionsValidator(["随机", "心态良好", "指挥有方", "操作亮眼"]))

    enableAutoPlayAgain = ConfigItem(
        "Functions", "EnableAutoPlayAgain", False, BoolValidator())

    autoPlayAgainDelay = RangeConfigItem(
        "Functions", "AutoPlayAgainDelay", 3, RangeValidator(0, 30))

    enableAutoSetSpells = ConfigItem(
        "Functions", "EnableAutoSetSpells", False, BoolValidator())

    autoSetSummonerSpell = ConfigItem(
        "Functions", "AutoSetSummonerSpell", [54, 54])

    autoSetSummonerSpellTop = ConfigItem(
        "Functions", "AutoSetSummonerSpellTop", [54, 54])
    autoSetSummonerSpellJug = ConfigItem(
        "Functions", "AutoSetSummonerSpellJug", [54, 54])
    autoSetSummonerSpellMid = ConfigItem(
        "Functions", "AutoSetSummonerSpellMid", [54, 54])
    autoSetSummonerSpellBot = ConfigItem(
        "Functions", "AutoSetSummonerSpellBot", [54, 54])
    autoSetSummonerSpellSup = ConfigItem(
        "Functions", "AutoSetSummonerSpellSup", [54, 54])

    queueFilter = ConfigItem("Functions", "QueueFilter", {
        "420": [],
        "430": [],
        "440": [],
        "450": [],
        "480": [],
    }, QueueFilterValidator())


YEAR = 2023
AUTHOR = "gyw10086"
VERSION = "2.2.49"
BETA = None

# 首启安全说明的版本号：**加一条说明就把这个数 +1**，老用户下次启动会再看到一次
SAFETY_NOTICE_VERSION = 1

# 更新源: 换成自己的仓库, 客户端直接从 GitHub Releases 取最新版
GITHUB_USER = "gyw10086"
GITHUB_REPO = "MMO-Trainer"
FEEDBACK_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/issues"
GITHUB_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"
# 手动下载兜底: Releases 页 (自动更新失败时给用户点这里)
RELEASES_URL = f"{GITHUB_URL}/releases/latest"

# 配置目录沿用 Seraphine, 老用户的标签/遇见记录/设置才不会丢
LOCAL_PATH = f"{os.getenv('APPDATA')}\\Seraphine"

cfg = Config()
# 新装默认深色：只改「默认值」，老用户 config.json 里已经存了 themeMode，
# load() 会用存的值覆盖，不会被这次改动影响
cfg.themeMode.value = Theme.DARK
qconfig.load(f"{LOCAL_PATH}\\config.json", cfg)
