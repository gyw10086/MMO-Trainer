# coding:utf-8
"""段位卡

LCU 的 `parseDetailRankInfo()` 每行 9 个字段：
    [队列, 总场, 胜率, 胜, 负, 当前段位, LP, 最高段位, 上季结算]

原来这 9 个字段横着铺成一张表，扫一眼拼不出「他什么水平」。
这里换成：左边一枚段位徽章 + 段位名（官方色）+ 右侧 LP，
下面一条 LP 进度条 + 一行小字（场次 / 胜率 / 最高 / 上季）。

未定级不整块消失，而是灰卡 + `--` 占位 —— 位置固定，用户不会以为「没加载出来」。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.common.design_tokens import color, font, mono

# LoL 官方段位色系，按 AA 4.5:1 提亮过；未定级走中性灰
TIER_COLOR = {
    '黑铁': '#8A8F98',
    '黄铜': '#C08A5E',
    '青铜': '#C08A5E',
    '白银': '#B8C2CE',
    '黄金': '#E8B93C',
    '铂金': '#4FE0BE',
    '翡翠': '#3ECF8E',
    '钻石': '#6FA8F5',
    '大师': '#B565E8',
    '宗师': '#FF6B6B',
    '王者': '#F0C674',
}

_UNRANKED = '#4A5058'

# 段位全名 -> 简称（"璀璨钻石" -> "钻石"）
_SHORT = {
    '坚韧黑铁': '黑铁', '英勇黄铜': '黄铜', '不屈白银': '白银', '荣耀黄金': '黄金',
    '华贵铂金': '铂金', '流光翡翠': '翡翠', '璀璨钻石': '钻石', '超凡大师': '大师',
    '傲世宗师': '宗师', '最强王者': '王者',
    'Iron': 'Iron', 'Bronze': 'Bronze', 'Silver': 'Silver', 'Gold': 'Gold',
    'Platinum': 'Platinum', 'Emerald': 'Emerald', 'Diamond': 'Diamond',
    'Master': 'Master', 'Grandmaster': 'Grandmaster', 'Challenger': 'Challenger',
}

# LCU 返回的是英文队列名，设计稿写的是「单双排 · 78 LP」
_QUEUE_NAME = {
    'Ranked Solo': '单双排',
    'Ranked Flex': '灵活组排',
    'RANKED_SOLO_5x5': '单双排',
    'RANKED_FLEX_SR': '灵活组排',
}

# 徽章里的短代号：设计稿 34px 方块写「D2」，中文段位名塞不进去
_TIER_CODE = {
    '黑铁': 'I', '黄铜': 'B', '白银': 'S', '黄金': 'G', '铂金': 'P',
    '翡翠': 'E', '钻石': 'D', '大师': 'M', '宗师': 'GM', '王者': 'C',
}
_DIVISION_DIGIT = {'Ⅰ': '1', 'Ⅱ': '2', 'Ⅲ': '3', 'Ⅳ': '4', '★': ''}

# 大段位没有罗马数字，用星级代替
_APEX = {'大师', '宗师', '王者', 'Master', 'Grandmaster', 'Challenger'}

_ROMAN = {'I': 'Ⅰ', 'II': 'Ⅱ', 'III': 'Ⅲ', 'IV': 'Ⅳ', 'NA': ''}


def splitRank(text: str):
    """'璀璨钻石 II' -> ('钻石', 'Ⅱ')；未定级返回 ('未定级', '')"""
    text = (text or '').strip()
    if not text or text == '--':
        return '未定级', ''

    parts = text.rsplit(' ', 1)
    tierRaw = parts[0]
    division = parts[1] if len(parts) > 1 else ''

    tier = _SHORT.get(tierRaw, tierRaw)
    if tier in _APEX:
        return tier, '★'

    return tier, _ROMAN.get(division, division)


def rankCode(tier: str, division: str) -> str:
    """『钻石 Ⅱ』->『D2』，恰好塞进徽章；没收录的段位退回罗马数字"""
    code = _TIER_CODE.get(tier)
    if not code:
        return division or '—'
    return f'{code}{_DIVISION_DIGIT.get(division, "")}'


def tierColor(tier: str) -> str:
    return TIER_COLOR.get(tier, _UNRANKED)


class RankCard(QFrame):
    """一张段位卡（单双 / 灵活各一张）

    row 直接吃 `parseDetailRankInfo()` 的一行，字段顺序不要改。
    """

    def __init__(self, row: list = None, compact: bool = False,
                 parent: QWidget = None):
        super().__init__(parent)
        self.setProperty('rankCard', True)

        self.__tier = '未定级'
        self.__color = _UNRANKED

        self.__emblem = QLabel(self)
        self.__emblem.setObjectName('rankEmblem')
        self.__emblem.setAlignment(Qt.AlignCenter)
        self.__emblem.setFixedSize(28 if compact else 32, 28 if compact else 32)

        self.__name = QLabel('未定级', self)
        self.__name.setObjectName('rankName')

        self.__compact = compact
        self.__queue = QLabel('', self)
        self.__queue.setObjectName('rankQueue')
        self.__queue.setStyleSheet(f"color: {color('textMuted')}; " + font(11))
        # 设计稿的生涯块是「钻石 Ⅱ / 单双排 · 78 LP」两行，
        # 紧凑模式把 LP 併到第二行，段位名右侧就不再单独挂一个数字
        self.__queue.setVisible(True)

        self.__lp = QLabel('--', self)
        self.__lp.setObjectName('rankLp')
        self.__lp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.__lp.setStyleSheet(mono(11))
        self.__lp.setVisible(not compact)

        nameBox = QVBoxLayout()
        nameBox.setContentsMargins(0, 0, 0, 0)
        nameBox.setSpacing(1)
        nameBox.addWidget(self.__name)
        nameBox.addWidget(self.__queue)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.__emblem, 0, Qt.AlignVCenter)
        top.addLayout(nameBox, 1)
        top.addWidget(self.__lp, 0, Qt.AlignVCenter)

        self.__bar = QFrame(self)
        self.__bar.setObjectName('rankBar')
        self.__bar.setFixedHeight(3)
        self.__barFill = QFrame(self.__bar)
        self.__barFill.setObjectName('rankBarFill')

        self.__meta = QLabel('', self)
        self.__meta.setObjectName('rankMeta')
        self.__meta.setStyleSheet(mono(10))

        # 设计稿把「最高 / 上季」顶到卡片右侧，左侧留给场次与胜率；
        # 挤在一个 label 里只能左对齐，右半张卡会空着
        self.__metaRight = QLabel('', self)
        self.__metaRight.setObjectName('rankMeta')
        self.__metaRight.setStyleSheet(mono(10))
        self.__metaRight.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # 总览的生涯块只有 125px，设计稿里没有「27 场 · 48% 胜率 最高…」这行；
        # 这些数字在右侧四个指标里已经有了，留着只会把段位名挤扁
        self.__meta.setVisible(not compact)
        self.__metaRight.setVisible(not compact)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6 if compact else 7)
        layout.addLayout(top)

        if compact:
            # 紧凑模式：进度条紧跟段位名，剩余空间留到底部
            layout.addWidget(self.__bar)
        else:
            # 弹簧放这儿: 多余高度留在「段位名」和「进度条」之间,
            # 进度条与底部小字才不会被顶到卡片最下沿
            layout.addStretch(1)
            layout.addWidget(self.__bar)

            metaRow = QHBoxLayout()
            metaRow.setContentsMargins(0, 0, 0, 0)
            metaRow.setSpacing(8)
            metaRow.addWidget(self.__meta)
            metaRow.addStretch(1)
            metaRow.addWidget(self.__metaRight)
            layout.addLayout(metaRow)

        self.update(row)

    def update(self, row: list = None):
        if not row or len(row) < 7:
            self.__apply('未定级', '', '--', 0, '未定级')
            return

        queue, total, winRate, wins, losses, rankText, lp = row[:7]
        queue = _QUEUE_NAME.get(queue, queue)
        highest = row[7] if len(row) > 7 else '--'
        previous = row[8] if len(row) > 8 else '--'

        tier, division = splitRank(rankText)
        colorHex = tierColor(tier)

        rated = tier != '未定级'
        if self.__compact:
            self.__queue.setText(f'{queue} · {lp} LP' if rated else queue)
        else:
            self.__queue.setText(queue)
        self.__apply(tier, division, f'{lp} LP' if rated else '--',
                     int(lp) if rated and str(lp).isdigit() else 0,
                     colorHex)

        if rated:
            # 左：本赛季战绩；右：最高段位与上季结算
            self.__meta.setText(
                f'{total} 场 · {winRate} 胜率 · {wins} 胜 {losses} 负')
            self.__metaRight.setText(
                f'最高 {shortTier(highest)}　上季 {shortTier(previous)}')
        else:
            self.__meta.setText('本赛季还没有打完定级赛')
            self.__metaRight.setText('')

    def __apply(self, tier: str, division: str, lpText: str, lp: int,
                colorHex: str):
        self.__tier = tier
        self.__color = colorHex

        self.__emblem.setText(rankCode(tier, division))
        self.__emblem.setStyleSheet(
            f'border: 1px solid {colorHex}; color: {colorHex}; '
            f'background: {colorHex}14; {mono(12, bold=True)}')

        self.__name.setText(f'{tier} {division}'.strip() if division else tier)
        self.__name.setStyleSheet(f'color: {colorHex}; {font(15, bold=True)}')

        self.__lp.setText(lpText)
        self.__lp.setStyleSheet(f'color: {color("textSub")}; {mono(10)}')

        self.__bar.setStyleSheet(
            f'background: {color("bg3")}; border: none;')
        pct = 0 if tier == '未定级' else max(3, min(100, lp))
        self.__barFill.setStyleSheet(
            f'background: {colorHex}; border: none;')
        self.__barFill.setGeometry(0, 0, max(2, int(self.__bar.width() * pct / 100)), 3)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 进度条宽度随卡片变化，这里按当前百分比重算
        lp = 0
        if self.__tier != '未定级':
            text = self.__lp.text().replace(' LP', '')
            lp = int(text) if text.isdigit() else 0
        self.__barFill.setGeometry(
            0, 0, max(2, int(self.__bar.width() * max(3, min(100, lp)) / 100)), 3)


class RankLine(QWidget):
    """一行式段位（总览 [01] 用）

    首屏要同时放单双排和灵活组排，设计稿那种「徽章 + 两行文字」的块会撑破
    92px 的内容区，所以压成一行：

        [P3] 铂金 Ⅲ                    单双排 · 41 LP

    段位名用官方段位色，徽章里是 D2 / P3 这样的代号。
    进度条不画在这里 —— 两条长横线并排看着像分隔线，LP 数字已经说明问题了。
    """

    HEIGHT = 24

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.__tier = '未定级'

        self.__emblem = QLabel('—', self)
        self.__emblem.setAlignment(Qt.AlignCenter)
        self.__emblem.setFixedSize(22, 22)
        self.__emblem.setStyleSheet(
            f'border: 1px solid {_UNRANKED}; color: {_UNRANKED}; '
            f'background: {_UNRANKED}14; {mono(9, bold=True)}')

        self.__name = QLabel('未定级', self)
        self.__name.setStyleSheet(f'color: {_UNRANKED}; {font(13, bold=True)}')
        # 段位名固定宽度：单双排 / 灵活组排两行的「队列 · LP」才会左对齐，
        # 否则「大师」这种没有小段位的名字会把第二行挤歪
        self.__name.setFixedWidth(66)

        self.__sub = QLabel('', self)
        self.__sub.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.__sub.setStyleSheet(f"color: {color('textMuted')}; " + font(10))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.__emblem, 0, Qt.AlignVCenter)
        layout.addWidget(self.__name, 0, Qt.AlignVCenter)
        # 「队列 · LP」紧跟在段位名后面，右半边留白交给和指标区的间距，
        # 不然 LP 会顶到「近 10 场」旁边，两块挤在一起
        layout.addWidget(self.__sub, 0, Qt.AlignVCenter)
        layout.addStretch(1)

    def update(self, row: list = None):
        if not row or len(row) < 7:
            self.__apply('未定级', '', '', _UNRANKED)
            return

        queue, total, winRate, wins, losses, rankText, lp = row[:7]
        tier, division = splitRank(rankText)
        rated = tier != '未定级'

        queueName = _QUEUE_NAME.get(queue, queue)
        sub = f'{queueName} · {lp} LP' if rated else queueName

        self.__apply(tier, division, sub, tierColor(tier))

    def __apply(self, tier: str, division: str, sub: str, colorHex: str):
        self.__tier = tier

        self.__emblem.setText(rankCode(tier, division))
        self.__emblem.setStyleSheet(
            f'border: 1px solid {colorHex}; color: {colorHex}; '
            f'background: {colorHex}14; {mono(9, bold=True)}')

        self.__name.setText(f'{tier} {division}'.strip() if division else tier)
        self.__name.setStyleSheet(f'color: {colorHex}; {font(13, bold=True)}')

        self.__sub.setText(sub)


def shortTier(text: str) -> str:
    """'璀璨钻石 III' -> '钻石 Ⅲ'，用于一行小字里省宽度"""
    tier, division = splitRank(text)
    if tier == '未定级':
        return '--'
    return f'{tier} {division}'.strip()
