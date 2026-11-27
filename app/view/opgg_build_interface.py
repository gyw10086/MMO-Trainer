from typing import List

from PyQt5.QtWidgets import (QHBoxLayout, QWidget, QFrame, QVBoxLayout, QSpacerItem,
                             QSizePolicy, QLabel, QHBoxLayout, QWidget, QLabel, QFrame,
                             QVBoxLayout, QSpacerItem, QSizePolicy, QLayout, QGridLayout)
from PyQt5.QtCore import Qt, pyqtSignal, QEasingCurve, QPointF, QRectF
from PyQt5.QtGui import (QPixmap, QColor, QCursor, QPainter, QPen,
                         QFontMetrics)
from qasync import asyncSlot

from app.lol.tools import ToolsTranslator, createAndSetRunePage
from app.components.animation_frame import (ColorAnimationFrame,
                                            NoBorderColorAnimationFrame, CardWidget)
from app.components.transparent_button import PrimaryButton
from app.components.champion_icon_widget import RoundIcon, RoundedLabel
from app.common.design_tokens import color, font, fontObject
from app.common.style_sheet import StyleSheet
from app.common.qfluentwidgets import (SmoothScrollArea, IconWidget, isDarkTheme,
                                       ToolTipFilter, ToolTipPosition, PushButton,
                                       PrimaryToolButton, FluentIcon, PillToolButton,
                                       TransparentToolButton, ThemeColor)
from app.common.icons import Icon
from app.common.config import qconfig, cfg
from app.common.signals import signalBus
from app.lol.connector import connector


class BuildInterface(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.championId = None
        self.vBoxLayout = QVBoxLayout(self)

        self.scrollArea = SmoothScrollArea()
        self.scrollWidget = QWidget()
        self.scrollLayout = QVBoxLayout()

        self.titleBar = ChampionTitleBar()
        self.summonerSpells = SummonerSpellsWidget()
        self.championSkills = ChampionSkillsWidget()
        self.championItems = ChampionItemWidget()
        self.championCounters = ChampionCountersWidget()
        self.championDuration = ChampionDurationWidget()
        self.championPerks = ChampionPerksWidget()
        self.championAugments = ChampionAugmentsWidget()
        self.championSynergies = ChampionSynergiesWidget()

        self.__initWidget()
        self.__initLayout()

        StyleSheet.OPGG_BUILD_INTERFACE.apply(self)

    def __initWidget(self):
        pass

    def __initLayout(self):
        self.scrollArea.setObjectName("scrollArea")
        self.scrollWidget.setObjectName("scrollWidget")

        self.scrollLayout.setAlignment(Qt.AlignTop)
        self.scrollWidget.setLayout(self.scrollLayout)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        # self.scrollArea.setVerticalScrollBarPolicy(
        #     Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # self.scrollArea.setViewportMargins(10, 10, 17, 10)
        self.scrollArea.setViewportMargins(0, 0, 15, 0)
        self.scrollWidget.setContentsMargins(0, 0, 0, 0)

        self.scrollLayout.addWidget(self.titleBar)
        self.scrollLayout.addWidget(self.summonerSpells)
        self.scrollLayout.addWidget(self.championPerks)
        self.scrollLayout.addWidget(self.championSkills)
        self.scrollLayout.addWidget(self.championItems)
        self.scrollLayout.addWidget(self.championCounters)
        self.scrollLayout.addWidget(self.championDuration)
        self.scrollLayout.addWidget(self.championAugments)
        self.scrollLayout.addWidget(self.championSynergies)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.scrollLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addWidget(self.scrollArea)

    def setCurrentChampionId(self, id):
        self.championId = id

    def getCurrentChampionId(self):
        return self.championId

    def updateInterface(self, data: dict):
        summary = data.get('summary')

        self.titleBar.updateWidget(summary)
        self.summonerSpells.updateWidget(data.get('summonerSpells'))
        self.championPerks.updateWidget(data.get('perks'), summary)
        self.championSkills.updateWidget(data.get('championSkills'))
        self.championItems.updateWidget(data.get('items'))
        self.championCounters.updateWidget(data.get('counters'), summary)
        self.championDuration.updateWidget(data.get('duration'))
        self.championAugments.updateWidget(data.get('augments'))
        self.championSynergies.updateWidget(data.get('synergies'))

        self.scrollArea.delegate.vScrollBar.resetValue(0)
        self.scrollArea.verticalScrollBar().setSliderPosition(0)


class ChampionTitleBar(ColorAnimationFrame):
    def __init__(self, parent: QWidget = None):
        super().__init__(type="default", parent=parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setAlignment(Qt.AlignLeft)

        self.nameLayout = QVBoxLayout()
        self.icon = RoundIcon('app/resource/images/champion-0.png', 54, 2, 3)
        self.name = QLabel()
        self.position = QLabel()

        self.winRateLayout = QVBoxLayout()
        self.winRateTextLabel = QLabel()
        self.winRateLabel = QLabel()

        self.pickRateLabel = QLabel()
        self.pickRateLayout = QVBoxLayout()
        self.pickRateTextLabel = QLabel()

        self.banRateLabel = QLabel()
        self.banRateLayout = QVBoxLayout()
        self.banRateTextLabel = QLabel()

        self.line1 = QFrame()
        self.line2 = QFrame()

        self.tierLabel = IconWidget()

        self.__initWidget()
        self.__initLayout()

        self.setVisible(False)

    def updateWidget(self, data):
        ts = ToolsTranslator()
        self.setType(f"tier{data['tier']}")

        self.icon.setIcon(data['icon'])
        self.name.setText(data['name'])

        if data['position'] != 'none':
            self.position.setText(ts.positionMap[data['position']])
            self.position.setVisible(True)
        else:
            self.position.setVisible(False)

        self.banRateTextLabel.setVisible(True)
        self.banRateLabel.setVisible(True)
        self.line2.setVisible(True)

        if firstRate := data.get('firstRate'):
            self.winRateTextLabel.setText(self.tr("第一名率"))
            self.winRateLabel.setText(f"{firstRate*100:.2f}%")

            self.pickRateTextLabel.setText(self.tr("平均名次"))
            self.pickRateLabel.setText(f"{data['averagePlace']:.2f}")

            self.banRateTextLabel.setText(self.tr("登场率"))
            self.banRateLabel.setText(f"{data['pickRate']*100:.2f}%")
        else:
            self.winRateTextLabel.setText(self.tr("胜率"))
            self.winRateLabel.setText(
                f"{data['winRate']*100:.2f}%" if data.get('winRate') else "--")

            self.pickRateTextLabel.setText(self.tr("登场率"))
            self.pickRateLabel.setText(
                f"{data['pickRate']*100:.2f}%" if data.get('pickRate') else "--")

            if banRate := data['banRate']:
                self.banRateTextLabel.setText(self.tr("禁用率"))
                self.banRateLabel.setText(f"{banRate*100:.2f}%")
            else:
                self.banRateTextLabel.setVisible(False)
                self.banRateLabel.setVisible(False)
                self.line2.setVisible(False)

        _, _, _, color = self.getColors()
        self.line1.setStyleSheet(f"color: {color.name(QColor.HexArgb)};")
        self.line2.setStyleSheet(f"color: {color.name(QColor.HexArgb)};")

        tierIcon = f"app/resource/images/icon-tier-{data['tier']}.svg"
        self.tierLabel.setIcon(tierIcon)

        self.setVisible(True)

    def __initWidget(self):
        self.name.setObjectName("titleLabel")
        self.name.setContentsMargins(0, 0, 0, 4)
        self.position.setObjectName("bodyLabel")
        self.winRateTextLabel.setObjectName("subtitleLabel")
        self.winRateLabel.setObjectName("bodyLabel")
        self.pickRateTextLabel.setObjectName("subtitleLabel")
        self.pickRateLabel.setObjectName("bodyLabel")
        self.banRateTextLabel.setObjectName("subtitleLabel")
        self.banRateLabel.setObjectName("bodyLabel")

        self.line1.setFrameShape(QFrame.Shape.VLine)
        self.line1.setLineWidth(1)
        self.line2.setFrameShape(QFrame.Shape.VLine)
        self.line2.setLineWidth(1)

        self.tierLabel.setFixedSize(30, 30)

    def __initLayout(self):
        self.nameLayout.setAlignment(Qt.AlignCenter)
        self.nameLayout.setContentsMargins(0, 0, 0, 0)
        self.nameLayout.setSpacing(0)
        self.nameLayout.addWidget(self.name)
        self.nameLayout.addWidget(self.position)

        self.winRateLayout.setAlignment(Qt.AlignCenter)
        self.winRateLayout.setSpacing(0)
        self.winRateLayout.setContentsMargins(0, 0, 0, 0)
        self.winRateLayout.addWidget(
            self.winRateTextLabel, alignment=Qt.AlignCenter)
        self.winRateLayout.addWidget(
            self.winRateLabel, alignment=Qt.AlignCenter)

        self.pickRateLayout.setAlignment(Qt.AlignCenter)
        self.pickRateLayout.setSpacing(0)
        self.pickRateLayout.setContentsMargins(0, 0, 0, 0)
        self.pickRateLayout.addWidget(
            self.pickRateTextLabel, alignment=Qt.AlignCenter)
        self.pickRateLayout.addWidget(
            self.pickRateLabel, alignment=Qt.AlignCenter)

        self.banRateLayout.setAlignment(Qt.AlignCenter)
        self.banRateLayout.setSpacing(0)
        self.banRateLayout.setContentsMargins(0, 0, 0, 0)
        self.banRateLayout.addWidget(
            self.banRateTextLabel, alignment=Qt.AlignCenter)
        self.banRateLayout.addWidget(
            self.banRateLabel, alignment=Qt.AlignCenter)

        self.hBoxLayout.addSpacing(2)
        self.hBoxLayout.addWidget(self.icon)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addLayout(self.nameLayout)

        self.hBoxLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))

        self.hBoxLayout.addLayout(self.winRateLayout)
        self.hBoxLayout.addSpacing(2)
        self.hBoxLayout.addWidget(self.line1)
        self.hBoxLayout.addSpacing(2)
        self.hBoxLayout.addLayout(self.pickRateLayout)
        self.hBoxLayout.addSpacing(2)
        self.hBoxLayout.addWidget(self.line2)
        self.hBoxLayout.addSpacing(2)
        self.hBoxLayout.addLayout(self.banRateLayout)
        self.hBoxLayout.addSpacing(15)
        self.hBoxLayout.addWidget(self.tierLabel)
        self.hBoxLayout.addSpacing(6)


class BuildWidgetBase(ColorAnimationFrame):
    def __init__(self, parent=None):
        super().__init__(type='default', parent=parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor
        self.setVisible(False)


class SeparatorLine(QFrame):
    def __init__(self, shape, parent=None):
        super().__init__(parent)
        self.setFrameShape(shape)
        self.setLineWidth(1)
        self.setObjectName("separatorLine")


class Spell(QFrame):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)

        self.iconSize = 32
        self.iconsLayout = QHBoxLayout()
        self.icon1 = RoundedLabel()
        self.icon2 = RoundedLabel()

        self.winRateLayout = QVBoxLayout()
        self.winRateLabel = QLabel()
        self.gamesLabel = QLabel()

        self.pickRateLabel = QLabel()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.icon1.setFixedSize(self.iconSize, self.iconSize)
        self.icon2.setFixedSize(self.iconSize, self.iconSize)

        self.winRateLabel.setObjectName("bodyLabel")
        self.gamesLabel.setObjectName("grayBodyLabel")
        self.pickRateLabel.setObjectName("boldBodyLabel")

        self.gamesLabel.setFixedWidth(81)
        self.gamesLabel.setAlignment(Qt.AlignCenter)
        self.pickRateLabel.setFixedWidth(43)
        self.pickRateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __initLayout(self):
        self.winRateLayout.setAlignment(Qt.AlignCenter)
        self.winRateLayout.setContentsMargins(0, 0, 0, 0)
        self.winRateLayout.setSpacing(0)

        self.winRateLayout.addWidget(
            self.winRateLabel, alignment=Qt.AlignCenter)
        self.winRateLayout.addWidget(
            self.gamesLabel, alignment=Qt.AlignCenter)

        self.iconsLayout.setContentsMargins(0, 0, 0, 0)
        self.iconsLayout.setAlignment(Qt.AlignLeft)
        self.iconsLayout.setSpacing(3)
        self.iconsLayout.addWidget(self.icon1)
        self.iconsLayout.addWidget(self.icon2)

        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addLayout(self.iconsLayout)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.hBoxLayout.addLayout(self.winRateLayout)
        self.hBoxLayout.addSpacing(22)
        self.hBoxLayout.addWidget(self.pickRateLabel)

    def updateSpell(self, data):
        self.icon1.setPicture(data['icons'][0])
        self.icon2.setPicture(data['icons'][1])

        play = data.get('play')

        if play:
            self.winRateLabel.setText(f"{data['win']/play*100:.2f}%")
            self.gamesLabel.setText(f"{play:,} " + self.tr("场次"))
        else:
            # 国服（101）只给胜率不给场次，藏掉场次那一行
            self.winRateLabel.setText(f"{(data.get('winRate') or 0)*100:.2f}%")
            self.gamesLabel.setText("")

        self.pickRateLabel.setText(f"{data['pickRate']*100:.2f}%")


class SummonerSpellsWidget(BuildWidgetBase):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent=parent)

        self.hBoxLayout = QHBoxLayout(self)

        self.spell1 = Spell()
        self.spell2 = Spell()

        self.vLine = SeparatorLine(QFrame.Shape.VLine)

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        pass

    def __initLayout(self):
        self.hBoxLayout.setContentsMargins(11, 9, 11, 9)
        self.hBoxLayout.addWidget(self.spell1)
        self.hBoxLayout.addSpacing(4)
        self.hBoxLayout.addWidget(self.vLine)
        self.hBoxLayout.addSpacing(4)
        self.hBoxLayout.addWidget(self.spell2)

    def updateWidget(self, data):
        if not data:
            self.setVisible(False)
            return

        self.spell1.updateSpell(data[0])
        self.spell2.updateSpell(data[1])

        self.setVisible(True)


class SkillIcon(QLabel):
    def __init__(self, text: str, type: str, parent=None):
        super().__init__(text, parent=parent)
        self.setProperty("name", text)
        self.setProperty("type", type)
        self.setAlignment(Qt.AlignCenter)

        if type == "main":
            self.setContentsMargins(0, 0, 0, 4)
            size = 32
        else:
            self.setContentsMargins(0, 0, 0, 0)
            size = 22

        self.setFixedSize(size, size)


class ChampionSkillsWidget(BuildWidgetBase):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent=parent)

        self.hBoxLayout = QHBoxLayout(self)

        self.skillsLayout = QVBoxLayout()
        self.mainSkillLayout = QHBoxLayout()
        self.skillOrderLayout = QHBoxLayout()

        self.gamesLayout = QVBoxLayout()
        self.winRateLabel = QLabel()
        self.gamesLabel = QLabel()

        self.pickRateLabel = QLabel()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.pickRateLabel.setObjectName("boldBodyLabel")
        self.winRateLabel.setObjectName("bodyLabel")
        self.gamesLabel.setObjectName("grayBodyLabel")

        self.gamesLabel.setFixedWidth(81)
        self.gamesLabel.setAlignment(Qt.AlignCenter)
        self.pickRateLabel.setFixedWidth(43)
        self.pickRateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __initLayout(self):
        self.mainSkillLayout.setContentsMargins(0, 0, 0, 0)
        self.mainSkillLayout.setAlignment(Qt.AlignLeft)
        self.skillOrderLayout.setContentsMargins(0, 0, 0, 0)
        self.skillOrderLayout.setAlignment(Qt.AlignLeft)
        self.skillOrderLayout.setSpacing(3)
        self.skillsLayout.setContentsMargins(0, 0, 0, 0)
        self.skillsLayout.setAlignment(Qt.AlignLeft)

        self.skillsLayout.addLayout(self.mainSkillLayout)
        self.skillsLayout.addLayout(self.skillOrderLayout)

        self.gamesLayout.setContentsMargins(0, 0, 0, 0)
        self.gamesLayout.setAlignment(Qt.AlignCenter)
        self.gamesLayout.setSpacing(0)
        self.gamesLayout.addWidget(self.winRateLabel, alignment=Qt.AlignCenter)
        self.gamesLayout.addWidget(self.gamesLabel, alignment=Qt.AlignCenter)

        self.hBoxLayout.addLayout(self.skillsLayout)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.hBoxLayout.addLayout(self.gamesLayout)
        self.hBoxLayout.addSpacing(22)
        self.hBoxLayout.addWidget(self.pickRateLabel)
        self.hBoxLayout.addSpacing(2)

    def __clearLayout(self, layout: QLayout):
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            layout.removeItem(item)

            if widget := item.widget():
                widget.deleteLater()

    def updateWidget(self, data):
        self.__clearLayout(self.mainSkillLayout)
        self.__clearLayout(self.skillOrderLayout)

        if not data:
            self.setVisible(False)
            return

        for i, skill in enumerate(data['masteries']):
            icon = SkillIcon(skill, 'main')
            self.mainSkillLayout.addWidget(icon)

            if i != len(data['masteries']) - 1:
                arrow = IconWidget(Icon.GRAYCHEVRONRIGHT)
                arrow.setFixedSize(20, 20)

                self.mainSkillLayout.addWidget(arrow)

        for skill in data['order']:
            icon = SkillIcon(skill, 'order')
            self.skillOrderLayout.addWidget(icon)

        play = data.get('play')

        if play:
            self.gamesLabel.setText(f"{play:,} " + self.tr("场次"))
            self.winRateLabel.setText(f"{data['win'] / play * 100:.2f}%")
        else:
            self.gamesLabel.setText("")
            self.winRateLabel.setText(f"{(data.get('winRate') or 0)*100:.2f}%")

        self.pickRateLabel.setText(f"{data['pickRate']*100:.2f}%")

        self.setVisible(True)


class ItemsWidget(QFrame):
    def __init__(self, data, useSeparator=False, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)
        self.iconsLayout = QHBoxLayout()

        self.iconSize = 32

        self.winRateLayout = QVBoxLayout()
        self.winRateLabel = QLabel()
        self.gamesLabel = QLabel()

        self.pickRateLabel = QLabel()

        self.useSeparator = useSeparator

        self.__initWidget()
        self.__initLayout()

        self.updateItems(data)

    def __initWidget(self):
        self.winRateLabel.setObjectName("bodyLabel")
        self.gamesLabel.setObjectName("grayBodyLabel")
        self.pickRateLabel.setObjectName("boldBodyLabel")

        self.gamesLabel.setFixedWidth(81)
        self.gamesLabel.setAlignment(Qt.AlignCenter)
        self.pickRateLabel.setFixedWidth(43)
        self.pickRateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __initLayout(self):
        self.iconsLayout.setContentsMargins(0, 0, 0, 0)
        self.iconsLayout.setAlignment(Qt.AlignLeft)
        self.iconsLayout.setSpacing(3)

        self.winRateLayout.setAlignment(Qt.AlignCenter)
        self.winRateLayout.setContentsMargins(0, 0, 0, 0)
        self.winRateLayout.setSpacing(0)

        self.winRateLayout.addWidget(
            self.winRateLabel, alignment=Qt.AlignCenter)
        self.winRateLayout.addWidget(
            self.gamesLabel, alignment=Qt.AlignCenter)

        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addLayout(self.iconsLayout)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.hBoxLayout.addLayout(self.winRateLayout)
        self.hBoxLayout.addSpacing(22)
        self.hBoxLayout.addWidget(self.pickRateLabel)

    def updateItems(self, data):
        for x, i in enumerate(data['icons']):
            icon = RoundedLabel()
            icon.setPicture(i)
            icon.setFixedSize(self.iconSize, self.iconSize)
            self.iconsLayout.addWidget(icon)

            if x != len(data['icons']) - 1 and self.useSeparator:
                arrow = IconWidget(Icon.GRAYCHEVRONRIGHT)
                arrow.setFixedSize(20, 20)

                self.iconsLayout.addSpacing(6)
                self.iconsLayout.addWidget(arrow)
                self.iconsLayout.addSpacing(6)

        play = data.get('play')

        if play:
            self.winRateLabel.setText(f"{data['win']/play*100:.2f}%")
            self.gamesLabel.setText(f"{play:,} " + self.tr("场次"))
        else:
            # 国服（101）只给胜率不给场次，藏掉场次那一行
            self.winRateLabel.setText(f"{(data.get('winRate') or 0)*100:.2f}%")
            self.gamesLabel.setText("")

        self.pickRateLabel.setText(f"{data['pickRate']*100:.2f}%")


class ChampionItemWidget(BuildWidgetBase):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)

        self.bootsAndStartLayout = QHBoxLayout()

        self.startItems = QVBoxLayout()
        self.boots = QVBoxLayout()
        self.coreItems = QVBoxLayout()
        self.lastItems = QHBoxLayout()

        self.vLine = SeparatorLine(QFrame.Shape.VLine)
        self.hLine1 = SeparatorLine(QFrame.Shape.HLine)
        self.hLine2 = SeparatorLine(QFrame.Shape.HLine)

        self.__initLayout()

    def __initLayout(self):
        self.bootsAndStartLayout.setContentsMargins(0, 0, 0, 0)
        self.bootsAndStartLayout.addLayout(self.startItems)
        self.bootsAndStartLayout.addSpacing(4)
        self.bootsAndStartLayout.addWidget(self.vLine)
        self.bootsAndStartLayout.addSpacing(4)
        self.bootsAndStartLayout.addLayout(self.boots)

        self.lastItems.setAlignment(Qt.AlignLeft)
        self.lastItems.setSpacing(3)
        self.startItems.setContentsMargins(0, 0, 0, 0)
        self.boots.setContentsMargins(0, 0, 0, 0)
        self.coreItems.setContentsMargins(0, 0, 0, 0)
        self.lastItems.setContentsMargins(0, 0, 0, 0)

        self.vBoxLayout.setContentsMargins(13, 11, 13, 11)
        self.vBoxLayout.addLayout(self.bootsAndStartLayout)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.hLine1)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addLayout(self.lastItems)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.hLine2)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addLayout(self.coreItems)

    def __updateLayout(self, layout: QLayout, data: list):
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            layout.removeItem(item)

            if widget := item.widget():
                widget.deleteLater()

        useSeparator = layout is self.coreItems

        if not layout is self.lastItems:
            for i, x in enumerate(data):
                items = ItemsWidget(x, useSeparator)
                layout.addWidget(items)
        else:
            for icon in data:
                label = RoundedLabel(icon)
                label.setFixedSize(32, 32)
                layout.addWidget(label)

    def updateWidget(self, data):
        if not data:
            self.setVisible(False)
            return

        self.__updateLayout(self.startItems, data['startItems'])
        self.__updateLayout(self.boots, data['boots'])
        self.__updateLayout(self.coreItems, data['coreItems'])
        self.__updateLayout(self.lastItems, data['lastItems'])

        self.setVisible(True)


class CounterChampionWidget(QFrame):
    def __init__(self, data, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)

        self.winRate = data['winRate']
        self.champion = ChampionWidget(data)
        self.winRateLabel = QLabel(f"{self.winRate*100:.2f}%")
        # 国服（101）的对位数据只有胜率，没有场次
        play = data.get('play')
        self.playsLabel = QLabel(
            f"{play:,} " + self.tr("场次") if play else "")

        if self.winRate > 0.5:
            self.color = min(255 * (data['winRate'] - 0.5)*16 + 40, 255)
        else:
            self.color = min(255 * (0.5 - data['winRate'])*12 + 40, 200)

        self.__initWidget()
        self.__initLayout()

        self.setFixedHeight(32)

    def __initWidget(self):
        self.winRateLabel.setObjectName("boldBodyLabel")
        self.winRateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.winRateLabel.setFixedWidth(43)
        self.winRateLabel.setContentsMargins(0, 0, 0, 2)

        self.playsLabel.setObjectName("grayBodyLabel")
        self.playsLabel.setAlignment(Qt.AlignCenter)
        self.playsLabel.setFixedWidth(81)
        self.playsLabel.setContentsMargins(0, 0, 0, 2)

        self.__setColor()
        # 必须等 qss 换完再重算颜色：themeChanged 是即时信号，那一刻库的配色
        # token 还是旧的，按它算出来的颜色会停在上一套主题上
        qconfig.themeChangedFinished.connect(self.__setColor)

    def __initLayout(self):
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addWidget(self.champion)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.hBoxLayout.addWidget(self.playsLabel)
        self.hBoxLayout.addSpacing(14)
        self.hBoxLayout.addWidget(self.winRateLabel)

    def __setColor(self):
        if self.winRate > 0.5:
            if not isDarkTheme():
                color = f"color: rgb({self.color}, 0, 0)"
            else:
                color = f"color: rgb(255, {255 - self.color}, {255 - self.color})"
        else:
            if not isDarkTheme():
                color = f"color: rgb(0, {self.color}, 0)"
            else:
                color = f"color: rgb({255 - self.color}, 255, {255 - self.color})"

        self.winRateLabel.setStyleSheet(color)


class ChampionWidget(QFrame):
    def __init__(self, data: dict, d=26, o=4, w=2, s=2, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)

        self.icon = RoundIcon(data['icon'], d, o, w)
        self.nameLabel = QLabel(data['name'])
        self.championId = data.get('championId')

        self.space = s

        self.__initWidget()
        self.__initLayout()

        # 颜色按当前主题拼，换主题要等 qss 换完再重拼（themeChanged 是即时信号）
        qconfig.themeChangedFinished.connect(self.__setDefaultLabelColor)

    def __initWidget(self):
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.nameLabel.setObjectName("bodyLabel")
        self.nameLabel.setContentsMargins(0, 0, 0, 1)

    def __initLayout(self):
        self.hBoxLayout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)

        self.hBoxLayout.addWidget(self.icon, alignment=Qt.AlignCenter)
        self.hBoxLayout.addSpacing(self.space)
        self.hBoxLayout.addWidget(self.nameLabel, alignment=Qt.AlignCenter)

    # Q: 为什么这堆东西不写到 qss 里面？
    # A: 因为不会 ^^_
    #    qss 写不了类似 ChampionWidget:hover>#bodyLabel 一类的玩意
    def __setDefaultLabelColor(self):
        color = 'white' if isDarkTheme() else 'black'
        self.nameLabel.setStyleSheet(f"QLabel {{color: {color};}}")

    def __setHoverLabelColor(self):
        color = ThemeColor.DARK_1.color()
        self.nameLabel.setStyleSheet(f"QLabel {{color: {color.name()};}}")

    def __setPressedLabelColor(self):
        color = ThemeColor.LIGHT_1.color()
        self.nameLabel.setStyleSheet(f"QLabel {{color: {color.name()};}}")

    def enterEvent(self, e):
        self.__setHoverLabelColor()
        return super().enterEvent(e)

    def leaveEvent(self, a0):
        self.__setDefaultLabelColor()
        return super().leaveEvent(a0)

    def mousePressEvent(self, a0):
        self.__setPressedLabelColor()
        return super().mousePressEvent(a0)

    def mouseReleaseEvent(self, a0):
        self.__setHoverLabelColor()
        self.__onMouseClicked()

        return super().mouseReleaseEvent(a0)

    def __onMouseClicked(self):
        if not self.championId:
            return

        signalBus.toOpggBuildInterface.emit(self.championId, "", "")


class ChampionCountersWidget(BuildWidgetBase):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)

        self.strongAgainstWidget = QWidget()
        self.strongAgainstLayout = QVBoxLayout(self.strongAgainstWidget)
        self.separatorLine = SeparatorLine(QFrame.Shape.VLine)

        self.weakAgainstWidget = QWidget()
        self.weakAgainstLayout = QVBoxLayout(self.weakAgainstWidget)

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        pass

    def __initLayout(self):
        self.strongAgainstLayout.setAlignment(Qt.AlignTop)
        self.strongAgainstLayout.setContentsMargins(0, 0, 0, 0)
        self.strongAgainstLayout.setSpacing(6)
        self.weakAgainstLayout.setAlignment(Qt.AlignTop)
        self.weakAgainstLayout.setContentsMargins(0, 0, 0, 0)
        self.weakAgainstLayout.setSpacing(6)

        self.hBoxLayout.setContentsMargins(13, 11, 13, 11)
        self.hBoxLayout.addWidget(self.strongAgainstWidget)
        self.hBoxLayout.addSpacing(4)
        self.hBoxLayout.addWidget(self.separatorLine)
        self.hBoxLayout.addSpacing(4)
        self.hBoxLayout.addWidget(self.weakAgainstWidget)

    def __updateLayout(self, layout: QLayout, data: list):
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            layout.removeItem(item)

            if widget := item.widget():
                widget.deleteLater()

        for x in data:
            item = CounterChampionWidget(x)
            layout.addWidget(item)

    def updateWidget(self, data, summary: dict):
        if not data:
            self.setVisible(False)
            return

        strong = data['strongAgainst']
        weak = data['weakAgainst']

        if len(strong) == 0 and len(weak) == 0:
            self.setVisible(False)
            return

        self.__updateLayout(self.strongAgainstLayout, strong)
        self.__updateLayout(self.weakAgainstLayout, weak)

        tooltip = f"<b>{summary.get('name')}</b>" + \
            self.tr("'s strong against")
        self.strongAgainstWidget.setToolTip(tooltip)
        self.strongAgainstWidget.installEventFilter(
            ToolTipFilter(self.strongAgainstWidget, 300))

        tooltip = f"<b>{summary.get('name')}</b>" + self.tr("'s weak against")
        self.weakAgainstWidget.setToolTip(tooltip)
        self.weakAgainstWidget.installEventFilter(
            ToolTipFilter(self.weakAgainstWidget))

        self.setVisible(True)


class DurationChart(QWidget):
    """分时段胜率柱图（自绘，6 根柱）

    以 50% 为基线：胜率 45% 画到最矮、55% 画到最高，中间线性映射、两端截断。
    柱子统一从底部往上画，50% 处补一条虚线 —— 高过线就是高过平均，比正负
    向两边长更好读，也不用处理"零轴"。
    """

    # 跌的柱子用深灰：本项目涨=黄、跌不用绿（绿在别处表示"有数据/正常"）
    BAR_LOW = '#3A4048'

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.rows = []

        self.setFixedHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setRows(self, rows):
        self.rows = list(rows or [])
        self.setToolTip(self.__toolTipText())
        self.update()

    def __formatPlay(self, play):
        """场次：上万用「万场」，否则原样带千分位"""
        return f"{play / 10000:.1f} 万场" if play >= 10000 else f"{play:,} 场"

    def __toolTipText(self):
        if not self.rows:
            return ''

        lines = [f"<b>{self.tr('游戏时长胜率')}</b>"]

        for row in self.rows:
            lines.append(
                f"{row['label']} 分钟　{row['winRate'] * 100:.2f}%"
                f"　{self.__formatPlay(row['play'])}")

        return '<br>'.join(lines)

    def paintEvent(self, event):
        if not self.rows:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 上下各留出「柱顶数值」「柱下时长」的位置；顶部留 20 是为了最高那根
        # 柱子（55% 封顶）上方还放得下一行数字，不会被窗口裁掉
        left = 4.0
        right = self.width() - 4.0
        top = 20.0
        bottom = self.height() - 18.0
        plotHeight = max(1.0, bottom - top)

        count = len(self.rows)
        gap = 10.0
        barWidth = max(8.0, (right - left - gap * (count - 1)) / count)

        valueFont = fontObject(10, 'demibold')
        labelFont = fontObject(9)

        # 先把每根柱的几何和数字位置算出来，再决定 50% 虚线怎么断
        bars = []

        for i, row in enumerate(self.rows):
            x = left + i * (barWidth + gap)

            # 45%~55% 映射到 0~1，超出直接截断
            ratio = (row['winRate'] * 100.0 - 45.0) / 10.0
            ratio = max(0.0, min(1.0, ratio))

            barHeight = max(2.0, ratio * plotHeight)

            bars.append({
                'row': row,
                'x': x,
                'y': bottom - barHeight,
                'height': barHeight,
                'text': f"{row['winRate'] * 100:.2f}",
                'textTop': max(0.0, bottom - barHeight - 14),
            })

        # 50% 基线：45%~55% 对半分，所以正好落在画布中间
        midY = top + plotHeight / 2.0

        # 数字压在虚线上会糊成一团（48% 那两根正好被中间穿过），所以按数字
        # 的**实际字宽**把虚线断开：左右各留 2px，其余区间照画。垫底色会
        # 在柱体和面板上留下色块，这里不用那个办法。
        metrics = QFontMetrics(valueFont)
        guards = []

        for bar in bars:
            half = metrics.width(bar['text']) / 2.0 + 2.0

            if bar['textTop'] - 1 <= midY <= bar['textTop'] + 14:
                centerX = bar['x'] + barWidth / 2.0
                guards.append((max(left, centerX - half),
                               min(right, centerX + half)))

        painter.setPen(QPen(QColor(color('lineStrong')), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)

        cursor = left

        for x1, x2 in sorted(guards):
            if x1 > cursor:
                painter.drawLine(QPointF(cursor, midY), QPointF(x1, midY))

            cursor = max(cursor, x2)

        if cursor < right:
            painter.drawLine(QPointF(cursor, midY), QPointF(right, midY))

        for bar in bars:
            row = bar['row']
            x, y = bar['x'], bar['y']
            up = row['winRate'] >= 0.5

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color('gold') if up else self.BAR_LOW))
            painter.drawRect(QRectF(x, y, barWidth, bar['height']))

            # 柱顶胜率：画在柱子外面（柱色会吃掉对比度），高位时往下贴一点
            painter.setFont(valueFont)
            painter.setPen(QColor(color('text') if up else color('textSub')))
            painter.drawText(QRectF(x - 6, bar['textTop'], barWidth + 12, 13),
                             Qt.AlignCenter, bar['text'])

            # 柱下时长标签
            painter.setFont(labelFont)
            painter.setPen(QColor(color('textMuted')))
            painter.drawText(QRectF(x - 6, bottom + 3, barWidth + 12, 12),
                             Qt.AlignCenter, row['label'])


class ChampionDurationWidget(BuildWidgetBase):
    """分时段胜率（101 独有）

    外服（OP.GG）没有这份数据，`updateWidget` 拿到空表就整块隐藏。
    """

    def __init__(self, parent: QWidget = None):
        super().__init__(parent=parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.headerLayout = QHBoxLayout()

        self.titleLabel = QLabel(self.tr("游戏时长胜率"))
        self.unitLabel = QLabel(self.tr("分钟"))
        self.sourceLabel = QLabel(self.tr("国服 101"))

        self.chart = DurationChart()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.__applyColor()
        qconfig.themeChangedFinished.connect(self.__applyColor)

    def __applyColor(self):
        self.titleLabel.setStyleSheet(
            f"color: {color('text')}; {font(13, bold=True)}")
        self.unitLabel.setStyleSheet(
            f"color: {color('textMuted')}; {font(11)}")
        self.sourceLabel.setStyleSheet(
            f"color: {color('textMuted')}; {font(11)}")

        # 柱子是自绘的，换主题要自己触发重画，不然会留着上一套配色
        self.chart.update()

    def __initLayout(self):
        self.headerLayout.setContentsMargins(0, 0, 0, 0)
        self.headerLayout.setSpacing(6)
        self.headerLayout.setAlignment(Qt.AlignVCenter)
        self.headerLayout.addWidget(self.titleLabel)
        self.headerLayout.addWidget(self.unitLabel)
        self.headerLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.headerLayout.addWidget(self.sourceLabel)

        self.vBoxLayout.setContentsMargins(13, 11, 13, 11)
        self.vBoxLayout.setSpacing(6)
        self.vBoxLayout.addLayout(self.headerLayout)
        self.vBoxLayout.addWidget(self.chart)

    def updateWidget(self, data):
        rows = [x for x in (data or []) if x.get('winRate')]

        if not rows:
            self.setVisible(False)
            return

        self.chart.setRows(rows)
        self.setVisible(True)


class ChampionPerksWidget(BuildWidgetBase):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)

        self.perkShowLayout = QVBoxLayout()
        self.setRuneButton = PrimaryButton(self.tr("应用符文页"))
        self.perksView = PerksWidget()

        self.vLine = SeparatorLine(QFrame.Shape.VLine)

        self.perkSelectLayout = QVBoxLayout()
        self.summaryWidgets = [PerksSummaryWidget(),
                               PerksSummaryWidget(),
                               PerksSummaryWidget()]

        self.data = None
        self.selectedIndex = 0

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        for i, widget in enumerate(self.summaryWidgets):
            widget.clicked.connect(
                lambda index=i: self.__onSummaryWidgetClicked(index))

        self.setRuneButton.clicked.connect(self.__onSetRunePageButtonClicked)

    def __initLayout(self):
        self.perkShowLayout.setContentsMargins(0, 0, 0, 0)
        self.perkShowLayout.addWidget(self.perksView)

        self.perkSelectLayout.setContentsMargins(0, 0, 0, 0)
        self.perkSelectLayout.setSpacing(4)
        for widget in self.summaryWidgets:
            self.perkSelectLayout.addWidget(widget)

        self.perkSelectLayout.addWidget(self.setRuneButton)

        self.hBoxLayout.setAlignment(Qt.AlignLeft)
        self.hBoxLayout.setContentsMargins(7, 7, 4, 7)
        self.hBoxLayout.addLayout(self.perkShowLayout)
        self.hBoxLayout.addWidget(self.vLine)
        self.hBoxLayout.addLayout(self.perkSelectLayout)

    def updateWidget(self, data: list, summary: dict):
        if not data:
            self.setVisible(False)
            return

        self.data = data
        self.summary = summary
        self.selectedIndex = 0

        self.perksView.setCurrentPerks(
            data[0]['primaryId'], data[0]['secondaryId'], data[0]['perks'],
            data[0].get('singleStats'))

        for i, (d, widget) in enumerate(zip(data, self.summaryWidgets)):
            widget.updateSummary(d)
            widget.setProperty('selected', i == 0)
            widget.style().polish(widget)

        self.setVisible(True)

    def __onSummaryWidgetClicked(self, index):
        last = self.summaryWidgets[self.selectedIndex]
        last.setProperty("selected", False)
        last.style().polish(last)

        new: PerksSummaryWidget = self.summaryWidgets[index]
        new.setProperty("selected", True)
        new.style().polish(new)

        self.selectedIndex = index
        data = self.data[index]
        self.perksView.setCurrentPerks(
            data['primaryId'], data['secondaryId'], data['perks'],
            data.get('singleStats'))

    @asyncSlot(bool)
    async def __onSetRunePageButtonClicked(self, _):
        data = self.data[self.selectedIndex]
        name = "MMO战队训练助手" + self.tr(": ") + self.summary['name']
        primaryId = data['primaryId']
        secondaryId = data['secondaryId']
        perks = data['perks']

        pages = await connector.getRunePages()

        if not len(pages):
            await createAndSetRunePage(name, primaryId, secondaryId, perks)
            return

        pageId = next((page.get("id")
                      for page in pages
                      if page.get("current") and page.get("isEditable")), None)

        if not pageId:
            await createAndSetRunePage(name, primaryId, secondaryId, perks)
            return

        await connector.putRunePage(pageId, name, primaryId, secondaryId, perks)


class PerksSummaryWidget(NoBorderColorAnimationFrame):
    def __init__(self, parent: QWidget = None):
        super().__init__(type='default', parent=parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.hBoxLayout = QHBoxLayout()
        self.runeIcon = RoundIcon(diameter=32, borderWidth=0)

        self.playsLayout = QVBoxLayout()
        self.winRateLabel = QLabel()
        self.playsLabel = QLabel()
        self.pickRateLabel = QLabel()

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.winRateLabel.setObjectName("bodyLabel")
        self.winRateLabel.setAlignment(Qt.AlignCenter)
        self.playsLabel.setObjectName("grayBodyLabel")
        self.playsLabel.setAlignment(Qt.AlignCenter)
        self.playsLabel.setFixedWidth(81)
        self.pickRateLabel.setObjectName("boldBodyLabel")
        self.pickRateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pickRateLabel.setFixedWidth(43)

    def __initLayout(self):
        self.playsLayout.setAlignment(Qt.AlignCenter)
        self.playsLayout.setContentsMargins(0, 0, 0, 0)
        self.playsLayout.setSpacing(0)
        self.playsLayout.addWidget(self.winRateLabel)
        self.playsLayout.addWidget(self.playsLabel)

        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addWidget(self.runeIcon)
        # self.hBoxLayout.addWidget(self.subIcon)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.hBoxLayout.addLayout(self.playsLayout)
        self.hBoxLayout.addSpacing(29)
        self.hBoxLayout.addWidget(self.pickRateLabel)

        self.vBoxLayout.setContentsMargins(5, 5, 8, 5)
        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.addLayout(self.hBoxLayout)

    def updateSummary(self, data):
        self.runeIcon.setIcon(data['icons'][0])

        play = data.get('play')

        if play:
            self.winRateLabel.setText(f"{data['win'] / play*100:.2f}%")
            self.playsLabel.setText(f"{play:,} " + self.tr("场次"))
        else:
            self.winRateLabel.setText(f"{(data.get('winRate') or 0)*100:.2f}%")
            self.playsLabel.setText("")

        self.pickRateLabel.setText(f"{data['pickRate']*100:.2f}%")


class PerksWidget(QFrame):
    # 单符文胜率的阈值：≥52% 黄、<49% 红，中间灰（本项目涨=黄，不用绿）
    SINGLE_GOOD = 0.52
    SINGLE_BAD = 0.49

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        # self.setStyleSheet("PerksWidget{border: 1px solid black;}")
        self.gridLayout = QGridLayout(self)

        self.iconSize = 26

        self.mainTitleIcon = RoundIcon(diameter=self.iconSize, borderWidth=0)

        self.primaryPerksLayout = QVBoxLayout()
        self.primaryPerksSlots = [
            QHBoxLayout(),
            QHBoxLayout(),
            QHBoxLayout(),
            QHBoxLayout(),
        ]

        self.secondaryTitleIcon = RoundIcon(
            diameter=self.iconSize, borderWidth=0)

        self.secondaryPerksLayout = QVBoxLayout()
        self.secondaryPerksSlots = [
            QHBoxLayout(),
            QHBoxLayout(),
            QHBoxLayout(),
        ]

        self.shardsPerksLayout = QVBoxLayout()
        self.shardsPerksSlots = [
            QHBoxLayout(),
            QHBoxLayout(),
            QHBoxLayout(),
        ]

        self.perks = {}
        # 胜率小字的索引：主副系按 runeId、碎片按 (runeId, 槽位)
        self.singleLabels = {}

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.mainTitleIcon.installEventFilter(ToolTipFilter(
            self.mainTitleIcon, 200, ToolTipPosition.TOP))
        self.secondaryTitleIcon.installEventFilter(ToolTipFilter(
            self.secondaryTitleIcon, 200, ToolTipPosition.TOP))

    def __initLayout(self):
        # 图标下方多了一行 9px 的胜率小字，槽间距从 8 收到 6，整块才不往外长
        self.primaryPerksLayout.setSpacing(6)
        self.primaryPerksLayout.setContentsMargins(0, 0, 0, 0)
        for layout in self.primaryPerksSlots:
            layout.setAlignment(Qt.AlignCenter)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            self.primaryPerksLayout.addLayout(layout)

        self.primaryPerksSlots[0].setSpacing(0)

        self.secondaryPerksLayout.setSpacing(6)
        self.secondaryPerksLayout.setContentsMargins(0, 0, 0, 0)
        for layout in self.secondaryPerksSlots:
            layout.setAlignment(Qt.AlignCenter)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            self.secondaryPerksLayout.addLayout(layout)

        self.shardsPerksLayout.setSpacing(6)
        self.shardsPerksLayout.setContentsMargins(0, 0, 0, 0)
        for layout in self.shardsPerksSlots:
            layout.setAlignment(Qt.AlignCenter)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            self.shardsPerksLayout.addLayout(layout)

        self.gridLayout.setAlignment(Qt.AlignCenter)
        self.gridLayout.setHorizontalSpacing(18)
        self.gridLayout.setContentsMargins(0, 0, 4, 4)
        self.gridLayout.addWidget(
            self.mainTitleIcon, 0, 0, alignment=Qt.AlignCenter)
        self.gridLayout.addWidget(
            self.secondaryTitleIcon, 0, 1, alignment=Qt.AlignCenter)
        self.gridLayout.addLayout(
            self.primaryPerksLayout, 1, 0, alignment=Qt.AlignCenter)
        self.gridLayout.addLayout(
            self.secondaryPerksLayout, 1, 1, alignment=Qt.AlignCenter)
        self.gridLayout.addLayout(
            self.shardsPerksLayout, 1, 2, alignment=Qt.AlignCenter)

    def __clearLayout(self, layout: QLayout):
        """递归清空一个槽位

        图标和小字被包在一个 VBox 里，只删最外层会漏掉子布局里的控件 ——
        那些控件会留在 PerksWidget 上叠着不动，所以必须递归。

        `deleteLater` 要等下一轮事件循环才真正销毁，这期间控件还挂在原位置
        可见（切符文页会看到旧数字叠在新数字上），所以先 hide + 断父再排队删。
        """
        while layout.count():
            item = layout.takeAt(0)

            if inner := item.layout():
                self.__clearLayout(inner)
            elif widget := item.widget():
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def __clearPerks(self, layouts: List[QLayout]):
        for layout in layouts:
            self.__clearLayout(layout)

    def clear(self):
        self.__clearPerks(self.primaryPerksSlots)
        self.__clearPerks(self.secondaryPerksSlots)
        self.__clearPerks(self.shardsPerksSlots)

        self.perks = {}
        self.shards = {}
        self.singleLabels = {}

    @staticmethod
    def __formatPlay(play):
        """场次：上万用「万场」，否则原样带千分位"""
        play = int(play or 0)

        return f"{play / 10000:.1f} 万场" if play >= 10000 else f"{play:,} 场"

    def __singleLabel(self, stat: dict) -> QLabel:
        """图标下方的单符文胜率小字

        9px、不带百分号（省宽度，一行 4 个图标也放得下）。颜色：≥52% 黄、
        <49% 红、其余灰。没有数据的槽位也建一个空标签占位，这样点亮时行高
        不会跳（保留高度由 retainSizeWhenHidden 负责）。
        """
        label = QLabel(f"{stat['winRate'] * 100:.1f}" if stat else '')
        label.setAlignment(Qt.AlignCenter)
        label.setFixedHeight(11)
        label.setContentsMargins(0, 0, 0, 0)

        # 隐藏时仍然占住 11px，否则点亮某个符文会把整行往上顶
        policy = label.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        label.setSizePolicy(policy)

        if not stat:
            labelColor = color('textMuted')
        elif stat['winRate'] >= self.SINGLE_GOOD:
            labelColor = color('gold')
        elif stat['winRate'] < self.SINGLE_BAD:
            labelColor = color('red')
        else:
            labelColor = color('textMuted')

        label.setStyleSheet(f"color: {labelColor}; {font(9)}")

        return label

    @staticmethod
    def __statOf(singleStats: dict, runeId, slot: int):
        """查单个符文的胜率：先按 `(runeId, 槽位)` 精确查，再退回该符文任意槽位

        属性碎片里 5008（适应之力）、5001（生命值）天然跨槽，只在 runeId 上
        存会让两行显示同一个数 —— 所以碎片查表必须带槽位。主副系符文 101
        一律下发 -1，第一查就命中；万一以后改语义，退回扫一遍也不会丢数据。
        """
        if not singleStats:
            return None

        if row := singleStats.get((runeId, slot)):
            return row

        # 槽位对不上（101 以后改语义才会出现）时取场次最大的那条，
        # 不能拿遍历到的第一条 —— 字典顺序是插入序，会看起来随机跳
        fallback = None

        for (_runeId, _slot), row in singleStats.items():
            if _runeId != runeId:
                continue

            if fallback is None or row['play'] > fallback['play']:
                fallback = row

        return fallback

    def __addPerk(self, layout: QLayout, icon: RoundIcon, perk: dict,
                  stat: dict = None, key=None, reserve: bool = True):
        """把一个符文图标放进槽位，并在下方挂一行胜率小字

        小字默认隐藏：一套符文页里 30 个图标只有 9 个是「我的」，全显示会让
        用户分不清哪条胜率对应自己选的符文。由 `setPerks` 点亮时再露出来。
        图标和标签的 tooltip 都保留 —— 没选中的符文照样能悬停看胜率。

        `reserve` 决定要不要连空标签一起建：国服符文整页都有单符文数据，必须
        建（否则点亮时行高会跳 11px）；外服（OP.GG）根本没有这份数据，建了
        只会白占 44px 高度，所以整块跳过。
        """
        tip = f"<b>{perk['name']}</b><br><br>{perk['desc']}"

        if stat:
            tip += (f"<br><br>单符文胜率 {stat['winRate'] * 100:.1f}% · "
                    f"同槽占比 {stat['pickRate'] * 100:.1f}% · "
                    f"{self.__formatPlay(stat['play'])}")

        icon.setToolTip(tip)
        icon.installEventFilter(ToolTipFilter(
            icon, 200, ToolTipPosition.TOP))

        if not reserve:
            layout.addWidget(icon)
            return

        label = self.__singleLabel(stat)
        label.setToolTip(tip)
        label.installEventFilter(ToolTipFilter(
            label, 200, ToolTipPosition.TOP))
        label.setVisible(False)

        if key is not None:
            self.singleLabels[key] = label

        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        box.addWidget(icon, alignment=Qt.AlignHCenter)
        box.addWidget(label, alignment=Qt.AlignHCenter)

        layout.addLayout(box)

    def setPerkStyle(self, main, sub, singleStats: dict = None):
        styles = connector.manager.getPerkStyles()
        singleStats = singleStats or {}

        # 整页都没有单符文数据（外服）时不留空行，块高与 2.2.17 一致
        reserve = bool(singleStats)

        main = styles[main]
        self.mainTitleIcon.setIcon(main['icon'])
        self.mainTitleIcon.setToolTip(main['name'])

        for i, slot in enumerate(main['slots'][:4]):
            for perk in slot:
                if i != 0:
                    icon = RoundIcon(
                        perk['icon'], self.iconSize, 0, 2, enabled=False)
                else:
                    icon = RoundIcon(
                        perk['icon'], self.iconSize + 12, 0, 0, enabled=False)

                self.perks[perk['runeId']] = icon
                self.__addPerk(self.primaryPerksSlots[i], icon, perk,
                               self.__statOf(singleStats, perk['runeId'], -1),
                               key=perk['runeId'], reserve=reserve)

        sub = styles[sub]
        self.secondaryTitleIcon.setIcon(sub['icon'])
        self.secondaryTitleIcon.setToolTip(sub['name'])

        for i, slot in enumerate(sub['slots'][1:4]):
            for perk in slot:
                icon = RoundIcon(
                    perk['icon'], self.iconSize, 0, 2, enabled=False)

                self.perks[perk['runeId']] = icon
                self.__addPerk(self.secondaryPerksSlots[i], icon, perk,
                               self.__statOf(singleStats, perk['runeId'], -1),
                               key=perk['runeId'], reserve=reserve)

        for i, slot in enumerate(main['slots'][4:]):
            for perk in slot:
                icon = RoundIcon(perk['icon'], self.iconSize-4,
                                 0, 0, drawBackground=True, enabled=False)

                self.shards[(perk['runeId'], i)] = icon
                self.__addPerk(self.shardsPerksSlots[i], icon, perk,
                               # 碎片槽位是 1-based，与 101 下发的第 7 位对齐
                               self.__statOf(singleStats, perk['runeId'], i + 1),
                               key=(perk['runeId'], i), reserve=reserve)

    def setPerks(self, perks):
        for id in perks[:-3]:
            if icon := self.perks.get(id):
                icon.setEnabeld(True)

            # 只有选中的那个符文才露出胜率小字
            if label := self.singleLabels.get(id):
                label.setVisible(True)

        for (i, id) in enumerate(perks[-3:]):
            if icon := self.shards.get((id, i)):
                icon.setEnabeld(True)

            if label := self.singleLabels.get((id, i)):
                label.setVisible(True)

    def setCurrentPerks(self, primaryId, secondaryId, perks,
                        singleStats: dict = None):
        self.clear()

        self.setPerkStyle(primaryId, secondaryId, singleStats)
        self.setPerks(perks)


class ChampionAugmentsWidget(BuildWidgetBase):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)
        self.augmentsLayouts = [QVBoxLayout() for _ in range(3)]

        self.vLine1 = SeparatorLine(QFrame.Shape.VLine)
        self.vLine2 = SeparatorLine(QFrame.Shape.VLine)

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        pass

    def __initLayout(self):
        for layout in self.augmentsLayouts:
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(Qt.AlignTop)
            layout.setSpacing(12)

        self.hBoxLayout.setSpacing(12)
        self.hBoxLayout.setContentsMargins(13, 11, 13, 11)
        self.hBoxLayout.addLayout(self.augmentsLayouts[0])
        self.hBoxLayout.addWidget(self.vLine1)
        self.hBoxLayout.addLayout(self.augmentsLayouts[1])
        self.hBoxLayout.addWidget(self.vLine2)
        self.hBoxLayout.addLayout(self.augmentsLayouts[2])

    def __clearLayouts(self):
        for layout in self.augmentsLayouts:
            for i in reversed(range(layout.count())):
                item = layout.itemAt(i)
                layout.removeItem(item)

                if widget := item.widget():
                    widget.deleteLater()

    def updateWidget(self, data):
        if data == None:
            self.setVisible(False)
            return

        self.__clearLayouts()
        for i, (data, layout) in enumerate(zip(data, self.augmentsLayouts)):
            for augment in data:
                item = AugmentItemBar(augment, i)
                layout.addWidget(item)

        self.setVisible(True)


class AugmentItemBar(QFrame):
    def __init__(self, data, color, parent: QWidget = None):
        super().__init__(parent)

        if color == 0:
            borderColor = QColor(153, 176, 180)
        elif color == 1:
            borderColor = QColor(194, 172, 141)
        else:
            borderColor = QColor(184, 123, 255)

        self.hBoxLayout = QHBoxLayout(self)
        self.icon = RoundedLabel(
            data['icon'], 4, 6, borderColor, True)

        self.nameLayout = QVBoxLayout()
        self.name = QLabel(data['name'])

        self.firstRateLayout = QHBoxLayout()
        self.firstRateText = QLabel(f"{data['play']:,} "+self.tr("场次"))
        self.firstRate = QLabel(
            f"{data['firstPlace'] / data['play'] * 100:.2f}%")

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.icon.setFixedSize(32, 32)
        self.name.setAlignment(Qt.AlignCenter)
        self.name.setObjectName("bodyLabel")

        self.firstRateText.setObjectName("grayBodyLabel")
        self.firstRate.setObjectName("boldBodyLabel")

        self.firstRate.setToolTip(self.tr("第一名率"))
        self.firstRate.installEventFilter(ToolTipFilter(self.firstRate, 100))

    def __initLayout(self):
        self.firstRateLayout.setContentsMargins(0, 0, 0, 0)
        self.firstRateLayout.setAlignment(Qt.AlignLeft)
        self.firstRateLayout.setSpacing(0)
        self.firstRateLayout.addWidget(self.firstRateText)
        self.firstRateLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.firstRateLayout.addWidget(self.firstRate)

        self.nameLayout.setContentsMargins(0, 0, 0, 0)
        self.nameLayout.setSpacing(0)
        self.nameLayout.setAlignment(Qt.AlignCenter)
        self.nameLayout.addWidget(self.name, alignment=Qt.AlignLeft)
        self.nameLayout.addLayout(self.firstRateLayout)

        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addWidget(self.icon)
        self.hBoxLayout.addLayout(self.nameLayout)


class ChampionSynergiesWidget(BuildWidgetBase):
    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        pass

    def __initLayout(self):
        self.vBoxLayout.setAlignment(Qt.AlignTop)
        self.vBoxLayout.setContentsMargins(13, 11, 13, 11)
        self.vBoxLayout.setSpacing(8)

    def __clearLayout(self):
        for i in reversed(range(self.vBoxLayout.count())):
            item = self.vBoxLayout.itemAt(i)
            self.vBoxLayout.removeItem(item)

            if widget := item.widget():
                widget.deleteLater()

    def updateWidget(self, data):
        if not data:
            self.setVisible(False)
            return

        self.__clearLayout()

        for x in data:
            item = SynergyItemWidget(x)
            self.vBoxLayout.addWidget(item)

        self.setVisible(True)


class SynergyItemWidget(QFrame):
    def __init__(self, data, parent: QWidget = None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)
        self.champion = ChampionWidget(data, 32, 4, 3, 5)

        self.averagePlaceLayout = QVBoxLayout()
        self.averagePlaceTextLabel = QLabel(self.tr("平均名次"))
        self.averagePlaceLabel = QLabel(
            f"{data['totalPlace'] / data['play']:.2f}")

        self.firstRateLayout = QVBoxLayout()
        self.firstRateTextLabel = QLabel(self.tr("第一名率"))
        self.firstRateLabel = QLabel(
            f"{data['firstPlace'] / data['play'] * 100:.2f}%")

        self.winRateLayout = QVBoxLayout()
        self.winRateLabel = QLabel(
            f"{data['win'] / data['play'] * 100:.2f}%")
        self.playLabel = QLabel(f"{data['play']:,} " + self.tr("场次"))

        self.pickRateLabel = QLabel(f"{data['pickRate']*100:.2f}%")

        self.__initWidget()
        self.__initLayout()

    def __initWidget(self):
        self.averagePlaceLabel.setObjectName("bodyLabel")
        self.averagePlaceTextLabel.setObjectName("grayBodyLabel")
        self.averagePlaceTextLabel.setFixedWidth(81)
        self.averagePlaceTextLabel.setAlignment(Qt.AlignCenter)

        self.firstRateLabel.setObjectName("bodyLabel")
        self.firstRateTextLabel.setObjectName("grayBodyLabel")
        self.firstRateTextLabel.setFixedWidth(81)
        self.firstRateTextLabel.setAlignment(Qt.AlignCenter)

        self.winRateLabel.setObjectName("bodyLabel")
        self.playLabel.setObjectName("grayBodyLabel")
        self.playLabel.setFixedWidth(81)
        self.playLabel.setAlignment(Qt.AlignCenter)

        self.pickRateLabel.setObjectName("boldBodyLabel")
        self.pickRateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pickRateLabel.setFixedWidth(43)

    def __initLayout(self):
        self.averagePlaceLayout.setContentsMargins(0, 0, 0, 0)
        self.averagePlaceLayout.setSpacing(0)
        self.averagePlaceLayout.setAlignment(Qt.AlignCenter)
        self.averagePlaceLayout.addWidget(
            self.averagePlaceLabel, alignment=Qt.AlignCenter)
        self.averagePlaceLayout.addWidget(
            self.averagePlaceTextLabel, alignment=Qt.AlignCenter)

        self.firstRateLayout.setContentsMargins(0, 0, 0, 0)
        self.firstRateLayout.setSpacing(0)
        self.firstRateLayout.setAlignment(Qt.AlignCenter)
        self.firstRateLayout.addWidget(
            self.firstRateLabel, alignment=Qt.AlignCenter)
        self.firstRateLayout.addWidget(
            self.firstRateTextLabel, alignment=Qt.AlignCenter)

        self.winRateLayout.setContentsMargins(0, 0, 0, 0)
        self.winRateLayout.setSpacing(0)
        self.winRateLayout.setAlignment(Qt.AlignCenter)
        self.winRateLayout.addWidget(
            self.winRateLabel, alignment=Qt.AlignCenter)
        self.winRateLayout.addWidget(self.playLabel, alignment=Qt.AlignCenter)

        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addWidget(self.champion)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed))
        self.hBoxLayout.addLayout(self.averagePlaceLayout)
        self.hBoxLayout.addSpacing(2)
        self.hBoxLayout.addLayout(self.firstRateLayout)
        # self.hBoxLayout.addSpacing(22)
        self.hBoxLayout.addLayout(self.winRateLayout)
        self.hBoxLayout.addSpacing(21)
        self.hBoxLayout.addWidget(self.pickRateLabel)
