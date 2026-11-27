"""设计令牌 (Design Tokens)

QSS 里没有 CSS 变量, 因此把颜色 / 间距 / 字号集中在这里维护,
组件通过 f-string 拼样式表, 避免同一套色值散落在十几个文件里。

用法::

    from app.common.design_tokens import color, font, SIDE_COLOR

    label.setStyleSheet(f"color: {color('textSub')}; {font(11)}")
"""

from app.common.qfluentwidgets import isDarkTheme

# 颜色按主题成对定义, 键名保持语义化 (不出现具体色相), 换配色时只改这里
# 深色 = 机能风: 近黑底 + 机能黄 + 终端青, 方角, 1px 边框
DARK = {
    'bg0': '#0A0B0D',
    'bg1': '#101215',
    'bg2': '#171A1E',
    'bg3': '#262C33',
    'line': '#1F2329',
    'lineStrong': '#2C3138',
    'text': '#EAECEF',
    'textSub': '#9BA3AD',
    'textMuted': '#8D949C',
    'brand': '#E3B23C',
    'brandSoft': '#29E3B23C',
    'brandMuted': '#8A7233',
    'accent': '#3FC7C0',
    'win': '#4FA07A',
    'gold': '#E3B23C',
    'warn': '#E8B93C',
    'blue': '#3FC7C0',
    'red': '#E05A4F',
    'chipBg': '#12FFFFFF',
    'track': '#1AFFFFFF',
    'hover': '#0DFFFFFF',
    'panel': '#09FFFFFF',
    'shadow': '#59000000',
}

# 浅色: 黄只做填充, 文字用深黄保证对比度; 青压深一档
LIGHT = {
    'bg0': '#F4F5F7',
    'bg1': '#FFFFFF',
    'bg2': '#FAFBFC',
    'bg3': '#E8EAEE',
    'line': '#E2E5EA',
    'lineStrong': '#CFD4DB',
    'text': '#12151A',
    'textSub': '#5A6272',
    # 浅色下 #8C93A3 在白底只有 2.8:1, "近 10 场" 这类标签会发飘;
    # 压到 #6E7585 是 4.2:1, 仍然比 textSub 弱一档但读得清
    'textMuted': '#6E7585',
    'brand': '#8A6A15',
    'brandSoft': '#338A6A15',
    'brandMuted': '#B08A2A',
    'accent': '#0F7C78',
    'win': '#2E7D57',
    'gold': '#8A6A15',
    'warn': '#A97710',
    'blue': '#0F7C78',
    'red': '#C0433A',
    'chipBg': '#0F0F141D',
    'track': '#140F141D',
    'hover': '#0A0F141D',
    'panel': '#060F141D',
    'shadow': '#1415223A',
}

# 阵营色固定不随主题变化, 保证"蓝方永远是蓝, 红方永远是红"
SIDE_COLOR = {'blue': '#3FC7C0', 'red': '#E05A4F'}

# 机能风一律方角; 只有分段控件内部的小块允许 0
RADIUS = {'chip': 0, 'card': 0, 'panel': 0, 'pill': 0}

SPACE = {'xs': 4, 'sm': 8, 'md': 12, 'lg': 16, 'xl': 20, 'xxl': 24}

FONT_FAMILY = "'Microsoft YaHei UI', 'Segoe UI', 'Microsoft YaHei'"
# 数字一律用等宽, 没有 Cascadia 时退到 Consolas, 保证表格竖向对齐
MONO_FAMILY = "'Cascadia Mono', 'Consolas', monospace"

# 卡面高度: 详细 / 标准 / 紧凑
# 详细 120 = 名字行 19 + 胜率行 16 + 战绩区 (摘要 16 + 战绩格 39) + 内边距
# 标准 117 = 同一套内容, 只留 3px 余量 (1080p 一屏放 5 人时用这档)
# 紧凑 62  = 只留名字 / 胜率 / 分路
# 注意战绩格 39 (24 头像 + 2 胜负条 + 13 K/D/A) 是死高度, 压不动;
# 再往这一行塞东西就会把 K/D/A 顶出卡片, 加内容前先量 gamesBlock 的实际高度。
CARD_HEIGHT = {'normal': 120, 'standard': 117, 'compact': 62}


def palette() -> dict:
    """当前主题的调色板"""
    return DARK if isDarkTheme() else LIGHT


def color(name: str) -> str:
    """按名字取当前主题颜色, 未定义时回退到主文字色, 避免 KeyError 打断绘制"""
    return palette().get(name, DARK['text'])


def withAlpha(hexColor: str, alpha: int) -> str:
    """给颜色加透明度

    Qt 的颜色字符串是 `#AARRGGBB`（alpha 在前），不是 CSS 的 `#RRGGBBAA`。
    直接把 `#E05A4F` 拼成 `#E05A4F33` 会被解析成「alpha=E0、颜色=5A4F33」，
    色相全错 —— 这个函数专门用来避免这个坑。
    """
    value = (hexColor or '').lstrip('#')
    if len(value) not in (6, 8):
        return hexColor
    return f'#{max(0, min(255, alpha)):02X}{value[-6:]}'


def sideColor(side: str) -> str:
    return SIDE_COLOR.get(side, SIDE_COLOR['blue'])


def font(size: int = 12, bold: bool = False) -> str:
    """生成 QSS 字体声明 (Qt 的 font 简写对中文字体支持不稳, 拆开写)

    Qt 的 QSS `font-weight` 只可靠支持 normal / bold, 数字字重在部分版本会被忽略,
    因此这里只暴露两档; 需要 Medium 之类的中间字重时用 `fontObject`。
    """
    return (f"font-family: {FONT_FAMILY}; font-size: {size}px; "
            f"font-weight: {'bold' if bold else 'normal'};")


def mono(size: int = 11, bold: bool = False) -> str:
    """等宽数字的 QSS 声明, 用于表格 / KDA / 时间 / 编号"""
    return (f"font-family: {MONO_FAMILY}; font-size: {size}px; "
            f"font-weight: {'bold' if bold else 'normal'};")


def fontObject(size: int = 12, weight: str = 'normal'):
    """自绘时用的 QFont

    Qt5 的 `QFont.setWeight()` 用的是 0-99 的老刻度, 直接传 400/500 会被当成最大值,
    所以这里用枚举名映射一次。
    """
    from PyQt5.QtGui import QFont

    weights = {
        'normal': QFont.Normal,
        'medium': QFont.Medium,
        'demibold': QFont.DemiBold,
        'bold': QFont.Bold,
    }

    f = QFont()
    f.setFamilies(['Microsoft YaHei UI', 'Segoe UI', 'Microsoft YaHei'])
    f.setPixelSize(size)
    f.setWeight(weights.get(weight, QFont.Normal))
    return f
