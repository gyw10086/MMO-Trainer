# coding:utf-8
"""主题切换的统一入口

一次切换完整走这条链：

    冻结重绘 → 库样式 → 自绘控件 → 组件 qss → 解冻只画一帧

为什么需要这个模块：原来点一次「应用主题」会跑两轮全量刷新 —— 设置页在下拉框的
`currentIndexChanged` 里又调了一次 `setTheme`，等于把 `cfg.set` 自己的链路整套
重跑一遍（实测 394ms / 580ms）；而组件 qss 挂在 `QApplication` 上，每换一次主题
都要全应用回头 polish 一遍（195ms）。两件事叠起来就是「点完要等一会儿」。

而且那条链是嵌在 `ComboBox.setCurrentIndex` 的调用栈深处的 —— Qt 会在长链中间
处理 paint 事件，于是导航栏（还没轮到重绘）和内容区（qss 已换）各画各的，
这就是「导航还是白的、内容已经黑了」的半套状态。冻结重绘正好治它。

三件事分工：

1. `applyTheme()` —— 唯一的切换入口，刷新全部做完才放行一帧
2. `registerQssRoot()` —— 组件 qss 只挂页面容器和各顶层窗口的子树，不全应用
3. `register()` / `registerPaintRoot()` —— 自绘控件统一登记，切主题时一起重算

另外补上「跟随系统」的运行时探针：库只在赋值那一刻读一次 `darkdetect.theme()`，
系统改了深色/浅色要重启才跟上。
"""
import weakref

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

# updateStyleSheet 没从包顶层导出，只能从子模块拿
from qfluentwidgets.common.style_sheet import updateStyleSheet

from app.common import skin
from app.common.config import cfg
from app.common.qfluentwidgets import Theme, qconfig

# 自绘控件：约定实现 `applyTheme()`（重算颜色 + update()）
_aware = weakref.WeakSet()

# 只需要重画的控件：库自绘、颜色在 paintEvent 里现读（导航栏 / 标题栏）
_paintRoots = weakref.WeakSet()

# 重画之前要先重算点的东西（导航项那层 autoFillBackground 的 QPalette 就是），
# 控件 -> 无参回调。弱键：控件销毁时自动摘掉
_paintHooks = weakref.WeakKeyDictionary()

# 组件 qss 的挂载根：主窗口的页面容器 + 各个不在主窗口子树里的顶层窗口
_qssRoots = weakref.WeakSet()

# 按主题生成的样式：控件 -> 无参模板。切主题时逐个重新调用
_styled = weakref.WeakKeyDictionary()

# 当前生效的主题：用来在切换时取出「上一套调色板」，做旧色值批量替换
_currentTheme = None

# 每个根自己的骨架样式（页面容器的底色之类），每次刷新按当前主题重算。
# 值必须是弱引用：bound method 持有宿主，强引用会让弱键永远死不掉（等于泄漏）
_qssChrome = weakref.WeakKeyDictionary()


# ---------------------------------------------------------------- 控件登记

def register(widget) -> None:
    """登记一个自绘控件，切主题时会被调用 `applyTheme()`

    比让每个控件各自 `qconfig.themeChangedFinished.connect(...)` 好在两处：
    不会漏（`nav_skin` / `titlebar_skin` 就是漏了两年的例子），顺序也统一，
    想给整条链路计时只有一个入口。
    """
    _aware.add(widget)


def registerPaintRoot(widget, refresh=None) -> None:
    """登记「只需要重画」的控件

    导航栏 / 标题栏这类库自绘控件，颜色是在 `paintEvent` 里现读 `color()` 的，
    库里没有任何主题信号。不补一次 `update()`，切完主题它会一直停在旧色，
    直到鼠标划过或者切页才"突然变过来"。

    `refresh` 是重画之前要先跑的补丁（导航栏用它重铺导航项的 `QPalette` ——
    那一层是 `autoFillBackground` 垫的底，会被库的重抛光打回系统浅灰，
    光 `update()` 只会把旧的浅灰再画一遍）。它必须是模块级函数或绑定方法：
    `theme` 只存弱引用，闭包捕获控件会形成「值持有键」的循环，控件回收不掉。
    """
    _paintRoots.add(widget)

    if refresh is not None:
        _paintHooks[widget] = _weakCallable(refresh)


def registerQssRoot(widget, chrome=None) -> None:
    """登记一棵需要组件 qss 的子树（页面容器 / 独立顶层窗口）

    建出来时就把当前主题挂上，否则新开的浮窗会先闪一帧无样式的内容。

    `chrome` 是这个根自己的骨架样式（页面容器的底色之类），给一个返回字符串的
    函数，刷新时按当前主题重算。它和组件 qss 拼在同一张样式表里一次刷完 ——
    分成两次 `setStyleSheet` 等于把整棵子树 polish 两遍，实测多花 60ms。
    """
    _qssRoots.add(widget)

    if chrome is not None:
        _qssChrome[widget] = _weakCallable(chrome)

    skin.applyTo(widget, None, chrome() if chrome else '')
    # 已经有页面容器接管，就不用再往 QApplication 上重挂了（那 195ms 的整树 polish）
    skin.useLayered()


def _weakCallable(fn):
    """绑定方法只存弱引用，避免「值持有键」把控件永远吊住"""
    return weakref.WeakMethod(fn) if getattr(fn, '__self__', None) is not None else fn


def _chromeQss(widget) -> str:
    """取某个根的骨架样式（宿主已销毁时当作没有）"""
    provider = _qssChrome.get(widget)

    if isinstance(provider, weakref.WeakMethod):
        provider = provider()

    return provider() if provider else ''


def refreshCustomWidgets() -> None:
    """把登记过的自绘控件全部重算一遍"""
    for widget in list(_aware):
        try:
            widget.applyTheme()
        except RuntimeError:
            # C++ 对象已经销毁（对话框关掉了），弱集里还留着一个壳
            pass


def refreshPaintRoots() -> None:
    """给只需要重画的控件补一次 update()

    父控件重绘会连带子控件（导航项是父容器的子控件），所以根上 `update()`
    一次就够 —— 但**只有底色本身也跟上了才成立**。挂着 `autoFillBackground`
    的导航项垫的是 `QPalette`，那玩意儿在切主题时会被库的重抛光打回系统
    默认浅灰，所以登记时带的补丁要在这里、`update()` 之前先跑。
    """
    for widget in list(_paintRoots):
        try:
            hook = _paintHooks.get(widget)

            if isinstance(hook, weakref.WeakMethod):
                hook = hook()

            if hook is not None:
                hook()

            widget.update()
        except RuntimeError:
            pass


def refreshQssRoots(theme=None) -> None:
    """把组件 qss（连同它自己的骨架样式）重新挂到各子树根上"""
    for widget in list(_qssRoots):
        try:
            skin.applyTo(widget, theme, _chromeQss(widget))
        except RuntimeError:
            pass


def styled(widget, template) -> None:
    """给控件挂一条「按当前主题生成」的样式，切主题时自动重算

    以前到处是构造时写死：

        label.setStyleSheet(f"color: {color('textMuted')}; " + font(11))

    `color()` 只在那一瞬间取值，之后换主题没有任何路径会回来改它 —— 浅色主题下
    这些标签就一直是深色主题的灰白，四个页面累计 200 多处。改成：

        theme.styled(label, lambda: f"color: {color('textMuted')}; " + font(11))

    好处是不用给每个页面手写 `applyTheme`、也不用把控件存成属性，漏不了。

    注意：`template` 里不要引用 widget 自己（值持有键会让弱引用失效，控件回收不掉），
    也不要在循环里直接捕获循环变量 —— 需要按项不同就传参，用
    `functools.partial` 或默认参数固定。
    """
    _styled[widget] = _weakCallable(template)
    widget.setStyleSheet(template())


def refreshStyled() -> None:
    """把所有经 `styled()` 挂的样式按当前主题重算一遍"""
    for widget, provider in list(_styled.items()):
        if isinstance(provider, weakref.WeakMethod):
            provider = provider()

            if provider is None:
                _styled.pop(widget, None)
                continue

        try:
            widget.setStyleSheet(provider())
        except RuntimeError:
            # C++ 对象已经销毁，把壳从表里摘掉
            _styled.pop(widget, None)


def _palette(resolved):
    """取某套主题的调色板字典"""
    from app.common.design_tokens import DARK, LIGHT

    return DARK if resolved == Theme.DARK else LIGHT


def refreshStaleColors(before, after) -> None:
    """把控件内联样式里的旧主题色值整批换成新主题的

    这是历史包袱的兜底。项目里大量控件在构造时把 `color()` 拼进了字符串：

        label.setStyleSheet(f"color: {color('textMuted')}; " + font(11))

    `color()` 只在那一瞬间取值，之后没有任何路径回来改它 —— 深色切浅色后，
    四个页面累计 200 多处仍是深色主题的灰白，页头标题甚至白底白字。

    逐个补 `applyTheme()` 既慢又会再漏（新加的控件同样会忘），所以这里按
    「旧调色板值 → 新调色板值」一次性重写：只认调色板里出现过的色值，
    图片、图标资源、用户自定义颜色都不受影响。

    同一个样式串在很多控件上重复，先按串去重再替换，避免几千次无谓的字符串操作。
    """
    mapping = {}

    for key, old in before.items():
        new = after.get(key)

        if new and old.upper() != new.upper():
            mapping[old.upper()] = new

    if not mapping:
        return

    # 样式里的色值大小写不统一（`#E3B23C` / `#e3b23c` 都有），
    # 用 `str.replace` 只能命中完全一致的那种，会漏掉一半
    import re

    patterns = [(re.compile(re.escape(old), re.IGNORECASE), new)
                for old, new in mapping.items()]

    def recolor(text):
        for pattern, value in patterns:
            text = pattern.sub(value, text)

        return text

    cache = {}

    for widget in QApplication.allWidgets():
        try:
            ss = widget.styleSheet()
        except RuntimeError:
            continue

        if not ss:
            continue

        if ss not in cache:
            cache[ss] = recolor(ss)

        if cache[ss] != ss:
            try:
                widget.setStyleSheet(cache[ss])
            except RuntimeError:
                pass


# ---------------------------------------------------------------- 切换

def systemTheme():
    """系统当前是深色还是浅色；读不到时返回 None"""
    try:
        import darkdetect
        return darkdetect.theme()
    except Exception:
        return None


def resolveTheme(theme=None) -> Theme:
    """把 AUTO / None 解析成实际的深色或浅色"""
    if theme is None or theme == Theme.AUTO:
        return Theme.DARK if systemTheme() == 'Dark' else Theme.LIGHT

    return theme


def applyTheme(theme=None) -> None:
    """切换主题的唯一入口

    冻结重绘期间 Qt 丢弃所有 paint 请求，全部刷完才放行最后一帧 —— 用户看到的
    是「点完立刻是新主题」，没有中间态。

    这里不用库的 `setTheme()`：它内部是 `qconfig.set(themeMode, ...)`，值没变时
    直接 early-return。「跟随系统」下系统换了主题，配置项一直是 AUTO，所以那一次
    调用等于什么都没做，界面反而不会跟。改成直接写运行时主题并补发信号，
    配置项保持原样 —— 否则「跟随系统」会被改写成解析后的固定主题，设置页的
    下拉框会自己跳到「深色」。
    """
    resolved = resolveTheme(theme)
    before = _palette(_currentTheme) if _currentTheme is not None else None
    roots = [w for w in QApplication.topLevelWidgets() if w.isVisible()]

    for widget in roots:
        widget.setUpdatesEnabled(False)

    try:
        qconfig.theme = resolved
        globals()['_currentTheme'] = resolved
        # cfg 和 qconfig 是两个 QObject，前面 load() 时前者转发到后者；
        # 发 cfg 的这一个就同时通知到了库内配色管理和项目里仍监听即时信号的控件
        cfg.themeChanged.emit(resolved)

        # 只刷「当前真正可见」的 widget：隐藏页打 dirty-qss 标记，由库自己的
        # DirtyStyleSheetWatcher 在它下一次 Paint **之前**补刷（先刷后画，看到的
        # 仍然是一次到位，不会出现"翻到那页才变色"）。实测 210ms -> 87ms，
        # 这是剩下最大的一块开销，也是唯一能在一次点击内做完的量级
        updateStyleSheet(lazy=True)

        # 必须补发: 这条信号只有库的 `setTheme()` 会发, 我们绕开了它。
        # 漏了这个, 所有 themeChangedFinished 监听(组件 qss 兜底 / OP.GG 配色 /
        # 浮窗 mica)都会静默失效
        qconfig.themeChangedFinished.emit()

        # 先自绘控件、再重算内联样式、最后挂组件 qss：前者可能整段重写自己的
        # 样式表（SpectatorWindow / 战绩行都是 `setStyleSheet(...)` 覆盖式写法），
        # 顺序反了会把刚挂上的 qss 顶掉；子树 qss 放最后，一次 polish 收尾
        refreshCustomWidgets()
        refreshStyled()

        # 兜底：把没走 styled() 的历史控件样式里的旧色值整批换掉
        if before is not None:
            refreshStaleColors(before, _palette(resolved))

        refreshQssRoots(resolved)
        refreshPaintRoots()
    finally:
        for widget in roots:
            widget.setUpdatesEnabled(True)
            widget.update()


# ---------------------------------------------------------------- 跟随系统

def watchSystemTheme(onChange):
    """2 秒轮询系统主题，只有值变了才回调

    不用 `WM_SETTINGCHANGE`：native event filter 在 Qt + qasync 下容易漏消息，
    而 `darkdetect.theme()` 单次读注册表不到 1ms，两秒一次可以忽略。
    """
    timer = QTimer()
    timer.setInterval(2000)
    last = [systemTheme()]

    def tick():
        now = systemTheme()

        # 读不到（非 Windows / 注册表被锁）时不动，免得来回抖
        if now is None or now == last[0]:
            return

        last[0] = now
        onChange(now)

    timer.timeout.connect(tick)
    timer.start()

    return timer


_watcher = None


def syncAutoWatch() -> None:
    """只在「跟随系统」时跑探针

    用户在设置里选了深色/浅色就是明确表态，系统改浅色不该动我们的界面。
    """
    if _watcher is None:
        return

    if cfg.get(cfg.themeMode) == Theme.AUTO:
        _watcher.start()
    else:
        _watcher.stop()


def startAutoWatch() -> None:
    """启动「跟随系统」的运行时探针（初始化完主题之后调用一次）"""
    global _watcher

    if _watcher is not None:
        return

    _watcher = watchSystemTheme(lambda _: applyTheme(Theme.AUTO))
    cfg.themeMode.valueChanged.connect(lambda *_: syncAutoWatch())
    syncAutoWatch()
