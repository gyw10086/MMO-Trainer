# coding:utf-8
"""LCU 客户端窗口尺寸修复（纯 ctypes，进程内完成）

历史实现是随安装包分发的 `app/resource/bin/fix_lcu_window.exe`：
一个 58KB、**没有代码签名**的小程序，用 `ShellExecuteW(None, "runas", ...)`
提权拉起，然后做三件事：

1. `FindWindowW(L"RCLIENT", L"League of Legends")` 拿到客户端主窗口
2. `FindWindowExW(..., L"CefBrowserWindow", ...)` 拿到里面的浏览器子窗口
3. 给两个窗口发 `WM_DPICHANGED`，再 `SetWindowPos` 把主窗口按 `1280*zoom x 720*zoom`
   居中、子窗口铺满

从这里往下都是同样的事，只是改在**本进程内**用 ctypes 直接调 user32。
不再落盘 exe、不再提权、不再起子进程 —— 这是杀软误报的主要来源之一。

设计约定：
- 所有 ctypes 函数都显式声明 `argtypes` / `restype`。句柄在 64 位下必须走
  `wintypes.HWND`（即 `c_void_p`），否则默认的 `c_int` 会把指针截成 32 位，
  表现是「窗口明明存在却 SetWindowPos 失败」。
- 任何一步失败**只返回 False，不抛异常**：这是给 UI 按钮调的，
  失败要能安静地退化成一条 InfoBar 提示。
"""

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

from app.common.logger import logger

_TAG = "WinWindow"

# 项目根目录：app/common/win_window.py -> 上溯三级
_PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

# ---- Win32 常量 ------------------------------------------------------------
WM_DPICHANGED = 0x02E0
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040
SW_SHOWMINIMIZED = 2
SM_CXSCREEN = 0
SM_CYSCREEN = 1

# ---- 目标窗口 --------------------------------------------------------------
# 客户端主窗口的类名/标题，以及里面承载页面的 Chromium 子窗口类名
CLIENT_WINDOW_CLASS = "RCLIENT"
CLIENT_WINDOW_TITLE = "League of Legends"
CEF_WINDOW_CLASS = "CefBrowserWindow"

# 客户端窗口的基准尺寸，zoom 由 LCU 的 /riotclient/zoom-scale 给出
BASE_WIDTH = 1280
BASE_HEIGHT = 720

# GetDpiForWindow 缺席（Win10 1607 之前）时的兜底 DPI
DEFAULT_DPI = 96

# 非 Windows 平台（开发机上跑测试）直接短路，模块仍然可 import
IS_WINDOWS = sys.platform == "win32"

# 最近一次 applyZoom 失败时的 Win32 错误码（0 表示没失败过）
# 5 = ERROR_ACCESS_DENIED，通常意味着客户端以管理员身份运行而本程序没有
ERROR_ACCESS_DENIED = 5
lastErrorCode = 0


class WINDOWPLACEMENT(ctypes.Structure):
    """user32.WINDOWPLACEMENT 的等价结构体

    `length` 必须在调用 `GetWindowPlacement` 之前填成结构体字节数，
    否则函数直接返回 0。
    """

    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


user32 = None
_HAS_GET_DPI_FOR_WINDOW = False


def _initUser32():
    """按需绑定 user32 并显式声明全部签名

    返回 (user32 模块, 是否有 GetDpiForWindow)。
    """
    if not IS_WINDOWS:
        return None, False

    lib = ctypes.WinDLL("user32", use_last_error=True)

    lib.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    lib.FindWindowW.restype = wintypes.HWND

    lib.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                  wintypes.LPCWSTR, wintypes.LPCWSTR]
    lib.FindWindowExW.restype = wintypes.HWND

    lib.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
    lib.SendMessageW.restype = ctypes.c_ssize_t

    lib.GetWindowPlacement.argtypes = [
        wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
    lib.GetWindowPlacement.restype = wintypes.BOOL

    lib.IsIconic.argtypes = [wintypes.HWND]
    lib.IsIconic.restype = wintypes.BOOL

    lib.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                 ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    lib.SetWindowPos.restype = wintypes.BOOL

    lib.GetSystemMetrics.argtypes = [ctypes.c_int]
    lib.GetSystemMetrics.restype = ctypes.c_int

    try:
        # Win10 1607 起才有, 老系统上访问属性会抛 AttributeError
        lib.GetDpiForWindow.argtypes = [wintypes.HWND]
        lib.GetDpiForWindow.restype = wintypes.UINT
        hasDpi = True
    except AttributeError:
        hasDpi = False

    return lib, hasDpi


user32, _HAS_GET_DPI_FOR_WINDOW = _initUser32()


def findClientWindows():
    """返回 (客户端主窗口句柄, Cef 子窗口句柄)，任一找不到就是 (0, 0)"""
    if user32 is None:
        return 0, 0

    try:
        clientHwnd = user32.FindWindowW(
            CLIENT_WINDOW_CLASS, CLIENT_WINDOW_TITLE)

        if not clientHwnd:
            return 0, 0

        cefHwnd = user32.FindWindowExW(
            clientHwnd, None, CEF_WINDOW_CLASS, None)

        if not cefHwnd:
            return 0, 0

        return int(clientHwnd), int(cefHwnd)
    except Exception:
        # 跨进程窗口枚举理论上不会抛, 但绝不把异常甩给 UI
        return 0, 0


def isMinimized(hwnd):
    """窗口是否处于最小化状态

    最小化的窗口 SetWindowPos 是无效的（还可能是缩略图尺寸，坐标到了
    -32000 那一带），所以必须先排除，否则会出现「提示修复成功但其实什么都没发生」。

    判定看 `WINDOWPLACEMENT.showCmd`（SW_SHOWMINIMIZED = 2），**不是 flags**：
    `flags` 是 WPF_* 位标志（1=WPF_SETMINPOSITION、2=WPF_RESTORETOMAXIMIZED、
    4=WPF_ASYNCWINDOWPLACEMENT），跟「现在是不是最小化」没有关系。
    历史 helper exe（已随这次改动删除的 `fix_lcu_window.c`）里写的就是
    `placement.flags == SW_SHOWMINIMIZED`，是同一个从 C 继承下来的笔误。

    判定顺序：
    1. `showCmd == SW_SHOWMINIMIZED` → 最小化
    2. 否则问 `IsIconic`：它是「是不是图标态」的权威判据，还能覆盖
       `showCmd == SW_SHOWMINNOACTIVE(7)` 这种也算最小化的情况
    3. `GetWindowPlacement` 失败时不当作「没最小化」就算了：仍然走 `IsIconic`，
       只在 IsIconic 也说不是时才返回 False —— 宁可多拦一次，也不要假报成功
    """
    if user32 is None or not hwnd:
        return False

    try:
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)

        if user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            if placement.showCmd == SW_SHOWMINIMIZED:
                return True

        return bool(user32.IsIconic(hwnd))
    except Exception:
        return False


def _getDpiForWindow(hwnd):
    """单窗口 DPI，取不到就用 96（等价于 100% 缩放）"""
    if user32 is None or not _HAS_GET_DPI_FOR_WINDOW or not hwnd:
        return DEFAULT_DPI

    try:
        dpi = int(user32.GetDpiForWindow(hwnd) or 0)
    except Exception:
        return DEFAULT_DPI

    return dpi if dpi > 0 else DEFAULT_DPI


def _patchDpiChangedMessage(hwnd):
    """给窗口补一条 WM_DPICHANGED

    客户端从后台恢复或者被外部改过尺寸后，内部的 DPI 缓存会和实际窗口对不上，
    页面于是按错误的缩放渲染（糊 / 超出 / 留白）。补一条 DPI 变更消息让它重算。
    wParam = MAKEWPARAM(dpi, dpi)，lParam 指向一个全 0 的 RECT（建议尺寸，
    全 0 表示「尺寸我自己另行 SetWindowPos」）。
    """
    dpi = _getDpiForWindow(hwnd)

    # MAKEWPARAM(low, high) = (high << 16) | low, 这里高低位都是同一个 dpi
    wParam = wintypes.WPARAM((dpi << 16) | (dpi & 0xFFFF))

    rect = wintypes.RECT(0, 0, 0, 0)

    user32.SendMessageW(hwnd, WM_DPICHANGED, wParam, ctypes.addressof(rect))


def _recordError(stage, errorCode=0):
    """记下失败原因（只写日志 + 模块变量，绝不抛）"""
    global lastErrorCode

    lastErrorCode = int(errorCode or 0)

    if lastErrorCode == ERROR_ACCESS_DENIED:
        # UIPI: 高完整性级别（管理员）进程的窗口不接受低完整性进程的 SetWindowPos
        logger.warning(
            f'applyZoom failed at {stage}: access denied, '
            'the client is probably running as administrator', _TAG)
        return

    logger.warning(
        f'applyZoom failed at {stage}, win32 error={lastErrorCode}', _TAG)


def applyZoom(zoom):
    """按缩放比把客户端窗口重设成 1280*zoom x 720*zoom 并居中

    :param zoom: LCU `/riotclient/zoom-scale` 的返回值，如 0.8 / 1.0 / 1.25
    :return: True 表示两个窗口都调整成功；任何一步失败都返回 False（不抛异常）
    """
    global lastErrorCode

    lastErrorCode = 0

    if user32 is None:
        _recordError('load-user32')
        return False

    try:
        zoomValue = float(zoom)
    except (TypeError, ValueError):
        _recordError('parse-zoom')
        return False

    if zoomValue <= 0:
        _recordError('parse-zoom')
        return False

    clientHwnd, cefHwnd = findClientWindows()

    # 客户端没开 / 界面还没起来
    if not clientHwnd or not cefHwnd:
        _recordError('find-window')
        return False

    # 最小化的窗口调不动，让用户自己还原
    if isMinimized(clientHwnd):
        _recordError('window-minimized')
        return False

    screenWidth = int(user32.GetSystemMetrics(SM_CXSCREEN) or 0)
    screenHeight = int(user32.GetSystemMetrics(SM_CYSCREEN) or 0)

    if screenWidth <= 0 or screenHeight <= 0:
        _recordError('screen-metrics')
        return False

    targetWidth = int(BASE_WIDTH * zoomValue)
    targetHeight = int(BASE_HEIGHT * zoomValue)

    if targetWidth <= 0 or targetHeight <= 0:
        _recordError('target-size')
        return False

    x = (screenWidth - targetWidth) // 2
    y = (screenHeight - targetHeight) // 2

    try:
        _patchDpiChangedMessage(clientHwnd)
        _patchDpiChangedMessage(cefHwnd)
    except Exception:
        _recordError('send-dpi-changed', ctypes.get_last_error())
        return False

    # SWP_NOZORDER: 不抢前台/Z 序，避免把用户正在看的窗口压下去
    flags = SWP_NOZORDER | SWP_SHOWWINDOW

    try:
        # 客户端主窗口: 居中 + 目标尺寸
        if not user32.SetWindowPos(clientHwnd, None, x, y,
                                   targetWidth, targetHeight, flags):
            # 错误码 5 基本就是「客户端以管理员身份跑, 本程序没有」
            _recordError('setpos-client', ctypes.get_last_error())
            return False

        # 里面的 Chromium 子窗口: 铺满主窗口工作区
        if not user32.SetWindowPos(cefHwnd, None, 0, 0,
                                   targetWidth, targetHeight, flags):
            _recordError('setpos-cef', ctypes.get_last_error())
            return False
    except Exception:
        _recordError('setpos', ctypes.get_last_error())
        return False

    return True


# ---- 提权重启 --------------------------------------------------------------
# 客户端以管理员身份运行时，UIPI 会拦住低完整性进程发来的 SetWindowPos
# （表现为 lastErrorCode == 5）。旧方案里那个 exe 用 ShellExecuteW("runas")
# 自己提权所以能做；改到进程内以后，只能让**本程序**也提权，即重启一次。
# 注意：不往命令行塞「已重启过」之类的标记 —— 提权后 Windows 会直接放行，
# 不需要靠标记防循环；反而标记一旦残留就会让功能永久失效。

def isAdmin():
    """当前进程是否以管理员身份运行（拿不到权限信息就按「不是」处理）"""
    if not IS_WINDOWS:
        return False

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as e:
        logger.warning(f'IsUserAnAdmin failed: {e}', _TAG)
        return False


def buildRelaunchArgs(argv=None):
    """算出「以管理员身份重启」要传给 `sys.executable` 的参数列表

    两种运行形态必须分开算：

    - 打包后（`sys.frozen` 为真）：`sys.executable` 就是 MMO-Trainer.exe，
      命令行参数原样带过去（onedir / onefile 都一样）
    - 源码运行：`sys.executable` 是 python.exe，第一个参数必须是 main.py
      的绝对路径，否则重启后 python 会去解释 `--xxx` 这类参数
    """
    rest = list(sys.argv[1:] if argv is None else argv)

    if getattr(sys, 'frozen', False):
        return rest

    mainPath = os.path.abspath(os.path.join(_PROJECT_ROOT, 'main.py'))

    return [mainPath] + rest


def _shellExecuteRunAs(executable, params, cwd):
    """`ShellExecuteW("runas", ...)` 的薄封装

    单独抽出来是为了让测试替换这一层：测试里绝不能真的拉起进程或弹 UAC。
    """
    return int(ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, params, cwd, 1))


def restartAsAdmin(args=None):
    """以管理员身份重新拉起本程序（会弹 UAC）

    :param args: 传给解释器/程序的参数（不含可执行文件本身）。
                 None 时用 `buildRelaunchArgs()` 按当前运行形态自己算
    :return: True 表示 UAC 通过且进程已拉起；False 表示用户取消 UAC 或接口不可用
             —— 调用方拿到 False **不应该**退出程序
    """
    if not IS_WINDOWS:
        return False

    relaunchArgs = buildRelaunchArgs() if args is None else list(args)
    params = subprocess.list2cmdline(relaunchArgs)

    try:
        # ShellExecuteW 的返回值是 HINSTANCE 语义的整数：**> 32 才是成功**，
        # 0~32 全是错误码（含用户点「否」取消 UAC），不能写成 != 0
        result = _shellExecuteRunAs(sys.executable, params, os.getcwd())
    except Exception as e:
        logger.warning(f'restartAsAdmin failed: {e}', _TAG)
        return False

    if result <= 32:
        logger.warning(
            f'restartAsAdmin refused by user or failed, code={result}', _TAG)
        return False

    logger.info('restarting with administrator privileges', _TAG)

    return True
