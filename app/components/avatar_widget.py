from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QPainter, QImage, QBrush, QColor

from ..common.design_tokens import color
from ..common.qfluentwidgets import NavigationWidget, isDarkTheme


class NavigationAvatarWidget(NavigationWidget):
    """导航底部的账号卡：头像 + 名字，点一下启动客户端 / 进设置

    qfluentwidgets 原版的 paintEvent 只画头像和文字，不铺底色，
    而后备缓冲是浅灰的 —— 这块在深色导航里就成了一条 #202020 的空白条。
    这里显式填 bg0，并补 setAvatar / setName 触发重绘。
    """

    HEIGHT = 40

    def __init__(self, avatar: str, name: str, parent=None):
        super().__init__(isSelectable=False, parent=parent)

        self.name = name
        self.avatar = QImage(avatar).scaled(
            24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setFixedHeight(self.HEIGHT)

    def setAvatar(self, avatar: str):
        self.avatar = QImage(avatar).scaled(
            24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.update()

    def setName(self, name: str):
        self.name = name
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.SmoothPixmapTransform | QPainter.Antialiasing)

        # 先铺不透明底，挡住后备缓冲的浅色
        painter.fillRect(self.rect(), QColor(color('bg0')))
        painter.setPen(Qt.NoPen)

        if self.isPressed:
            painter.setOpacity(0.7)

        # draw background
        if self.isEnter:
            c = 255 if isDarkTheme() else 0
            painter.setBrush(QColor(c, c, c, 10))
            painter.drawRoundedRect(self.rect(), 5, 5)

        # draw avatar
        painter.setBrush(QBrush(self.avatar))
        painter.translate(8, 6)
        painter.drawEllipse(0, 0, 24, 24)
        painter.translate(-8, -6)

        if not self.isCompacted:
            painter.setPen(QColor(color('textSub')))
            painter.drawText(QRect(44, -1, 255, 36),
                             Qt.AlignVCenter, self.name)
            self.setStyleSheet(
                "NavigationWidget{font: 14px 'Segoe UI', 'Microsoft YaHei'}"
            )
