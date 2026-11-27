# coding:utf-8
"""客户端窗口修复（ctypes 版）回归

原来这一步是 `ShellExecuteW("runas", fix_lcu_window.exe)`：提权拉起一个未签名
exe。现在是 `app/common/win_window.py` 里纯 ctypes 调 user32。这里守住三件事：

覆盖：
  - 常量与 Win32 头文件一致（DPICHANGED / SWP_* / SM_* / SW_SHOWMINIMIZED）
  - 每个 user32 函数都显式声明了 argtypes / restype，句柄走 HWND（c_void_p），
    64 位下不会被截成 32 位
  - applyZoom 的所有失败分支**只返回 False，不抛异常**：zoom 非法 / 找不到窗口 /
    最小化 / 屏幕尺寸取不到 / SetWindowPos 任一返回 0
  - 成功分支的几何换算：1280*zoom x 720*zoom、居中、Cef 子窗口铺满 (0,0)
  - WM_DPICHANGED 的 wParam 是 MAKEWPARAM(dpi, dpi)
  - `fixLCUWindow()` 的返回值就是 applyZoom 的返回值，zoom 取自 getClientZoom
  - 仓库里不再有「起子进程查命令行」的回退 / fix_lcu_window.exe
    （这两个是杀软误报源，删了就别加回来）

全部用假 user32 / 假窗口，不碰真实客户端，也不写用户配置。

运行: python _dev_tests/test_win_window.py
"""
import asyncio
import ctypes
import functools
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

from ctypes import wintypes

from app.common import win_window as W

_failures = []

# 被删除的那个命令行工具名（拼出来，别让本文件自己被扫到）
_FORBIDDEN_CMD_TOOL = 'w' + 'mic'


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


def asInt(value):
    """把 ctypes 标量实例 / int 统一取成 Python int

    `int(c_ulonglong(...))` 在部分 ctypes 版本上会走 buffer 协议并抛 ValueError，
    只能读 `.value`（这里两个都兼容）。
    """
    if isinstance(value, int):
        return value

    if hasattr(value, 'value'):
        return int(value.value)

    return int.from_bytes(bytes(value), 'little')


class FakeUser32:
    """只实现 win_window 会调到的几个入口，记录调用顺序"""

    def __init__(self, setposResults=(1, 1), screen=(1920, 1080), dpi=144,
                 placement=(0, 1), placementOk=True, iconic=0):
        self.calls = []
        self.setposResults = list(setposResults)
        self.screen = screen
        self.dpi = dpi
        self.placement = placement
        self.placementOk = placementOk
        self.iconic = iconic

    def GetSystemMetrics(self, index):
        self.calls.append(('GetSystemMetrics', index))
        return self.screen[0] if index == W.SM_CXSCREEN else self.screen[1]

    def GetDpiForWindow(self, hwnd):
        self.calls.append(('GetDpiForWindow', hwnd))
        return self.dpi

    def SendMessageW(self, hwnd, msg, wParam, lParam):
        self.calls.append(('SendMessageW', hwnd, msg, wParam, lParam))
        return 0

    def SetWindowPos(self, hwnd, insertAfter, x, y, cx, cy, flags):
        self.calls.append(('SetWindowPos', hwnd, insertAfter, x, y, cx, cy, flags))

        if self.setposResults:
            return self.setposResults.pop(0)

        return 0

    def GetWindowPlacement(self, hwnd, ptr):
        """往调用方给的 WINDOWPLACEMENT 里写字（placement=(flags, showCmd)）"""
        self.calls.append(('GetWindowPlacement', hwnd))

        placement = ctypes.cast(ptr, ctypes.POINTER(W.WINDOWPLACEMENT)).contents
        placement.length = ctypes.sizeof(W.WINDOWPLACEMENT)
        placement.flags, placement.showCmd = self.placement

        return 1 if self.placementOk else 0

    def IsIconic(self, hwnd):
        self.calls.append(('IsIconic', hwnd))
        return self.iconic


class FakeHarness:
    """临时把模块里的 user32 / findClientWindows / isMinimized 换成假的

    `patchIsMinimized=False` 时不替换 `isMinimized`，让真的那个去问假 user32 ——
    这样才测得到 showCmd / IsIconic 的判定本身。
    """

    def __init__(self, clientHwnd=1111, cefHwnd=2222, minimized=False,
                 patchIsMinimized=True, **kwargs):
        self.clientHwnd = clientHwnd
        self.cefHwnd = cefHwnd
        self.minimized = minimized
        self.patchIsMinimized = patchIsMinimized
        self.user32 = FakeUser32(**kwargs)

    def __enter__(self):
        self._orig = (W.user32, W.findClientWindows, W.isMinimized,
                      W._HAS_GET_DPI_FOR_WINDOW)

        W.user32 = self.user32
        W._HAS_GET_DPI_FOR_WINDOW = True
        W.findClientWindows = lambda: (self.clientHwnd, self.cefHwnd)

        if self.patchIsMinimized:
            W.isMinimized = lambda hwnd: self.minimized

        return self

    def __exit__(self, *exc):
        (W.user32, W.findClientWindows, W.isMinimized,
         W._HAS_GET_DPI_FOR_WINDOW) = self._orig
        return False


# ------------------------------------------------------------------ 常量/签名
def testConstants():
    check('WM_DPICHANGED == 0x02E0', W.WM_DPICHANGED == 0x02E0, hex(W.WM_DPICHANGED))
    check('SWP_NOZORDER == 0x0004', W.SWP_NOZORDER == 0x0004)
    check('SWP_SHOWWINDOW == 0x0040', W.SWP_SHOWWINDOW == 0x0040)
    check('SW_SHOWMINIMIZED == 2', W.SW_SHOWMINIMIZED == 2)
    check('SM_CXSCREEN == 0', W.SM_CXSCREEN == 0)
    check('SM_CYSCREEN == 1', W.SM_CYSCREEN == 1)
    check('基准尺寸 1280x720',
          (W.BASE_WIDTH, W.BASE_HEIGHT) == (1280, 720))


def testSignatures():
    if not W.IS_WINDOWS:
        p('SKIP  非 Windows 平台，跳过签名检查')
        return

    required = ['FindWindowW', 'FindWindowExW', 'SendMessageW',
                'GetWindowPlacement', 'IsIconic', 'SetWindowPos',
                'GetSystemMetrics']

    for name in required:
        fn = getattr(W.user32, name, None)

        if fn is None:
            check(f'user32.{name} 存在', False)
            continue

        check(f'user32.{name} 声明了 argtypes', bool(fn.argtypes))
        check(f'user32.{name} 声明了 restype', fn.restype is not None)

    # 句柄绝不能落到默认的 c_int 上，否则 64 位指针被截断
    check('FindWindowW 返回 HWND(c_void_p)',
          W.user32.FindWindowW.restype is wintypes.HWND
          and wintypes.HWND is __import__('ctypes').c_void_p)
    check('FindWindowExW 返回 HWND(c_void_p)',
          W.user32.FindWindowExW.restype is wintypes.HWND)
    check('SetWindowPos 第一参数是 HWND',
          W.user32.SetWindowPos.argtypes[0] is wintypes.HWND)
    check('SendMessageW 第二参数是 UINT',
          W.user32.SendMessageW.argtypes[1] is wintypes.UINT)
    check('SendMessageW 的 lParam 是 LPARAM',
          W.user32.SendMessageW.argtypes[3] is wintypes.LPARAM)


# ------------------------------------------------------------------ 失败分支
def testNeverRaises():
    """非法 zoom 一律 False，不抛异常（UI 按钮直接调，抛了就炸界面）"""
    origFind = W.findClientWindows
    W.findClientWindows = lambda: (0, 0)

    try:
        for bad in (None, 'abc', '', -1, 0, 0.0, [], {}, object()):
            try:
                result = W.applyZoom(bad)
            except Exception as e:  # noqa: BLE001
                check(f'applyZoom({bad!r}) 不抛异常', False, repr(e))
                continue

            check(f'applyZoom({bad!r}) 返回 False', result is False, repr(result))
    finally:
        W.findClientWindows = origFind


def testNoWindow():
    with FakeHarness(clientHwnd=0, cefHwnd=0) as h:
        check('找不到客户端窗口 → False', W.applyZoom(1.0) is False)
        check('找不到窗口时一次 SetWindowPos 都不发',
              not [c for c in h.user32.calls if c[0] == 'SetWindowPos'])

    # 主窗口在、Cef 子窗口不在（页面还没加载完）也一样
    with FakeHarness(clientHwnd=1111, cefHwnd=0) as h:
        check('缺 Cef 子窗口 → False', W.applyZoom(1.0) is False)
        check('缺 Cef 子窗口时不发 SetWindowPos',
              not [c for c in h.user32.calls if c[0] == 'SetWindowPos'])


def testMinimized():
    with FakeHarness(minimized=True) as h:
        check('最小化 → False', W.applyZoom(1.0) is False)
        check('最小化时不发 SetWindowPos',
              not [c for c in h.user32.calls if c[0] == 'SetWindowPos'])
        check('最小化时不发 WM_DPICHANGED',
              not [c for c in h.user32.calls if c[0] == 'SendMessageW'])


def testIsMinimizedSemantics():
    """最小化要看 `showCmd`，不是 `flags`

    历史 helper exe 里写的是 `placement.flags == SW_SHOWMINIMIZED`，是同一个笔误：
    flags 是 WPF_* 位标志（1=SETMINPOSITION / 2=RESTORETOMAXIMIZED /
    4=ASYNCWINDOWPLACEMENT），跟「现在是不是最小化」无关。判错的后果是
    applyZoom 在最小化窗口上「报告成功但什么都没发生」。
    """
    # flags=0（没有任何 WPF_* 位）+ showCmd=2：真最小化
    with FakeHarness(patchIsMinimized=False, placement=(0, 2), iconic=1):
        check('flags=0 / showCmd=2 → 最小化', W.isMinimized(1234) is True)

    # flags=2（WPF_RESTORETOMAXIMIZED）+ showCmd=1：正常窗口，不能被误判
    with FakeHarness(patchIsMinimized=False, placement=(2, 1), iconic=0):
        check('flags=2 / showCmd=1 → 不是最小化', W.isMinimized(1234) is False)

    # flags=2（有最大化恢复点）同时 showCmd=2：依然是最小化，flags 不参与判断
    with FakeHarness(patchIsMinimized=False, placement=(2, 2), iconic=1):
        check('flags=2 / showCmd=2 → 最小化', W.isMinimized(1234) is True)

    # SW_SHOWMINNOACTIVE(7) 也是最小化，靠 IsIconic 兜住
    with FakeHarness(patchIsMinimized=False, placement=(0, 7), iconic=1):
        check('showCmd=7 + IsIconic=1 → 最小化', W.isMinimized(1234) is True)

    # IsIconic 交叉验证：showCmd 说正常、IsIconic 说图标态 → 按图标态算（保守）
    with FakeHarness(patchIsMinimized=False, placement=(0, 1), iconic=1):
        check('IsIconic 交叉验证：showCmd=1 但 IsIconic=1 → 最小化',
              W.isMinimized(1234) is True)

    # GetWindowPlacement 失败时不能当成「没最小化」直接放行，仍要问 IsIconic
    with FakeHarness(patchIsMinimized=False, placement=(0, 2), placementOk=False,
                     iconic=1):
        check('GetWindowPlacement 失败 → 用 IsIconic 兜住', W.isMinimized(1234) is True)

    with FakeHarness(patchIsMinimized=False, placement=(0, 1), placementOk=False,
                     iconic=0):
        check('两个判据都说没最小化 → False', W.isMinimized(1234) is False)

    with FakeHarness(patchIsMinimized=False):
        check('hwnd=0 → False', W.isMinimized(0) is False)


def testApplyZoomRealMinimizedCheck():
    """真跑一遍 isMinimized（不 patch），确认最小化窗口不会被报成功"""
    with FakeHarness(patchIsMinimized=False, placement=(0, 2), iconic=1) as h:
        check('真最小化 → applyZoom 返回 False', W.applyZoom(1.0) is False)
        check('真最小化 → 一次 SetWindowPos 都不发',
              not [c for c in h.user32.calls if c[0] == 'SetWindowPos'])
        check('真最小化 → 也不发 WM_DPICHANGED',
              not [c for c in h.user32.calls if c[0] == 'SendMessageW'])

    # 反过来：flags=2 但窗口正常 → 应该照常修复
    with FakeHarness(patchIsMinimized=False, placement=(2, 1), iconic=0) as h:
        check('flags=2 的正常窗口 → applyZoom 照常成功', W.applyZoom(1.0) is True)
        check('flags=2 的正常窗口 → 两次 SetWindowPos',
              len([c for c in h.user32.calls if c[0] == 'SetWindowPos']) == 2)



def testScreenMetrics():
    with FakeHarness(screen=(0, 0)) as h:
        check('屏幕尺寸取不到 → False', W.applyZoom(1.0) is False)
        check('屏幕尺寸为 0 时不发 SetWindowPos',
              not [c for c in h.user32.calls if c[0] == 'SetWindowPos'])


def testSetWindowPosFail():
    with FakeHarness(setposResults=(0, 1)) as h:
        check('主窗口 SetWindowPos 返回 0 → False', W.applyZoom(1.0) is False)
        check('主窗口失败后不再动 Cef 子窗口',
              len([c for c in h.user32.calls if c[0] == 'SetWindowPos']) == 1)

    with FakeHarness(setposResults=(1, 0)) as h:
        check('Cef 子窗口 SetWindowPos 返回 0 → False', W.applyZoom(1.0) is False)
        check('两次 SetWindowPos 都尝试过',
              len([c for c in h.user32.calls if c[0] == 'SetWindowPos']) == 2)


# ------------------------------------------------------------------ 成功分支
def testApplyZoomOk():
    with FakeHarness(screen=(1920, 1080), dpi=144) as h:
        check('正常路径返回 True', W.applyZoom(1.25) is True)

        setpos = [c for c in h.user32.calls if c[0] == 'SetWindowPos']
        check('恰好两次 SetWindowPos', len(setpos) == 2, str(len(setpos)))

        if len(setpos) == 2:
            # ('SetWindowPos', hwnd, insertAfter, x, y, cx, cy, flags)
            _, hwnd, _, x, y, cx, cy, flags = setpos[0]
            check('主窗口用客户端句柄', hwnd == 1111)
            check('尺寸 = 1280*1.25 x 720*1.25', (cx, cy) == (1600, 900), f'{cx}x{cy}')
            check('水平居中', x == (1920 - 1600) // 2, str(x))
            check('垂直居中', y == (1080 - 900) // 2, str(y))
            check('SWP_NOZORDER | SWP_SHOWWINDOW',
                  flags == (W.SWP_NOZORDER | W.SWP_SHOWWINDOW), hex(flags))

            _, hwnd2, _, x2, y2, cx2, cy2, flags2 = setpos[1]
            check('Cef 子窗口用子窗口句柄', hwnd2 == 2222)
            check('Cef 子窗口从 (0,0) 铺满', (x2, y2, cx2, cy2) == (0, 0, 1600, 900))
            check('Cef 子窗口 flags 一致', flags2 == flags)

        msgs = [c for c in h.user32.calls if c[0] == 'SendMessageW']
        check('两个窗口各发一条 WM_DPICHANGED', len(msgs) == 2, str(len(msgs)))

        if msgs:
            check('消息号是 WM_DPICHANGED', msgs[0][2] == W.WM_DPICHANGED)
            expectedWParam = (144 << 16) | 144
            # 假 user32 收到的是 WPARAM 实例（c_ulonglong），比大小前先取 int
            check('wParam = MAKEWPARAM(dpi, dpi)',
                  asInt(msgs[0][3]) == expectedWParam,
                  f'{asInt(msgs[0][3])} != {expectedWParam}')
            check('lParam 非 0（指向 RECT）', bool(msgs[0][4]))

        check('两个窗口句柄都覆盖到',
              {msgs[0][1], msgs[1][1]} == {1111, 2222} if len(msgs) == 2 else False)


def testZoomVariants():
    for zoom, (wantW, wantH) in ((0.8, (1024, 576)),
                                 (1.0, (1280, 720)),
                                 (1.25, (1600, 900))):
        with FakeHarness(screen=(2560, 1440)) as h:
            ok = W.applyZoom(zoom)
            setpos = [c for c in h.user32.calls if c[0] == 'SetWindowPos']

            check(f'zoom {zoom} 成功', ok is True)
            check(f'zoom {zoom} 尺寸 {wantW}x{wantH}',
                  bool(setpos) and (setpos[0][5], setpos[0][6]) == (wantW, wantH))


def testDpiFallback():
    """GetDpiForWindow 缺席（Win10 1607 之前）时用 96"""
    with FakeHarness() as h:
        W._HAS_GET_DPI_FOR_WINDOW = False
        W.applyZoom(1.0)

        msgs = [c for c in h.user32.calls if c[0] == 'SendMessageW']
        check('无 GetDpiForWindow 时 wParam 用 96',
              bool(msgs) and asInt(msgs[0][3]) == ((96 << 16) | 96),
              str(asInt(msgs[0][3])) if msgs else 'no message')


# ------------------------------------------------------------------ tools 层
def testFixLCUWindow():
    from app.lol import tools

    check('fixLCUWindow 是协程函数', inspect.iscoroutinefunction(tools.fixLCUWindow))
    check('旧的 fixLCUWindowViaExe 已删除',
          not hasattr(tools, 'fixLCUWindowViaExe'))

    calls = []
    results = {'apply': True}

    async def fakeGetClientZoom():
        return 1.25

    async def fakeGetClientZoomError():
        raise RuntimeError('not connected')

    def fakeApplyZoom(zoom):
        calls.append(zoom)
        return results['apply']

    origZoom = tools.connector.getClientZoom
    origApply = W.applyZoom

    def run():
        return asyncio.get_event_loop().run_until_complete(tools.fixLCUWindow())

    # 1) 正常: 拿到 zoom, applyZoom 成功
    tools.connector.getClientZoom = fakeGetClientZoom
    W.applyZoom = fakeApplyZoom

    try:
        result = run()
    finally:
        tools.connector.getClientZoom = origZoom
        W.applyZoom = origApply

    check('返回 applyZoom 的结果', result is True, repr(result))
    check('zoom 透传自 getClientZoom', calls == [1.25], str(calls))

    # 2) applyZoom 返回 False 也要原样传出去, 不能吞成 True
    calls.clear()
    results['apply'] = False

    tools.connector.getClientZoom = fakeGetClientZoom
    W.applyZoom = fakeApplyZoom

    try:
        result2 = run()
    finally:
        tools.connector.getClientZoom = origZoom
        W.applyZoom = origApply

    check('applyZoom 失败时返回 False', result2 is False, repr(result2))

    # 3) 客户端没连上时 getClientZoom 会抛（semaphore 为 None / 重试耗尽），
    #    这时也要返回 False，不能把异常甩进 qasync 的槽里
    calls.clear()
    results['apply'] = True

    tools.connector.getClientZoom = fakeGetClientZoomError
    W.applyZoom = fakeApplyZoom

    try:
        result3 = run()
    finally:
        tools.connector.getClientZoom = origZoom
        W.applyZoom = origApply

    check('取缩放比失败时返回 False（不抛异常）', result3 is False, repr(result3))
    check('取缩放比失败时不去动窗口', not calls, str(calls))


# ------------------------------------------------------------------ 仓库卫生
def testRepoClean():
    """「查进程命令行」那套回退与未签名 exe 是这次要根除的两个误报源

    关键字本身也拼接生成：这个文件自己不能被同一把尺子量出关键词。
    """
    hits = []
    exes = []

    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'dist', 'build')]

        for f in files:
            path = os.path.join(base, f)

            if f.lower() == 'fix_lcu_window.exe':
                exes.append(os.path.relpath(path, ROOT))

            if not f.endswith('.py'):
                continue

            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fp:
                    text = fp.read()
            except OSError:
                continue

            if _FORBIDDEN_CMD_TOOL in text.lower():
                hits.append(os.path.relpath(path, ROOT))

    check(f'全仓 .py 无 {_FORBIDDEN_CMD_TOOL} 调用', not hits, str(hits))
    check('安装包里不再有 fix_lcu_window.exe', not exes, str(exes))

    # bin 目录只剩一份说明：C 源码已删（它里面那句 `flags == SW_SHOWMINIMIZED`
    # 是带 bug 的旧实现，留着等于给后人留一个把 bug 复活的模板）
    binDir = os.path.join(ROOT, 'app', 'resource', 'bin')
    leftovers = sorted(os.listdir(binDir)) if os.path.isdir(binDir) else []

    check('app/resource/bin 下只有 readme.md', leftovers == ['readme.md'],
          str(leftovers))
    check('app/resource/bin 下没有可执行文件/源码',
          not [f for f in leftovers
               if f.lower().endswith(('.exe', '.dll', '.c', '.cpp', '.h'))],
          str(leftovers))


def main():
    p(f'平台: {sys.platform}  IS_WINDOWS={W.IS_WINDOWS}')

    testConstants()
    testSignatures()
    testNeverRaises()
    testNoWindow()
    testMinimized()
    testIsMinimizedSemantics()
    testApplyZoomRealMinimizedCheck()
    testScreenMetrics()
    testSetWindowPosFail()
    testApplyZoomOk()
    testZoomVariants()
    testDpiFallback()
    testFixLCUWindow()
    testRepoClean()

    print()

    if _failures:
        p(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    p('全部通过')


if __name__ == '__main__':
    main()
