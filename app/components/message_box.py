from qasync import asyncSlot
import aiohttp
import os
import shutil
import sys
import webbrowser
import zipfile

from PyQt5.QtCore import Qt, pyqtSignal, QUrl
from PyQt5.QtWidgets import (QPushButton, QVBoxLayout, QWidget, QLabel, QFileDialog,
                             QHBoxLayout)
from PyQt5.QtGui import QFont, QPixmap

from app.common.qfluentwidgets import (MessageBoxBase, SmoothScrollArea,
                                       BodyLabel, TextEdit, TitleLabel,
                                       ProgressBar, PrimaryPushButton, ComboBox,
                                       PipsScrollButtonDisplayMode, HorizontalPipsPager,
                                       FlyoutViewBase, PushButton)

from app.common.config import VERSION, cfg, LOCAL_PATH, BETA, RELEASES_URL
from app.common.util import getLolClientPidSlowly
from app.common.signals import signalBus
from app.common.update import (candidateUrls, proxyUrl, runUpdater,
                               verifyPackage)
from app.common.logger import logger
from app.lol.connector import connector
from app.components.multi_champion_select import MultiChampionSelectWidget
from app.components.multi_lol_path_setting import PathDraggableWidget
from app.components.champion_icon_widget import RoundedLabel


class UpdateMessageBox(MessageBoxBase):
    def __init__(self, info, parent=None):
        super().__init__(parent=parent)
        self.info = info

        self.myYesButton = QPushButton(
            self.tr("更新并重启"), self.buttonGroup)
        self.myCancelButton = QPushButton(self.tr("Ok"), self.buttonGroup)
        self.manuallyButton = PrimaryPushButton(self.tr("手动下载"))

        self.titleLabel = TitleLabel()
        self.content = BodyLabel()

        self.textEdit = TextEdit()
        self.infoLabel = BodyLabel()
        self.bar = ProgressBar()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)

        self.myCancelButton.setObjectName("cancelButton")
        self.myYesButton.setObjectName('cancelButton')

        self.buttonLayout.addWidget(self.manuallyButton)
        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

        self.titleLabel.setText(self.tr("发现新版本"))
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        self.content.setText(self.tr("当前：v") + (BETA or VERSION) + self.tr(", ")
                             + self.tr("最新：v") + self.info.get("tag_name")[1:])
        self.content.setContentsMargins(8, 0, 5, 0)

        self.textEdit.setFixedWidth(int(self.width() * .6))
        self.textEdit.setFixedHeight(int(self.height() * .4))
        self.textEdit.setMarkdown(self.info.get("body"))
        self.textEdit.setReadOnly(True)

        self.infoLabel.setVisible(False)
        self.bar.setVisible(False)

        self.manuallyButton.clicked.connect(self.__onManuallyButtonClicked)
        self.myYesButton.clicked.connect(self.__onYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__onCancelButtonClicked)

        # 简单判断下打开方式
        if not os.path.exists(os.path.basename(sys.executable)):
            self.infoLabel.setVisible(True)
            self.infoLabel.setText(
                self.tr("只有发布版支持自动更新"))

            self.myYesButton.setEnabled(False)

        # 当前版本被禁用时, 不允许忽略版本
        self.myCancelButton.setEnabled(not self.info["forbidden"])

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)
        self.viewLayout.addWidget(self.textEdit)
        self.viewLayout.addWidget(self.infoLabel, alignment=Qt.AlignRight)
        self.viewLayout.addWidget(self.bar)

    @asyncSlot()
    async def __onYesButtonClicked(self):
        '''
        该函数负责
        1. 删除 `LOCAL_PATH` 中之前可能下载过的文件
        2. 重新下载新版本压缩包
        3. 解压缩，并删除压缩包
        4. 释放并运行 bat 文件，关闭自己
        5. 删除当前文件夹下的自己，并将解压好的新版本拷贝进来
        6. 重新运行自己
        '''

        # 候选地址可能有两条以上（jsDelivr / raw 互补），逐条试，
        # 哪条先拿到完整包就用哪条 —— 任何单条在国内都有连不上的可能
        urls = candidateUrls(self.info)

        # 一个地址都没有（清单只写了公告没打包）→ 退回手动下载,
        # 别让用户点一下什么都没发生
        if not urls:
            webbrowser.open(self.info.get('html_url') or RELEASES_URL)
            self.reject()
            self.rejected.emit()
            return

        expected = 0

        for asset in self.info.get('assets') or []:
            if isinstance(asset, dict) and asset.get('size'):
                try:
                    expected = int(asset['size'])
                except (TypeError, ValueError):
                    expected = 0
                break

        self.myYesButton.setEnabled(False)
        self.myCancelButton.setEnabled(False)
        self.manuallyButton.setEnabled(False)

        self.infoLabel.setVisible(True)
        self.bar.setVisible(True)
        self.bar.setValue(0)

        zipPath = f'{LOCAL_PATH}/update.zip'
        dirPath = f'{LOCAL_PATH}/temp'

        if os.path.exists(dirPath):
            shutil.rmtree(dirPath, ignore_errors=True)

        # 代理要和「检查更新」用同一套设置，否则会出现
        # 「能查到新版本、点更新却卡在 0%」
        proxy = proxyUrl() or None
        lastError = ''

        for url in urls:
            if os.path.exists(zipPath):
                os.remove(zipPath)

            host = url.split('/')[2] if '//' in url else url

            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(
                            url, proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=900)) as resp:
                        resp.raise_for_status()

                        length = int(resp.headers.get('content-length') or 0)
                        self.bar.setMaximum(max(length, 1))
                        cur = 0

                        with open(zipPath, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(1024 * 1024):
                                f.write(chunk)

                                cur += len(chunk)
                                self.bar.setValue(cur)

                                if length:
                                    self.infoLabel.setText(
                                        f"{host}  {cur / (1024*1024):.2f} / "
                                        f"{length / (1024*1024):.2f} MB "
                                        f"({cur * 100 / length:.1f}%)")
                                else:
                                    # 有些 CDN 不给 content-length，只报已下载量
                                    self.infoLabel.setText(
                                        f"{host}  已下载 {cur / (1024*1024):.2f} MB")
            except Exception as e:
                lastError = f'{host}: {e}'
                logger.warning(f'更新包下载失败 {url}: {e}', 'Update')
                continue

            good, why = verifyPackage(zipPath, expected)

            if not good:
                lastError = f'{host}: {why}'
                logger.warning(f'更新包校验失败 {url}: {why}', 'Update')
                continue

            lastError = ''
            break

        if lastError:
            # 全部失败：把按钮放回去，让用户还能选「手动下载」
            self.bar.setVisible(False)
            self.infoLabel.setText(self.tr("下载失败，试试手动下载"))
            self.manuallyButton.setEnabled(True)
            self.myCancelButton.setEnabled(True)

            logger.error(f'所有下载源都失败: {lastError}', 'Update')
            return

        self.infoLabel.setText(self.tr("下载完成，正在解压…"))

        # 发布包统一用 zip: 标准库就能解, 用户也能双击打开检查内容
        with zipfile.ZipFile(zipPath) as z:
            z.extractall(dirPath)

        os.remove(zipPath)
        self.infoLabel.setText("Waiting for restart...")

        # 覆盖自己
        runUpdater()

        # 关掉 QThread 之后退出
        signalBus.terminateListeners.emit()
        sys.exit()

    def __onCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()

    def __onManuallyButtonClicked(self):
        webbrowser.open(self.info.get('html_url') or RELEASES_URL)

        self.reject()
        self.rejected.emit()


class NoticeMessageBox(MessageBoxBase):
    def __init__(self, msg, parent=None):
        super().__init__(parent=parent)
        self.titleLabel = TitleLabel(self.tr("公告"), self)
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        textEdit = TextEdit(self)
        textEdit.setFixedWidth(int(self.width() * 0.6))
        textEdit.setFixedHeight(int(self.height() * 0.4))
        textEdit.setMarkdown(msg)
        textEdit.setReadOnly(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(textEdit)

        self.hideCancelButton()

        self.yesButton.setText(self.tr("Ok"))


class WaitingForLolMessageBox(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.myYesButton = PrimaryPushButton(
            self.tr("连接客户端"), self.buttonGroup)
        self.myCancelButton = QPushButton(
            self.tr('退出 MMO战队训练助手'), self.buttonGroup)

        self.titleLabel = TitleLabel()
        self.content = BodyLabel()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)

        self.myCancelButton.setObjectName("cancelButton")
        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

        self.titleLabel.setText(self.tr("无法读取进程列表（tasklist）"))
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        self.content.setText(
            self.tr("客户端完全启动后，请手动点「连接客户端」"))
        self.content.setContentsMargins(8, 0, 5, 0)

        self.myYesButton.clicked.connect(self.__onYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__onCancelButtonClicked)

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)

    def __onYesButtonClicked(self):
        pid = getLolClientPidSlowly()

        if pid == -1:
            return

        signalBus.lolClientStarted.emit(pid)
        self.accept()
        self.accepted.emit()

    def __onCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class ChangeClientMessageBox(MessageBoxBase):
    def __init__(self, pids, parent=None):
        super().__init__(parent)

        self.myYesButton = PrimaryPushButton(
            self.tr("重新连接"), self.buttonGroup)
        self.myCancelButton = QPushButton(
            self.tr("取消"), self.buttonGroup)

        self.pids = pids

        self.titleLabel = TitleLabel()
        self.content = BodyLabel()

        self.comboBox = ComboBox()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)
        self.myYesButton.clicked.connect(self.__onYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__onCancelButtonClicked)

        self.myCancelButton.setObjectName("cancelButton")
        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

        self.titleLabel.setText(self.tr("切换客户端"))
        self.titleLabel.setContentsMargins(5, 0, 5, 0)

        self.content.setText(
            self.tr("请选择要连接的英雄联盟客户端："))
        self.content.setContentsMargins(8, 0, 5, 0)

        # 默认选中第一项: 下面按 pid 比对时可能一个都对不上 (进程刚好退了),
        # 那时 currentIdx 未赋值, 直接 setCurrentIndex 会 UnboundLocalError
        currentIdx = 0

        for i, pid in enumerate(self.pids):
            summoner = connector.getLoginSummonerByPid(pid)

            # getLoginSummonerByPid 拿不到凭据/请求失败时返回 None,
            # 这时用占位名, 别让选择框整个建不出来
            if summoner:
                name = summoner.get("gameName") or summoner.get("displayName")
            else:
                name = None

            item = (name or self.tr("未知召唤师")) + \
                self.tr(", ") + self.tr("进程号：") + str(pid)

            if pid == connector.pid:
                item += " " + self.tr("（当前）")
                currentIdx = i

            self.comboBox.addItem(item)

        self.comboBox.setCurrentIndex(currentIdx)
        self.comboBox.setMinimumWidth(400)

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)
        self.viewLayout.addWidget(self.comboBox)

    def __onYesButtonClicked(self):
        pid = self.pids[self.comboBox.currentIndex()]
        if connector.pid != pid:
            signalBus.lolClientChanged.emit(pid)

        self.accept()
        self.accepted.emit()

    def __onCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class ExceptionMessageBox(MessageBoxBase):
    def __init__(self, title, content, parent):
        super().__init__(parent=parent)

        self.titleLabel = TitleLabel(title)
        self.textEdit = TextEdit()
        self.textEdit.setText(content)

        self.scrollArea = SmoothScrollArea()
        self.scrollWidget = QWidget()
        self.scrollLayout = QVBoxLayout()

        self.__intiWidget()
        self.__initLayout()

    def __intiWidget(self):
        self.yesButton.setText(self.tr("复制错误信息并退出"))
        self.cancelButton.setText(self.tr("退出"))

        self.textEdit.setFixedWidth(int(self.width() * .6))
        self.textEdit.setFixedHeight(int(self.height() * .4))

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.textEdit)


class MultiChampionSelectMsgBox(MessageBoxBase):
    completed = pyqtSignal(list)

    def __init__(self, champions: dict, selected: list, parent=None):
        super().__init__(parent)

        self.myYesButton = PrimaryPushButton(self.tr('OK'), self.buttonGroup)
        self.myCancelButton = QPushButton(self.tr("取消"), self.buttonGroup)

        self.titleLabel = TitleLabel(self.tr("选择英雄"))
        self.championSelectWidget = MultiChampionSelectWidget(
            champions, selected)

        self.__initWidget()
        self.__initLayout()

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.championSelectWidget)

        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)
        self.myCancelButton.setObjectName("cancelButton")

        self.myYesButton.clicked.connect(self.__myOnYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__myOnCancelButtonClicked)

        # 强迫症 TV 之这玩意左边多出来了
        self.titleLabel.setStyleSheet("padding-left: 3px")

    def __myOnYesButtonClicked(self):
        self.completed.emit(
            self.championSelectWidget.getSelectedChampionIds())

        self.accept()
        self.accepted.emit()

    def __myOnCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class MultiPathSettingMsgBox(MessageBoxBase):

    def __init__(self, paths: list, parent=None):
        super().__init__(parent)
        self.myYesButton = PrimaryPushButton(self.tr('OK'), self.buttonGroup)
        self.myCancelButton = QPushButton(self.tr("取消"), self.buttonGroup)

        self.titleLabel = TitleLabel(self.tr("设置客户端路径"))
        self.pathsWidget = PathDraggableWidget(paths)

        self.__initWidget()
        self.__initLayout()

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.pathsWidget)

        self.buttonLayout.addWidget(self.myYesButton)
        self.buttonLayout.addWidget(self.myCancelButton)

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)
        self.myCancelButton.setObjectName("cancelButton")

        self.myYesButton.clicked.connect(self.__myOnYesButtonClicked)
        self.myCancelButton.clicked.connect(self.__myOnCancelButtonClicked)

        # 强迫症 TV 之这玩意左边多出来了
        self.titleLabel.setStyleSheet("padding-left: 1px")

    def __myOnYesButtonClicked(self):
        cfg.set(cfg.lolFolder, self.pathsWidget.getCurrentPaths())

        self.accept()
        self.accepted.emit()

    def __myOnCancelButtonClicked(self):
        self.reject()
        self.rejected.emit()


class ChangeDpiMessageBox(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.myYesButton = PrimaryPushButton(
            self.tr('OK'), self.buttonGroup)

        self.titleLabel = TitleLabel(self.tr("窗口尺寸过大！"))
        self.content = BodyLabel(
            self.tr("将重置窗口大小，请重启 MMO战队训练助手。"))

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.yesButton.setVisible(False)
        self.cancelButton.setVisible(False)

        self.buttonLayout.addWidget(self.myYesButton)

        self.titleLabel.setContentsMargins(5, 0, 5, 0)
        self.content.setContentsMargins(8, 0, 5, 0)

        self.myYesButton.clicked.connect(self.__onYesButtonClicked)

    def __initLayout(self):
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.content)

    def __onYesButtonClicked(self):
        signalBus.terminateListeners.emit()
        sys.exit()
