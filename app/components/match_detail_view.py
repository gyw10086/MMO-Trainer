# coding:utf-8
"""单场对局详情（设计稿 S10）

原来战绩查询页里有一份 400 行的 `GameDetailView`（标题条 + 队伍块 + 玩家行），
对局页另写了一份 30 行的 `GameDetailFlyout`，字段各写一遍。这里收成一份：

    标题条    结果方块 · 模式 · 时长 / 时间 / 对局 ID · 复制    （右侧留操作位给浮层放 上一场 / 下一场）
    队伍块 ×2  队伍条（塔 / 水晶 / 男爵 / 龙 / 先锋 · KDA / 经济 / 伤害 / 禁用）+ 5 行玩家
    本场之最  最高伤害 / 最高经济 / 最高参团 / 最高 KDA —— 自算，LCU 不给 MVP

字段全部来自 `parseGameDetailData()`，没有额外请求；
参团率与队伍伤害是前端按已有字段加出来的，界面上标「自算」。
"""
import pyperclip
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QStackedWidget,
                             QVBoxLayout, QWidget)

from app.common.design_tokens import SIDE_COLOR, color, font, mono
from app.common.icons import Icon
from app.common.qfluentwidgets import (IndeterminateProgressRing, InfoBar,
                                       InfoBarPosition, SmoothScrollArea,
                                       ToolButton, ToolTipFilter, ToolTipPosition)
from app.common.signals import signalBus
from app.components.rank_card import _APEX, _SHORT, tierColor
from app.components.states import ErrorState
from app.components.summoner_name_button import SummonerName

# 窗口宽度断点：行的固定列加起来约 780，再窄就得收列
#   0 宽裕      全列
#   1 < 800     收补刀
#   2 < 700     再收经济，名字列变窄，伤害条变短
COMPACT_WIDTHS = (800, 700)

_ROMAN_STR = {'I': 'Ⅰ', 'II': 'Ⅱ', 'III': 'Ⅲ', 'IV': 'Ⅳ'}


def fmtK(n) -> str:
    """12345 -> '12.3k'，1000 -> '1.0k'，不足一千原样"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return '--'
    return f'{n / 1000:.1f}k' if n >= 1000 else str(n)


def rankText(s: dict) -> str:
    """'钻石 Ⅱ · 78 LP'；大段位没有分段；未定级 / 关闭段位显示时返回空串"""
    tier = (s.get('tier') or '').strip()
    if not s.get('rankInfo'):
        return ''
    if s.get('subteamPlacement') is not None:
        # 斗魂竞技场没有段位，LCU 给的是一个竞技场分
        return f"{s.get('lp', '--')} 竞技场分" if s.get('lp') else ''
    if not tier or tier == '--':
        return '未定级'
    short = _SHORT.get(tier, tier)
    if short in _APEX:
        return f'{short} · {s.get("lp", 0)} LP'
    division = _ROMAN_STR.get(s.get('division') or '', s.get('division') or '')
    return f'{short} {division} · {s.get("lp", 0)} LP'.replace('  ', ' ')


class SquareIcon(QLabel):
    """方角图标格：有机就填图，没图就是一格空槽（1px 边框）"""

    def __init__(self, path: str = '', size: int = 18, parent=None):
        super().__init__(parent)
        self.setObjectName('squareIcon')
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setPath(path)

    def setPath(self, path: str):
        self.__path = path or ''
        pm = QPixmap(self.__path)
        if pm.isNull():
            self.setProperty('empty', True)
            self.clear()
        else:
            self.setProperty('empty', False)
            self.setPixmap(pm.scaled(self.size() * self.devicePixelRatioF(),
                                     Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.style().unpolish(self)
        self.style().polish(self)


class MatchHead(QFrame):
    """标题条：结果方块 + 模式 + 时长/时间/对局 ID；右侧 `actionLayout` 留给宿主放按钮"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('matchHead')
        self.__gameId = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)

        self.__result = QLabel('—', self)
        self.__result.setObjectName('matchResult')
        self.__result.setFixedSize(46, 46)
        self.__result.setAlignment(Qt.AlignCenter)
        self.__result.setStyleSheet(mono(15, bold=True))

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(4)

        self.__title = QLabel('', self)
        self.__title.setObjectName('matchTitle')

        sub = QHBoxLayout()
        sub.setContentsMargins(0, 0, 0, 0)
        sub.setSpacing(8)
        self.__duration = QLabel('', self)
        self.__time = QLabel('', self)
        self.__gameIdLabel = QLabel('', self)
        for label in (self.__duration, self.__time, self.__gameIdLabel):
            label.setObjectName('matchSub')
            sub.addWidget(label)

        copy = ToolButton(Icon.COPY, self)
        copy.setFixedSize(20, 20)
        copy.setToolTip('复制对局 ID')
        copy.installEventFilter(ToolTipFilter(copy, 500, ToolTipPosition.TOP))
        copy.clicked.connect(self.__copyGameId)
        sub.addWidget(copy)

        main.addWidget(self.__title)
        main.addLayout(sub)

        self.actionLayout = QHBoxLayout()
        self.actionLayout.setContentsMargins(0, 0, 0, 0)
        self.actionLayout.setSpacing(8)

        layout.addWidget(self.__result)
        layout.addLayout(main)
        layout.addStretch(1)
        layout.addLayout(self.actionLayout)

    def __copyGameId(self):
        if self.__gameId is not None:
            pyperclip.copy(str(self.__gameId))

    def updateHead(self, game: dict):
        remake = game.get('remake')
        win = game.get('win')
        placement = game.get('cherryResult')

        if placement:
            text, kind = f'{placement}', 'neutral'
        elif remake:
            text, kind = '重开', 'neutral'
        elif win is None:
            # 被查的人不在这场里（看好友单排的对局）—— 胜负未知，
            # 不能落进下面的 else 判成「负」
            text, kind = '—', 'neutral'
        elif win:
            text, kind = '胜', 'win'
        else:
            text, kind = '负', 'lose'

        base = {'win': color('win'), 'lose': color('red')}.get(kind, color('textSub'))
        bg = 'transparent' if kind == 'neutral' else base + '1A'
        self.__result.setText(text)
        self.__result.setToolTip(f'第 {placement} 名' if placement else '')
        self.__result.setStyleSheet(
            f"QLabel {{ color: {base}; background: {bg};"
            f"  border: 1px solid {base}{'66' if kind != 'neutral' else ''}; {mono(15, bold=True)} }}")

        self.__gameId = game.get('gameId')
        self.__title.setText(f"{game.get('mapName', '')} · {game.get('modeName', '')}")
        self.__duration.setText(game.get('gameDuration', '--:--'))
        self.__time.setText(game.get('gameCreation', ''))
        self.__gameIdLabel.setText(f"对局 ID {self.__gameId}")


class TeamBar(QWidget):
    """队伍条：阵营 · 结果 · 五项资源 ── KDA / 经济 / 伤害 / 禁用"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('teamBar')
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        self.__side = QLabel('', self)
        self.__side.setObjectName('teamSide')
        self.__side.setFixedWidth(34)
        self.__side.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.__result = QLabel('', self)
        self.__result.setObjectName('teamResult')
        self.__result.setFixedWidth(38)
        self.__result.setAlignment(Qt.AlignCenter)
        self.__result.setStyleSheet(mono(10))
        layout.addWidget(self.__side)
        layout.addWidget(self.__result)

        self.__resources = {}
        for key, label in (('towerKills', '塔'), ('inhibitorKills', '水晶'),
                           ('baronKills', '男爵'), ('dragonKills', '龙')):
            layout.addWidget(self.__kv(label, key))

        # 斗魂竞技场没有先锋 / 虚空幼体，这一格按需隐藏
        self.__herald = self.__kv('先锋', 'riftHeraldKills')
        layout.addWidget(self.__herald)

        layout.addStretch(1)

        self.__kda = QLabel('', self)
        self.__kda.setObjectName('teamStat')
        layout.addWidget(self.__kda)

        self.__gold = self.__kv(None, 'gold')
        layout.addWidget(self.__gold)

        self.__damage = self.__kv(None, 'damage')
        layout.addWidget(self.__damage)

        self.__banLabel = QLabel('禁用', self)
        self.__banLabel.setObjectName('teamLabel')
        layout.addWidget(self.__banLabel)

        self.__bans = QHBoxLayout()
        self.__bans.setContentsMargins(0, 0, 0, 0)
        self.__bans.setSpacing(2)
        layout.addLayout(self.__bans)
        self.__hasBans = False

    def __kv(self, text, key: str) -> QWidget:
        """一个「标签 + 数值」小组；text 为 None 时标签在数据里给（经济 / 伤害）"""
        box = QWidget(self)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        label = QLabel(text or '', box)
        label.setObjectName('teamLabel')
        value = QLabel('', box)
        value.setObjectName('teamValue')
        value.setStyleSheet(mono(11, bold=True))
        lay.addWidget(label)
        lay.addWidget(value)
        self.__resources[key] = (label, value, box)
        return box

    def setCompact(self, level: int):
        self.__resources['gold'][2].setVisible(level < 2)
        self.__banLabel.setVisible(level < 2 and self.__hasBans)
        for i in range(self.__bans.count()):
            item = self.__bans.itemAt(i)
            if item.widget():
                item.widget().setVisible(level < 2)

    def updateTeam(self, team: dict, side: str, resultText: str, resultKind: str):
        self.__side.setText({'blue': '蓝方', 'red': '红方'}.get(side, '队伍'))
        self.__side.setStyleSheet(f"color: {SIDE_COLOR[side]}; {font(12, bold=True)}")

        self.__result.setText(resultText)
        if resultKind == 'none':
            self.__result.hide()
        else:
            self.__result.show()
            base = color('win') if resultKind == 'win' else color('red')
            self.__result.setStyleSheet(
                f"QLabel {{ color: {base}; background: {base}14;"
                f"  border: 1px solid {base}73; {mono(10)} }}")

        for key in ('towerKills', 'inhibitorKills', 'baronKills', 'dragonKills'):
            self.__resources[key][1].setText(str(team.get(key, 0)))

        herald = team.get('riftHeraldKills', 0)
        horde = team.get('hordeKills', 0)
        self.__herald.setVisible(bool(herald or horde))
        self.__resources['riftHeraldKills'][1].setText(f'{herald} / {horde}')

        self.__kda.setText(f"{team.get('kills', 0)} / {team.get('deaths', 0)} / {team.get('assists', 0)}")
        self.__kda.setStyleSheet(mono(11, bold=True))

        self.__resources['gold'][0].setText('经济')
        self.__resources['gold'][1].setText(fmtK(team.get('gold', 0)))

        self.__resources['damage'][0].setText('伤害')
        total = sum(s.get('demage', 0) or 0 for s in team.get('summoners', []))
        self.__resources['damage'][1].setText(fmtK(total))

        while self.__bans.count():
            item = self.__bans.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        bans = team.get('bans') or []
        names = team.get('banNames') or []
        for i, path in enumerate(bans[:5]):
            icon = SquareIcon(path, 17, self)
            name = names[i] if i < len(names) else ''
            if name:
                icon.setToolTip(f'禁用：{name}')
            self.__bans.addWidget(icon)
        self.__hasBans = bool(bans)
        self.__banLabel.setVisible(bool(bans))


class SummonerRow(QFrame):
    """一名玩家：英雄等级 · 名字/段位 · 双招 · 符文 · 7 格装备 · KDA · 补刀 · 经济 · 伤害条 · 参团率"""

    def __init__(self, summoner: dict, maxDamage: int, teamKills: int, parent=None):
        super().__init__(parent)
        self.setObjectName('summonerRow')
        self.setProperty('current', bool(summoner.get('isCurrent')))
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        # 英雄 + 等级角标
        champ = QFrame(self)
        champ.setFixedSize(28, 28)
        champ.setObjectName('champSlot')
        icon = SquareIcon(summoner.get('championIcon') or '', 26, champ)
        icon.move(1, 1)
        level = QLabel(str(summoner.get('champLevel', '')), champ)
        level.setObjectName('champLevel')
        level.setFixedHeight(11)
        level.setAlignment(Qt.AlignCenter)
        level.setStyleSheet(mono(9))
        level.adjustSize()
        level.move(28 - level.width() + 1, 28 - level.height() + 1)
        layout.addWidget(champ)

        nameBox = QVBoxLayout()
        nameBox.setContentsMargins(0, 0, 0, 0)
        nameBox.setSpacing(1)
        self.__name = SummonerName(
            summoner.get('summonerName') or '--', isPublic=summoner.get('isPublic', True),
            tagLine='', parent=self)
        self.__name.setObjectName('rowName')
        self.__name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.__name.setWordWrap(False)
        self.__name.clicked.connect(lambda: signalBus.toCareerInterface.emit(summoner.get('puuid')))
        self.__rank = QLabel('', self)
        self.__rank.setObjectName('rowRank')
        self.__rank.setStyleSheet(mono(10))
        # 段位名用官方色：一列玩家扫下来，谁高谁低先看颜色
        rank = rankText(summoner)
        if rank:
            self.__rank.setText(rank)
            self.__rank.setStyleSheet(
                mono(10) + f"color: {tierColor(_SHORT.get(summoner.get('tier', ''), summoner.get('tier', '')))};")
        else:
            # 设置里关了段位查询时这里会空一行, 拿英雄名补上: 19px 图标认英雄不现实
            self.__rank.setText(summoner.get('championName') or '')
            self.__rank.setStyleSheet(mono(10))
        nameBox.addWidget(self.__name)
        nameBox.addWidget(self.__rank)
        self.__nameCol = QWidget(self)
        col = QHBoxLayout(self.__nameCol)
        col.setContentsMargins(0, 0, 0, 0)
        col.addLayout(nameBox)
        self.__nameCol.setFixedWidth(150)
        layout.addWidget(self.__nameCol)

        for path in (summoner.get('spell1Icon'), summoner.get('spell2Icon')):
            layout.addWidget(SquareIcon(path or '', 17, self))
        layout.addWidget(SquareIcon(summoner.get('runeIcon') or '', 17, self))

        items = QHBoxLayout()
        items.setContentsMargins(0, 0, 0, 0)
        items.setSpacing(2)
        for path in summoner.get('itemIcons') or []:
            empty = not path or str(path).endswith('item-0.png')
            items.addWidget(SquareIcon('' if empty else path, 19, self))
        layout.addLayout(items)

        # 弹簧放这儿而不是行尾：数字列要贴着右边框，
        # 否则整行内容全堆在左侧，右边空出三百多像素
        layout.addStretch(1)

        kda = QLabel(f"{summoner.get('kills', 0)}/{summoner.get('deaths', 0)}/"
                     f"{summoner.get('assists', 0)}", self)
        kda.setObjectName('rowKda')
        kda.setFixedWidth(66)
        kda.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        kda.setStyleSheet(mono(11, bold=True))
        layout.addWidget(kda)

        self.__cs = self.__num(str(summoner.get('cs', 0)), 34)
        layout.addWidget(self.__cs)
        self.__gold = self.__num(fmtK(summoner.get('gold', 0)), 40, 'rowGold')
        layout.addWidget(self.__gold)

        self.__level = 0
        damageCell = QWidget(self)
        self.__damageCell = damageCell
        damageBox = QVBoxLayout(damageCell)
        damageBox.setContentsMargins(0, 0, 0, 0)
        damageBox.setSpacing(3)
        dmg = QLabel(fmtK(summoner.get('demage', 0)), self)
        dmg.setObjectName('rowDamage')
        dmg.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dmg.setStyleSheet(mono(10))
        bar = QFrame(damageCell)
        bar.setObjectName('damageBar')
        bar.setFixedHeight(3)
        fill = QFrame(bar)
        fill.setObjectName('damageFill')
        fill.setStyleSheet(f"background: {color('brand')};")
        damageBox.addWidget(dmg)
        damageBox.addWidget(bar)
        self.__damageBox = damageBox
        self.__bar = bar
        self.__fill = fill
        layout.addWidget(damageCell)

        kp = 0
        if teamKills:
            kp = round((summoner.get('kills', 0) + summoner.get('assists', 0)) * 100 / teamKills)
        self.__kp = self.__num(f'{min(kp, 100)}%', 42)
        self.__kp.setToolTip('参团率 = (击杀 + 助攻) ÷ 队伍击杀 · 自算')
        layout.addWidget(self.__kp)

        self.__setDamage(summoner.get('demage', 0) or 0, maxDamage)
        self.setCompact(0)

    def __num(self, text: str, width: int, name: str = 'rowNum') -> QLabel:
        label = QLabel(text, self)
        label.setObjectName(name)
        label.setFixedWidth(width)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setStyleSheet(mono(10))
        return label

    def __setDamage(self, damage: int, maxDamage: int):
        self.__ratio = (damage / maxDamage) if maxDamage else 0
        # 低于全场上限 35% 的条换灰色：它不是「少一点」，是「基本没打出伤害」
        self.__fill.setStyleSheet(
            f"background: {color('textMuted') if self.__ratio < 0.35 else color('brand')};")

    def __barWidth(self) -> int:
        return 130 if self.__level < 2 else 84

    def setCompact(self, level: int):
        self.__level = level
        self.__cs.setVisible(level < 1)
        self.__gold.setVisible(level < 2)
        self.__nameCol.setFixedWidth(150 if level < 2 else 126)
        self.__barWidthValue = self.__barWidth()
        self.__damageCell.setFixedWidth(self.__barWidthValue)
        self.__fill.setFixedWidth(int(self.__barWidthValue * self.__ratio))

    # 等级角标要在首帧布局后按实际宽度贴到右下角
    def showEvent(self, e):
        super().showEvent(e)
        level = self.findChild(QLabel, 'champLevel')
        if level:
            level.move(28 - level.width() + 1, 28 - level.height() + 1)


class TeamBlock(QFrame):
    """一个队伍：队伍条 + 若干玩家行；左侧 3px 色条区分阵营"""

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self.setObjectName('teamBlock')
        self.setProperty('side', side)
        self.__side = side
        self.__rows = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.__bar = TeamBar(self)
        layout.addWidget(self.__bar)
        self.__rowsLayout = QVBoxLayout()
        self.__rowsLayout.setContentsMargins(0, 0, 0, 0)
        self.__rowsLayout.setSpacing(0)
        layout.addLayout(self.__rowsLayout)

    def setCompact(self, level: int):
        self.__bar.setCompact(level)
        for row in self.__rows:
            row.setCompact(level)

    def updateTeam(self, team: dict, resultText: str, resultKind: str,
                   maxDamage: int):
        self.__bar.updateTeam(team, self.__side, resultText, resultKind)

        for row in self.__rows:
            self.__rowsLayout.removeWidget(row)
            row.deleteLater()
        self.__rows = []

        kills = team.get('kills', 0) or 0
        for summoner in team.get('summoners', []):
            row = SummonerRow(summoner, maxDamage, kills, self)
            self.__rowsLayout.addWidget(row)
            self.__rows.append(row)


class BestStrip(QFrame):
    """本场之最：四项都是前端按已有字段算的，LCU 没有官方 MVP"""

    def __init__(self, summoners: list, parent=None):
        super().__init__(parent)
        self.setObjectName('bestStrip')
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(18)

        tag = QLabel('本场之最 · 自算', self)
        tag.setObjectName('bestKey')
        tag.setStyleSheet(mono(10))
        layout.addWidget(tag)
        layout.addSpacing(6)

        def pick(metric):
            return max(summoners, key=lambda s: s.get(metric, 0) or 0) if summoners else None

        damage = pick('demage')
        gold = pick('gold')
        bestKda = pick('_kda')
        bestKp = pick('_kp')

        self.__who = []
        items = [
            ('最高伤害', fmtK(damage['demage']) if damage else '--', damage),
            ('最高经济', fmtK(gold['gold']) if gold else '--', gold),
            ('最高参团', f"{bestKp['_kp']}%" if bestKp else '--', bestKp),
            ('最高 KDA', f"{bestKda['_kda']:.1f}" if bestKda else '--', bestKda),
        ]
        for key, value, who in items:
            box = QHBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(6)
            k = QLabel(key, self)
            k.setObjectName('bestKey')
            k.setStyleSheet(mono(10))
            v = QLabel(value, self)
            v.setObjectName('bestValue')
            v.setStyleSheet(mono(12, bold=True))
            name = QLabel(who['summonerName'] if who else '--', self)
            name.setObjectName('bestWho')
            box.addWidget(k)
            box.addWidget(v)
            box.addWidget(name)
            layout.addLayout(box)
            self.__who.append(name)

        layout.addStretch(1)

    def setCompact(self, level: int):
        """窄屏只留「项目 + 数值」：这一行四项连名字要 700 多像素，会把整块撑宽"""
        for name in self.__who:
            name.setVisible(level < 2)


class MatchDetailView(QFrame):
    """单场对局详情：加载态 / 内容态两页，对外三个方法

    clear()                  清空内容（换人、换筛选时调）
    setLoadingPageEnabled()  切到转圈页 / 切回内容页
    updateGame(game)         game 为 `parseGameDetailData()` 的返回值
    setError(title, hint)    拉不到数据时给一句人话 + 重试按钮
    """

    # 重试按钮：由宿主（浮层 / 战绩查询页）决定重拉哪一场
    retry = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('matchDetailView')
        self.setProperty('matchDetail', True)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.__stack = QStackedWidget(self)

        loadingPage = QWidget(self)
        loadingLayout = QHBoxLayout(loadingPage)
        ring = IndeterminateProgressRing()
        ring.setFixedSize(32, 32)
        loadingLayout.addWidget(ring, 0, Qt.AlignHCenter)

        self.__head = MatchHead(self)
        self.actionLayout = self.__head.actionLayout

        self.__scroll = SmoothScrollArea(self)
        self.__scroll.setObjectName('matchScroll')
        self.__scroll.setWidgetResizable(True)
        self.__scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(self)
        content.setObjectName('matchContent')
        self.__body = QVBoxLayout(content)
        self.__body.setContentsMargins(0, 0, 0, 0)
        self.__body.setSpacing(10)
        self.__scroll.setWidget(content)

        infoPage = QWidget(self)
        infoLayout = QVBoxLayout(infoPage)
        infoLayout.setContentsMargins(0, 0, 0, 0)
        infoLayout.setSpacing(10)
        infoLayout.addWidget(self.__head)
        infoLayout.addWidget(self.__scroll, 1)
        # 没数据时不露标题条: 一个「—」加复制按钮挂在那儿像加载失败
        self.__head.hide()

        self.__stack.addWidget(loadingPage)
        self.__stack.addWidget(infoPage)
        self.__stack.setCurrentIndex(1)
        root.addWidget(self.__stack)

        self.__blocks = []
        self.__best = None
        self.__compact = 0

    # ---------------------------------------------------------------- 对外
    def clear(self):
        while self.__body.count():
            item = self.__body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.__blocks = []
        self.__best = None
        self.__head.hide()

    def setLoadingPageEnabled(self, enable: bool):
        self.__stack.setCurrentIndex(0 if enable else 1)

    def setError(self, title: str = '读取失败', hint: str = '客户端可能没响应，稍后重试'):
        self.clear()
        state = ErrorState(title, hint, height=200, parent=self)
        state.retry.connect(self.retry)
        self.__body.addWidget(state, 1)

    def updateGame(self, game: dict):
        self.clear()

        if not game:
            InfoBar.error(
                self.tr("获取对局详情失败"),
                self.tr("服务器返回了异常内容"),
                orient=Qt.Vertical, duration=5000,
                position=InfoBarPosition.BOTTOM_RIGHT, parent=self.window())
            return

        self.__head.updateHead(game)
        self.__head.show()
        teams = game.get('teams') or {}
        isCherry = game.get('queueId') == 1700

        # 斗魂竞技场是 8 队各 2 人，teamId = 名次 × 100；按 teamId 排就是按名次排
        order = sorted(k for k, t in teams.items() if t.get('summoners'))
        maxDamage = max(
            (s.get('demage', 0) or 0 for k in order for s in teams[k]['summoners']),
            default=0)
        every = []
        for k in order:
            kills = teams[k].get('kills', 0) or 0
            for s in teams[k]['summoners']:
                deaths = s.get('deaths', 0) or 1
                # 参团率 / KDA 只给「本场之最」用，写在前端；玩家行各自算各自的
                every.append({**s,
                              '_kp': min(100, round((s.get('kills', 0) + s.get('assists', 0)) * 100 / kills)) if kills else 0,
                              '_kda': (s.get('kills', 0) + s.get('assists', 0)) / deaths})

        for i, key in enumerate(order):
            team = teams[key]
            if isCherry:
                side = 'blue' if (i % 2 == 0) else 'red'
                resultText, kind = f'#{i + 1}', 'none'
            else:
                side = 'blue' if key in (100, 300, 500, 700) else 'red'
                win = team.get('win') == 'Win'
                resultText, kind = ('WIN' if win else 'LOSE'), ('win' if win else 'lose')

            block = TeamBlock(side, self)
            block.updateTeam(team, resultText, kind, maxDamage)
            self.__body.addWidget(block)
            self.__blocks.append(block)

        self.__best = BestStrip(every, self)
        self.__body.addWidget(self.__best)
        self.__body.addStretch(1)
        self.__compact = -1
        self.__applyCompact()

    # ---------------------------------------------------------------- 响应式
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.__applyCompact()

    def __applyCompact(self):
        w = self.__scroll.viewport().width()
        if w < 100:
            return
        level = 0 if w >= COMPACT_WIDTHS[0] else 1 if w >= COMPACT_WIDTHS[1] else 2
        if level != self.__compact:
            self.__compact = level
            for block in self.__blocks:
                block.setCompact(level)
            # 数据还没填进来时没有「本场之最」; 这里是 resizeEvent 里跑的,
            # 抛异常没人接, 整个进程会直接没掉
            if self.__best is not None:
                self.__best.setCompact(level)


# 兼容旧名：战绩查询页与详情浮层原来都用这个名字
GameDetailView = MatchDetailView
