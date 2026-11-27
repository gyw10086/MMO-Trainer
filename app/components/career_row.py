# coding:utf-8
"""生涯页「逐场记录」的表格行

设计稿 S8 的逐场记录是一张**单行表格**，一行里横向摆 9 列：

    色条 · 头像 · 英雄 104 · 分路 34 · 模式 48 · KDA 78 · 补刀 44 ·
    伤害 48 · 评分 36 · 时长 42 · 时间 50 · ▸

和总览 [04] 的 `GameRow` 不是一回事：那边面板只有 38% 宽，
英雄名和模式只能上下两行；这边面板有 62% 宽，两行反而把 38px 的行塞满了，
一屏少看 4 场，也不像表格。所以单独写一份，列宽全部按设计稿定死。

设计稿的 12/11/10px 在 1427 宽的窗口里偏小（用户反馈「字太小看着累」），
整体上调 1px：名字 13、数字列 11、评分与 KDA 12，行高跟着 38 → 40。

窄窗口下按「次要信息先走」的顺序收列：伤害 → 时长 → 补刀 → 时间。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                             QWidget)

from app.common.design_tokens import color, font, mono
from app.components.patch_news import ElidedLabel

# 队列 → 两字缩写（设计稿「单双」「乱斗」这一列只有 44px）
QUEUE_SHORT = {
    420: '单双', 440: '灵活',
    400: '匹配', 430: '匹配', 490: '快速',
    450: '乱斗', 720: '斗魂', 1700: '斗魂', 1900: '无限',
    0: '自定义',
}

# 位置两字化：LCU 有时给中文全称，有时给英文 lane
POSITION_TEXT = {
    'TOP': '上单', '上单': '上单', '上': '上单',
    'JUNGLE': '打野', '打野': '打野', '野': '打野',
    'MIDDLE': '中路', 'MID': '中路', '中单': '中路', '中': '中路',
    'BOTTOM': '下路', 'BOT': '下路', 'ADC': '下路', '下路': '下路', '下': '下路',
    'UTILITY': '辅助', 'SUPPORT': '辅助', '辅助': '辅助', '辅': '辅助',
}


def modeText(game: dict) -> str:
    """模式缩写：先用 queueId 查表，查不到再从名字里截两个字"""
    short = QUEUE_SHORT.get(game.get('queueId'))
    if short:
        return short

    name = game.get('name') or ''
    if '大乱斗' in name:
        return '乱斗'
    if '云顶' in name:
        return '云顶'
    if '排位' in name or '单双' in name:
        return '单双'
    return name[:2] or '--'


def positionText(game: dict) -> str:
    raw = (game.get('position') or '').strip()
    if not raw:
        return ''

    key = raw.upper()
    if key in POSITION_TEXT:
        return POSITION_TEXT[key]

    # LCU 的 lane 值有 SUPPORT / UTILITY / SUP 几种写法，前缀判断一次
    for prefix, text in (('SUP', '辅助'), ('UTIL', '辅助'), ('JUNG', '打野'),
                         ('MID', '中路'), ('BOT', '下路'), ('TOP', '上单')):
        if key.startswith(prefix):
            return text
    return raw[:2]


def damageText(value) -> str:
    """19400 → 19.4k；不到一千直接给整数"""
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return '--'
    if value >= 10000:
        return f'{value / 1000:.1f}k'
    return str(value)


def scoreTone(score: float) -> str:
    if score >= 7:
        return color('win')
    if score >= 5:
        return color('accent')
    return color('red')


class CareerRow(QFrame):
    """40px 单行战绩

    点击整行发 `clicked(game)`，由页面决定是开浮层还是跳对局信息。
    """

    clicked = pyqtSignal(dict)

    HEIGHT = 40

    def __init__(self, game: dict, parent: QWidget = None):
        super().__init__(parent)
        self.game = game or {}
        self.setProperty('careerRow', True)
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 6, 0)
        layout.setSpacing(9)

        self.__accent = QFrame(self)
        self.__accent.setFixedSize(2, 24)
        layout.addWidget(self.__accent, 0, Qt.AlignVCenter)

        self.__hero = QLabel(self)
        self.__hero.setFixedSize(26, 26)
        self.__hero.setScaledContents(True)
        layout.addWidget(self.__hero, 0, Qt.AlignVCenter)

        self.__name = ElidedLabel('', self)
        # ElidedLabel 默认 Horizontal=Ignored（让省略号生效），
        # 那样 setFixedWidth 会被一起忽略，列宽塌成 0 —— 换回 Preferred，
        # 省略仍然靠 resizeEvent 里的实测宽度
        self.__name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.__name.setFixedWidth(104)
        layout.addWidget(self.__name, 0, Qt.AlignVCenter)

        self.__lane = self.__cell(34, Qt.AlignLeft, mono(11))
        self.__mode = self.__cell(48, Qt.AlignLeft, mono(11))
        layout.addWidget(self.__lane)
        layout.addWidget(self.__mode)

        self.__kda = self.__cell(78, Qt.AlignRight, mono(12))
        layout.addWidget(self.__kda)

        self.__cs = self.__cell(44, Qt.AlignRight, mono(11))
        self.__damage = self.__cell(48, Qt.AlignRight, mono(11))
        self.__score = self.__cell(36, Qt.AlignRight, mono(12, bold=True))
        self.__duration = self.__cell(42, Qt.AlignRight, mono(11))
        for cell in (self.__cs, self.__damage, self.__score, self.__duration):
            layout.addWidget(cell)

        self.__time = QLabel('', self)
        self.__time.setFixedWidth(50)
        self.__time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.__time.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))
        layout.addWidget(self.__time)

        layout.addStretch(1)

        self.__fold = QLabel('▸', self)
        self.__fold.setFixedWidth(14)
        self.__fold.setAlignment(Qt.AlignCenter)
        self.__fold.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))
        layout.addWidget(self.__fold)

        self.__apply()

    # ------------------------------------------------------------------ 绘制
    def __cell(self, width: int, align, style: str) -> QLabel:
        label = QLabel('', self)
        label.setFixedWidth(width)
        label.setAlignment(align | Qt.AlignVCenter)
        label.setStyleSheet(style)
        return label

    def __apply(self):
        g = self.game
        win = bool(g.get('win'))
        remake = bool(g.get('remake'))

        accent = (color('textMuted') if remake
                  else (color('win') if win else color('red')))
        self.__accent.setStyleSheet(f'background: {accent}; border: none;')

        pix = QPixmap(g.get('championIcon') or '')
        if not pix.isNull():
            self.__hero.setPixmap(pix)

        self.__name.setText(g.get('championName') or '--')
        self.__name.setStyleSheet(f"color: {color('text')}; " + font(13))

        self.__lane.setText(positionText(g))
        self.__lane.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))

        self.__mode.setText(modeText(g))
        self.__mode.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))

        kills = g.get('kills', 0)
        deaths = g.get('deaths', 0)
        assists = g.get('assists', 0)
        self.__kda.setText(
            f'<b>{kills}</b>/{deaths}/<b>{assists}</b>')
        self.__kda.setStyleSheet(f"color: {color('textSub')}; " + mono(12))

        self.__cs.setText(str(g.get('cs', 0)))
        self.__cs.setStyleSheet(f"color: {color('textSub')}; " + mono(11))

        self.__damage.setText(damageText(g.get('damage')))
        self.__damage.setStyleSheet(f"color: {color('textMuted')}; " + mono(11))

        score = float(g.get('score') or 0)
        self.__score.setText(f'{score:.1f}')
        self.__score.setStyleSheet(f"color: {scoreTone(score)}; " + mono(12, bold=True))

        self.__duration.setText(g.get('duration') or '--')
        self.__duration.setStyleSheet(f"color: {color('textSub')}; " + mono(11))

        # 「11-26 / 02:14」两行：46px 宽塞不下「11-26 02:14」一行。
        # shortTime 只有日期（11/26），钟点得从完整时间戳里取
        stamp = g.get('time') or ''
        date = clock = ''
        if ' ' in stamp:
            day, _, rest = stamp.partition(' ')
            date = (day[5:] if len(day) >= 10 else day).replace('/', '-')
            clock = rest[:5]
        self.__time.setText(f'{date}\n{clock}' if clock
                            else (g.get('shortTime') or '').replace('/', '-'))

        self.setToolTip(
            f"{g.get('championName', '')} · {g.get('name', '')}\n"
            f"{kills}/{deaths}/{assists} · {g.get('cs', 0)} 刀 · "
            f"{damageText(g.get('damage'))} 伤害 · {g.get('duration', '')}")

    def setCompact(self, level: int = 0):
        """窄窗口收列：1 = 去伤害，2 = 再去时长，3 = 再去补刀和时间"""
        self.__damage.setVisible(level < 1)
        self.__duration.setVisible(level < 2)
        self.__cs.setVisible(level < 3)
        self.__time.setVisible(level < 3)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit(self.game)
        super().mouseReleaseEvent(e)
