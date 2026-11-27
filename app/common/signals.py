from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget


class SignalBus(QObject):
    # listener:
    tasklistNotFound = pyqtSignal()

    lolClientStarted = pyqtSignal(int)
    lolClientEnded = pyqtSignal()
    lolClientChanged = pyqtSignal(int)

    terminateListeners = pyqtSignal()

    # connector:
    lcuApiExceptionRaised = pyqtSignal(str, BaseException)
    currentSummonerProfileChanged = pyqtSignal(dict)
    gameStatusChanged = pyqtSignal(str)
    champSelectChanged = pyqtSignal(dict)
    getCmdlineError = pyqtSignal()

    # career_interface
    careerGameBarClicked = pyqtSignal(str)

    # 生涯页算好的汇总数据, 总览页直接复用, 不重复请求 LCU
    infoLoaded = pyqtSignal(dict)

    # search_interface:
    gameTabClicked = pyqtSignal(QWidget)

    # jumps:
    toCareerInterface = pyqtSignal(str)
    toSearchInterface = pyqtSignal(str)

    # style:
    customColorChanged = pyqtSignal(str)

    # OPGG:
    toOpggBuildInterface = pyqtSignal(int, str, str)


signalBus = SignalBus()
