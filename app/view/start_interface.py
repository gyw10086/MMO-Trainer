import subprocess
import os
import threading

import pyperclip
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QWidget, QVBoxLayout,
                             QSpacerItem, QSizePolicy)

from app.common.qfluentwidgets import (InfoBar, InfoBarPosition, PushButton, SmoothScrollArea,
                                       IndeterminateProgressBar, ComboBox)
from app.components.seraphine_interface import SeraphineInterface

from app.lol.connector import connector
from app.common.config import cfg
from app.common.style_sheet import StyleSheet
from app.common.icons import Icon
from app.common.util import getTasklistPath, getLolClientPids, getLolClientPidsSlowly
from app.common.signals import signalBus
from app.components.message_box import ChangeClientMessageBox


class StartInterface(SeraphineInterface):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.loading = True

        self.processBar = IndeterminateProgressBar(self)
        self.label1 = QLabel()
        self.label2 = QLabel()
        self.label3 = QLabel()
        self.pushButton = PushButton()

        self.vBoxLayout = QVBoxLayout(self)

        self.pathComboBox = ComboBox()
        self.pathLayout = QHBoxLayout()

        self.__initWidget()
        self.__initLayout()
        self.showLoadingPage()

    def __initLayout(self):
        self.label1.setAlignment(Qt.AlignCenter)
        self.label2.setAlignment(Qt.AlignCenter)

        self.pathLayout.setContentsMargins(0, 0, 0, 0)
        self.pathLayout.setAlignment(Qt.AlignCenter)
        self.pathLayout.addWidget(self.label2)
        self.pathLayout.addWidget(self.pathComboBox)

        self.vBoxLayout.addWidget(self.processBar)
        self.vBoxLayout.addItem(
            QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.vBoxLayout.addWidget(self.label1)
        self.vBoxLayout.addSpacing(20)
        self.vBoxLayout.addWidget(self.pushButton, alignment=Qt.AlignCenter)
        self.vBoxLayout.addWidget(self.label3, alignment=Qt.AlignCenter)
        self.vBoxLayout.addSpacing(20)
        self.vBoxLayout.addLayout(self.pathLayout)
        self.vBoxLayout.addItem(
            QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def __initWidget(self):
        self.pushButton.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        self.label1.setObjectName('label1')
        self.label2.setObjectName('label2')
        self.label3.setObjectName("label3")

        paths = cfg.get(cfg.lolFolder)
        self.pathComboBox.addItems(paths)

        self.__connectSignalToSlot()
        StyleSheet.START_INTERFACE.apply(self)

    def hideLoadingPage(self):
        self.processBar.stop()
        self.loading = False

        self.label1.setText(self.tr("已连接英雄联盟客户端") + " 🎉")
        self.label2.setText(
            f"PID = {connector.pid}\n--app-port = {connector.port}\n--remoting-auth-token = {connector.token}")
        self.label3.setVisible(False)
        self.pathComboBox.setVisible(False)

        self.pushButton.setText(self.tr("已切换客户端"))
        self.pushButton.setIcon(Icon.DUALSCREEN)

    def showLoadingPage(self):
        self.processBar.start()
        self.loading = True

        self.label1.setText(self.tr("正在连接英雄联盟客户端…"))
        self.label2.setText(self.tr("客户端目录："))
        self.label3.setText(self.tr("（也可以用其他方式启动）"))

        self.label3.setVisible(True)
        self.pathComboBox.setVisible(True)

        self.pushButton.setIcon(Icon.CIRCLERIGHT)
        self.pushButton.setText(self.tr("启动客户端"))

    def __connectSignalToSlot(self):
        self.pushButton.clicked.connect(self.__onPushButtonClicked)

    def __onPushButtonClicked(self):
        if self.loading:
            for clientName in ("client.exe", "LeagueClient.exe"):
                path = f'{self.pathComboBox.currentText()}/{clientName}'
                if os.path.exists(path):
                    # 走 ShellExecuteW, 中文路径不会被 cmd.exe 的代码页吃掉
                    try:
                        os.startfile(path)
                    except OSError:
                        os.popen(f'"{path}"')
                    self.__showStartLolSuccessInfo()
                    break
            else:
                self.__showLolClientPathErrorInfo()
        else:
            path = getTasklistPath()
            if path:
                pids = getLolClientPids(path)
            else:
                pids = getLolClientPidsSlowly()

            if len(pids) == 0:
                signalBus.lolClientEnded.emit()
            elif len(pids) == 1:
                self.__showCantChangeLolClientInfo()
            else:
                box = ChangeClientMessageBox(pids=pids, parent=self.window())
                box.exec()

    def __showCantChangeLolClientInfo(self):
        InfoBar.error(
            title=self.tr("无法切换客户端"),
            content=self.tr("只能运行一个客户端"),
            orient=Qt.Vertical,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self)

    def __showStartLolSuccessInfo(self):
        InfoBar.success(title=self.tr("客户端已启动"),
                        orient=Qt.Vertical,
                        content="",
                        isClosable=True,
                        position=InfoBarPosition.BOTTOM_RIGHT,
                        duration=5000,
                        parent=self)

    def __showLolClientPathErrorInfo(self):
        InfoBar.error(
            title=self.tr("路径无效"),
            content=self.
            tr('Please set the correct directory of the LOL client in the setting page'
               ),
            orient=Qt.Vertical,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self)
