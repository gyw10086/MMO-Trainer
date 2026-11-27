# coding:utf-8
"""全局皮肤收尾

qfluentwidgets 内部控件（按钮 / 下拉 / 输入框 / 浮层 / 提示条）的样式写死在
Qt 资源 `:/qfluentwidgets/qss/{theme}/*.qss` 里，项目改不到文件，控件级
`setStyleSheet` 又会把库样式整段顶掉。唯一干净的拦截点是 `StyleSheetBase.content()`
—— 库和本项目的 qss 都要经过它，一处后处理即全局生效。

这里做三件事：
1. 圆角归零（`50%` 的圆形指示点除外，那是几何不是风格）
2. 残留的 Fluent 蓝（#0078D4 一族）换成当前主题色
3. `QScrollArea` 的 transparent 换成本页底色（见 `solid`）

不做：颜色体系、间距、字号 —— 那些在 design_tokens 与各页 qss 里管。
"""
import re

from app.common.design_tokens import color
from app.common.qfluentwidgets import Theme, qconfig, themeColor
from qfluentwidgets.common import style_sheet as _style_sheet

# 只处理 px 值；`border-radius: 50%` 留给圆形指示点
_RADIUS_PX = re.compile(r'border-radius\s*:\s*([0-9.]+)px')

# Fluent 默认蓝及其深浅变体。库里有少量写死的值不会跟着 setThemeColor 变
_FLUENT_BLUE = re.compile(
    r'#(?:0078[dD]4|005[fF][bB]8|0067[cC]1|0[fF]6[bB][cC]4|2[bB]88[dD]4|106[eE][bB][eE]|1[aA]86[cC]0)'
)

# QScrollArea 的整条规则（选择器 + 花括号块）
_SCROLL_AREA = re.compile(r'QScrollArea[^{}]*\{[^}]*\}')

_installed = False


def _hex(color) -> str:
    return '#%02X%02X%02X' % (color.red(), color.green(), color.blue())


def square(qss: str) -> str:
    """把 qss 里的 px 圆角清零，其余原样返回"""
    return _RADIUS_PX.sub(
        lambda m: 'border-radius: 0px' if float(m.group(1)) else m.group(0), qss)


def recolor(qss: str) -> str:
    """把残留的 Fluent 蓝替换成当前主题色"""
    color = _hex(themeColor())
    return _FLUENT_BLUE.sub(color, qss)


def solid(qss: str) -> str:
    """把 QScrollArea 的 transparent 换成本页底色

    战绩查询 / 其他功能 / 设置三页的 qss 都写着 `QScrollArea{transparent}`，
    指望父容器（stackedWidget 的 #0A0B0D）透上来。但主窗口开了 Mica，
    窗口本身是半透明的：viewport 有 `SeraphineInterface` 钉住的底色没事，
    **viewport 以外的边距区**（这几页都设了 viewportMargins）一透就直接
    露出模糊壁纸 —— 实测 #080808，卡片再叠 5% 白变成 #151515，
    而且跟着壁纸变色，看起来就是「软件底色不对」。
    这里把外层也刷成 bg0，Mica 只在窗口圆角边缘还有一点。
    """
    background = color('bg0')

    def replace(match):
        return re.sub(r'background(-color)?\s*:\s*transparent',
                      f'background-color: {background}', match.group(0))

    return _SCROLL_AREA.sub(replace, qss)


def finish(qss: str) -> str:
    return recolor(solid(square(qss)))


# 自己那一段的起止标记：组件 qss 会和页面 qss / 弹窗壳 qss 压在同一张样式表上，
# 只能替换标记之后的部分，整段覆盖会把别人的规则一起冲掉
MARKER = '/* ===== seraphine components ===== */'

_app = None
_layered = False


def _themeName(theme=None) -> str:
    """把主题取值换成目录名；AUTO / None 走当前运行时主题"""
    if theme is None or theme == Theme.AUTO:
        theme = qconfig.theme

    return 'dark' if theme == Theme.DARK else 'light'


def componentsQss(theme=None) -> str:
    """组件级 qss 字符串（面板 / 战绩行 / 分页条 / 段位卡）

    只返回字符串，挂到哪儿由调用方决定 —— 见 `applyTo` 与 `applyGlobal`。
    """
    path = f'app/resource/qss/{_themeName(theme)}/components.qss'

    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return ''


def applyTo(widget, theme=None, chrome='') -> None:
    """把组件 qss 挂到一棵子树的根上

    挂到 `QApplication` 会让应用里每个 widget 重新计算样式（实测 195ms），
    同一份 5.5KB 的内容挂在页面子树上只要 41ms。代价是不在主窗口子树里的
    顶层窗口（OP.GG / 观战 / 战绩详情 / 弹窗壳）必须各自挂一份，漏一个就有
    一块区域不跟着变色。

    `chrome` 是这个根自己的骨架样式（比如页面容器的底色）。它和组件 qss 拼在
    同一张样式表里一次刷完 —— 分开刷等于把整棵子树 polish 两遍，实测多花 60ms。

    用 marker 做「只替换自己那一段」：这些根上往往还压着页面 qss、弹窗壳 qss，
    直接 `setStyleSheet` 会把它们整段顶掉。
    """
    qss = componentsQss(theme)

    if not qss:
        return

    current = widget.styleSheet()

    if MARKER in current:
        current = current.split(MARKER)[0]

    widget.setStyleSheet(f'{current.rstrip()}\n{MARKER}\n{chrome}{qss}')


def useLayered() -> None:
    """有页面容器接管组件 qss 之后，关掉全应用兜底

    页面容器和各顶层窗口已经各挂了一份，再往 `QApplication` 上重挂一遍等于让
    每个 widget 多 polish 一次（195ms），纯浪费。
    """
    global _layered
    _layered = True


def _refreshApp() -> None:
    """把组件 qss 重新挂到 QApplication 上（兜底路径）"""
    if _app is None:
        return

    qss = componentsQss()

    if not qss:
        return

    current = _app.styleSheet()

    if MARKER in current:
        current = current.split(MARKER)[0]

    _app.setStyleSheet(f'{current.rstrip()}\n{MARKER}\n{qss}')


def _onThemeChangedFinished(*_) -> None:
    # 已经分层挂载：再走 app.setStyleSheet 就是全应用回头 polish
    if _layered:
        return

    _refreshApp()


def applyGlobal(app) -> None:
    """把组件级样式挂到 QApplication（主窗口还没建好时的兜底）

    面板 / 战绩行 / 分页条这些东西每个页面都有，挂在 widget 级会各抄一份；
    主窗口建好之后改走 `applyTo`，这里只负责「一个窗口都还没有」的那段时间，
    以及无窗口的单页测试。

    注意：不能用 `app.setStyleSheet(qss)` 直接覆盖 —— qfluentwidgets 自己的
    样式（标题栏、导航栏、滚动条）也是挂在 QApplication 上的，一覆盖就把它们
    全冲掉，标题栏和侧栏会退回系统浅色，看起来就是「软件底色是白的」。
    这里只替换自己那一段，其余原样保留。
    """
    global _app

    first = _app is None
    _app = app

    _refreshApp()

    if first:
        qconfig.themeChangedFinished.connect(_onThemeChangedFinished)


def install() -> None:
    """进程级安装，必须在创建任何窗口之前调用一次"""
    global _installed
    if _installed:
        return

    original = _style_sheet.StyleSheetBase.content

    def content(self, theme=Theme.AUTO):
        return finish(original(self, theme))

    _style_sheet.StyleSheetBase.content = content

    # 导航项的选中/悬停底色写死在库的 paintEvent 里（6% 白），qss 改不到
    from app.common import nav_skin
    nav_skin.install()

    # 标题栏的 paintEvent 自绘 backgroundColor，Mica 打开时那个颜色是
    # QColor(0,0,0,0) —— 整条标题栏直接是黑的，qss 也盖不住
    from app.common import titlebar_skin
    titlebar_skin.install()

    # 设置卡 / 分组 / 开关都靠 paintEvent 自绘，qss 同样改不到
    from app.common import setting_skin
    setting_skin.install()

    _installed = True
