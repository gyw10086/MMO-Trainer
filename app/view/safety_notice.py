# coding:utf-8
"""首启安全说明

这类第三方助手最常见的投诉不是功能问题，而是「杀毒软件报毒 → 用户以为是外挂」。
所以在第一次启动时把话说清楚：本程序对游戏进程做了什么、没做什么。
文案控制在四条 + 一句兜底，不解释技术细节，只给结论和可自查的出口。

版本策略：`SAFETY_NOTICE_VERSION`（定义在 app/common/config.py）与配置里的
`safetyNoticeVersion` 比大小，只在后者更小时弹一次；设置页里「安全说明」那张卡
用 `force=True` 随时再看，且不写配置。

文案边界（改这四条之前先读）：第 2 条写的是「不向**游戏进程**发送任何消息」，
指的是对局里的 `League of Legends.exe`。窗口修复会给**客户端**窗口发一条
`WM_DPICHANGED`（`app/common/win_window.py`），那属于改自己的窗口尺寸，
不是往对局进程里塞消息 —— 所以措辞别再升级成「不向任何其他进程发送消息」，
那样就不再是实话了。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QWidget

from app.common.config import cfg, SAFETY_NOTICE_VERSION
from app.common.logger import logger
from app.common.qfluentwidgets import MessageBox

TAG = "SafetyNotice"

TITLE = "关于本程序的说明"

# 四条边界说明（顺序固定，先说「不做什么」，再说数据从哪来，最后说唯一的改动点）
BULLETS = (
    "不读取游戏内存、不注入 DLL、不加载任何驱动",
    "不模拟键盘鼠标，不向游戏进程发送任何消息",
    "所有数据来自官方客户端本地接口（LCU）、官方 Live Client Data API 与公开战绩接口",
    "唯一会改动的是「锁定配置」开关对 PersistedSettings.json 的只读权限，默认关闭",
)

FOOTER = "若杀毒软件误报，可将本程序加入信任区；安装包 SHA256 见发布页。"

# QWidget 的最大尺寸（QWIDGETSIZE_MAX），用来解掉库写死的固定尺寸
_MAX_WIDGET_SIZE = 16777215

# 兜底父窗口的模块级引用。
# 没有活动窗口时我们会临时造一个隐藏 QWidget 当 parent，但它一旦被 GC，
# Qt 会连带销毁挂在它下面的对话框（实测 RuntimeError: wrapped C/C++ object
# of type MessageBox has been deleted），所以必须由模块级变量把它引用住。
_fallbackParent = None


def buildContent():
    """拼出对话框正文：四条要点 + 空行 + 兜底一句"""
    lines = [f"• {text}" for text in BULLETS]

    return "\n".join(lines) + "\n\n" + FOOTER


def _activeWindow():
    """当前活动窗口；没有（启动早期 / 无 GUI 会话）就返回 None"""
    try:
        return QApplication.activeWindow()
    except Exception:
        return None


def _resolveParent(parent=None):
    """把 parent 归一成「一定能当 MessageBox 父窗口」的对象

    MessageBox 内部直接 `parent.width()`，传 None 就是 AttributeError，
    结果是整条首启说明被静默跳过（正好把「让用户看到这四条」这件事废掉）。
    三级兜底：
    1. 显式传入的 parent
    2. `QApplication.activeWindow()`
    3. 临时造一个隐藏的 `QWidget`（`Qt.Tool`：不进任务栏、不抢焦点；不 `show()`），
       并由模块级 `_fallbackParent` 引用住，避免 `exec()` 期间被 GC
    """
    global _fallbackParent

    if parent is not None:
        return parent

    active = _activeWindow()

    if active is not None:
        return active

    if QApplication.instance() is None:
        # 连 QApplication 都没有，造不出任何 QWidget
        return None

    if _fallbackParent is None:
        _fallbackParent = QWidget()
        # Tool 窗口不会出现在任务栏、也不会抢焦点，作为「无主对话框」的载体最合适
        _fallbackParent.setWindowFlags(Qt.Tool)

    return _fallbackParent


def buildSafetyNoticeBox(parent=None):
    """构造（不弹出）安全说明对话框

    单独抽出来是为了让 `_dev_tests` 能在 offscreen 下量尺寸：库里的 MessageBox
    按构造那一刻的 Label 几何写死对话框大小，那时布局还没跑过，多行正文会被截掉，
    所以这里必须重算一遍（见 `_fitToContent`）。

    :return: MessageBox 实例；没有 QApplication（连兜底 QWidget 都造不出来）时返回 None
    """
    parent = _resolveParent(parent)

    if parent is None:
        logger.warning('no usable parent window for safety notice', TAG)
        return None

    box = MessageBox(TITLE, buildContent(), parent)

    # 保活的双保险：主保证是模块级 `_fallbackParent`，这里再让 box 也引用一份。
    # Qt 里 parent 被 delete 会连带销毁子对象，而这个兜底 QWidget 谁都不引用时
    # 会被 GC 掉，对话框就跟着一起没了。
    box._safetyParent = parent

    box.yesButton.setText("我知道了")
    box.cancelButton.hide()

    _fitToContent(box)

    return box


def _fitToContent(box):
    """按正文真实需要的宽高重新固定对话框尺寸（原地修改）

    库里的 `MessageBox.__init__` 用构造那一瞬间的 Label 几何算固定尺寸，那时布局
    还没跑过（Label 只有默认的 100x30），多行正文必被截掉。而且它还有个
    `_adjustText` 挂在窗口 resize 上，换行宽度跟着窗口宽度变 —— 那会把行数再改一次，
    和已经定死的高度对不上。所以这里：摘掉 resize 钩子，按布局自己的 sizeHint 定尺寸。
    """
    # 摘掉「窗口 resize 时重新换行」的钩子（实例级覆盖，不影响库本身）
    box._adjustText = lambda: None

    # 解掉 MessageBox.__init__ 里写死的固定尺寸，否则布局没有伸展空间
    box.widget.setMinimumSize(0, 0)
    box.widget.setMaximumSize(_MAX_WIDGET_SIZE, _MAX_WIDGET_SIZE)

    box.widget.layout().activate()
    box.widget.setFixedSize(box.widget.sizeHint())


def showSafetyNotice(parent=None, force=False):
    """弹一次安全说明

    :param parent: 窗口级 parent（主窗口）。传 None 会让模态遮罩错位，
                   所以这里退化成「当前活动窗口」；连活动窗口都没有时再兜一个
                   临时顶层 QWidget —— MessageBox 内部直接 `parent.width()`，
                   传 None 会 AttributeError，那样整条说明会被静默跳过。
    :param force: True = 用户主动点进来看，每次都弹且**不写配置**
    :return: True 表示这次真的弹了
    """
    if not force:
        try:
            shownVersion = int(cfg.get(cfg.safetyNoticeVersion) or 0)
        except (TypeError, ValueError):
            # 配置被手改成脏数据时当成「没弹过」，多弹一次不算事故
            shownVersion = 0

        if shownVersion >= SAFETY_NOTICE_VERSION:
            return False

    if QApplication.instance() is None:
        # 连 QApplication 都没有，弹不出任何东西，别硬来
        logger.warning('no QApplication instance, skip safety notice', TAG)
        return False

    # 在这里就把 parent 归一好（build 里还有一层，幂等）：调用方传 None 时
    # 也能拿到一个能当父窗口的东西，而不是让 MessageBox 内部 parent.width() 崩掉
    parent = _resolveParent(parent)

    box = buildSafetyNoticeBox(parent) if parent is not None else None

    if box is None:
        logger.warning('no usable parent window, skip safety notice', TAG)
        return False

    box.exec()

    if not force:
        try:
            cfg.set(cfg.safetyNoticeVersion, SAFETY_NOTICE_VERSION)
        except Exception as e:
            logger.warning(
                f"failed to persist safety notice version: {e}", TAG)

    return True
