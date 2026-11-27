"""设置页控件外观（对齐设计稿 S4）

qfluentwidgets 的设置卡是 Win11 风格：每行一张独立圆角卡、5% 白底、
70/50 高、胶囊开关。设计稿要的是「一块面板里的分隔行」：

    ┌───────────────────────────────┐
    │ [01] 功能                     │  30px 组头，10px 等宽，底部 1px 线
    ├───────────────────────────────┤
    │  LCU 接口并发数         开 ●  │  50px 行，行间 1px 线
    ├───────────────────────────────┤
    │  默认对局数量           20    │
    └───────────────────────────────┘

绘制入口都在库的 paintEvent 里，qss 改不到，所以在这里整体替换：

1. `SettingCard.paintEvent`   —— 底色改 bg1、去圆角、底部 1px 分隔线
2. `SettingCardGroup`         —— 组头压到 30px 并带编号、卡片区加 1px 外框、
                                 卡片间距 2 → 0
3. `Indicator` / `SwitchButton` —— 34×18 方角开关，滑块 12×12

只在 `skin.install()` 里调用一次。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen

from app.common import theme

from app.common.design_tokens import color

# 组头高度与卡片行高（设计稿 .set-head / .set-row）
HEAD_H = 30
ROW_H = 50

# 开关尺寸（设计稿 .switch）
SWITCH_W, SWITCH_H = 34, 18
KNOB = 12

_installed = False


def _installCard() -> None:
    from qfluentwidgets.components.settings.setting_card import SettingCard

    def paintEvent(self, e):
        painter = QPainter(self)
        background = QColor(color('bg1'))
        line = QColor(color('line'))

        # 底色铺满，左右各留 1px 给分组画外框
        painter.fillRect(self.rect().adjusted(1, 0, -1, 0), background)

        # 行间分隔线：贴底画一条
        painter.setPen(QPen(line, 1))
        painter.drawLine(1, self.height() - 1, self.width() - 2,
                         self.height() - 1)

    SettingCard.paintEvent = paintEvent

    # 行高：设计稿统一 50，原来是 70（有描述）/ 50
    originalInit = SettingCard.__init__

    def init(self, *args, **kwargs):
        originalInit(self, *args, **kwargs)
        self.setFixedHeight(ROW_H)

        # 设计稿 .set-row：左右 padding 12、图标与文字间距 12
        self.hBoxLayout.setContentsMargins(12, 0, 12, 0)
        self.iconLabel.setFixedSize(22, 22)
        gap = self.hBoxLayout.itemAt(1)

        if gap and gap.spacerItem():
            gap.spacerItem().changeSize(12, 0)

        # 标题 14 → 13，描述 11px 用次级灰（库里是 rgb(208,208,208)，偏白）。
        # 必须经 theme.styled 登记：颜色是拼进字符串的，只在构造时算一次的话，
        # 切到浅色后 50 个标题会全部留成深色主题的近白色，白底上看不见
        theme.styled(self.titleLabel, lambda: (
            f'color: {color("text")}; font-size: 13px; background: transparent;'))
        theme.styled(self.contentLabel, lambda: (
            f'color: {color("textMuted")}; font-size: 11px;'
            f' background: transparent;'))

    SettingCard.__init__ = init


def _installGroup() -> None:
    from qfluentwidgets.components.settings.setting_card_group import (
        SettingCardGroup)

    originalInit = SettingCardGroup.__init__

    def __init__(self, title: str, parent=None):
        originalInit(self, title, parent)
        self.setProperty('title', title)
        self.setProperty('serial', '')

        # 卡片连成一片：库默认 spacing 2
        self.cardLayout.setSpacing(0)

        # 组头：30px，等宽小字
        self.titleLabel.setFixedHeight(HEAD_H)
        self.titleLabel.setContentsMargins(12, 0, 12, 0)
        theme.styled(self.titleLabel, lambda: (
            f'color: {color("textMuted")}; font-size: 10px;'
            f' background: transparent; letter-spacing: 1px;'))
        self.titleLabel.setText(title)

        # 原来标题下面有 12px 空隙，现在组头自己就是分隔，去掉
        spacer = self.vBoxLayout.itemAt(1)

        if spacer and spacer.spacerItem():
            self.vBoxLayout.removeItem(spacer)

    SettingCardGroup.__init__ = __init__

    def setTitle(self, title: str):
        """页面构造后再改标题时，也要带上编号"""
        self.setProperty('title', title)
        serial = self.property('serial') or ''
        self.titleLabel.setText(f'[{serial}] {title}' if serial else title)
        self.adjustSize()

    def setSerial(self, serial: str):
        self.setProperty('serial', serial)
        self.setTitle(self.property('title') or self.titleLabel.text())

    SettingCardGroup.setTitle = setTitle
    SettingCardGroup.setSerial = setSerial

    def paintEvent(self, e):
        painter = QPainter(self)
        background = QColor(color('bg1'))
        line = QColor(color('line'))
        pen = QPen(line, 1)

        # 先铺底：组头一条 + 卡片区
        painter.fillRect(0, 0, self.width(), HEAD_H, background)
        painter.fillRect(0, HEAD_H, self.width(), self.height() - HEAD_H,
                         background)

        # 整组外框（含组头的左右边），再补组头底线
        painter.setPen(pen)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.drawLine(0, HEAD_H - 1, self.width() - 1, HEAD_H - 1)

    SettingCardGroup.paintEvent = paintEvent

    def adjustSize(self):
        h = self.cardLayout.heightForWidth(self.width()) + HEAD_H
        return self.resize(self.width(), h)

    SettingCardGroup.adjustSize = adjustSize


def _installSwitch() -> None:
    from qfluentwidgets.components.widgets.switch_button import (
        Indicator, SwitchButton)

    def indicatorInit(self, *args, **kwargs):
        originalInit(self, *args, **kwargs)
        self.setFixedSize(SWITCH_W, SWITCH_H)
        # 库里初值 5 是按 42 宽算的，34 宽要收到 2
        self._sliderX = 2 if not self.isChecked() else SWITCH_W - KNOB - 2
        self.slideAni.setEndValue(self._sliderX)

    originalInit = Indicator.__init__
    Indicator.__init__ = indicatorInit

    def toggleSlider(self):
        self.slideAni.setEndValue(SWITCH_W - KNOB - 2 if self.isChecked() else 2)
        self.slideAni.start()

    Indicator._toggleSlider = toggleSlider

    def paintIndicator(self, e):
        painter = QPainter(self)
        # 方角开关，不需要抗锯齿
        self._drawBackground(painter)
        self._drawCircle(painter)

    Indicator.paintEvent = paintIndicator

    def drawBackground(self, painter: QPainter):
        brand = QColor(color('brand'))
        lineStrong = QColor(color('lineStrong'))

        if not self.isEnabled():
            painter.setPen(QPen(lineStrong, 1))
            painter.setBrush(QColor(color('bg0')))
        elif self.isChecked():
            # 选中：黄边 + 黄 16% 底
            painter.setPen(QPen(brand, 1))
            fill = QColor(brand)
            fill.setAlpha(41)
            painter.setBrush(fill)
        elif self.isHover:
            painter.setPen(QPen(lineStrong, 1))
            painter.setBrush(QColor(color('bg3')))
        else:
            painter.setPen(QPen(lineStrong, 1))
            painter.setBrush(QColor(color('bg0')))

        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    Indicator._drawBackground = drawBackground

    def drawKnob(self, painter: QPainter):
        painter.setPen(Qt.NoPen)
        brand = QColor(color('brand'))

        if not self.isEnabled():
            painter.setBrush(QColor(color('textMuted')))
        elif self.isChecked():
            painter.setBrush(brand)
        else:
            painter.setBrush(QColor(color('textMuted')))

        painter.drawRect(int(self.sliderX), (SWITCH_H - KNOB) // 2, KNOB, KNOB)

    Indicator._drawCircle = drawKnob

    def switchInit(self, *args, **kwargs):
        originalSwitchInit(self, *args, **kwargs)
        self.setFixedHeight(SWITCH_H)
        self.hBox.setContentsMargins(0, 0, 0, 0)
        self.hBox.setSpacing(8)
        # 开关右侧的「开 / 关」用 11px 次级灰，和设计稿的 .set-val 一致
        self.label.setStyleSheet(
            f'color: {color("textMuted")}; font-size: 11px;'
            f' background: transparent; min-width: 12px;')

    originalSwitchInit = SwitchButton._SwitchButton__initWidget

    def switchInit(self):
        originalSwitchInit(self)
        self.setFixedHeight(SWITCH_H)
        self.hBox.setContentsMargins(0, 0, 0, 0)
        self.hBox.setSpacing(8)
        # 开关右侧的「开 / 关」用 11px 次级灰，和设计稿的 .set-val 一致
        self.label.setStyleSheet(
            f'color: {color("textMuted")}; font-size: 11px;'
            f' background: transparent;')

    SwitchButton._SwitchButton__initWidget = switchInit


def _installExpandable() -> None:
    """可展开卡（代理 / 主题色 / 队列过滤这些）也要进同一块面板

    它们不是 SettingCard 子类——`ExpandSettingCard` 是 QScrollArea，
    里面套一张 `HeaderSettingCard`（这个才是 SettingCard 子类，但自己
    重写了 paintEvent 画 5% 白 + 圆角）。展开区 `#view` 同样是 5% 白。
    """
    from qfluentwidgets.components.settings.expand_setting_card import (
        ExpandBorderWidget, ExpandSettingCard, HeaderSettingCard)

    def headerPaint(self, e):
        painter = QPainter(self)
        painter.fillRect(self.rect().adjusted(1, 0, -1, 0),
                         QColor(color('bg1')))
        painter.setPen(QPen(QColor(color('line')), 1))
        painter.drawLine(1, self.height() - 1, self.width() - 2,
                         self.height() - 1)

    HeaderSettingCard.paintEvent = headerPaint

    # 展开态的外框由分组统一画，这里不再叠一层圆角边
    def borderPaint(self, e):
        pass

    ExpandBorderWidget.paintEvent = borderPaint

    def expandInit(self, *args, **kwargs):
        originalInit(self, *args, **kwargs)

        # QScrollArea 的 viewport 会用 palette 的 Window 色铺满整块，
        # 把分组画在左边的 1px 外框盖掉 —— 关掉自动填充，让它透明
        self.viewport().setAutoFillBackground(False)
        self.setFrameShape(0)
        self.setStyleSheet(
            f'ExpandSettingCard {{ background: transparent; border: none; }}'
            f'#scrollWidget {{ background: transparent; }}'
            f'#view {{ background: {color("bg1")}; border: none;'
            f' border-radius: 0px; }}'
            f'QLabel {{ color: {color("text")}; font-size: 13px;'
            f' background: transparent; }}'
            f'QLabel#contentLabel {{ color: {color("textMuted")};'
            f' font-size: 11px; }}')

    originalInit = ExpandSettingCard.__init__
    ExpandSettingCard.__init__ = expandInit


def _installIcon() -> None:
    """行首图标做成 22×22 方框（设计稿 .set-ico），图标本体 16×16 居中"""
    from qfluentwidgets.common.icon import drawIcon
    from qfluentwidgets.components.settings.setting_card import SettingIconWidget

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(color('lineStrong')), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        if not self.isEnabled():
            painter.setOpacity(0.36)

        painter.setRenderHints(QPainter.Antialiasing
                               | QPainter.SmoothPixmapTransform)
        drawIcon(self._icon, painter, self.rect().adjusted(3, 3, -3, -3))

    SettingIconWidget.paintEvent = paintEvent


def install() -> None:
    """进程级安装，创建设置页之前调用一次"""
    global _installed

    if _installed:
        return

    _installCard()
    _installGroup()
    _installExpandable()
    _installIcon()
    _installSwitch()
    _installed = True
