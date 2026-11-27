"""对局信息页

从"我方 / 敌方 Tab 切换"改成"左蓝右红双列并置 + 按分路对齐"——
对位关系直接由位置表达, BP 时不用再来回点按钮。

页面结构::

    页头   对局信息 · [选人/对局中/观战] · 数据来源        排序 / 密度
    汇总条 蓝方平均胜率 ============== 红方平均胜率
    对阵区 ┌ 蓝方 5 张卡 ┐  ┌ 红方 5 张卡 ┐

数据来源有三个, 但都归一化成 `game_data.cardFromSummoner/cardFromLive` 的产物:

- 选人: LCU champ-select session
- 对局加载: LCU gameflow session
- 对局中 / 观战: 2999 Live Client Data (由 `spectator.LiveGameWatcher` 推过来)
"""

import time

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QSizePolicy, QVBoxLayout, QWidget)

from app.common.config import cfg
from app.common.design_tokens import CARD_HEIGHT, color, font, mono, sideColor
from app.common.qfluentwidgets import (ComboBox, Flyout, FlyoutViewBase,
                                       TransparentTogglePushButton)
from app.common import theme
from app.common.signals import signalBus
from app.components.page_head import PageHead
from app.components.player_card import Chip, PlayerCard, shortNumber
from app.components.seraphine_interface import SeraphineInterface
from app.lol.connector import connector
from app.lol.cn_data import cnData
from app.lol.cn_strategy import cnStrategy
from app.lol.game_data import (alignByPosition, averageWinRate, buildBadges,
                               cardFromSummoner, enrichWithPlayerMeta,
                               normalizeAlias, normalizePosition, sortCards)
from app.lol.opgg import opgg
from app.lol.player_db import TAG_PRESETS, playerDB
from app.lol.premade import detectGroups, groupsFromColors, mergeGroups
from app.lol.tools import parseSummonerOrder

PHASE_TEXT = {
    'select': '选人',
    'game': '对局中',
    'spectate': '观战',
}

PHASE_HINT = {
    'select': '数据来自客户端公开接口, 不读取游戏内存',
    'game': '实时数据来自本机 2999 接口, 约 1 秒刷新',
    'spectate': '观战数据由游戏进程提供, 存在约 3 分钟延迟',
}


class VersusBar(QWidget):
    """双方平均胜率对比条: 左蓝右红, 中线为 50%"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.blue = None
        self.red = None

        self.setFixedHeight(8)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setValues(self, blue, red):
        self.blue = blue
        self.red = red
        self.update()

    def __halfOnly(self, painter, rect, radius, blueWidth, right=False):
        """只有一方有数据：把已知的一侧画满它的半边"""
        if right:
            bar = QRectF(self.width() * 0.5, 0, self.width() * 0.5, self.height())
            tone = sideColor('red')
        else:
            bar = QRectF(0, 0, blueWidth, self.height())
            tone = sideColor('blue')

        painter.save()
        painter.setClipRect(bar)
        painter.setBrush(QColor(tone))
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        rect = QRectF(0, 0, self.width(), self.height())
        radius = self.height() / 2

        painter.setBrush(QColor(color('lineStrong')))
        painter.drawRoundedRect(rect, radius, radius)

        # BP 阶段对面是匿名的，常常只有我方有胜率。
        # 这时候按整条比例画会把「56% vs 未知」画成满条蓝，像碾压；
        # 所以只在各自那一半里画，未知的一侧留空。
        if not self.blue and not self.red:
            return

        if self.blue and self.red:
            total = self.blue + self.red
            blueWidth = self.width() * self.blue / total if total > 0 else 0
        elif self.blue:
            blueWidth = self.width() * 0.5
            self.__halfOnly(painter, rect, radius, blueWidth)
            return
        else:
            self.__halfOnly(painter, rect, radius, 0, right=True)
            return

        painter.save()
        painter.setClipRect(QRectF(0, 0, blueWidth, self.height()))
        painter.setBrush(QColor(sideColor('blue')))
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()

        painter.save()
        painter.setClipRect(
            QRectF(blueWidth, 0, self.width() - blueWidth, self.height()))
        painter.setBrush(QColor(sideColor('red')))
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()


class GameRow(QFrame):
    """战绩详情里的一行: 英雄 / 模式 / 时间 / KDA / 补刀 / 经济 / 伤害 / 评分 / 胜负

    列宽固定, 保证多行纵向对齐; 表头 `GamesHeader` 用的是同一组宽度。
    """

    ICON = 26
    COLUMNS = (58, 52, 62, 40, 46, 46, 34, 24)

    def __init__(self, game: dict, parent=None):
        super().__init__(parent)

        if game.get('remake'):
            result, resultColor = '重开', color('textMuted')
        elif game.get('win'):
            result, resultColor = '胜', color('win')
        else:
            result, resultColor = '负', color('red')

        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)

        icon = QLabel(self)
        icon.setFixedSize(self.ICON, self.ICON)
        icon.setPixmap(QPixmap(game.get('championIcon') or
                               'app/resource/images/champion-0.png').scaled(
                                   self.ICON, self.ICON, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation))

        def cell(text, width, align=Qt.AlignRight, styles=None):
            label = QLabel(str(text), self)
            label.setFixedWidth(width)
            label.setAlignment(align | Qt.AlignVCenter)
            label.setStyleSheet(styles or
                                f"QLabel {{ {font(10)} color: {color('textSub')}; }}")
            return label

        mode = cell((game.get('name') or '').replace('排位赛 ', ''), 58,
                    Qt.AlignLeft)
        time = cell(game.get('shortTime') or '', 52, Qt.AlignLeft,
                    f"QLabel {{ {font(10)} color: {color('textMuted')}; }}")

        kda = cell(f"{game.get('kills', 0)}/{game.get('deaths', 0)}/"
                   f"{game.get('assists', 0)}", 62, Qt.AlignRight,
                   f"QLabel {{ {font(10)} color: {color('text')}; }}")
        cs = cell(game.get('cs') if game.get('cs') is not None else '—', 40)
        gold = cell(shortNumber(game.get('gold')), 46)
        damage = cell(shortNumber(game.get('damage')), 46)

        score = game.get('score') or 0
        scoreColor = (color('gold') if score >= 7 else
                      color('textMuted') if score < 4.5 else color('brand'))
        scoreLabel = cell(f"{score:.1f}" if score else '—', 34, Qt.AlignRight,
                          f"QLabel {{ {font(10, True)} color: {scoreColor}; }}")

        resultLabel = cell(result, self.COLUMNS[-1], Qt.AlignCenter,
                           f"QLabel {{ {font(10, True)} color: {resultColor}; }}")

        layout.addWidget(icon)
        layout.addWidget(mode)
        layout.addWidget(time)
        layout.addWidget(kda)
        layout.addWidget(cs)
        layout.addWidget(gold)
        layout.addWidget(damage)
        layout.addWidget(scoreLabel)
        layout.addWidget(resultLabel)
        layout.addStretch(1)


class GamesHeader(QWidget):
    """战绩表的表头, 列宽与 `GameRow` 严格一致"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setFixedHeight(18)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(8)

        layout.addSpacing(GameRow.ICON)

        titles = ('模式', '时间', 'K/D/A', '补刀', '经济', '伤害', '评分', '结果')
        aligns = (Qt.AlignLeft, Qt.AlignLeft, Qt.AlignRight, Qt.AlignRight,
                  Qt.AlignRight, Qt.AlignRight, Qt.AlignRight, Qt.AlignCenter)

        for title, width, align in zip(titles, GameRow.COLUMNS, aligns):
            label = QLabel(title, self)
            label.setFixedWidth(width)
            label.setAlignment(align | Qt.AlignVCenter)
            label.setStyleSheet(
                f"QLabel {{ {font(9)} color: {color('textMuted')}; }}")
            layout.addWidget(label)

        layout.addStretch(1)


class TagEditor(QWidget):
    """备注与快捷标签

    标签/备注都写进本地 `players.json`, 只对自己可见。
    """

    def __init__(self, card: dict, onChanged=None, parent=None):
        super().__init__(parent)

        self.card = card
        self.puuid = card.get('puuid') or ''
        self.onChanged = onChanged

        self.tagsRow = QHBoxLayout()
        self.tagsRow.setContentsMargins(0, 0, 0, 0)
        self.tagsRow.setSpacing(4)

        self.presetRow = QHBoxLayout()
        self.presetRow.setContentsMargins(0, 0, 0, 0)
        self.presetRow.setSpacing(4)

        self.noteEdit = QLineEdit(self)
        self.noteEdit.setPlaceholderText('备注 (回车保存)')
        self.noteEdit.setMaxLength(120)
        self.noteEdit.setText((card.get('meta') or {}).get('note') or '')
        self.noteEdit.setFixedHeight(26)
        self.noteEdit.setStyleSheet(
            f"QLineEdit {{ {font(11)} color: {color('text')};"
            f" background: {color('chipBg')}; border: 1px solid {color('line')};"
            f" border-radius: 6px; padding: 0 6px; }}")
        self.noteEdit.returnPressed.connect(self.__saveNote)
        self.noteEdit.editingFinished.connect(self.__saveNote)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(self.tagsRow)
        layout.addLayout(self.presetRow)
        layout.addWidget(self.noteEdit)

        self.refresh()

    def refresh(self):
        self.__clear(self.tagsRow)
        self.__clear(self.presetRow)

        tags = (self.card.get('meta') or {}).get('tags') or []

        if not tags:
            hint = QLabel('未标记', self)
            hint.setStyleSheet(
                f"QLabel {{ {font(10)} color: {color('textMuted')}; }}")
            self.tagsRow.addWidget(hint)
        else:
            for tag in tags:
                self.tagsRow.addWidget(self.__button(
                    f"{tag} ×", color('accent'), lambda t=tag: self.__toggle(t)))

        self.tagsRow.addStretch(1)

        for tag in TAG_PRESETS:
            if tag in tags:
                continue

            self.presetRow.addWidget(self.__button(
                f"+ {tag}", color('textMuted'), lambda t=tag: self.__toggle(t)))

        self.presetRow.addStretch(1)

    def __button(self, text, tint, onClick):
        label = QLabel(text, self)
        label.setCursor(Qt.PointingHandCursor)
        label.setStyleSheet(
            f"QLabel {{ {font(10)} color: {tint};"
            f" background: {color('chipBg')}; border-radius: 7px;"
            f" padding: 1px 6px; }}")
        label.mouseReleaseEvent = lambda e: onClick()

        return label

    @staticmethod
    def __clear(layout):
        while layout.count():
            item = layout.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

            elif item.layout():
                TagEditor.__clear(item.layout())

    def __toggle(self, tag):
        playerDB.toggleTag(self.puuid, tag)

        enrichWithPlayerMeta(self.card)
        self.card['badges'] = buildBadges(self.card)

        self.refresh()

        if self.onChanged:
            self.onChanged()

    def __saveNote(self):
        note = self.noteEdit.text().strip()

        if note == ((self.card.get('meta') or {}).get('note') or ''):
            return

        playerDB.setNote(self.puuid, note)
        enrichWithPlayerMeta(self.card)

        if self.onChanged:
            self.onChanged()


def formatLastSeen(timestamp) -> str:
    if not timestamp:
        return ''

    days = int((time.time() - timestamp) // 86400)

    if days <= 0:
        return '今天遇过'
    if days == 1:
        return '昨天遇过'
    if days < 30:
        return f'{days} 天前遇过'

    return f'{days // 30} 个月前遇过'


class GameDetailFlyout(FlyoutViewBase):
    """点某一格战绩弹出的单场详情

    卡片上只塞得下 K/D/A, 补刀 / 经济 / 伤害 / 装备 / 符文放这里,
    否则为了"什么都看得见"会把它撑成一张表格。
    """

    def __init__(self, game: dict, parent=None):
        super().__init__(parent)

        self.setMinimumWidth(320)
        self.game = dict(game or {})

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        layout.addLayout(self.__buildHeader())
        layout.addWidget(self.__divider())
        layout.addLayout(self.__buildCombat())
        layout.addWidget(self.__divider())
        layout.addLayout(self.__buildLoadout())

    # ------------------------------------------------------------------ 构建

    def __divider(self):
        line = QFrame(self)
        line.setFixedHeight(1)
        line.setStyleSheet(
            f"QFrame {{ background-color: {color('lineStrong')}; }}")

        return line

    def __championName(self):
        # 观战 / 直播来源的卡可能带英文名, 优先用它; 否则查 id
        name = self.game.get('championName')

        if name:
            return name

        manager = getattr(connector, 'manager', None)
        championId = self.game.get('championId') or 0

        if not manager or not championId:
            return ''

        try:
            return manager.getChampionNameById(championId) or ''
        except Exception:
            return ''

    def __buildHeader(self):
        game = self.game

        row = QHBoxLayout()
        row.setSpacing(10)

        avatar = QLabel(self)
        avatar.setFixedSize(46, 46)
        avatar.setStyleSheet("QLabel { border-radius: 8px; }")

        pixmap = QPixmap(game.get('championIcon') or '')

        if not pixmap.isNull():
            avatar.setPixmap(pixmap.scaled(
                46, 46, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        row.addWidget(avatar, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(3)

        title = QLabel(self.__championName() or '未知英雄', self)
        title.setStyleSheet(
            f"QLabel {{ {font(14, True)} color: {color('text')}; }}")

        meta = QLabel(' · '.join(filter(None, [
            (game.get('name') or '').replace('排位赛 ', ''),
            game.get('shortTime'),
        ])), self)
        meta.setStyleSheet(
            f"QLabel {{ {font(10)} color: {color('textMuted')}; }}")

        if game.get('remake'):
            result, resultColor = '重开', color('textMuted')
        elif game.get('win'):
            result, resultColor = '胜利', color('win')
        else:
            result, resultColor = '失败', color('red')

        score = game.get('score') or 0
        scoreColor = (color('gold') if score >= 7 else
                      color('textMuted') if score < 4.5 else color('brand'))

        tail = QLabel(
            f"<span style='color:{resultColor}'>{result}</span>"
            + (f"<span style='color:{color('textMuted')}'> · 评分 </span>"
               f"<span style='color:{scoreColor}'>{score:.1f}</span>"
               if score else ''), self)
        tail.setTextFormat(Qt.RichText)
        tail.setStyleSheet(f"QLabel {{ {font(11, True)} }}")

        column.addWidget(title)
        column.addWidget(meta)
        column.addWidget(tail)

        row.addLayout(column, 1)

        return row

    def __buildCombat(self):
        game = self.game

        kills = game.get('kills', 0) or 0
        deaths = game.get('deaths', 0) or 0
        assists = game.get('assists', 0) or 0
        ratio = (kills + assists) / max(1, deaths)

        layout = QVBoxLayout()
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)

        caption = QLabel('击杀 / 死亡 / 助攻', self)
        caption.setStyleSheet(
            f"QLabel {{ {font(9)} color: {color('textMuted')}; }}")

        ratioLabel = QLabel(f"KDA {ratio:.1f}", self)
        ratioLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ratioLabel.setStyleSheet(
            f"QLabel {{ {font(10, True)} color: {color('brand')}; }}")

        top.addWidget(caption)
        top.addStretch(1)
        top.addWidget(ratioLabel)

        value = QLabel(f"{kills} / {deaths} / {assists}", self)
        value.setStyleSheet(
            f"QLabel {{ {font(18, True)} color: {color('text')}; }}")

        layout.addLayout(top)
        layout.addWidget(value)

        grid = QHBoxLayout()
        grid.setSpacing(20)

        stats = [
            ('补刀', str(game['cs']) if game.get('cs') is not None else '—'),
            ('经济', shortNumber(game['gold']) if game.get('gold') else '—'),
            ('伤害', shortNumber(game['damage']) if game.get('damage') else '—'),
            ('时长', game.get('duration') or '—'),
        ]

        for label, text in stats:
            cell = QVBoxLayout()
            cell.setSpacing(1)

            name = QLabel(label, self)
            name.setStyleSheet(
                f"QLabel {{ {font(9)} color: {color('textMuted')}; }}")

            number = QLabel(text, self)
            number.setStyleSheet(
                f"QLabel {{ {font(12, True)} color: {color('textSub')}; }}")

            cell.addWidget(name)
            cell.addWidget(number)
            grid.addLayout(cell)

        grid.addStretch(1)
        layout.addLayout(grid)

        return layout

    def __buildLoadout(self):
        game = self.game

        row = QHBoxLayout()
        row.setSpacing(3)

        icons = [icon for icon in (game.get('itemIcons') or []) if icon]

        for icon in icons[:7]:
            slot = QLabel(self)
            slot.setFixedSize(24, 24)
            slot.setStyleSheet("QLabel { border-radius: 4px; }")

            pixmap = QPixmap(icon)

            if not pixmap.isNull():
                slot.setPixmap(pixmap.scaled(
                    24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation))

            row.addWidget(slot)

        if not icons:
            empty = QLabel('无装备数据', self)
            empty.setStyleSheet(
                f"QLabel {{ {font(10)} color: {color('textMuted')}; }}")
            row.addWidget(empty)

        row.addStretch(1)

        for icon in (game.get('runeIcon'), game.get('spell1Icon'),
                     game.get('spell2Icon')):
            if not icon:
                continue

            label = QLabel(self)
            label.setFixedSize(18, 18)

            pixmap = QPixmap(icon)

            if not pixmap.isNull():
                label.setPixmap(pixmap.scaled(
                    18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation))

            row.addWidget(label)

        return row


class GamesFlyout(FlyoutViewBase):
    """点卡片弹出的近几场战绩 + 本地标记"""

    def __init__(self, card: dict, onChanged=None, parent=None):
        super().__init__(parent)

        self.setMinimumWidth(470)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        name = card.get('name') or '匿名玩家'
        rank = card.get('rank') or {}
        rankText = f"{rank.get('tier', '')}{rank.get('division', '')}".strip()
        winRate = card.get('winRate')

        title = QLabel(name + (f" #{card['tagLine']}" if card.get('tagLine')
                               else ''), self)
        title.setStyleSheet(f"QLabel {{ {font(13, True)} color: {color('text')}; }}")

        subtitle = QLabel(' · '.join(filter(None, [
            rankText,
            f"胜率 {winRate}%" if winRate is not None else '',
            f"KDA {card.get('kdaValue', 0):.1f}" if card.get('kdaValue') else '',
        ])), self)
        subtitle.setStyleSheet(
            f"QLabel {{ {font(11)} color: {color('textMuted')}; }}")

        layout.addWidget(title)

        if subtitle.text():
            layout.addWidget(subtitle)

        meta = card.get('meta') or {}
        lastSeen = formatLastSeen(meta.get('lastSeen'))

        if meta.get('total'):
            seen = QLabel(
                f"遇见 {meta['total']} 次 · 同队 {meta['ally']} / "
                f"对手 {meta['enemy']}" + (f" · {lastSeen}" if lastSeen else ''),
                self)
            seen.setStyleSheet(
                f"QLabel {{ {font(10)} color: {color('brand')}; }}")
            layout.addWidget(seen)

        if card.get('puuid'):
            layout.addSpacing(2)
            layout.addWidget(TagEditor(card, onChanged, self))

        layout.addSpacing(4)

        games = card.get('games') or []

        if not games:
            empty = QLabel('暂无战绩数据 (观战/对局中只显示本局)', self)
            empty.setStyleSheet(
                f"QLabel {{ {font(11)} color: {color('textMuted')}; }}")
            layout.addWidget(empty)
        else:
            layout.addWidget(GamesHeader(self))

            for game in games[:6]:
                layout.addWidget(GameRow(game, self))

        if card.get('puuid'):
            career = QLabel('查看生涯 →', self)
            career.setCursor(Qt.PointingHandCursor)
            career.setStyleSheet(
                f"QLabel {{ {font(11)} color: {color('brand')}; }}")
            career.mouseReleaseEvent = lambda e: signalBus.toCareerInterface.emit(
                card['puuid'])
            layout.addSpacing(2)
            layout.addWidget(career)


class TeamBoard(QFrame):
    """一侧队伍: 列头 + 5 张卡"""

    cardClicked = pyqtSignal(dict, object)
    gameClicked = pyqtSignal(dict, object)

    def __init__(self, side: str, title: str, parent=None):
        super().__init__(parent)

        self.side = side
        self.title = title
        self.cards = []
        self.cardsByKey = {}
        self.datas = {}
        self.keys = []
        # 由页面按可用高度统一下发, 新建的卡片要跟着当前档位
        self.density = 'full'

        self.__initWidget()
        self.__initLayout()
        self.__applyStyle()

        theme.register(self)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__applyStyle()

    def __initWidget(self):
        self.dot = QLabel(self)
        self.dot.setFixedSize(6, 6)

        self.titleLabel = QLabel(self.title, self)
        # 汇总条已经写了双方平均胜率，队伍头再写一遍是重复信息
        self.avgLabel = QLabel('', self)
        self.avgLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __initLayout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        header.setSpacing(6)
        header.addWidget(self.dot)
        header.addWidget(self.titleLabel)
        header.addStretch(1)
        header.addWidget(self.avgLabel)

        self.cardsLayout = QVBoxLayout()
        self.cardsLayout.setContentsMargins(0, 0, 0, 0)
        self.cardsLayout.setSpacing(8)

        layout.addLayout(header)
        layout.addLayout(self.cardsLayout)
        layout.addStretch(1)

    # ------------------------------------------------------------------ 数据

    def setCards(self, cards, phase='select'):
        """整列重建 (切换对局 / 换边时)"""
        keys = [c.get('key') for c in cards]

        # summonerId 缺失时每张卡的 key 都是 0，keys 看起来"没变"，
        # 走 updateCards 只会刷新 cardsByKey[0]（最后一张），其余四张
        # 停在上一局 —— 表现为"新开一局还是上一把的人"。
        keysValid = bool(keys) and 0 not in keys \
            and len(set(keys)) == len(keys)

        if keysValid and keys == self.keys and self.cards:
            self.updateCards(cards)
            return

        self.__clearCards()

        for card in cards:
            card['phase'] = phase

            widget = PlayerCard(self.side, self)
            widget.setCompact(cfg.get(cfg.gameInfoCompact))
            widget.setDensity(self.density)
            widget.setData(card)
            widget.clicked.connect(
                lambda data, w=widget: self.cardClicked.emit(data, w))
            widget.gameClicked.connect(
                lambda game, w=widget: self.gameClicked.emit(game, w))

            self.cardsLayout.addWidget(widget)

            self.cards.append(widget)
            self.cardsByKey[card.get('key')] = widget
            self.datas[card.get('key')] = card

        self.keys = keys
        self.__refreshAverage()

    def updateCards(self, cards):
        """数据没换人, 只刷新内容"""
        for card in cards:
            widget = self.cardsByKey.get(card.get('key'))

            if widget is None:
                continue

            self.datas[card.get('key')] = {**self.datas.get(card.get('key'), {}),
                                          **card}
            widget.setData(self.datas[card.get('key')])

        self.__refreshAverage()

    def mergeLive(self, liveCards, phase='game'):
        """把 2999 的实时数据并进 LCU 的卡里

        对局中仍然保留 LCU 查到的段位/胜率, 只把本局数据换上去;
        匹配顺序: 名字+编号 -> 英雄 id (唯一时)。
        """
        # 没有 LCU 数据 (例如软件在进游戏后才启动) 时直接以实时数据建卡
        if not self.datas:
            self.setCards(liveCards, phase)
            return

        byName = {}
        byChampion = {}

        for key, card in self.datas.items():
            nameKey = f"{normalizeAlias(card.get('name'))}|{card.get('tagLine') or ''}"
            byName[nameKey] = key

            championId = card.get('championId')

            if championId:
                byChampion.setdefault(championId, []).append(key)

        used = set()

        for live in liveCards:
            nameKey = (f"{normalizeAlias(live.get('name'))}|"
                       f"{live.get('tagLine') or ''}")

            key = byName.get(nameKey)

            if key is None:
                candidates = byChampion.get(live.get('championId'), [])
                candidates = [k for k in candidates if k not in used]

                if len(candidates) == 1:
                    key = candidates[0]

            if key is None or key in used:
                continue

            used.add(key)

            card = self.datas[key]
            card['live'] = live.get('live')
            card['phase'] = phase
            card['championIcon'] = live.get('championIcon') or card.get(
                'championIcon')

            # 对局中 LCU 的 selectedPosition / assignedPosition 往往是空的,
            # 分路只有 2999 给得准, 这里补上, 否则卡片显示"—"且排序失效
            if not card.get('position') and live.get('position'):
                card['position'] = live['position']

            widget = self.cardsByKey.get(key)

            if widget:
                widget.updateLive(live.get('live'), phase)
                widget.setData(card)

    def sortByPosition(self):
        """按当前分路重排 (合并到 2999 的分路后调用)"""
        cards = sortCards(list(self.datas.values()), 'order')

        if not cards:
            return

        if not all(c.get('position') for c in cards):
            # 有人没分路就不排, 免得把有分路的挤到上面、没分路的乱插
            return

        order = [c.get('key') for c in cards]

        self.reorder(order)

        # reorder 只动布局, 让 datas 的迭代顺序跟着走, 否则 dump/排查时全是旧顺序
        self.datas = {k: self.datas[k] for k in order if k in self.datas}

    def setPhase(self, phase: str):
        """整列切状态 (选人 -> 对局中 / 观战)

        没拿到实时数据的卡保持原样: 切过去只会显示成 KDA 0/0/0 + 六格空装备,
        那是在骗人, 不如继续显示选人阶段查到的信息。
        """
        for key, card in self.datas.items():
            if phase != 'select' and not card.get('live'):
                continue

            card['phase'] = phase

            widget = self.cardsByKey.get(key)

            if widget:
                widget.updateLive(card.get('live'), phase)

    def reorder(self, order):
        if not all(key in self.cardsByKey for key in order):
            return

        for i in reversed(range(self.cardsLayout.count())):
            self.cardsLayout.removeItem(self.cardsLayout.itemAt(i))

        for key in order:
            self.cardsLayout.addWidget(self.cardsByKey[key])

        self.keys = list(order)

    def applyGroups(self, groups: dict):
        """按"谁和谁一起排"上色

        @param groups: {cardKey: {'size','games','color','name','confirmed'}}

        同一组的人拿同一个颜色: 名字行的「双排」标签 + 卡片底边色条都是它。
        单排的人没有颜色, 底边保持普通描边 —— 十张卡全染色就分不出"哪条
        是成对的"了。
        """
        for key, card in self.datas.items():
            group = (groups or {}).get(key) or {}

            card['premade'] = group.get('size') or 0
            card['premadeColor'] = group.get('color') or ''
            card['premadeGames'] = group.get('games') or 0
            card['premadeConfirmed'] = bool(group.get('confirmed'))
            card['premadeLabel'] = self.__premadeLabel(group)

            card['badges'] = buildBadges(card)

            widget = self.cardsByKey.get(key)

            if widget:
                widget.setData(card)

    @staticmethod
    def __premadeLabel(group) -> str:
        """「双排」/「三排?」—— 只同队过 1 场的加问号, 别把撞车说成开黑"""
        if not group:
            return ''

        name = group.get('name') or f"{group.get('size')} 人"

        if not group.get('confirmed') and (group.get('games') or 0) < 2:
            return f'{name}?'

        return name

    def isPlaceholder(self):
        """整列都是 BP 阶段的敌方占位卡 (没有身份, 进游戏后必须重建)"""
        return bool(self.datas) and all(
            card.get('isPlaceholder') for card in self.datas.values())

    def updateChampion(self, key, championId, icon, pickState=None):
        card = self.datas.get(key)

        if card is None:
            return

        card['championId'] = championId
        card['championIcon'] = icon

        if pickState:
            card['pickState'] = pickState

        widget = self.cardsByKey.get(key)

        if widget:
            widget.setData(card)

    def setPickState(self, key, pickState):
        """英雄没变, 只是从"选择中"变成"已锁定"时, 别重复换图"""
        card = self.datas.get(key)

        if card is None or card.get('pickState') == pickState:
            return

        card['pickState'] = pickState

        widget = self.cardsByKey.get(key)

        if widget:
            widget.setData(card)

    # ------------------------------------------------------------------ 展示

    def __refreshAverage(self):
        winRate = averageWinRate(list(self.datas.values()))

        if winRate:
            self.avgLabel.setText('')
        else:
            self.avgLabel.setText('')

    def summaryWinRate(self):
        return averageWinRate(list(self.datas.values()))

    def __clearCards(self):
        for i in reversed(range(self.cardsLayout.count())):
            item = self.cardsLayout.itemAt(i)

            if item.widget():
                widget = item.widget()

                # deleteLater 要等事件循环空闲才真正销毁, 期间旧卡会和重建的
                # 新卡叠在同一位置闪一下, 所以先从布局里摘掉
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        self.cards = []
        self.cardsByKey = {}
        self.datas = {}
        self.keys = []

    def clear(self):
        self.__clearCards()
        self.avgLabel.setText('')

    def __applyStyle(self):
        self.titleLabel.setStyleSheet(
            f"QLabel {{ {font(12, True)} color: {sideColor(self.side)}; }}")
        self.avgLabel.setStyleSheet(
            f"QLabel {{ {font(10)} color: {color('textMuted')}; }}")
        self.dot.setStyleSheet(
            f"QLabel {{ background-color: {sideColor(self.side)};"
            f" border-radius: 3px; }}")


class GameInfoInterface(SeraphineInterface):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.isAram = False
        self.phase = 'select'
        self.gameTime = 0

        # LCU 的预组队编号 {summonerId: group}, 敌方匿名时是空的
        self.allyColors = {}
        self.enemyColors = {}

        # 组排色是按主题取的 (浅色下要压暗), 换主题得重算一遍再下发
        # 由 theme 统一回调 (applyTheme 里同时刷新 premade 与页面配色)

        # 与旧实现保持同名, 主窗口与 autoPick 的逻辑仍然依赖这两个字段
        self.allyChampions = {}
        self.allyOrder = []

        # 自动密度: full / standard, 由可用高度决定
        self.density = 'full'

        # championId -> 克制者列表, 避免同一个人在 BP 里被反复查
        self.counterCache = {}
        # championId -> 数据源说明 (国服 翡翠+ / OP.GG 外服), 给 tooltip 用
        self.counterLabel = {}
        # 已经查过的英雄 (含查到空结果的), 不再重复请求
        self.counterDone = set()

        self.__initWidget()
        self.__initLayout()
        self.__applyStyle()

        theme.register(self)

    # ------------------------------------------------------------------ 构建

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调

        页面自己的 qss 由库的样式管理器重刷，这里重算两处拼字符串的颜色：
        页面标题/汇总条，以及按主题分套的组排色。
        """
        self.__applyStyle()
        self.__refreshPremade()

    def __initWidget(self):
        self.container = QWidget()
        self.container.setObjectName("GameInfoContainer")

        # 设计稿 S1 页头：总览 › 对局信息 [选人中] 说明 …… 排序 / 紧凑
        # 面包屑直接并进现有 header，不再单独占一行
        self.pageHead = PageHead(
            self.tr('对局信息'), crumbs=(self.tr('总览'),), sec='',
            parent=self.container)
        self.pageHead.crumbClicked.connect(self.__onCrumb)

        self.phaseChip = Chip('选人', 'brand', self.container)
        self.hintLabel = QLabel('', self.container)

        self.sortCombo = ComboBox(self.container)
        self.sortCombo.addItem('按分路', userData='order')
        self.sortCombo.addItem('按胜率', userData='winrate')
        self.sortCombo.addItem('按 KDA', userData='kda')
        self.sortCombo.setCurrentIndex(
            max(0, self.sortCombo.findData(cfg.get(cfg.gameInfoSort))))
        self.sortCombo.currentIndexChanged.connect(self.__onSortChanged)

        self.compactButton = TransparentTogglePushButton('紧凑', self.container)
        self.compactButton.setCheckable(True)
        self.compactButton.setChecked(cfg.get(cfg.gameInfoCompact))
        self.compactButton.clicked.connect(self.__onCompactChanged)

        self.blueBoard = TeamBoard('blue', '蓝方', self.container)
        self.redBoard = TeamBoard('red', '红方', self.container)

        self.blueAvgLabel = QLabel('—', self.container)
        self.redAvgLabel = QLabel('—', self.container)
        self.versusBar = VersusBar(self.container)
        self.emptyState = self.__buildEmptyState()

        self.blueBoard.cardClicked.connect(self.__onCardClicked)
        self.redBoard.cardClicked.connect(self.__onCardClicked)
        self.blueBoard.gameClicked.connect(self.__onGameClicked)
        self.redBoard.gameClicked.connect(self.__onGameClicked)

        self.setWidget(self.container)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def __initLayout(self):
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.pageHead)
        header.addWidget(self.phaseChip)
        header.addSpacing(2)
        header.addWidget(self.hintLabel)
        header.addStretch(1)
        header.addWidget(self.sortCombo)
        header.addWidget(self.compactButton)

        self.summaryLayout = QHBoxLayout()
        self.summaryLayout.setSpacing(10)
        self.summaryLayout.addWidget(self.blueAvgLabel)
        self.summaryLayout.addWidget(self.versusBar, 1)
        self.summaryLayout.addWidget(self.redAvgLabel)

        board = QHBoxLayout()
        board.setSpacing(16)
        board.addWidget(self.blueBoard, 1)
        board.addWidget(self.redBoard, 1)

        layout.addLayout(header)
        layout.addLayout(self.summaryLayout)
        layout.addSpacing(2)
        layout.addLayout(board)
        layout.addWidget(self.emptyState, 1)
        layout.addStretch(1)
        # 空态要和尾部弹簧抢空间才垂直居中；有卡时把弹簧还给列表
        self.__bodyLayout = layout
        self.__tailStretch = layout.count() - 1

        self.__updateHeader()
        self.__updateEmptyState()

    # ------------------------------------------------------------------ 样式

    def __applyStyle(self):
        # 页面必须自己铺底色：承载页面的 PopUpAniStackedWidget 用的是系统
        # 浅色调色板，这里写 transparent 就会整屏发白（之前正是这样）
        self.setStyleSheet(
            f"GameInfoInterface {{ border: none; background: {color('bg0')}; }}")
        self.container.setStyleSheet(
            "QWidget#GameInfoContainer { background: transparent; }")

        self.hintLabel.setStyleSheet(
            f"QLabel {{ {font(10)} color: {color('textMuted')}; }}")

        self.blueAvgLabel.setStyleSheet(
            f"QLabel {{ {font(12, True)} color: {sideColor('blue')}; }}")
        self.redAvgLabel.setStyleSheet(
            f"QLabel {{ {font(12, True)} color: {sideColor('red')}; }}")

        self.versusBar.update()
        self.phaseChip.applyStyle()

    # ------------------------------------------------------------------ 状态

    def __setPhase(self, phase: str):
        if phase != self.phase:
            self.phase = phase

        self.__updateHeader()

    def __updateHeader(self):
        # 一张卡都没有时别挂在「选人」上，那是上一局的阶段，会误导
        if not (self.blueBoard.cards or self.redBoard.cards):
            self.phaseChip.setText('空闲')
            self.phaseChip.setTone('muted')
            self.hintLabel.setText('客户端已连接，等待进入选人')
            return

        self.phaseChip.setText(PHASE_TEXT.get(self.phase, '选人'))
        self.phaseChip.setTone('brand' if self.phase == 'select' else 'gold')
        self.hintLabel.setText(PHASE_HINT.get(self.phase, ''))

    def __onCrumb(self, name: str):
        """面包屑前级：总览 → 跳回总览页"""
        if name != self.tr('总览'):
            return

        window = self.window()
        target = getattr(window, 'overviewInterface', None)

        if target is not None and hasattr(window, 'switchTo'):
            window.switchTo(target)

    def __refreshSummary(self):
        blue = self.blueBoard.summaryWinRate()
        red = self.redBoard.summaryWinRate()

        if self.phase == 'select':
            self.blueAvgLabel.setText(f"蓝方 {blue}%" if blue else '蓝方 —')
            self.redAvgLabel.setText(f"红方 {red}%" if red else '红方 —')
            self.versusBar.setValues(blue, red)
        else:
            # 对局中/观战: 胜率让位给游戏时间
            minutes = int(self.gameTime) // 60
            seconds = int(self.gameTime) % 60
            self.blueAvgLabel.setText(f"对局 {minutes:02d}:{seconds:02d}")
            self.redAvgLabel.setText('')

        self.__updateEmptyState()
        self.__updateHeader()

    def __buildEmptyState(self) -> QWidget:
        """不在对局时的占位

        没有它的话，客户端连着但没进对局时这页只剩一条「蓝方 — 红方 —」，
        看着像加载失败。
        """
        box = QWidget(self.container)
        box.setObjectName('gameInfoEmpty')

        glyph = QLabel('◎', box)
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedSize(44, 44)
        glyph.setStyleSheet(
            f"color: {color('textMuted')}; border: 1px solid {color('line')}; "
            + mono(22))

        title = QLabel('当前不在对局中', box)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {color('textSub')}; " + font(15))

        hint = QLabel(
            '进入选人阶段或对局后，双方十人的段位、胜率与近 5 场战损会自动出现在这里。<br>'
            '最近一起排过的人会用同一个颜色标出来（卡片底边 + 「双排」标签）。<br>'
            '每局结束回到大厅会清空，不会残留上一把的阵容。', box)
        hint.setTextFormat(Qt.RichText)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color: {color('textMuted')}; " + font(11))

        keys = QHBoxLayout()
        keys.setContentsMargins(0, 0, 0, 0)
        keys.setSpacing(8)
        keys.addStretch(1)
        for text in ('Ctrl + 2 切到本页', 'F5 刷新'):
            chip = QLabel(text, box)
            chip.setStyleSheet(
                f"color: {color('textMuted')}; border: 1px solid {color('lineStrong')}; "
                f"padding: 2px 7px; " + mono(9))
            keys.addWidget(chip)
        keys.addStretch(1)

        layout = QVBoxLayout(box)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addStretch(1)
        layout.addWidget(glyph, 0, Qt.AlignHCenter)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(keys)
        layout.addStretch(1)
        return box

    def __updateEmptyState(self):
        """空态与主体互斥：没有一张卡时只显示空态"""
        empty = not (self.blueBoard.cards or self.redBoard.cards)
        self.emptyState.setVisible(empty)
        # 空态时让空态占满剩余高度（内容自居中），否则空态只拿到一半、整体偏上
        body = getattr(self, '_GameInfoInterface__bodyLayout', None)
        if body is not None:
            body.setStretch(self.__tailStretch, 0 if empty else 1)

        show = not empty
        for widget in (self.blueAvgLabel, self.redAvgLabel, self.blueBoard,
                       self.redBoard, self.sortCombo, self.compactButton):
            widget.setVisible(show)
        self.versusBar.setVisible(show and self.phase == 'select')

    # ------------------------------------------------------------------ 外部接口

    def updateAllySummoners(self, info):
        if not info or len(info['summoners']) > 5:
            return

        self.isAram = info.get('isAram', False)
        self.allyChampions = info['champions']
        self.allyOrder = info['order']

        cards = [cardFromSummoner(s, 'blue') for s in info['summoners']]

        self.blueBoard.setCards(self.__sortCards(cards), 'select')
        self.__setPhase('select')
        self.__refreshSummary()
        self.__refreshPremade()

    def updateEnemySummoners(self, info):
        if not info or len(info['summoners']) > 5:
            return

        cards = [cardFromSummoner(s, 'red') for s in info['summoners']]

        # 敌方既没有分路也没有段位, 唯一有意义的定位是"第几手", 补上楼层号
        for index, card in enumerate(cards):
            card['slotText'] = f'{index + 1} 楼'
            # BP 阶段的敌方卡没有任何身份字段, 进游戏后一条都匹配不上实时
            # 数据, 只能整列替换。但进游戏时 gameflow 会给真名和 puuid, 那
            # 种卡能按名字合并 —— 标成占位就白白丢掉刚查到的近几场战绩
            # (敌方"N 排"正是靠这些战绩算出来的)。
            card['isPlaceholder'] = not card.get('puuid')

        # 敌方没有胜率/分路, 按胜率排只会得到随机顺序, 固定按楼层
        self.redBoard.setCards(sortCards(cards, 'order'), 'select')
        self.__refreshSummary()
        self.__refreshPremade()

    def resetCounters(self):
        """新一局 BP: 上一局的克制结果作废 (已被 ban 的人选会变)"""
        self.counterCache = {}
        self.counterLabel = {}
        self.counterDone = set()

    async def __queryCounters(self, championId, player):
        """取「谁克制他」，返回 (克制者列表, 数据源说明)

        默认走国服（腾讯 101 数据站）。国服和外服的登场率、对位胜率不是一个
        体系，照搬韩服名单在国内排位里经常对不上号。

        分路按三级兜底：LCU 的 assignedPosition → 英雄主玩分路 → 放弃国服
        直接退回 OP.GG。敌人匿名时 LCU 不给分路，主玩分路是唯一可靠的线索。
        """
        tier = cfg.get(cfg.opggTier)

        # 数据源跟着 OP.GG 弹窗的地区下拉走：国服 -> 101，韩服/全部地区 -> OP.GG。
        # 原来这里有个独立的 counterSource 开关，两个地方容易打架，已经并掉了。
        if cfg.get(cfg.opggRegion) != 'cn':
            return (await opgg.getChampionCounters(
                cfg.get(cfg.opggRegion), championId, tier, 6)), 'OP.GG 外服'

        # 换位后 selectedPosition 才是当前位置，assignedPosition 可能还是
        # 系统最初分配的（补位辅助换去中路时会一直读到 UTILITY）
        lane = (normalizePosition(player.get('selectedPosition') or '')
                or normalizePosition(player.get('assignedPosition') or ''))

        if not lane:
            # 国服态用 101 自己的全分路索引（按登场率定主分路）；拿它去查
            # OP.GG 是不对的 —— 两边的「加里奥主玩什么」结论不一定一样，
            # 而且国服网络下 OP.GG 经常直接返回空
            if cfg.get(cfg.opggRegion) == 'cn':
                try:
                    lanes = await cnData.getChampionLanes(tier)
                except Exception:
                    lanes = {}

                positions = lanes.get(championId) or []
                lane = positions[0]['position'] if positions else ''
            else:
                try:
                    positions = await opgg.getChampionPositions(
                        cfg.get(cfg.opggRegion), championId, tier)
                except Exception:
                    positions = []

                lane = positions[0] if positions else ''

        if lane:
            try:
                raw = await cnStrategy.getChampionCounters(
                    championId, lane, tier, 6)
            except Exception:
                raw = []

            if raw:
                return raw, cnStrategy.label(tier, lane)

        # 国服没数据（新版本刚上线 / 接口抽风）再退回外服
        return (await opgg.getChampionCounters(
            cfg.get(cfg.opggRegion), championId, tier, 6)), 'OP.GG 外服'

    async def updateEnemyCounters(self, team, excluded=None):
        """给敌方已锁的英雄查"谁克制他", 结果是给对方选的应对参考

        @param team:     champ-select 的 theirTeam
        @param excluded: 本局已被 ban / 已被选的英雄 id, 会被过滤掉

        数据源可在设置页切: 默认国服 (腾讯 101), 也可选 OP.GG 外服。
        首次查询要走网络, 查过的英雄会记住 (包括查到空结果的), 不重复请求。
        """
        if not cfg.get(cfg.enableChampionCounter):
            return

        excluded = excluded or set()
        changed = False

        for player in team or []:
            championId = player.get('championId') or 0

            if not championId or championId in self.counterDone:
                continue

            self.counterDone.add(championId)

            try:
                raw, label = await self.__queryCounters(championId, player)
            except Exception:
                # 数据源挂了 / 没网都不能影响 BP 主流程
                continue

            counters = []

            for item in raw:
                if item['championId'] in excluded:
                    continue

                counters.append({
                    'championId': item['championId'],
                    'winRate': item['winRate'],
                    'icon': await connector.getChampionIcon(
                        item['championId']),
                    'name': (connector.manager.getChampionNameById(
                        item['championId'])
                        if connector.manager else ''),
                })

                if len(counters) >= 3:
                    break

            self.counterCache[championId] = counters
            self.counterLabel[championId] = label
            changed = True

        if not changed:
            return

        for key, card in self.redBoard.datas.items():
            championId = card.get('championId') or 0

            if not championId or championId not in self.counterCache:
                continue

            card['counters'] = self.counterCache[championId]
            card['counterSource'] = self.counterLabel.get(championId, '')

            widget = self.redBoard.cardsByKey.get(key)

            if widget:
                widget.setData(card)

    def updateAllySummonersOrder(self, team):
        if not self.allyOrder:
            return

        order = parseSummonerOrder(team)

        if not order or len(order) != len(self.allyOrder) or order == self.allyOrder:
            return

        self.blueBoard.reorder(order)
        self.allyOrder = order

    async def updateAllyIcon(self, team):
        """选人阶段刷新我方英雄

        已锁 (`championId`) 和悬停预选 (`championPickIntent`) 都要画出来 ——
        队友在犹豫哪个英雄是 BP 阶段最有价值的信息之一。
        """
        for new in team:
            summonerId = new.get('summonerId')

            if not summonerId:
                continue

            championId = (new.get('championId')
                          or new.get('championPickIntent') or 0)

            if not championId:
                continue

            pickState = 'locked' if new.get('championId') else 'picking'

            if self.allyChampions.get(summonerId) == championId:
                # 英雄没变, 只可能发生"悬停 -> 锁定"的状态切换
                self.blueBoard.setPickState(summonerId, pickState)
                continue

            icon = await connector.getChampionIcon(championId)
            self.allyChampions[summonerId] = championId
            self.blueBoard.updateChampion(
                summonerId, championId, icon, pickState)

    async def updateEnemyIcon(self, team):
        """刷新敌方英雄

        客户端对敌方**只下发已锁定的 championId**, 悬停中的预选恒为 0,
        所以这里没有"选择中"这个状态。
        """
        for new in team or []:
            cellId = new.get('cellId')
            championId = new.get('championId') or 0

            if cellId is None or not championId:
                continue

            key = -(cellId + 1)
            card = self.redBoard.datas.get(key)

            if card is None:
                continue

            if card.get('championId') == championId:
                self.redBoard.setPickState(key, 'locked')
                continue

            icon = await connector.getChampionIcon(championId)
            self.redBoard.updateChampion(key, championId, icon, 'locked')

    def updateTeamColor(self, allyColor, enemyColor):
        """预组队颜色 -> "双排 / 三排" 标签

        LCU 的 `teamParticipantId` 是本局真实的预组队, 比战绩反推可靠,
        所以存下来参与合并 (敌方匿名时它给的是空, 那边只能靠战绩推断)。
        """
        self.allyColors = allyColor or {}
        self.enemyColors = enemyColor or {}

        self.__refreshPremade()

    def __refreshPremade(self):
        """重算"谁和谁一起排", 给同组的人上同一个颜色

        两个来源, 各有各的边界:
        - LCU 的 teamParticipantId: 本局准确, 但只有我方, 且只在选人阶段
        - 近几场战绩: 同场且胜负相同即同队, 敌方匿名时唯一能用的线索

        两边都有时以 LCU 为准 —— 它说的是"这一把", 战绩只能说明"上一把"。
        每侧独立合并, 免得我方确认的双排把我方某人和敌方某人串成一个色号。
        """
        blueCards = list(self.blueBoard.datas.values())
        redCards = list(self.redBoard.datas.values())

        if not (blueCards or redCards):
            return

        # 全局算一次, 色号跨两侧不重复 (2+2+1 时六个人不会撞成三种颜色)
        detected = detectGroups(blueCards + redCards)

        for board, colors in ((self.blueBoard, self.allyColors),
                              (self.redBoard, self.enemyColors)):
            members = list(board.datas.values())
            keys = {card.get('key') for card in members}

            detectedSide = {key: info for key, info in detected.items()
                            if key in keys}

            board.applyGroups(mergeGroups(
                detectedSide, groupsFromColors(colors, members)))

    def applyLive(self, snapshot: dict):
        """接收 `LiveGameWatcher` 的快照

        - 自家对局: 把实时数据并进已有的 LCU 卡, 保留段位/胜率
        - 观战且此前查过 LCU: 同样并进去, 只是状态标成"观战"
        - 观战但没有任何 LCU 数据 (软件中途启动): 直接用 2999 建卡
        """
        spectating = snapshot.get('spectating', False)
        self.gameTime = snapshot.get('gameTime') or 0

        phase = 'spectate' if spectating else 'game'
        hasLcuCards = bool(self.blueBoard.datas or self.redBoard.datas)

        if spectating and not hasLcuCards:
            self.blueBoard.setCards(self.__sortCards(snapshot['blue']), phase)
            self.redBoard.setCards(self.__sortCards(snapshot['red']), phase)
        else:
            blue, red = self.__alignLiveSides(snapshot['blue'], snapshot['red'])

            # 整局换人（上一把没走 Lobby、软件中途重连、观战切换）时，
            # mergeLive 按名字/英雄一个都匹配不上，旧卡会原样留在页面上。
            # 这里先看两边有没有交集，完全没有就整列重建。
            if not spectating:
                existing = {self.__sideKey(c)
                            for c in list(self.blueBoard.datas.values())
                            + list(self.redBoard.datas.values())}
                live = {self.__sideKey(c) for c in list(blue) + list(red)}

                existing.discard('')
                live.discard('')

                if existing and live and not (existing & live):
                    self.blueBoard.setCards(self.__sortCards(blue), phase)
                    self.redBoard.setCards(sortCards(red, 'order'), phase)
                    self.blueBoard.setPhase(phase)
                    self.redBoard.setPhase(phase)
                    self.__setPhase(phase)
                    self.__refreshSummary()
                    self.__refreshPremade()
                    return

            self.blueBoard.mergeLive(blue, phase)

            # 敌方在 BP 阶段只有"楼层 + 英雄"的占位卡 (没有 puuid / 名字),
            # mergeLive 一条都匹配不上, 直接用实时数据整列重建
            if self.redBoard.isPlaceholder():
                self.redBoard.setCards(sortCards(red, 'order'), phase)
            else:
                self.redBoard.mergeLive(red, phase)

            # 分路是合并时才补上的, 这里再排一次, 否则"按分路"只是摆设
            self.blueBoard.sortByPosition()
            self.redBoard.sortByPosition()

            self.blueBoard.setPhase(phase)
            self.redBoard.setPhase(phase)

        self.__setPhase(phase)
        self.__refreshSummary()
        self.__refreshPremade()

    def clear(self):
        self.allyChampions = {}
        self.allyOrder = []
        self.isAram = False
        self.gameTime = 0
        self.allyColors = {}
        self.enemyColors = {}

        self.blueBoard.clear()
        self.redBoard.clear()

        self.phase = 'select'
        self.__updateHeader()
        self.__refreshSummary()

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def __sideKey(card) -> str:
        """判断"同一个人"用的键: 编号优先 (同一局唯一), 没编号退化成名字"""
        tag = str(card.get('tagLine') or '').strip()

        return tag or (card.get('name') or '').strip()

    def __alignLiveSides(self, blue, red):
        """判断 2999 的 ORDER/CHAOS 与 LCU 的蓝/红是否同向

        `separateTeams` 靠 summonerId 找自己, 找不到时会退化成"teamTwo 就是我方",
        而 2999 的 ORDER 是固定的 —— 两边于是整体错位, 实时数据一条都合并不上。
        这里用编号集合的交集投票, 换过来能对上更多人时才交换。
        """
        lcuBlue = {self.__sideKey(c) for c in self.blueBoard.datas.values()}
        lcuRed = {self.__sideKey(c) for c in self.redBoard.datas.values()}

        if not (lcuBlue or lcuRed):
            return blue, red

        liveBlue = {self.__sideKey(c) for c in blue}
        liveRed = {self.__sideKey(c) for c in red}

        direct = len(lcuBlue & liveBlue) + len(lcuRed & liveRed)
        swapped = len(lcuBlue & liveRed) + len(lcuRed & liveBlue)

        if swapped > direct:
            return red, blue

        return blue, red

    def __sortCards(self, cards):
        return sortCards(cards, cfg.get(cfg.gameInfoSort))

    def __onSortChanged(self, *_):
        mode = self.sortCombo.currentData()
        cfg.set(cfg.gameInfoSort, mode)

        blue = alignByPosition(list(self.blueBoard.datas.values()))
        red = alignByPosition(list(self.redBoard.datas.values()))

        if mode == 'order':
            self.blueBoard.reorder([c['key'] for c in blue])
            self.redBoard.reorder([c['key'] for c in red])
        else:
            self.blueBoard.reorder(
                [c['key'] for c in sortCards(blue, mode)])
            self.redBoard.reorder(
                [c['key'] for c in sortCards(red, mode)])

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.__applyAutoDensity()

    def __applyAutoDensity(self):
        """按可用高度选密度, 保证 5 张卡不用滚动

        三档阈值由实测得出 (页头 + 汇总条 + 4 段 8px 间距 ≈ 130):
        - 详细 834: 5×134 + 130 + 32
        - 标准 739: 去掉场均数据行
        - 再矮只能走紧凑, 否则第 5 个人会被切掉半张
        用户手动点了"紧凑"就完全让位, 不再自动改。
        """
        if not hasattr(self, 'blueBoard') or not hasattr(self, 'compactButton'):
            return

        if not cfg.get(cfg.gameInfoAutoDensity):
            return

        if self.compactButton.isChecked():
            return

        chrome = 4 * 8 + 130
        needFull = 5 * CARD_HEIGHT['normal'] + chrome
        needStandard = 5 * CARD_HEIGHT['standard'] + chrome

        if self.height() >= needFull:
            mode = 'full'
        elif self.height() >= needStandard:
            mode = 'standard'
        else:
            mode = 'compact'

        if mode == self.density:
            return

        self.density = mode

        for board in (self.blueBoard, self.redBoard):
            board.density = mode

            for card in board.cards:
                card.setDensity(mode)

    def __onCompactChanged(self, *_):
        compact = self.compactButton.isChecked()
        cfg.set(cfg.gameInfoCompact, compact)

        for board in (self.blueBoard, self.redBoard):
            for card in board.cards:
                card.setCompact(compact)

        # 从紧凑切回时, 按当前窗口高度重新算一次档位
        if not compact:
            self.__applyAutoDensity()

    def __onCardClicked(self, card: dict, target):
        # 观战卡没有战绩, 但有 puuid, 一样可以打标签
        if not card.get('games') and not card.get('puuid'):
            return

        puuid = card.get('puuid')
        view = GamesFlyout(card, lambda: self.__refreshPlayer(puuid))

        if not isinstance(target, PlayerCard):
            target = self.blueBoard

        Flyout.make(view, target, self.window(), isDeleteOnClose=True)

    def __onGameClicked(self, game: dict, target):
        """点了某一格战绩: 只弹这一场, 不去打扰整卡的战绩列表"""
        if not game:
            return

        view = GameDetailFlyout(game)

        if not isinstance(target, PlayerCard):
            target = self.blueBoard

        Flyout.make(view, target, self.window(), isDeleteOnClose=True)

    def __refreshPlayer(self, puuid):
        """标签/备注改动后, 把这个人所有的卡重刷一次"""
        if not puuid:
            return

        for board in (self.blueBoard, self.redBoard):
            for key, data in board.datas.items():
                if data.get('puuid') != puuid:
                    continue

                enrichWithPlayerMeta(data)
                data['badges'] = buildBadges(data)

                widget = board.cardsByKey.get(key)

                if widget:
                    widget.setData(data)
