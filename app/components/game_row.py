# coding:utf-8
"""单场战绩行

设计稿里「我的战绩」「今日对战」「生涯逐场」是同一个东西的三种摆放，
原来分别写成 `GameInfoBar`（86px 三行）和 `GameTab`（141×54 竖排）两套。
这里收成一份：横向一行，列由调用方按需求裁。

为什么压到 36~44：86px 一屏只能看 6 场，38px 能看 9 场；
而逐场的核心信息只有「英雄 / KDA / 结果」，其余都是次要的。

2026-11 改版：原来是「英雄名 + 第二行 分路·模式」的两行排法，12px 名字
挤在 10px 副行上面，看着累。按设计稿 `.game-row` 改成单行——分路缩成
10px 跟在名字后面（.gr-lane），模式由面板头的分段控件表达，不再重复。

字段全部来自 `parseGameData()`，没有额外请求。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                             QWidget)

from app.common.design_tokens import color, font, mono
from app.common import theme

# 位置缩写：LCU 给的是中文全称或英文 lane
POSITION_SHORT = {
    '上单': '上', '打野': '野', '中单': '中', '下路': '下', '辅助': '辅',
    'Top': '上', 'Jungle': '野', 'Middle': '中', 'Bottom': '下', 'Support': '辅',
    # 分路统一成标准值之后（TOP/JUNGLE/...）走这一组
    'TOP': '上', 'JUNGLE': '野', 'MIDDLE': '中', 'BOTTOM': '下', 'UTILITY': '辅',
}

# 列宽取自设计稿 .gr-kda / .gr-num / .gr-time
KDA_W = 58
CS_W = 36
DUR_W = 38
SCORE_W = 52
BADGE_W, BADGE_H = 20, 16


class GameRow(QFrame):
    """单场行

    variant
    ------
    'full'  : 英雄 分路 ─ KDA ─ 补刀 ─ 时长 ─ 结果   （我的战绩、生涯）
    'today' : 时间 ─ 英雄 分路 ─ KDA ─ 评分 ─ 结果   （今日对战）

    「今日对战」不显示 LP 增减：LCU 不返回每局 LP 变化，
    编一个数字出来会让用户拿它当真实依据。改用自算的 0-10 评分。
    """

    clicked = pyqtSignal(dict)

    def __init__(self, game: dict, variant: str = 'full',
                 parent: QWidget = None):
        super().__init__(parent)
        self.game = game or {}
        self.variant = variant
        self.setProperty('gameRow', True)
        # 设计稿：[03] 今日对战 44px（news-item 规格），[04] 我的战绩 36px；
        # 这里统一留 2px 余量，12px 正文上下不顶格
        self.setFixedHeight(44 if variant == 'today' else 38)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 8, 0)
        layout.setSpacing(8)

        if variant == 'today':
            self.__time = QLabel('', self)
            self.__time.setAlignment(Qt.AlignCenter)
            self.__time.setFixedWidth(36)
            layout.addWidget(self.__time)

        self.__accent = QFrame(self)
        self.__accent.setFixedWidth(2)
        # 不设 Expanding 的话, 色条只会和图标一样高, 看起来像断了一截
        self.__accent.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        layout.addWidget(self.__accent)

        self.__hero = QLabel(self)
        self.__hero.setFixedSize(24, 24)
        self.__hero.setScaledContents(True)
        layout.addWidget(self.__hero, 0, Qt.AlignVCenter)

        # 名字 + 分路同一行：分路 10px 灰字跟在后面（设计稿 .gr-lane）
        nameBox = QHBoxLayout()
        nameBox.setContentsMargins(0, 0, 0, 0)
        nameBox.setSpacing(5)
        self.__name = QLabel('--', self)
        self.__lane = QLabel('', self)
        nameBox.addWidget(self.__name, 0, Qt.AlignVCenter)
        nameBox.addWidget(self.__lane, 0, Qt.AlignVCenter)
        nameBox.addStretch(1)
        layout.addLayout(nameBox, 1)

        self.__kda = QLabel('', self)
        self.__kda.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.__kda.setFixedWidth(KDA_W)
        layout.addWidget(self.__kda)

        self.__stat = QLabel('', self)
        self.__stat.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.__stat.setFixedWidth(CS_W if variant == 'full' else SCORE_W)
        layout.addWidget(self.__stat)

        if variant == 'full':
            self.__duration = QLabel('', self)
            self.__duration.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.__duration.setFixedWidth(DUR_W)
            layout.addWidget(self.__duration)

        self.__result = QLabel('', self)
        self.__result.setAlignment(Qt.AlignCenter)
        self.__result.setFixedSize(BADGE_W, BADGE_H)
        layout.addWidget(self.__result, 0, Qt.AlignVCenter)

        self.__apply()
        # 颜色是拼进 qss 字符串的, 换主题必须重新拼一次
        theme.register(self)

    def applyTheme(self):
        """切主题时由 `theme.applyTheme()` 统一回调"""
        self.__apply()

    def __apply(self, *_):
        g = self.game
        win = bool(g.get('win'))
        remake = bool(g.get('remake'))

        accent = (color('textMuted') if remake
                  else (color('win') if win else color('red')))
        self.__accent.setStyleSheet(f'background: {accent}; border: none;')

        icon = g.get('championIcon')
        if icon:
            pix = QPixmap(icon)
            if not pix.isNull():
                self.__hero.setPixmap(pix)

        self.__name.setText(g.get('championName') or '')
        self.__name.setStyleSheet(f"color: {color('text')}; " + font(12))

        position = POSITION_SHORT.get(g.get('position') or '',
                                      g.get('position') or '')
        self.__lane.setText(position)
        self.__lane.setStyleSheet(f"color: {color('textMuted')}; " + font(10))
        self.__lane.setVisible(bool(position))

        if self.variant == 'today':
            stamp = g.get('time') or ''
            clock = stamp.split(' ')[-1][:5] if ' ' in stamp else ''
            self.__time.setText(clock)
            self.__time.setStyleSheet(f"color: {color('textMuted')}; " + mono(10))

        self.__kda.setText(
            f"{g.get('kills', 0)}/{g.get('deaths', 0)}/{g.get('assists', 0)}")
        self.__kda.setStyleSheet(f"color: {color('text')}; " + mono(11))

        if self.variant == 'full':
            self.__stat.setText(f"{g.get('cs', 0)}")
            self.__duration.setText(g.get('duration') or '--')
            self.__duration.setStyleSheet(
                f"color: {color('textMuted')}; " + mono(10))
        else:
            self.__stat.setText(f"{g.get('score', 0):.1f}")
        self.__stat.setStyleSheet(f"color: {color('textSub')}; " + mono(10))

        self.__result.setText('重开' if remake else ('胜' if win else '负'))
        self.__result.setStyleSheet(
            f'color: {accent}; border: 1px solid {accent}66; '
            f'background: {accent}14; ' + font(9, bold=True))

    def setCompact(self, compact: bool):
        """窄窗口下把补刀 / 时长 / 评分藏掉，这几列是「值得知道」不是「必须知道」"""
        self.__stat.setVisible(not compact)
        duration = getattr(self, '_GameRow__duration', None)
        if duration is not None:
            duration.setVisible(not compact)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit(self.game)
        super().mouseReleaseEvent(e)
