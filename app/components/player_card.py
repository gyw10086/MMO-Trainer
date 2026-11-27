"""对局信息页的玩家卡

设计目标 (见 `design/UI-优化方案.md`):

- 三段式: 第一行认人, 第二行判强弱, 第三行看英雄池
- 阵营只靠 3px 左色条 + 头像描边表达, 不整块染色
- 胜率不用红绿对撞: >=60% 金色 / <=45% 灰化 / 中间品牌青
- 同一张卡在"选人 / 对局中 / 观战"三种状态下复用, 只切换数据区

数据由 `app/lol/game_data.py` 归一化后传入, 本模块不碰任何网络请求。
"""

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (QColor, QPainter, QPainterPath, QPen, QPixmap)
from PyQt5.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect,
                             QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
                             QStackedWidget, QVBoxLayout, QWidget)

from app.common.design_tokens import (CARD_HEIGHT, color, font, fontObject,
                                      sideColor, withAlpha)
from app.common.qfluentwidgets import Action, RoundMenu
from app.common import theme
from app.common.signals import signalBus

# LCU 与 gameflow 里的分路字段不完全统一, 这里做一次归一
POSITION_TEXT = {
    'TOP': '上单',
    'JUNGLE': '打野',
    'MIDDLE': '中路',
    'MID': '中路',
    'BOTTOM': '下路',
    'ADC': '下路',
    'UTILITY': '辅助',
    'SUPPORT': '辅助',
}

# 标签配色: 背景用半透明色, 深浅主题都能用; 前景色按语义取
# 'premade' 是占位, 实际颜色由 Chip.setAccent() 按组排色动态给
CHIP_TONES = {
    'default': ('chipBg', 'textSub'),
    'brand': ('brandSoft', 'brand'),
    'gold': ('rgba(233, 196, 106, 0.16)', 'gold'),
    'win': ('rgba(62, 207, 142, 0.14)', 'win'),
    'lose': ('rgba(255, 107, 107, 0.14)', 'red'),
    'violet': ('rgba(150, 120, 255, 0.16)', 'accent'),
    'muted': ('chipBg', 'textMuted'),
    'premade': ('chipBg', 'textSub'),
}


def shortNumber(value):
    """大数字压缩: 经济/伤害上万后写成 12.3k, 一行才放得下五个字段"""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return '0'

    if number >= 10000:
        return f"{number / 1000:.1f}k"

    return f"{int(round(number))}"


class Chip(QLabel):
    """10px 胶囊标签

    `tone='premade'` 时颜色由 `setAccent()` 给, 因为同一个"双排"标签在
    不同组里要显示不同颜色 —— 靠固定色调表做不到。
    """

    def __init__(self, text='', tone='default', parent=None):
        super().__init__(text, parent)

        self.tone = tone
        self.accent = ''

        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.applyStyle()

    def setTone(self, tone: str):
        if tone != self.tone:
            self.tone = tone
            self.applyStyle()

    def setAccent(self, accent: str):
        accent = accent or ''

        if accent != self.accent:
            self.accent = accent
            self.applyStyle()

    def applyStyle(self):
        if self.tone == 'premade' and self.accent:
            self.setStyleSheet(
                f"Chip {{"
                f"  background-color: {withAlpha(self.accent, 0x2E)};"
                f"  color: {self.accent};"
                f"  border-radius: 8px;"
                f"  padding: 0 6px;"
                f"  {font(10, True)}"
                f"}}"
            )
            return

        bg, fg = CHIP_TONES.get(self.tone, CHIP_TONES['default'])

        self.setStyleSheet(
            f"Chip {{"
            f"  background-color: {color(bg)};"
            f"  color: {color(fg)};"
            f"  border-radius: 8px;"
            f"  padding: 0 6px;"
            f"  {font(10)}"
            f"}}"
        )


class CardAvatar(QWidget):
    """44px 圆形头像, 支持阵营描边 / 等级角标 / 匿名像素化 + 锁"""

    def __init__(self, diameter=44, parent=None):
        super().__init__(parent)

        self.diameter = diameter
        self.pixmap = QPixmap()
        self.iconPath = None
        self.side = 'blue'
        self.level = 0
        # 是否遮住英雄图 (只有连英雄都不知道时才为 True)
        self.mask = False

        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def setData(self, iconPath, side='blue', level=0, mask=False):
        self.side = side
        self.level = level
        self.mask = mask

        # 对局中每秒都会刷新, 路径没变就不重新读盘
        if iconPath != self.iconPath:
            self.iconPath = iconPath
            self.pixmap = QPixmap(iconPath) if iconPath else QPixmap()

        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = QRectF(0, 0, self.diameter, self.diameter)

        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)

        # 底色: 没选英雄 / 图标缺失时不至于是一片透明
        painter.fillPath(path, QColor(color('bg3')))

        if not self.pixmap.isNull():
            # 连英雄都没有: 先缩到 6px 再放大, 得到"马赛克"效果, 比高斯模糊省事且更彻底
            if self.mask:
                small = self.pixmap.scaled(
                    6, 6, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                image = small.scaled(self.size(), Qt.IgnoreAspectRatio,
                                     Qt.SmoothTransformation)
            else:
                image = self.pixmap.scaled(
                    self.size() * self.devicePixelRatioF(),
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation)

            painter.drawPixmap(self.rect(), image)

        painter.setClipping(False)

        # 阵营描边
        pen = QPen(QColor(sideColor(self.side)), 2)
        painter.setPen(pen)
        painter.drawEllipse(rect.adjusted(1, 1, -1, -1))

        if self.mask:
            self.__paintLock(painter)

        if self.level:
            self.__paintLevelBadge(painter)

    def __paintLock(self, painter: QPainter):
        """居中画一个锁, 避免依赖 emoji 字体"""
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawEllipse(QRectF(self.diameter / 2 - 11, self.diameter / 2 - 11,
                                  22, 22))

        pen = QPen(QColor(232, 236, 244), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # 锁梁
        painter.drawArc(QRectF(self.diameter / 2 - 4.5, self.diameter / 2 - 6.5,
                               9, 9), 0, 180 * 16)
        # 锁身
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(232, 236, 244))
        painter.drawRoundedRect(
            QRectF(self.diameter / 2 - 5.5, self.diameter / 2 - 1.5, 11, 8), 1.5,
            1.5)
        painter.restore()

    def __paintLevelBadge(self, painter: QPainter):
        size = 15
        x = self.diameter - size - 1
        y = self.diameter - size - 1

        painter.save()
        painter.setPen(QPen(QColor(sideColor(self.side)), 1))
        painter.setBrush(QColor(color('bg1')))
        painter.drawEllipse(QRectF(x, y, size, size))

        painter.setPen(QColor(color('text')))
        painter.setFont(fontObject(9, 'bold'))
        painter.drawText(QRectF(x, y, size, size), Qt.AlignCenter,
                         str(self.level))
        painter.restore()


class WinRateBar(QWidget):
    """胜率条: 圆角轨道 + 进度 + 50% 刻度线"""

    def __init__(self, width=72, height=6, parent=None):
        super().__init__(parent)

        self.value = None

        self.setFixedSize(width, height)

    def setValue(self, value):
        self.value = value
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(0, 0, self.width(), self.height())
        radius = self.height() / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color('track')))
        painter.drawRoundedRect(rect, radius, radius)

        if self.value is None:
            return

        value = max(0, min(100, self.value))

        if value >= 60:
            fill = color('gold')
        elif value <= 45:
            fill = color('textMuted')
        else:
            fill = color('brand')

        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(
            QRectF(0, 0, self.width() * value / 100, self.height()), radius,
            radius)

        # 50% 刻度: 让"过半"这件事有参照, 用半透明避免看起来像断了一截
        x = self.width() / 2
        tick = QColor(color('bg1'))
        tick.setAlpha(110)
        painter.setPen(QPen(tick, 1))
        painter.drawLine(int(x), 1, int(x), int(self.height()) - 1)


class RecentDots(QWidget):
    """近 5 场胜负点"""

    DOT = 7
    GAP = 4

    def __init__(self, parent=None):
        super().__init__(parent)

        self.results = []

        width = 5 * self.DOT + 4 * self.GAP
        self.setFixedSize(width, self.DOT)

    def setResults(self, results):
        self.results = list(results)[:5]
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        for i in range(5):
            x = i * (self.DOT + self.GAP)
            result = self.results[i] if i < len(self.results) else None

            if result == 'W':
                painter.setBrush(QColor(color('win')))
            elif result == 'L':
                painter.setBrush(QColor(color('textMuted')))
            else:
                painter.setBrush(QColor(color('track')))

            painter.drawRoundedRect(QRectF(x, 0, self.DOT, self.DOT), 3, 3)


class CounterIcons(QWidget):
    """克制者头像: 打得过这个英雄的几个选择

    BP 阶段敌方一锁英雄就能给应对参考。头像用红框, 提示这是"劣势对位"。
    """

    ICON = 22
    GAP = 2

    def __init__(self, parent=None):
        super().__init__(parent)

        self.counters = []

        width = 3 * self.ICON + 2 * self.GAP
        self.setFixedSize(width, self.ICON)

    def setCounters(self, counters, source=''):
        self.counters = list(counters)[:3]

        if self.counters:
            lines = []

            for item in self.counters:
                name = item.get('name') or str(item.get('championId'))
                lines.append(f"{name}  {item['winRate'] * 100:.1f}%")

            title = f'克制英雄 ({source})' if source else '克制英雄'
            self.setToolTip(title + '\n' + '\n'.join(lines))
        else:
            self.setToolTip('')

        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        for i, item in enumerate(self.counters):
            x = i * (self.ICON + self.GAP)
            rect = QRectF(x, 0, self.ICON, self.ICON)

            path = QPainterPath()
            path.addRoundedRect(rect, 5, 5)

            painter.setClipPath(path)
            painter.fillPath(path, QColor(color('bg3')))

            pixmap = QPixmap(item.get('icon') or '')

            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.ICON * 2, self.ICON * 2, Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation)
                painter.drawPixmap(rect.toRect(), scaled)

            painter.setClipping(False)

            painter.setBrush(Qt.NoBrush)

            edge = QColor(color('red'))
            edge.setAlpha(170)
            painter.setPen(QPen(edge, 1))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)


class RecentGames(QWidget):
    """最近几场的迷你战绩: 英雄头像 + 胜负条 + 该场 K/D/A

    每格是竖排的 (头像 / 胜负条 / 11/3/17), 横向排 5 格。
    头像下面的胜负条只说明输赢, 下面的数字才是"这场打得怎么样" ——
    所以数字按战损比着色: 4 以上金色 (尽力局 / 碾压局), 1.2 以下红色
    (死太多), 中间灰白。全用同一种灰的话五格数字糊成一片, 一眼看不出
    谁在送、谁在 carry。
    鼠标悬停还能看到补刀 / 经济 / 伤害 / 评分, 点某一格弹出该场详情。
    没有战绩时收成 0 宽, 这样两列卡片的行仍然齐平。
    """

    ICON = 24
    CELL = 44
    GAP = 4
    BAR = 2
    TEXT = 13          # KDA 文字行高 (9px 字体)
    MAX = 5
    HEIGHT = ICON + BAR + TEXT
    FULL_WIDTH = MAX * CELL + (MAX - 1) * GAP

    gameClicked = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.games = []
        self.hoverIndex = -1

        self.setFixedHeight(self.HEIGHT)
        self.setFixedWidth(self.FULL_WIDTH)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    @staticmethod
    def kdaRatio(game) -> float:
        """单场战损比 (K + A) / D; 死 0 次按 1 当分母, 不写成无穷大"""
        kills = game.get('kills', 0) or 0
        deaths = game.get('deaths', 0) or 0
        assists = game.get('assists', 0) or 0

        return (kills + assists) / max(deaths, 1)

    @classmethod
    def kdaTint(cls, game):
        """单场 KDA 的前景色 (含字重), 规则与 `GameRow` 的评分一致"""
        if game.get('remake'):
            return fontObject(9), color('textMuted')

        ratio = cls.kdaRatio(game)

        if ratio >= 4:
            return fontObject(9, 'bold'), color('gold')

        if game.get('deaths') and ratio < 1.2:
            return fontObject(9, 'bold'), color('red')

        return fontObject(9), color('textSub')

    def setGames(self, games):
        self.games = list(games or [])[:self.MAX]

        # 没有战绩 (敌方 BP 阶段) 就收成 0 宽: 高度不变所以两列仍然齐平,
        # 但第三行的"1 楼 已锁定"不会被推到中间
        self.setFixedWidth(self.FULL_WIDTH if self.games else 0)

        if self.games:
            tips = []

            for game in self.games:
                if game.get('remake'):
                    result = '重开'
                else:
                    result = '胜' if game.get('win') else '负'

                line = (f"{game.get('kills', 0)}/{game.get('deaths', 0)}/"
                        f"{game.get('assists', 0)}  {result}"
                        f"  战损 {self.kdaRatio(game):.1f}"
                        f"  {game.get('shortTime') or ''}")

                detail = ' · '.join(filter(None, [
                    f"补刀 {game['cs']}" if game.get('cs') is not None else '',
                    f"经济 {shortNumber(game.get('gold'))}"
                    if game.get('gold') else '',
                    f"伤害 {shortNumber(game.get('damage'))}"
                    if game.get('damage') else '',
                    f"评分 {game['score']:.1f}" if game.get('score') else '',
                ]))

                tips.append(line + ('\n  ' + detail if detail else ''))

            self.setToolTip('最近几场\n' + '\n'.join(tips))
        else:
            self.setToolTip('')

        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.TextAntialiasing)

        textRect = self.TEXT

        for i, game in enumerate(self.games):
            cellX = i * (self.CELL + self.GAP)
            iconX = cellX + (self.CELL - self.ICON) / 2
            rect = QRectF(iconX, 0, self.ICON, self.ICON)

            path = QPainterPath()
            path.addRoundedRect(rect, 4, 4)

            painter.setClipPath(path)
            painter.fillPath(path, QColor(color('bg3')))

            pixmap = QPixmap(game.get('championIcon') or '')

            if not pixmap.isNull():
                painter.drawPixmap(rect.toRect(), pixmap.scaled(
                    self.ICON * 2, self.ICON * 2,
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

            painter.setClipping(False)

            if game.get('remake'):
                tint = QColor(color('textMuted'))
            elif game.get('win'):
                tint = QColor(color('win'))
            else:
                tint = QColor(color('red'))

            painter.setPen(Qt.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(
                QRectF(iconX, self.ICON + 0.5, self.ICON, self.BAR), 1, 1)

            # 该场 K/D/A: 格子只有 44px 宽, 9px 字体刚好放下 "11/3/17"
            kdaFont, kdaColor = self.kdaTint(game)
            painter.setFont(kdaFont)
            painter.setPen(QColor(kdaColor))
            painter.drawText(
                QRectF(cellX, self.ICON + self.BAR, self.CELL, textRect),
                Qt.AlignHCenter | Qt.AlignVCenter,
                f"{game.get('kills', 0)}/{game.get('deaths', 0)}/"
                f"{game.get('assists', 0)}")

        # 悬停高亮: 让"这里可以点"变得明确, 否则没人会去点战绩格
        if 0 <= self.hoverIndex < len(self.games):
            cellX = self.hoverIndex * (self.CELL + self.GAP)

            edge = QColor(color('brand'))
            edge.setAlpha(150)

            painter.setPen(QPen(edge, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                QRectF(cellX - 1, -1, self.CELL + 2, self.HEIGHT + 1), 5, 5)

    def mouseMoveEvent(self, e):
        index = self.__indexAt(e.pos().x())

        if index != self.hoverIndex:
            self.hoverIndex = index
            self.setCursor(
                Qt.PointingHandCursor if index >= 0 else Qt.ArrowCursor)
            self.update()

        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hoverIndex != -1:
            self.hoverIndex = -1
            self.update()

        super().leaveEvent(e)

    def __indexAt(self, x: int) -> int:
        """横坐标落在第几格; 落在格间空隙返回 -1"""
        for i in range(len(self.games)):
            left = i * (self.CELL + self.GAP)

            if left <= x < left + self.CELL:
                return i

        return -1

    def mousePressEvent(self, e):
        # press 必须在这里接住, 否则 Qt 会把后续 release 交给父级,
        # 点击就变成"整卡点击"而不是"某一格"
        if e.button() == Qt.LeftButton and self.__indexAt(e.pos().x()) >= 0:
            e.accept()
            return

        e.ignore()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            index = self.__indexAt(e.pos().x())

            if index >= 0:
                # 吃掉事件, 避免继续冒泡到卡片本身又弹一次整页战绩
                e.accept()
                self.gameClicked.emit(self.games[index])
                return

            # 空隙: 交回卡片, 走原来的整卡点击
            e.ignore()

        super().mouseReleaseEvent(e)


class ItemSlots(QWidget):
    """3x2 装备格"""

    SLOT = 15
    GAP = 2

    def __init__(self, parent=None):
        super().__init__(parent)

        width = 3 * self.SLOT + 2 * self.GAP
        height = 2 * self.SLOT + self.GAP
        self.setFixedSize(width, height)

        self.slots = []

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.GAP)

        for i in range(6):
            slot = QLabel(self)
            slot.setFixedSize(self.SLOT, self.SLOT)
            layout.addWidget(slot, i // 3, i % 3)

            self.slots.append(slot)

    def setIcons(self, icons):
        for i, slot in enumerate(self.slots):
            icon = icons[i] if i < len(icons) else None

            if icon:
                slot.setPixmap(QPixmap(icon).scaled(
                    self.SLOT, self.SLOT, Qt.KeepAspectRatio,
                    Qt.SmoothTransformation))
                slot.setStyleSheet("border-radius: 3px;")
            else:
                slot.clear()
                slot.setStyleSheet(
                    f"background-color: {color('track')}; border-radius: 3px;")


class PlayerCard(QFrame):
    """单个玩家卡, 选人 / 对局中 / 观战三态复用"""

    clicked = pyqtSignal(dict)
    # 点了某一格战绩: (那一场的数据, 卡片自己)
    gameClicked = pyqtSignal(dict, object)

    # 窄窗降级阈值: 先砍标签, 再砍段位, 最后砍第三行, 保证"名字 + 胜率"永远可读
    BREAKPOINT_TAG = 400
    BREAKPOINT_CHIP = 355

    def __init__(self, side='blue', parent=None):
        super().__init__(parent)

        self.side = side
        self.data = {}
        self.compact = False
        self.density = 'full'
        self.dead = False
        # 当前生效的组排色 (空 = 单排), 只在变化时重刷一次 QSS
        self.groupColor = ''

        self.setObjectName("PlayerCard")
        self.setCursor(Qt.PointingHandCursor)

        # 死亡时整张卡降到 70% 不透明度, 比换背景色干净
        self.opacityEffect = QGraphicsOpacityEffect(self)
        self.opacityEffect.setOpacity(1.0)
        self.setGraphicsEffect(self.opacityEffect)
        self.setFixedHeight(CARD_HEIGHT['normal'])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.__initWidget()
        self.__initLayout()
        self.__applyStyle()

        theme.register(self)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__applyStyle()

    # ------------------------------------------------------------------ 构建

    def __initWidget(self):
        self.avatar = CardAvatar(44, self)

        self.nameLabel = QLabel(self)
        self.nameLabel.setObjectName("PlayerName")
        self.nameLabel.setCursor(Qt.PointingHandCursor)

        self.tagLabel = QLabel(self)
        self.tagLabel.setObjectName("PlayerTag")

        self.rankChip = Chip('', 'default', self)
        self.chip1 = Chip('', 'brand', self)
        self.chip2 = Chip('', 'gold', self)

        self.winBar = WinRateBar(72, 6, self)
        self.winLabel = QLabel(self)
        self.winLabel.setObjectName("PlayerWinRate")
        self.winLabel.setFixedWidth(32)
        self.kdaLabel = QLabel(self)
        self.kdaLabel.setObjectName("PlayerStat")

        self.mainsLayout = QHBoxLayout()
        self.mainsLayout.setContentsMargins(0, 0, 0, 0)
        self.mainsLayout.setSpacing(2)
        self.mainIcons = []
        for _ in range(3):
            icon = QLabel(self)
            icon.setFixedSize(16, 16)
            self.mainsLayout.addWidget(icon)
            self.mainIcons.append(icon)

        self.posLabel = QLabel(self)
        self.posLabel.setObjectName("PlayerStat")
        self.stateLabel = QLabel(self)
        self.stateLabel.setObjectName("PlayerStat")
        self.stateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # 右侧数据区: 选人看近 5 场 / 克制英雄, 对局中看装备
        # 文案跟 RecentGames.MAX 同源，别写死 5
        self.recentCaption = QLabel(f'近{RecentGames.MAX}场', self)
        self.recentDots = RecentDots(self)
        self.itemSlots = ItemSlots(self)
        self.recentGames = RecentGames(self)
        self.recentGames.gameClicked.connect(
            lambda game: self.gameClicked.emit(game, self))

        # 战绩区: 文字摘要 (3 胜 2 负 + 战损) + 近 5 场战绩格
        #
        # 原来这里还有一行场均数据 (补刀/经济/伤害/评分), 结果 120px 的卡
        # 装不下三行: QVBoxLayout 把 gamesBlock 从 77px 压到 62px, 而
        # recentGames 是 setFixedHeight 压不动, 于是每场的 K/D/A 数字被
        # 整行裁掉 —— 卡片上根本看不到战损。场均数据挪进摘要行的 tooltip,
        # 腾出来的 18px 正好让 KDA 数字露出来。
        self.gamesSummaryLabel = QLabel(self)
        self.gamesSummaryLabel.setObjectName("GamesSummary")
        self.gamesSummaryLabel.setTextFormat(Qt.RichText)

        self.gamesBlock = QWidget(self)
        self.gamesBlock.setMinimumWidth(RecentGames.FULL_WIDTH)
        gamesLayout = QVBoxLayout(self.gamesBlock)
        gamesLayout.setContentsMargins(0, 0, 0, 0)
        gamesLayout.setSpacing(3)
        gamesLayout.addWidget(self.gamesSummaryLabel, 0, Qt.AlignLeft)
        gamesLayout.addWidget(self.recentGames, 0, Qt.AlignLeft)

        self.counterIcons = CounterIcons(self)
        self.counterCaption = QLabel('克制', self)
        self.counterCaption.setObjectName("PlayerCaption")
        self.counterCaption.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.recentPanel = QWidget(self)
        recentLayout = QVBoxLayout(self.recentPanel)
        recentLayout.setContentsMargins(0, 0, 0, 0)
        recentLayout.setSpacing(3)
        recentLayout.addWidget(self.recentCaption, 0, Qt.AlignRight)
        recentLayout.addWidget(self.recentDots, 0, Qt.AlignRight)

        self.counterPanel = QWidget(self)
        counterLayout = QVBoxLayout(self.counterPanel)
        counterLayout.setContentsMargins(0, 0, 0, 0)
        counterLayout.setSpacing(3)
        counterLayout.addWidget(self.counterCaption, 0, Qt.AlignRight)
        counterLayout.addWidget(self.counterIcons, 0, Qt.AlignRight)

        self.itemPanel = QWidget(self)
        itemLayout = QVBoxLayout(self.itemPanel)
        itemLayout.setContentsMargins(0, 0, 0, 0)
        itemLayout.setSpacing(2)
        itemLayout.addWidget(self.itemSlots, 0, Qt.AlignCenter)

        self.rightStack = QStackedWidget(self)
        self.rightStack.setContentsMargins(0, 0, 0, 0)
        self.rightStack.addWidget(self.recentPanel)
        self.rightStack.addWidget(self.itemPanel)
        self.rightStack.addWidget(self.counterPanel)
        self.rightStack.setFixedWidth(72)

    def __initLayout(self):
        hBox = QHBoxLayout(self)
        hBox.setContentsMargins(12, 8, 12, 8)
        hBox.setSpacing(10)

        self.infoLayout = QVBoxLayout()
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.infoLayout.setSpacing(2)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(5)
        row1.addWidget(self.nameLabel)
        row1.addWidget(self.tagLabel)
        row1.addWidget(self.rankChip)
        row1.addWidget(self.chip1)
        row1.addWidget(self.chip2)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        row2.addWidget(self.winBar)
        row2.addWidget(self.winLabel)
        row2.addWidget(self.kdaLabel)
        row2.addStretch(1)

        row3 = QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(8)
        row3.addWidget(self.gamesBlock, 0, Qt.AlignBottom)
        row3.addWidget(self.posLabel, 0, Qt.AlignBottom)
        row3.addWidget(self.stateLabel, 0, Qt.AlignBottom)
        row3.addStretch(1)

        self.infoLayout.addLayout(row1)
        self.infoLayout.addLayout(row2)
        self.infoLayout.addLayout(row3)

        hBox.addWidget(self.avatar)
        hBox.addLayout(self.infoLayout, 1)
        hBox.addWidget(self.rightStack, 0, Qt.AlignVCenter)

    # ------------------------------------------------------------------ 数据

    def setData(self, data: dict):
        """重建卡面 (切换对局时才调用, 不用于每秒刷新)"""
        self.data = dict(data or {})
        self.side = self.data.get('side', self.side)

        self.__render()

    def updateLive(self, live, phase=None):
        """对局中的增量刷新: 只改文本与图标, 不重建控件树"""
        if phase:
            self.data['phase'] = phase

        if live:
            self.data['live'] = live

        self.__render()

    def setCompact(self, compact: bool):
        """手动紧凑: 用户点页头按钮时的开关, 优先级高于自动密度"""
        self.compact = compact
        self.__applyDensity()

    def setDensity(self, mode: str):
        """自动密度: 'full' / 'standard' / 'compact'

        窗口不够高时退到 standard (去掉场均数据行),
        这样 1366×768 的笔记本也能一屏看完 10 个人。
        """
        if mode not in ('full', 'standard', 'compact'):
            mode = 'full'

        self.density = mode
        self.__applyDensity()

    def __applyDensity(self):
        mode = 'compact' if self.compact else self.density
        heightKey = 'normal' if mode == 'full' else mode

        self.setFixedHeight(CARD_HEIGHT[heightKey])
        self.gamesBlock.setVisible(mode != 'compact')

        for icon in self.mainIcons:
            icon.setVisible(False)

        self.__applyStyle()

    def setPhase(self, phase: str):
        self.data['phase'] = phase
        self.__render()

    # ------------------------------------------------------------------ 渲染

    def __render(self):
        data = self.data
        phase = data.get('phase', 'select')
        anonymous = data.get('isAnonymous', False)
        live = data.get('live') or {}

        self.dead = bool(live.get('isDead'))
        self.opacityEffect.setOpacity(0.7 if self.dead else 1.0)

        # 头像
        level = live.get('level', 0) if phase != 'select' else 0

        # 只有"连英雄都没拿到"时才打码: 匿名 = 身份未知, 英雄本身是客户端公开的,
        # 糊掉英雄等于把最有用的信息藏起来 (敌方 BP 阶段全是这种卡)
        mask = anonymous and not data.get('championId')

        self.avatar.setData(data.get('championIcon'), self.side, level, mask)

        # 第一行
        name = data.get('name') or ('匿名玩家' if anonymous else '—')
        tagLine = data.get('tagLine') or ''

        self.nameLabel.setText(name)
        self.nameLabel.setToolTip(f"{name}#{tagLine}" if tagLine else name)
        self.nameLabel.setProperty('anonymous', anonymous)
        self.nameLabel.style().polish(self.nameLabel)

        self.tagLabel.setText(f"#{tagLine}" if tagLine else '')
        self.tagLabel.setVisible(bool(tagLine))

        rank = data.get('rank') or {}
        rankText = f"{rank.get('tier', '')}{rank.get('division', '')}".strip()
        self.rankChip.setText(rankText)
        self.rankChip.setVisible(bool(rankText) and not anonymous)

        badges = data.get('badges') or []
        groupColor = data.get('premadeColor') or ''

        for chip, item in zip((self.chip1, self.chip2), badges[:2]):
            chip.setToolTip('')

            if item:
                chip.setText(item[0])
                chip.setTone(item[1])
                # 开黑标签按组排色染, 同一组的人颜色一致
                chip.setAccent(groupColor if item[1] == 'premade' else '')
                chip.setVisible(True)

                if item[1] == 'premade':
                    chip.setToolTip(self.__premadeTip(data))
            else:
                chip.setVisible(False)

        # 第二行
        if phase == 'select':
            self.winBar.setVisible(True)
            self.winBar.setValue(data.get('winRate'))
            self.winLabel.setText(
                f"{data['winRate']}%" if data.get('winRate') is not None else '—')

            kda = data.get('kdaValue')
            self.kdaLabel.setText(f"KDA {kda:.1f}" if kda else 'KDA —')
            self.kdaLabel.setVisible(True)
        else:
            # 对局中 / 观战: 胜率退居次要, 让位给本局数据
            self.winBar.setVisible(False)
            self.winLabel.setText('')

            self.kdaLabel.setText(
                f"KDA {live.get('kills', 0)}/{live.get('deaths', 0)}/"
                f"{live.get('assists', 0)}")
            self.kdaLabel.setVisible(True)

        # 第三行: 最近几场 (敌方在 BP 阶段没有战绩, 摘要写 "—" 占位, 两列才齐平)
        self.__renderGames(data.get('games') or [])

        # 主玩英雄让位给"最近几场": 同一张卡里两排英雄头像分不清谁是谁
        for icon in self.mainIcons:
            icon.setVisible(False)

        position = data.get('position') or ''
        # 敌方 BP 阶段拿不到分路, 退化成"第几楼"; 进了游戏 2999 给了分路
        # 就换回"上单/打野", 楼层号在那之后没有意义
        posText = data.get('slotText') or ''

        if position:
            posText = POSITION_TEXT.get(position.upper(), '') or posText

        if phase == 'select':
            self.posLabel.setText(posText or '—')
            self.stateLabel.setText(self.__pickStateText(data))
        else:
            # 补刀即使为 0 也照写：早先写成「0 就退回分路」，
            # 结果开局时左边一个「上路」、右边状态位又一个「上路」
            cs = live.get('cs', 0)
            self.posLabel.setText(f"补刀 {cs}")

            if self.dead and live.get('respawn'):
                self.stateLabel.setText(f"复活 {int(live['respawn'])}s")
            else:
                self.stateLabel.setText(posText or '—')

        self.stateLabel.setStyleSheet(
            f"{font(11)} color: {color('red') if self.dead else color('textMuted')};")

        # 右侧
        if phase == 'select':
            counters = data.get('counters') or []

            if counters:
                self.counterIcons.setCounters(
                    counters, data.get('counterSource') or '')
                self.rightStack.setCurrentWidget(self.counterPanel)
            else:
                recent = data.get('recent') or []

                self.rightStack.setCurrentWidget(self.recentPanel)
                self.recentCaption.setText('胜负' if recent else '')
                self.recentDots.setResults(recent)
                self.recentDots.setVisible(bool(recent))
        else:
            self.rightStack.setCurrentWidget(self.itemPanel)
            self.itemSlots.setIcons(live.get('items') or [])

        self.__layoutForWidth(self.width())

        # 组排色变了要重刷边框 (卡片底边那条色条), QSS 只能在 __applyStyle 里改
        if groupColor != self.groupColor:
            self.groupColor = groupColor
            self.__applyStyle()

    @staticmethod
    def __premadeTip(data) -> str:
        """开黑标签的悬停说明: 说清是客户端确认的还是战绩推出来的"""
        size = data.get('premade') or 0
        games = data.get('premadeGames') or 0

        if data.get('premadeConfirmed'):
            return f'客户端确认本局 {size} 人预组队'

        if games >= 2:
            return (f'近 5 场里同队 {games} 次, 基本可以确认是 {size} 排\n'
                    f'(同场且胜负相同的场次)')

        return (f'近 5 场里同队 {games} 次 — 也有可能是单纯撞车\n'
                f'(同场且胜负相同, 对手的胜负必然相反)')

    def __pickStateText(self, data):
        state = data.get('pickState')

        return {'locked': '已锁定', 'picking': '选择中'}.get(state, '')

    def __renderGames(self, games):
        """战绩区: 上一行文字摘要, 下一行英雄头像

        摘要场次必须和头像数量一致: LCU 一次会给十几场, 只取最近 5 场统计,
        否则会出现 "近11场" 配 5 个头像。
        重开不计入胜负 (算胜算负都不公平), 所以胜负数之和可能小于场次。
        战损按总击杀/总死亡算, 不是"场均 KDA 再平均" —— 后者会让 0 死的
        一场把整体拉飞; 总和比值才是真正打了多少输出、送了多少。
        """
        games = list(games)[:RecentGames.MAX]

        if games:
            wins = sum(1 for g in games if g.get('win') and not g.get('remake'))
            losses = sum(
                1 for g in games if not g.get('win') and not g.get('remake'))

            text = (f"近{len(games)}场 "
                    f"<span style='color:{color('win')}'>{wins}胜</span>"
                    f"<span style='color:{color('textMuted')}'> </span>"
                    f"<span style='color:{color('red')}'>{losses}负</span>")

            ratio = self.__recentKda(games)

            if ratio:
                if ratio >= 4:
                    tint = color('gold')
                elif ratio < 2:
                    tint = color('textMuted')
                else:
                    tint = color('textSub')

                text += (f"<span style='color:{color('lineStrong')}'> · </span>"
                         f"<span style='color:{color('textMuted')}'>战损 </span>"
                         f"<span style='color:{tint}'>{ratio:.1f}</span>")

            self.gamesSummaryLabel.setText(text)

            # 场均数据挪到这里: 卡片上放不下, 但悬停就能看到
            stats = self.__gamesStats(games)
            self.gamesSummaryLabel.setToolTip(
                f'近 {len(games)} 场场均\n{stats}' if stats else '')
        else:
            self.gamesSummaryLabel.setText(
                f"<span style='color:{color('textMuted')}'>"
                f"近{RecentGames.MAX}场 —</span>")
            self.gamesSummaryLabel.setToolTip('')

        self.recentGames.setGames(games)

    @staticmethod
    def __recentKda(games) -> float:
        """近几场的总战损比 (K + A) / D, 重开局不参与"""
        games = [g for g in games if not g.get('remake')]

        if not games:
            return 0.0

        kills = sum(g.get('kills', 0) or 0 for g in games)
        deaths = sum(g.get('deaths', 0) or 0 for g in games)
        assists = sum(g.get('assists', 0) or 0 for g in games)

        return (kills + assists) / max(deaths, 1)

    def __gamesStats(self, games):
        """近几场的平均数据: 补刀 / 经济 / 伤害 / 评分

        击杀死亡助攻不在这里 —— 每场头像下面单独写了, 再算一遍场均就是重复。
        重开局不参与统计 (0 杀 0 伤那种数据会把平均拉得很低)。
        返回值是给 tooltip 用的纯文本, 不再占卡片一行 (120px 装不下)。
        """
        games = [g for g in games if not g.get('remake')]

        if not games:
            return ''

        count = len(games)
        parts = []

        cs = sum(g.get('cs', 0) or 0 for g in games) / count

        if cs:
            parts.append(f"补刀 {cs:.0f}")

        gold = sum(g.get('gold', 0) or 0 for g in games) / count

        if gold:
            parts.append(f"经济 {shortNumber(gold)}")

        damage = sum(g.get('damage', 0) or 0 for g in games) / count

        if damage:
            parts.append(f"伤害 {shortNumber(damage)}")

        scores = [g['score'] for g in games if g.get('score')]

        if scores:
            parts.append(f"评分 {sum(scores) / len(scores):.1f}")

        return ' · '.join(parts)

    def __layoutForWidth(self, width: int):
        """窄窗降级: 保住名字与胜率, 依次丢掉标签 / 段位"""
        if width <= 0:
            return

        showChips = width >= self.BREAKPOINT_TAG
        showTag = width >= self.BREAKPOINT_TAG
        showRank = width >= self.BREAKPOINT_CHIP

        self.chip1.setVisible(showChips and self.chip1.text() != '')
        self.chip2.setVisible(showChips and self.chip2.text() != '' and len(
            self.data.get('badges') or []) > 1)
        self.tagLabel.setVisible(showTag and self.tagLabel.text() != '')
        self.rankChip.setVisible(showRank and self.rankChip.text() != '')

    # ------------------------------------------------------------------ 样式

    def __applyStyle(self):
        bg = color('bg2')
        hover = color('bg3')
        line = color('line')
        border = '1px dashed' if self.data.get('isAnonymous') else '1px solid'
        groupColor = self.groupColor or ''

        # 底边承担"同组同色"。十张卡排成两列时, 同组的两个人往往不挨着
        # (按分路对齐), 靠底边扫一眼就能配对; 单排保持普通描边, 否则满屏
        # 都是彩条, 反而看不出哪条是"一组"。
        bottom = (f"border-bottom: 2px solid {groupColor};"
                  if groupColor else f"border-bottom: {border} {line};")

        self.setStyleSheet(
            f"QFrame#PlayerCard {{"
            f"  background-color: {bg};"
            f"  border-top: {border} {line};"
            f"  border-right: {border} {line};"
            f"  {bottom}"
            f"  border-left: 3px solid {sideColor(self.side)};"
            f"  border-radius: 10px;"
            f"}}"
            f"QFrame#PlayerCard:hover {{"
            f"  background-color: {hover};"
            f"}}"
            f"QLabel#PlayerName {{"
            f"  {font(14, True)}"
            f"  color: {color('text')};"
            f"}}"
            f"QLabel#PlayerName[anonymous=\"true\"] {{"
            f"  color: {color('textSub')};"
            f"}}"
            f"QLabel#PlayerTag {{"
            f"  {font(10)}"
            f"  color: {color('textMuted')};"
            f"}}"
            f"QLabel#PlayerWinRate {{"
            f"  {font(11, True)}"
            f"  color: {color('text')};"
            f"}}"
            f"QLabel#PlayerStat {{"
            f"  {font(11)}"
            f"  color: {color('textSub')};"
            f"}}"
        )

        for caption in (self.recentCaption, self.counterCaption):
            caption.setStyleSheet(
                f"QLabel {{ {font(9)} color: {color('textMuted')}; }}")

        self.gamesSummaryLabel.setStyleSheet(
            f"QLabel {{ {font(10)} color: {color('textSub')}; }}")

        # Chip 自己不带主题监听, 换主题时要手动重刷一次
        for chip in (self.rankChip, self.chip1, self.chip2):
            chip.applyStyle()

        self.recentDots.update()
        self.winBar.update()
        self.avatar.update()

        # 战绩摘要里的胜负色是拼进富文本的, 换主题要重算一遍
        self.__renderGames(self.data.get('games') or [])

    def resizeEvent(self, e):
        super().resizeEvent(e)

        self.__layoutForWidth(self.width())

    # ------------------------------------------------------------------ 交互

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit(self.data)

        super().mouseReleaseEvent(e)

    # ------------------------------------------------------------------ 右键菜单

    def contextMenuEvent(self, e):
        """右键: 复制 ID / 复制战绩 / 查生涯 / 搜人 / 看详情

        卡片上只写了"念尘词 #78063", 想加好友还得手打, 这里给条捷径。
        """
        menu = self.buildContextMenu()

        if menu is None:
            return

        menu.exec_(e.globalPos())

    def buildContextMenu(self):
        """返回右键菜单; 没有任何可用操作时返回 None (占位卡)"""
        data = self.data or {}
        name = (data.get('name') or '').strip()
        tagLine = data.get('tagLine') or ''
        puuid = data.get('puuid') or ''

        if name == '—':
            name = ''

        if not name and not puuid:
            return None

        menu = RoundMenu(parent=self)

        if name:
            action = Action('复制 ID', menu)
            action.triggered.connect(
                lambda: QApplication.clipboard().setText(
                    f"{name}#{tagLine}" if tagLine else name))
            menu.addAction(action)

        if data.get('games'):
            action = Action('复制最近战绩', menu)
            action.triggered.connect(self.__copyGames)
            menu.addAction(action)

        if puuid:
            menu.addSeparator()

            action = Action('查看生涯', menu)
            action.triggered.connect(
                lambda: signalBus.toCareerInterface.emit(puuid))
            menu.addAction(action)

        if name:
            if not puuid:
                menu.addSeparator()

            action = Action('搜索这个玩家', menu)
            # 和「复制 ID」一样带上编号：只传名字的话，同区重名会搜到别人
            action.triggered.connect(
                lambda: signalBus.toSearchInterface.emit(
                    f'{name}#{tagLine}' if tagLine else name))
            menu.addAction(action)

        menu.addSeparator()

        action = Action('战绩详情 / 标签', menu)
        action.triggered.connect(lambda: self.clicked.emit(self.data))
        menu.addAction(action)

        return menu

    def __copyGames(self):
        lines = []

        for game in (self.data.get('games') or [])[:5]:
            if game.get('remake'):
                result = '重开'
            else:
                result = '胜' if game.get('win') else '负'

            lines.append(
                f"{game.get('kills', 0)}/{game.get('deaths', 0)}/"
                f"{game.get('assists', 0)}  {result}  "
                f"{game.get('shortTime') or ''}")

        QApplication.clipboard().setText('\n'.join(lines))
