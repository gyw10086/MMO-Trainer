# coding:utf-8
"""v2.2.48「降低杀软误报」加固的对抗性审计

这一轮改动不加功能，换掉的是三处会被杀软盯上的行为：
  1. 删掉「起系统命令行工具枚举进程参数」的凭据回退，失败改为抛异常
  2. 删掉随包分发的未签名 exe，窗口修复改在进程内用 ctypes 做
  3. 首启弹一次安全说明

**这个文件是独立写的对抗性验证，不重复工程师那四份测试的断言。**
刻意从「实现方最可能偷懒的地方」下手：

  §1 凭据异常契约：不返回 (None,None,None)；部分字段算失败；重试真的重试
     （把 time.sleep 换掉，不靠真实等待计时，避免慢机器上误判）
  §2 getLoginSummonerByPid 的降级：异常不穿到 UI 线程
  §3 applyZoom 的真实几何：**起一个真窗口子进程**（QT_QPA_PLATFORM=windows），
     用 GetWindowRect 读回真实矩形核对 1280*zoom x 720*zoom 与居中；
     拿不到真窗口时退回假 user32，但必须核对四个入参
  §4 buildRelaunchArgs 两种形态，且参数里不许有自造标记
  §5 restartAsAdmin 的 ShellExecuteW「> 32 才算成功」语义 + 异常不抛
  §6 安全说明版本门禁：0 / 1 / 脏值 / 负数，force=True 绝不写配置
  §7 提权分支的循环防护：isAdmin=True 时绝不弹框
  §8 回归专项：connector.start 的裸 except、message_box 吃 None、
     LockConfigCard 四种空目录、safety_notice 无主窗口
  §9 isMinimized 的 Win32 语义（本轮新代码，见文件末尾的缺陷说明）

约定：
  - 不碰真实 config.json：凡是构造 UI 的段先给模块级 cfg 换成 FakeCfg
  - 不弹 UAC：restartAsAdmin 那一层整体替换
  - offscreen 下 QFontDatabase 找不到字体，只断言尺寸/结构/调用次数

运行: python _dev_tests/test_hardening_audit.py
"""
import ctypes
import functools
import json
import os
import subprocess
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)

from app.common import util  # noqa: E402
from app.common.util import (LcuCredentialsUnavailable,  # noqa: E402
                            getPortTokenServerByPid)
from app.common import win_window as W  # noqa: E402
from app.common.signals import signalBus  # noqa: E402
import app.lol.connector as connectorModule  # noqa: E402
from app.lol.connector import connector  # noqa: E402
from app.components import message_box as MB  # noqa: E402
from app.view import safety_notice as S  # noqa: E402
from app.view import auxiliary_interface as A  # noqa: E402

_failures = []
_checks = 0

# 所有造出来的 QWidget / QDialog 都登记，退出前统一销毁。
# 不登记的话，解释器退出时 Qt 的析构顺序不可控，偶发 segfault（实测 8 次里 1 次
# EXIT=139，而所有断言都过了）—— 那是测试自身的坑，不是产品的坑。
_widgets = []


def keep(widget):
    if widget is not None:
        _widgets.append(widget)

    return widget


def disposeWidgets():
    """按登记顺序销毁所有控件，让退出时不留活着的 QWidget 给 Qt 收尾"""
    for widget in _widgets:
        try:
            widget.deleteLater()
        except RuntimeError:
            # 已经跟着父对象一起没了
            pass

    del _widgets[:]

    for _ in range(3):
        app.processEvents()


def check(name, ok, detail=''):
    global _checks
    _checks += 1
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


# --------------------------------------------------------------------- 小工具
class PatchModule:
    """临时替换模块属性（含不存在于模块里的名字也能塞）"""

    def __init__(self, module, **kwargs):
        self.module = module
        self.kwargs = kwargs

    def __enter__(self):
        self.orig = {}
        self.missing = []

        for key in self.kwargs:
            if hasattr(self.module, key):
                self.orig[key] = getattr(self.module, key)
            else:
                self.missing.append(key)

        for key, value in self.kwargs.items():
            setattr(self.module, key, value)

        return self

    def __exit__(self, *exc):
        for key, value in self.orig.items():
            setattr(self.module, key, value)

        for key in self.missing:
            delattr(self.module, key)

        return False


class FakeTime:
    """替掉 util.time：sleep 不真睡，只记账"""

    def __init__(self):
        self.sleeps = []
        self.time = __import__('time').time
        self.strftime = __import__('time').strftime
        self.localtime = __import__('time').localtime
        self.gmtime = __import__('time').gmtime

    def sleep(self, seconds):
        self.sleeps.append(seconds)


class FakePsutil:
    """按顺序吐出预设结果：Exception 实例 -> 抛，其它 -> 当返回值"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, pid):
        self.calls += 1

        if not self.results:
            raise AssertionError('pskutil fake 被多调了一次')

        item = self.results.pop(0)

        if isinstance(item, BaseException):
            raise item

        return item


OK_CREDS = ('50001', 'tok-abc', 'HN1')


class _Signal:
    def connect(self, *args, **kwargs):
        pass

    def disconnect(self, *args, **kwargs):
        pass


class _Item:
    def __init__(self, value=None):
        self.value = value
        self.valueChanged = _Signal()


class FakeCfg:
    """只认用得到的项；属性缓存，保证 get(cfg.x) 前后是同一个对象"""

    def __init__(self, **values):
        self._values = values
        self.writes = []

    def __getattr__(self, name):
        item = _Item(self._values.get(name))
        object.__setattr__(self, name, item)
        return item

    def get(self, item):
        return item.value

    def set(self, item, value):
        self.writes.append((item, value))
        item.value = value


# =========================================================== §1 凭据异常契约
def testCredsRaiseNeverTripleNone():
    """psutil 两次都抛 -> 必须抛 LcuCredentialsUnavailable，不许返回 (None,None,None)"""
    fake = FakePsutil([PermissionError('AccessDenied'), PermissionError('AccessDenied')])
    fakeTime = FakeTime()
    raised = False
    result = 'NOT-CALLED'
    cause = None
    message = ''

    with PatchModule(util, getPortTokenServerByPidViaPsutil=fake, time=fakeTime):
        try:
            result = getPortTokenServerByPid(7777)
        except LcuCredentialsUnavailable as e:
            raised = True
            cause = e.__cause__
            message = str(e)

    check('§1 两次都失败 -> 抛 LcuCredentialsUnavailable', raised, repr(result))
    check('§1 失败绝不返回 (None, None, None)',
          not (isinstance(result, tuple) and all(v is None for v in result)),
          repr(result))
    check('§1 恰好尝试两次', fake.calls == 2, str(fake.calls))
    check('§1 中间等待 0.15s（一次）', fakeTime.sleeps == [0.15], str(fakeTime.sleeps))
    check('§1 原始异常挂在 __cause__ 上',
          isinstance(cause, PermissionError), repr(cause))
    check('§1 异常信息里带 pid', '7777' in message, message)


def testCredsPartialIsFailure():
    """只有 port / 只有 token+server 都算没拿到"""
    cases = [
        ('只有 port', ('50001', None, None)),
        ('只有 token 和 server', (None, 'tok-abc', 'HN1')),
        ('空串', ('', '', '')),
    ]

    for label, partial in cases:
        fake = FakePsutil([partial, partial])
        raised = False

        with PatchModule(util, getPortTokenServerByPidViaPsutil=fake, time=FakeTime()):
            try:
                getPortTokenServerByPid(4242)
            except LcuCredentialsUnavailable:
                raised = True

        check(f'§1 {label} -> 抛异常', raised, repr(partial))
        check(f'§1 {label} -> 重试过（2 次）', fake.calls == 2, str(fake.calls))


def testCredsRetryActuallyRetries():
    """第一次挂、第二次成 -> 必须返回第二次的值（证明重试不是摆设）"""
    fake = FakePsutil([OSError('process gone'), OK_CREDS])
    fakeTime = FakeTime()

    with PatchModule(util, getPortTokenServerByPidViaPsutil=fake, time=fakeTime):
        result = getPortTokenServerByPid(4242)

    check('§1 第一次失败第二次成功 -> 返回成功值', result == OK_CREDS, repr(result))
    check('§1 调了两次', fake.calls == 2, str(fake.calls))
    check('§1 两次之间睡过一次 0.15s', fakeTime.sleeps == [0.15], str(fakeTime.sleeps))


def testCredsPartialThenOk():
    """第一次参数不全（客户端刚起）、第二次齐了"""
    fake = FakePsutil([('50001', 'tok-abc', None), OK_CREDS])

    with PatchModule(util, getPortTokenServerByPidViaPsutil=fake, time=FakeTime()):
        result = getPortTokenServerByPid(4242)

    check('§1 参数不全也会重试并拿到结果', result == OK_CREDS, repr(result))


def testCredsFirstTryOkNoSleep():
    fake = FakePsutil([OK_CREDS])
    fakeTime = FakeTime()

    with PatchModule(util, getPortTokenServerByPidViaPsutil=fake, time=fakeTime):
        result = getPortTokenServerByPid(4242)

    check('§1 一次就成功 -> 只调一次', fake.calls == 1, str(fake.calls))
    check('§1 一次就成功 -> 不睡', fakeTime.sleeps == [], str(fakeTime.sleeps))
    check('§1 一次就成功 -> 原样返回', result == OK_CREDS, repr(result))


def testCredsRealPsutilOnDeadPid():
    """不 mock psutil：用一个必然不存在的 pid 撞真实 NoSuchProcess"""
    deadPid = 0x7FFF0000
    fakeTime = FakeTime()
    raised = False
    cause = None

    with PatchModule(util, time=fakeTime):
        try:
            getPortTokenServerByPid(deadPid)
        except LcuCredentialsUnavailable as e:
            raised = True
            cause = e.__cause__
        except Exception as e:  # noqa: BLE001
            check('§1 真实 psutil 失败要走 LcuCredentialsUnavailable', False, repr(e))

    check('§1 真实 psutil（pid 不存在）-> LcuCredentialsUnavailable', raised,
          repr(cause))
    check('§1 真实路径也重试了一次', fakeTime.sleeps == [0.15], str(fakeTime.sleeps))


# ================================================ §2 getLoginSummonerByPid 降级
class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def json(self):
        if self.error:
            raise self.error

        return self.payload


class FakeRequests:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(self.payload, self.error)


def testLoginSummonerDegrades():
    # 1) 凭据拿不到 -> None 且不发请求
    def noCreds(pid):
        raise LcuCredentialsUnavailable('nope')

    fakeRequests = FakeRequests(payload={'gameName': 'Faker'})

    with PatchModule(connectorModule, getPortTokenServerByPid=noCreds,
                     requests=fakeRequests):
        result = connector.getLoginSummonerByPid(4242)

    check('§2 凭据拿不到 -> 返回 None', result is None, repr(result))
    check('§2 凭据拿不到 -> 一个请求都不发', fakeRequests.calls == [],
          str(fakeRequests.calls))

    # 2) requests 抛异常 -> None
    fakeRequests = FakeRequests(error=ConnectionResetError('reset'))

    with PatchModule(connectorModule, getPortTokenServerByPid=lambda pid: OK_CREDS,
                     requests=fakeRequests):
        result = connector.getLoginSummonerByPid(4242)

    check('§2 请求抛异常 -> 返回 None 不往外抛', result is None, repr(result))

    # 3) .json() 抛异常（返回了非 JSON 的 HTML 登录页）-> None
    fakeRequests = FakeRequests(error=json.JSONDecodeError('x', '', 0))

    with PatchModule(connectorModule, getPortTokenServerByPid=lambda pid: OK_CREDS,
                     requests=fakeRequests):
        result = connector.getLoginSummonerByPid(4242)

    check('§2 响应不是 JSON -> 返回 None', result is None, repr(result))

    # 4) 走真实 util 链路（psutil 失败）也要降级成 None
    fakeRequests = FakeRequests(payload={'gameName': 'Faker'})
    fake = FakePsutil([OSError('gone'), OSError('gone')])

    with PatchModule(util, getPortTokenServerByPidViaPsutil=fake, time=FakeTime()):
        with PatchModule(connectorModule, requests=fakeRequests):
            result = connector.getLoginSummonerByPid(4242)

    check('§2 真实 util 链路失败 -> 仍然返回 None', result is None, repr(result))
    check('§2 真实 util 链路失败 -> 不发请求', fakeRequests.calls == [],
          str(fakeRequests.calls))


# ============================================== §3 applyZoom 的真实几何
REAL_WINDOW_CHILD = r'''
import ctypes
import json
import os
import sys
from ctypes import wintypes

ROOT = r"{ROOT}"
sys.path.insert(0, ROOT)

# offscreen 平台下 winId() 是假的（返回 1），GetWindowRect 全是 0，
# 所以这一段必须用真窗口平台跑
os.environ['QT_QPA_PLATFORM'] = 'windows'

from PyQt5.QtWidgets import QApplication, QWidget

app = QApplication(sys.argv)

from app.common import win_window as W

lib = ctypes.windll.user32
lib.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
lib.GetWindowRect.restype = wintypes.BOOL


def rectOf(hwnd):
    r = wintypes.RECT()

    if not lib.GetWindowRect(hwnd, ctypes.byref(r)):
        return None

    return [int(r.left), int(r.top), int(r.right), int(r.bottom)]


def placementOf(hwnd):
    pl = W.WINDOWPLACEMENT()
    pl.length = ctypes.sizeof(W.WINDOWPLACEMENT)
    ok = W.user32.GetWindowPlacement(hwnd, ctypes.byref(pl))

    return [int(ok), int(pl.flags), int(pl.showCmd)]


out = {"zooms": {}}

client = QWidget()
cef = QWidget()

for w in (client, cef):
    w.resize(420, 30)
    w.show()

app.processEvents()

ch = int(client.winId())
eh = int(cef.winId())

out["hwnd"] = [ch, eh]
out["realHandle"] = bool(ch > 1 and eh > 1)
out["screen"] = [int(W.user32.GetSystemMetrics(W.SM_CXSCREEN)),
                 int(W.user32.GetSystemMetrics(W.SM_CYSCREEN))]

W.findClientWindows = lambda: (ch, eh)

for zoom in {ZOOMS}:
    ok = W.applyZoom(zoom)
    app.processEvents()
    out["zooms"][str(zoom)] = {
        "ok": bool(ok),
        "lastError": int(W.lastErrorCode),
        "client": rectOf(ch),
        "cef": rectOf(eh),
    }

out["placementNormal"] = placementOf(ch)
out["isMinimizedNormal"] = bool(W.isMinimized(ch))

# 最小化之后：既要判定为「最小化」，applyZoom 也必须立刻放弃
client.showMinimized()
app.processEvents()

out["placementMinimized"] = placementOf(ch)
out["isMinimizedMinimized"] = bool(W.isMinimized(ch))
out["minimizedRectBefore"] = rectOf(ch)

okMin = W.applyZoom(1.0)
app.processEvents()

out["minimizedApplyOk"] = bool(okMin)
out["minimizedApplyError"] = int(W.lastErrorCode)
out["minimizedRectAfter"] = rectOf(ch)
out["minimizedStillIconic"] = bool(lib.IsIconic(ch))

print("RESULT:" + json.dumps(out))
'''


def runRealWindowProbe(zooms=(1.25, 0.8, 1.0)):
    """起一个真窗口子进程，返回 (是否拿到真窗口, 结果字典)

    拿不到（无桌面 / 无 win32 平台插件）就返回 (False, {})，由调用方退回假 user32。
    """
    code = REAL_WINDOW_CHILD.replace('{ROOT}', ROOT).replace(
        '{ZOOMS}', repr(list(zooms)))

    env = dict(os.environ)
    env['QT_QPA_PLATFORM'] = 'windows'
    env['PYTHONIOENCODING'] = 'utf-8'

    try:
        proc = subprocess.run([sys.executable, '-c', code], capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=180, env=env, cwd=ROOT)
    except Exception as e:  # noqa: BLE001
        p(f'  (真窗口子进程起不来: {e!r})')
        return False, {}

    for line in (proc.stdout or '').splitlines():
        if line.startswith('RESULT:'):
            data = json.loads(line[len('RESULT:'):])

            if not data.get('realHandle'):
                return False, data

            return True, data

    p('  (子进程没有输出 RESULT, stderr 尾部:)')
    p((proc.stderr or '')[-800:])

    return False, {}


class RecordingUser32:
    """假 user32：只记录调用，SetWindowPos 一律成功

    offscreen 下真窗口拿不到，但至少要卡住「传给 Win32 的四个入参」。
    """

    def __init__(self, screen=(2560, 1440), dpi=96):
        self.screen = screen
        self.dpi = dpi
        self.setPosCalls = []
        self.messages = []

    def FindWindowW(self, *args):
        return 0

    def FindWindowExW(self, *args):
        return 0

    def GetWindowPlacement(self, hwnd, ptr):
        return 0

    def IsIconic(self, hwnd):
        return 0

    def GetSystemMetrics(self, index):
        return self.screen[index]

    def GetDpiForWindow(self, hwnd):
        return self.dpi

    def SendMessageW(self, hwnd, msg, wParam, lParam):
        self.messages.append((int(hwnd), int(msg), int(wParam), int(lParam)))
        return 0

    def SetWindowPos(self, hwnd, insertAfter, x, y, cx, cy, flags):
        self.setPosCalls.append((int(hwnd), insertAfter, int(x), int(y),
                                 int(cx), int(cy), int(flags)))
        return 1


def testApplyZoomRealGeometry():
    """真窗口：1280*zoom x 720*zoom、居中；Cef 子窗口 (0,0) 铺满"""
    available, data = runRealWindowProbe()

    if not available:
        p('  (本机拿不到真窗口，§3 几何改走假 user32 核对入参)')
        testApplyZoomArguments()
        return

    screenWidth, screenHeight = data['screen']

    for zoom, expectSize in ((1.25, (1600, 900)), (0.8, (1024, 576)),
                             (1.0, (1280, 720))):
        item = data['zooms'][str(zoom)]
        targetWidth, targetHeight = expectSize
        x = (screenWidth - targetWidth) // 2
        y = (screenHeight - targetHeight) // 2

        check(f'§3 [真窗口] applyZoom({zoom}) 返回 True', item['ok'] is True,
              str(item))
        check(f'§3 [真窗口] zoom={zoom} 不受权限拦截',
              item['lastError'] == 0, str(item['lastError']))
        check(f'§3 [真窗口] zoom={zoom} 主窗口尺寸 = {targetWidth}x{targetHeight}',
              item['client'] == [x, y, x + targetWidth, y + targetHeight],
              str(item['client']))
        check(f'§3 [真窗口] zoom={zoom} 主窗口居中（x={x}, y={y}）',
              item['client'][:2] == [x, y], str(item['client']))
        check(f'§3 [真窗口] zoom={zoom} Cef 子窗口铺满 (0,0)',
              item['cef'] == [0, 0, targetWidth, targetHeight], str(item['cef']))

    # 顺带取一条真实窗口的最小化证据（§9 的缺陷在这里被实机复现）
    check('§3 [真窗口] 正常窗口 placement = (ok=1, flags=0, showCmd=1)',
          data['placementNormal'] == [1, 0, 1], str(data['placementNormal']))
    check('§3 [真窗口] 正常窗口 isMinimized = False',
          data['isMinimizedNormal'] is False, str(data['isMinimizedNormal']))
    check('§3 [真窗口] 最小化后 showCmd 变成 2（Win32 用 showCmd 表示最小化）',
          data['placementMinimized'][2] == W.SW_SHOWMINIMIZED,
          str(data['placementMinimized']))
    check('§9 [缺陷·已修] 真机最小化窗口 -> isMinimized 应为 True',
          data['isMinimizedMinimized'] is True,
          f"placement={data['placementMinimized']} 实际返回 "
          f"{data['isMinimizedMinimized']!r}")
    check('§3 [真窗口] 最小化时 applyZoom 必须返回 False',
          data['minimizedApplyOk'] is False, str(data['minimizedApplyOk']))
    check('§3 [真窗口] 最小化时不许报「修复成功」的 lastErrorCode=0 假成功',
          data['minimizedApplyOk'] is False or data['minimizedApplyError'] != 0,
          f"ok={data['minimizedApplyOk']} err={data['minimizedApplyError']}")
    check('§3 [真窗口] 最小化时窗口矩形一个像素都不许动',
          data['minimizedRectAfter'] == data['minimizedRectBefore'],
          f"{data['minimizedRectBefore']} -> {data['minimizedRectAfter']}")
    check('§3 [真窗口] 最小化状态没被修好（说明确实没动它）',
          data['minimizedStillIconic'] is True, str(data['minimizedStillIconic']))


def testApplyZoomArguments():
    """拿不到真窗口时的退路：核对传给 SetWindowPos 的四个入参 + flags"""
    fake = RecordingUser32(screen=(2560, 1440))

    with PatchModule(W, user32=fake,
                     findClientWindows=lambda: (101, 202),
                     isMinimized=lambda hwnd: False,
                     lastErrorCode=0):
        ok = W.applyZoom(1.25)

    check('§3 [入参] applyZoom(1.25) 返回 True', ok is True, repr(ok))
    check('§3 [入参] SetWindowPos 调了两次', len(fake.setPosCalls) == 2,
          str(fake.setPosCalls))

    if len(fake.setPosCalls) == 2:
        clientCall, cefCall = fake.setPosCalls
        check('§3 [入参] 主窗口 = (480, 270, 1600, 900)', clientCall[1:6] == (
            None, 480, 270, 1600, 900), str(clientCall))
        check('§3 [入参] Cef 子窗口 = (0, 0, 1600, 900)',
              cefCall[1:6] == (None, 0, 0, 1600, 900), str(cefCall))
        check('§3 [入参] flags = SWP_NOZORDER | SWP_SHOWWINDOW (0x44)',
              clientCall[6] == 0x44 and cefCall[6] == 0x44,
              f'{clientCall[6]:#x} / {cefCall[6]:#x}')
        check('§3 [入参] 两个窗口各收到一条 WM_DPICHANGED',
              [m[1] for m in fake.messages] == [W.WM_DPICHANGED] * 2,
              str(fake.messages))
        check('§3 [入参] wParam = MAKEWPARAM(dpi, dpi) 且 dpi=96',
              all(m[2] == ((96 << 16) | 96) for m in fake.messages),
              str([m[2] for m in fake.messages]))


# ================================================== §4 buildRelaunchArgs
def _withSys(argv, frozen):
    """临时改 sys.argv / sys.frozen"""

    class Ctx:
        def __enter__(self):
            self.hadArgv = sys.argv
            self.hadFrozen = hasattr(sys, 'frozen')
            self.frozenValue = getattr(sys, 'frozen', None)

            sys.argv = list(argv)

            if frozen is None:
                if self.hadFrozen:
                    del sys.frozen
            else:
                sys.frozen = frozen

            return self

        def __exit__(self, *exc):
            sys.argv = self.hadArgv

            if self.hadFrozen:
                sys.frozen = self.frozenValue
            elif hasattr(sys, 'frozen'):
                del sys.frozen

            return False

    return Ctx()


MARKERS = ('admin', 'elevat', 'runas', 'relaunch', 'restart')


def testRelaunchArgsFrozen():
    argv = ['C:/tools/MMO-Trainer.exe', '--tray', '--log-level', 'debug']

    with _withSys(argv, frozen=True):
        args = W.buildRelaunchArgs()

    check('§4 打包后（frozen）参数原样透传',
          args == ['--tray', '--log-level', 'debug'], str(args))
    check('§4 打包后不重复塞可执行文件',
          not any(a.endswith('.exe') for a in args), str(args))


def testRelaunchArgsSource():
    argv = ['main.py', '--tray']

    with _withSys(argv, frozen=None):
        args = W.buildRelaunchArgs()

    check('§4 源码运行 -> 第一个参数是 main.py 绝对路径',
          bool(args) and os.path.isabs(args[0])
          and args[0].replace('\\', '/').endswith('/main.py'), str(args[:1]))
    check('§4 源码运行 -> main.py 真实存在', os.path.exists(args[0]), str(args[:1]))
    check('§4 源码运行 -> 其余参数保留', args[1:] == ['--tray'], str(args[1:]))

    with _withSys(argv, frozen=None):
        explicit = W.buildRelaunchArgs(['--x', '1'])

    check('§4 显式传 argv 时不受 sys.argv 影响',
          explicit == [os.path.abspath(os.path.join(
              os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
              'main.py')), '--x', '1'], str(explicit))


def testRelaunchArgsNoSelfMadeMarker():
    """不许往命令行塞「已重启/已提权」之类的自造标记（残留会让功能永久失效）"""
    for frozen in (True, None):
        with _withSys(['main.py', '--tray'], frozen=frozen):
            args = W.buildRelaunchArgs()

        hits = [a for a in args if any(m in a.lower() for m in MARKERS)]
        check(f'§4 frozen={frozen} 时参数里没有自造标记', not hits, str(hits))


# ================================================== §5 restartAsAdmin 语义
def testRestartAsAdminReturnContract():
    calls = []

    def fakeShellExecute(executable, params, cwd):
        calls.append((executable, params, cwd))
        return fakeShellExecute.result

    fakeShellExecute.result = 42

    with _withSys(['main.py'], frozen=None):
        with PatchModule(W, _shellExecuteRunAs=fakeShellExecute):
            for code in (0, 2, 5, 31, 32):
                fakeShellExecute.result = code
                result = W.restartAsAdmin(['--tray'])
                check(f'§5 ShellExecuteW 返回 {code} -> False', result is False,
                      repr(result))

            fakeShellExecute.result = 32
            check('§5 32 是错误码（不能写成 != 0）',
                  W.restartAsAdmin(['--tray']) is False)

            for code in (33, 42):
                fakeShellExecute.result = code
                result = W.restartAsAdmin(['--tray'])
                check(f'§5 ShellExecuteW 返回 {code} -> True', result is True,
                      repr(result))

            def boom(executable, params, cwd):
                raise OSError('shell32 不可用')

            fakeShellExecute.result = 42
            with PatchModule(W, _shellExecuteRunAs=boom):
                try:
                    result = W.restartAsAdmin(['--tray'])
                except Exception as e:  # noqa: BLE001
                    check('§5 ShellExecuteW 抛异常 -> 不往外抛', False, repr(e))
                else:
                    check('§5 ShellExecuteW 抛异常 -> 返回 False',
                          result is False, repr(result))

            with PatchModule(W, IS_WINDOWS=False):
                result = W.restartAsAdmin(['--tray'])
            check('§5 非 Windows -> False 且不调 ShellExecuteW',
                  result is False, repr(result))

            # args 缺省时要自己按运行形态算什么参数（源码运行 -> 带 main.py）
            calls.clear()
            fakeShellExecute.result = 42
            W.restartAsAdmin()

    check('§5 提权目标始终是 sys.executable',
          bool(calls) and all(c[0] == sys.executable for c in calls),
          str(set(c[0] for c in calls)))
    check('§5 args 缺省时参数里带 main.py（源码运行的形态）',
          any('main.py' in c[1] for c in calls), str(calls[:1]))
    check('§5 工作目录透传下去',
          all(c[2] for c in calls), str(calls[:1]))


# ================================================== §6 安全说明版本门禁
class FakeBox:
    def __init__(self):
        self.execCount = 0
        self.parent = 'UNSET'

    def exec(self):
        self.execCount += 1
        return 1


def _noticeCase(version, force=False, cfgSetRaises=False):
    fakeCfg = FakeCfg(safetyNoticeVersion=version)
    box = FakeBox()

    def factory(parent=None):
        box.parent = parent
        return box

    if cfgSetRaises:
        def boomSet(item, value):
            raise OSError('config.json 只读')

        fakeCfg.set = boomSet

    with PatchModule(S, cfg=fakeCfg, buildSafetyNoticeBox=factory):
        try:
            shown = S.showSafetyNotice(parent=object(), force=force)
        except Exception as e:  # noqa: BLE001
            return 'RAISED:' + repr(e), box, fakeCfg

    return shown, box, fakeCfg


def testSafetyNoticeGate():
    shown, box, fakeCfg = _noticeCase(0)
    check('§6 配置 0 -> 弹', shown is True, repr(shown))
    check('§6 配置 0 -> exec 一次', box.execCount == 1, str(box.execCount))
    check('§6 配置 0 -> 写回当前版本',
          [w[1] for w in fakeCfg.writes] == [S.SAFETY_NOTICE_VERSION],
          str(fakeCfg.writes))

    shown, box, fakeCfg = _noticeCase(S.SAFETY_NOTICE_VERSION)
    check('§6 配置已是最新 -> 不弹', shown is False, repr(shown))
    check('§6 配置已是最新 -> 不 exec', box.execCount == 0, str(box.execCount))
    check('§6 配置已是最新 -> 不写配置', fakeCfg.writes == [], str(fakeCfg.writes))

    shown, box, fakeCfg = _noticeCase('abc')
    check('§6 脏值 "abc" -> 当成没弹过（弹）', shown is True, repr(shown))
    check('§6 脏值 "abc" -> 写回正确版本',
          [w[1] for w in fakeCfg.writes] == [S.SAFETY_NOTICE_VERSION],
          str(fakeCfg.writes))

    shown, box, fakeCfg = _noticeCase(-1)
    check('§6 负数 -1 -> 弹', shown is True, repr(shown))
    check('§6 负数 -1 -> 写回正确版本',
          [w[1] for w in fakeCfg.writes] == [S.SAFETY_NOTICE_VERSION],
          str(fakeCfg.writes))

    shown, box, fakeCfg = _noticeCase(None)
    check('§6 配置是 None -> 弹', shown is True, repr(shown))

    shown, box, fakeCfg = _noticeCase(S.SAFETY_NOTICE_VERSION + 3)
    check('§6 配置版本更高（装过新版又回退）-> 不弹', shown is False, repr(shown))


def testSafetyNoticeForceNeverWrites():
    for version in (0, S.SAFETY_NOTICE_VERSION, 'abc', -1):
        shown, box, fakeCfg = _noticeCase(version, force=True)
        check(f'§6 force=True（配置={version!r}）-> 每次都弹',
              shown is True and box.execCount == 1, f'{shown!r}/{box.execCount}')
        check(f'§6 force=True（配置={version!r}）-> 绝不写配置',
              fakeCfg.writes == [], str(fakeCfg.writes))

    # 写配置炸了（只读 / 磁盘满）也不能把异常甩给 UI
    shown, box, _ = _noticeCase(0, cfgSetRaises=True)
    check('§6 写配置失败 -> 不影响弹窗、不抛异常', shown is True, repr(shown))
    check('§6 写配置失败 -> 弹窗还是弹了', box.execCount == 1, str(box.execCount))


def testSafetyNoticeNoParent():
    """没有主窗口时（parent=None）不能崩

    `showSafetyNotice(parent=None)` 会退化成 `QApplication.activeWindow()`；
    一个窗口都没显示时它是 None，于是真的去 `MessageBox(..., None)`——
    qfluentwidgets 的 MessageBoxBase 直接 `parent.width()`，**这里会抛**。
    产品里两个调用点都传了 parent 且外面包了 try/except，所以线上看不到崩溃，
    但「首启说明」会被静默跳过。记为低危缺陷。
    """
    fakeCfg = FakeCfg(safetyNoticeVersion=0)
    box = FakeBox()

    with PatchModule(S, cfg=fakeCfg, buildSafetyNoticeBox=lambda parent=None: box):
        try:
            shown = S.showSafetyNotice(parent=None)
        except Exception as e:  # noqa: BLE001
            check('§8 parent=None 且弹窗工厂健壮时不崩', False, repr(e))
        else:
            check('§8 parent=None 且弹窗工厂健壮时不崩', shown is True, repr(shown))

    # 真实 MessageBox：无活动窗口时到底行不行
    hasActive = QApplication.activeWindow() is not None

    try:
        realBox = keep(S.buildSafetyNoticeBox(None))
        keep(getattr(realBox, '_safetyParent', None))
    except Exception as e:  # noqa: BLE001
        check('§8 [缺陷·低] 无活动窗口时 MessageBox(parent=None) 造得出来',
              False, f'{e!r}（activeWindow 存在={hasActive}）')
    else:
        check('§8 [缺陷·低] 无活动窗口时 MessageBox(parent=None) 造得出来', True)
        check('§8 安全说明正文有四条要点',
              S.buildContent().count('•') == 4, str(S.buildContent().count('•')))
        check('§8 对话框宽度够放正文', realBox.width() > 200, str(realBox.width()))


def testSafetyNoticeParentFallback():
    """parent 归一：显式 parent > 活动窗口 > 临时顶层 QWidget > 干脆不弹

    这层兜底是修缺陷 2 加的，必须验证「临时 widget 活得比函数调用久」——
    只被局部变量引用时，buildSafetyNoticeBox 一返回就会被 GC，随后 Qt 里
    parent 被 delete 会连带销毁子对话框（RuntimeError: wrapped C/C++ object
    has been deleted）。
    """
    sentinel = keep(QWidget())
    check('§6 _resolveParent 幂等（显式 parent 原样返回）',
          S._resolveParent(sentinel) is sentinel)

    # 没有活动窗口 -> 兜一个隐藏的 Tool 窗口，且必须由模块级变量引用住
    with PatchModule(S, _activeWindow=lambda: None):
        box = keep(S.buildSafetyNoticeBox(None))
        keep(getattr(box, '_safetyParent', None))
        firstParent = S._fallbackParent
        secondParent = S._resolveParent(None)

    check('§6 无活动窗口时 buildSafetyNoticeBox(None) 造得出对话框',
          box is not None, repr(box))

    if box is not None:
        parent = getattr(box, '_safetyParent', None)
        check('§6 兜底父窗口是 QWidget', isinstance(parent, QWidget), repr(parent))
        check('§6 兜底父窗口留了模块级引用（主保活手段）',
              S._fallbackParent is parent, repr(S._fallbackParent))
        check('§6 兜底父窗口是 Qt.Tool（不进任务栏、不抢焦点）',
              bool(parent.windowFlags() & Qt.Tool) if parent else False,
              repr(parent.windowFlags()))
        check('§6 兜底父窗口没有被 show 出来',
              parent is not None and parent.isVisible() is False,
              repr(parent.isVisible() if parent else None))
        check('§6 多次调用复用同一个兜底父窗口（不越造越多）',
              secondParent is firstParent, f'{firstParent} vs {secondParent}')

        # 强制回收一轮：如果父窗口没被引用住，这里就该炸
        import gc
        gc.collect()

        try:
            label = box.contentLabel
            text = label.text()
        except Exception as e:  # noqa: BLE001
            check('§6 强制 GC 之后对话框仍然可用（父窗口保活生效）', False, repr(e))
        else:
            check('§6 强制 GC 之后对话框仍然可用（父窗口保活生效）',
                  '不读取游戏内存' in text, text[:40])

        check('§6 上下文文本没被截（尺寸仍按 sizeHint）',
              box.widget.height() == box.widget.sizeHint().height(),
              f'{box.widget.height()} vs {box.widget.sizeHint().height()}')

    # 连 QApplication 都没有：不许造 QWidget（造不出来会抛），安静返回
    class NoApplication:
        @staticmethod
        def instance():
            return None

        @staticmethod
        def activeWindow():
            return None

    fakeCfg = FakeCfg(safetyNoticeVersion=0)
    box = FakeBox()

    with PatchModule(S, QApplication=NoApplication):
        built = S.buildSafetyNoticeBox(None)

    check('§6 没有 QApplication -> buildSafetyNoticeBox 返回 None（不硬造 QWidget）',
          built is None, repr(built))

    with PatchModule(S, QApplication=NoApplication, cfg=fakeCfg,
                     buildSafetyNoticeBox=lambda parent=None: box):
        shown = S.showSafetyNotice(parent=None)

    check('§6 没有 QApplication -> showSafetyNotice 返回 False', shown is False,
          repr(shown))
    check('§6 没有 QApplication -> 一个对话框都不造', box.execCount == 0,
          str(box.execCount))
    check('§6 没有 QApplication -> 不写配置', fakeCfg.writes == [],
          str(fakeCfg.writes))


# ============================================ §7 提权分支的循环防护
class RecordingInfoBar:
    """替掉 InfoBar：只记录，不建真控件"""

    def __init__(self):
        self.warnings = []
        self.successes = []

    def warning(self, **kwargs):
        self.warnings.append(kwargs)

    def success(self, **kwargs):
        self.successes.append(kwargs)

    def error(self, **kwargs):
        pass


class RecordingButton:
    def __init__(self):
        self.text = None

    def setText(self, text):
        self.text = text

    def hide(self):
        pass


class RecordingMessageBox:
    """替掉 MessageBox：记录弹了几次、用户点了什么"""

    instances = []
    answer = None  # None 表示按「确定」处理

    # qfluentwidgets.MessageBox 用 Accepted / Rejected 两个类属性区分返回值
    Accepted = 1
    Rejected = 0

    def __init__(self, title, content, parent=None):
        self.title = title
        self.content = content
        self.parent = parent
        self.execCount = 0
        self.yesButton = RecordingButton()
        self.cancelButton = RecordingButton()
        RecordingMessageBox.instances.append(self)

    def exec(self):
        self.execCount += 1
        return 1 if RecordingMessageBox.answer is None else RecordingMessageBox.answer


class RecordingApp:
    """替掉 QApplication：记录 quit()"""

    quits = 0

    @classmethod
    def instance(cls):
        return cls

    @classmethod
    def quit(cls):
        cls.quits += 1


def _runFixFailed(isAdmin, lastErrorCode, accepted=True, restartResult=True):
    """跑一遍「修复失败」分支，返回 (弹框次数, 提权调用次数, quit 次数, infoBar)"""
    card = keep(A.FixClientDpiCard('title', 'content', None))
    bar = RecordingInfoBar()
    RecordingMessageBox.instances = []
    RecordingMessageBox.answer = 1 if accepted else 0
    RecordingApp.quits = 0
    restartCalls = []

    def fakeRestart(args=None):
        restartCalls.append(args)
        return restartResult

    with PatchModule(A, InfoBar=bar, MessageBox=RecordingMessageBox,
                     QApplication=RecordingApp):
        with PatchModule(W, isAdmin=lambda: isAdmin,
                         lastErrorCode=lastErrorCode,
                         restartAsAdmin=fakeRestart,
                         buildRelaunchArgs=lambda argv=None: ['main.py']):
            card._FixClientDpiCard__onFixFailed()

    return (len(RecordingMessageBox.instances), len(restartCalls),
            RecordingApp.quits, bar)


def testFixFailedNoRestartLoopForAdmin():
    asked, restarts, quits, bar = _runFixFailed(isAdmin=True, lastErrorCode=5)
    check('§7 已是管理员 + 错误码 5 -> 不弹询问框', asked == 0, str(asked))
    check('§7 已是管理员 + 错误码 5 -> 不调 restartAsAdmin', restarts == 0,
          str(restarts))
    check('§7 已是管理员 + 错误码 5 -> 不退出程序', quits == 0, str(quits))
    check('§7 已是管理员 + 错误码 5 -> 给一条 warning 提示', len(bar.warnings) == 1,
          str(len(bar.warnings)))

    asked, restarts, quits, bar = _runFixFailed(isAdmin=True, lastErrorCode=5,
                                                accepted=True, restartResult=True)
    check('§7 管理员分支即使 restartAsAdmin 会成功也不退出', quits == 0, str(quits))


def testFixFailedAsksWhenNotAdmin():
    asked, restarts, quits, bar = _runFixFailed(isAdmin=False, lastErrorCode=5,
                                                accepted=False)
    check('§7 非管理员 + 错误码 5 -> 弹询问框', asked == 1, str(asked))
    check('§7 用户取消 -> 不调 restartAsAdmin', restarts == 0, str(restarts))
    check('§7 用户取消 -> 不退出程序', quits == 0, str(quits))
    check('§7 用户取消 -> 提示「已取消」', len(bar.warnings) == 1,
          str([w.get('title') for w in bar.warnings]))

    asked, restarts, quits, bar = _runFixFailed(isAdmin=False, lastErrorCode=5,
                                                accepted=True, restartResult=False)
    check('§7 同意但 UAC 被取消 -> 调了 restartAsAdmin', restarts == 1, str(restarts))
    check('§7 同意但 UAC 被取消 -> 不退出程序（关键：否则用户就丢了程序）',
          quits == 0, str(quits))
    check('§7 同意但 UAC 被取消 -> 提示「已取消」', len(bar.warnings) == 1,
          str(len(bar.warnings)))

    asked, restarts, quits, bar = _runFixFailed(isAdmin=False, lastErrorCode=5,
                                                accepted=True, restartResult=True)
    check('§7 同意且提权成功 -> 退出程序交给新进程', quits == 1, str(quits))


def testFixFailedOtherErrors():
    for code in (0, 87, 1400):
        asked, restarts, quits, bar = _runFixFailed(isAdmin=False,
                                                    lastErrorCode=code)
        check(f'§7 错误码 {code}（非权限）-> 不弹询问框', asked == 0, str(asked))
        check(f'§7 错误码 {code}（非权限）-> 给一条 warning', len(bar.warnings) == 1,
              str(len(bar.warnings)))


# ================================================ §8 回归专项
def testConnectorStartSwallowsCredentialError():
    """connector.start() 的裸 except 必须接住新异常，走 getCmdlineError"""
    emitted = []

    def onError():
        emitted.append(1)

    signalBus.getCmdlineError.connect(onError)

    try:
        # 1) 真实链路：psutil 必然失败的 pid
        inst = connectorModule.LolClientConnector()
        fakeTime = FakeTime()

        with PatchModule(util, time=fakeTime):
            import asyncio
            asyncio.run(inst.start(0x7FFF0000))

        check('§8 start() 遇到凭据异常不往外抛（真实 psutil）', True)
        check('§8 start() 发了 getCmdlineError（真实 psutil）', emitted == [1],
              str(emitted))
        check('§8 start() 失败后 port 保持 None', inst.port is None, repr(inst.port))

        # 2) 直接抛自定义异常（模拟 sign 变了 / 上层包了一层）
        emitted.clear()
        inst2 = connectorModule.LolClientConnector()

        def boom(pid):
            raise LcuCredentialsUnavailable('no creds')

        with PatchModule(connectorModule, getPortTokenServerByPid=boom):
            import asyncio
            asyncio.run(inst2.start(4242))

        check('§8 start() 也接得住直接抛出的 LcuCredentialsUnavailable',
              emitted == [1], str(emitted))
    finally:
        signalBus.getCmdlineError.disconnect(onError)


def testMessageBoxCallSites():
    """message_box 里每个 getLoginSummonerByPid 调用点都要能吃 None"""
    hits = []

    for dirpath, _, filenames in os.walk(os.path.join(ROOT, 'app')):
        for name in filenames:
            if not name.endswith('.py'):
                continue

            full = os.path.join(dirpath, name)

            with open(full, 'r', encoding='utf-8') as fh:
                for lineno, line in enumerate(fh, 1):
                    stripped = line.strip()

                    # 注释 / 文档里提到不算调用点
                    if stripped.startswith('#'):
                        continue

                    if 'getLoginSummonerByPid' in line and 'def ' not in line:
                        hits.append((os.path.relpath(full, ROOT), lineno))

    check('§8 getLoginSummonerByPid 的调用点只在 message_box 里',
          all(h[0].replace('\\', '/') == 'app/components/message_box.py' for h in hits),
          str(hits))
    check('§8 message_box 里只有一个调用点',
          len([h for h in hits if 'message_box' in h[0]]) == 1, str(hits))

    parent = keep(QWidget())
    parent.resize(640, 480)

    origGet = connector.getLoginSummonerByPid
    origPid = getattr(connector, 'pid', 'MISSING')
    try:
        # a) 全部拿不到 -> 占位名，且不崩
        connector.getLoginSummonerByPid = lambda pid: None
        connector.pid = 111
        box = MB.ChangeClientMessageBox([111, 222], parent)

        check('§8 两个 pid 都拿不到 -> 两个条目都建出来', box.comboBox.count() == 2,
              str(box.comboBox.count()))
        check('§8 拿不到 -> 占位「未知召唤师」',
              all('未知召唤师' in box.comboBox.itemText(i) for i in range(2)),
              str([box.comboBox.itemText(i) for i in range(2)]))
        check('§8 当前进程那一项被标出来并选中',
              '（当前）' in box.comboBox.itemText(0) and
              box.comboBox.currentIndex() == 0,
              f'{box.comboBox.itemText(0)} / {box.comboBox.currentIndex()}')

        # b) 一个拿得到一个拿不到
        connector.getLoginSummonerByPid = (
            lambda pid: {'gameName': 'Faker'} if pid == 222 else None)
        connector.pid = 222
        box = MB.ChangeClientMessageBox([111, 222], parent)

        check('§8 混着 None 也能建出来（长度 2）', box.comboBox.count() == 2,
              str(box.comboBox.count()))
        check('§8 拿到名字的正常显示', 'Faker' in box.comboBox.itemText(1),
              box.comboBox.itemText(1))
        check('§8 拿不到的显示占位', '未知召唤师' in box.comboBox.itemText(0),
              box.comboBox.itemText(0))
        check('§8 currentIdx 指到当前进程（修过的 UnboundLocalError 分支）',
              box.comboBox.currentIndex() == 1,
              str(box.comboBox.currentIndex()))

        # c) 一个 pid 都对不上（进程刚好退了）-> 不许 UnboundLocalError
        connector.pid = 999
        try:
            box = MB.ChangeClientMessageBox([111, 222], parent)
        except Exception as e:  # noqa: BLE001
            check('§8 一个 pid 都对不上时不许抛异常', False, repr(e))
        else:
            check('§8 一个 pid 都对不上时不许抛异常', True)
            check('§8 对不上时默认选第 0 项', box.comboBox.currentIndex() == 0,
                  str(box.comboBox.currentIndex()))

        # d) 空列表 -> 不崩，也别 setCurrentIndex(0) 到不存在的项上
        connector.pid = 999
        try:
            box = MB.ChangeClientMessageBox([], parent)
        except Exception as e:  # noqa: BLE001
            check('§8 空 pid 列表不崩', False, repr(e))
        else:
            check('§8 空 pid 列表不崩', box.comboBox.count() == 0,
                  str(box.comboBox.count()))
    finally:
        connector.getLoginSummonerByPid = origGet

        if origPid == 'MISSING':
            if hasattr(connector, 'pid'):
                del connector.pid
        else:
            connector.pid = origPid


def testLockConfigCardEmptyFolders():
    """lolFolder 为 [] / '' / None / [''] 时两个入口都不许抛

    注意：这里**只许用不存在的路径**。用真实客户端目录会真的去 chmod 用户的
    PersistedSettings.json（本文件第一版就踩到了这个坑，别再加回来）。
    """
    card = keep(A.LockConfigCard('title', 'content', None))
    bar = RecordingInfoBar()

    for label, folder in (('[]', []), ("''", ''), ('None', None), ("['']", ['']),
                          ('裸字符串', 'Z:/no-such-lol/Seraphine-audit/TCLS')):
        fakeCfg = FakeCfg(lolFolder=folder)

        with PatchModule(A, cfg=fakeCfg, InfoBar=bar):
            try:
                card.loadNowMode()
            except Exception as e:  # noqa: BLE001
                check(f'§8 lolFolder={label} -> loadNowMode 不抛', False, repr(e))
            else:
                check(f'§8 lolFolder={label} -> loadNowMode 不抛', True)

            for enable in (True, False):
                try:
                    result = card.setConfigFileReadOnlyEnabled(enable)
                except Exception as e:  # noqa: BLE001
                    check(f'§8 lolFolder={label} -> setReadOnly({enable}) 不抛',
                          False, repr(e))
                else:
                    check(f'§8 lolFolder={label} -> setReadOnly({enable}) 不抛',
                          True)
                    check(f'§8 lolFolder={label} -> setReadOnly({enable}) 返回 False'
                          '（目录没选/路径不存在）', result is False, repr(result))

    check('§8 空目录时开关被禁用（用户不会误点）',
          card.switchButton.isEnabled() is False, str(card.switchButton.isEnabled()))


# ==================================== §9 isMinimized 的 Win32 语义
class PlacementUser32:
    """只回 GetWindowPlacement / IsIconic，按 Win32 文档填结构体"""

    def __init__(self, flags, showCmd, ok=True, isIconic=0):
        self.flags = flags
        self.showCmd = showCmd
        self.ok = ok
        self.isIconic = isIconic
        self.lengthSeen = None
        self.iconicCalls = 0

    def GetWindowPlacement(self, hwnd, ptr):
        if not self.ok:
            return 0

        placement = ctypes.cast(
            ptr, ctypes.POINTER(W.WINDOWPLACEMENT)).contents
        self.lengthSeen = placement.length
        placement.flags = self.flags
        placement.showCmd = self.showCmd

        return 1

    def IsIconic(self, hwnd):
        self.iconicCalls += 1
        return self.isIconic


def testIsMinimizedSemantics():
    """Win32 文档：showCmd == SW_SHOWMINIMIZED(2) 才是最小化；

    flags 是 WPF_* 位标志（1=SETMINPOSITION, 2=RESTORETOMAXIMIZED, 4=ASYNC），
    拿 flags 跟 SW_SHOWMINIMIZED 比是比错了字段。
    """
    fake = PlacementUser32(flags=0, showCmd=W.SW_SHOWMINIMIZED)

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 最小化窗口（flags=0, showCmd=2）-> isMinimized True',
          got is True, f'实际返回 {got!r}')
    check('§9 结构体 length 在调用前填好了',
          fake.lengthSeen == ctypes.sizeof(W.WINDOWPLACEMENT), str(fake.lengthSeen))

    fake = PlacementUser32(flags=0, showCmd=1)  # SW_SHOWNORMAL

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 正常窗口（flags=0, showCmd=1）-> False', got is False, repr(got))

    # flags 恰好等于 2（WPF_RESTORETOMAXIMIZED）但窗口是正常状态：
    # 旧实现会把它误判成最小化，于是 applyZoom 直接放弃并提示「请确认没有最小化」
    fake = PlacementUser32(flags=2, showCmd=1)

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 flags=2(WPF_RESTORETOMAXIMIZED) + showCmd=1 -> False',
          got is False, f'实际返回 {got!r}')

    # flags 里恰好带着 2 也不能影响判定：真正的最小化看 showCmd
    fake = PlacementUser32(flags=2, showCmd=W.SW_SHOWMINIMIZED)

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 flags=2 + showCmd=2 -> True（flags 不参与判定）', got is True,
          f'实际返回 {got!r}')

    # showCmd 说正常，但 IsIconic 说是图标态：以 IsIconic 为准（交叉验证）
    fake = PlacementUser32(flags=0, showCmd=1, isIconic=1)

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 showCmd=1 但 IsIconic=1 -> True（宁可多拦，不假报成功）',
          got is True, f'实际返回 {got!r}')

    # 新实现用 IsIconic 兜底：SW_SHOWMINNOACTIVE(7) 也是最小化，但 showCmd != 2
    fake = PlacementUser32(flags=0, showCmd=7, isIconic=1)

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 showCmd=7(SW_SHOWMINNOACTIVE) + IsIconic=1 -> True（兜底生效）',
          got is True, f'实际返回 {got!r}')
    check('§9 showCmd 不是 2 时会问一次 IsIconic', fake.iconicCalls == 1,
          str(fake.iconicCalls))

    # GetWindowPlacement 失败也要靠 IsIconic 兜住
    fake = PlacementUser32(flags=0, showCmd=0, ok=False, isIconic=1)

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 GetWindowPlacement 失败 + IsIconic=1 -> True',
          got is True, repr(got))

    fake = PlacementUser32(flags=0, showCmd=0, ok=False, isIconic=0)

    with PatchModule(W, user32=fake):
        got = W.isMinimized(1234)

    check('§9 两个判据都为假 -> False（不拦着修）', got is False, repr(got))

    with PatchModule(W, user32=None):
        check('§9 user32 不可用 -> False', W.isMinimized(1234) is False)

    with PatchModule(W, user32=PlacementUser32(0, 2)):
        check('§9 hwnd=0 -> False', W.isMinimized(0) is False)


# --------------------------------------------------------------------- 入口
def main():
    print('§1 凭据异常契约')
    testCredsRaiseNeverTripleNone()
    testCredsPartialIsFailure()
    testCredsRetryActuallyRetries()
    testCredsPartialThenOk()
    testCredsFirstTryOkNoSleep()
    testCredsRealPsutilOnDeadPid()

    print('\n§2 getLoginSummonerByPid 降级')
    testLoginSummonerDegrades()

    print('\n§3 applyZoom 的真实几何')
    testApplyZoomRealGeometry()

    print('\n§4 buildRelaunchArgs')
    testRelaunchArgsFrozen()
    testRelaunchArgsSource()
    testRelaunchArgsNoSelfMadeMarker()

    print('\n§5 restartAsAdmin 返回语义')
    testRestartAsAdminReturnContract()

    print('\n§6 安全说明版本门禁')
    testSafetyNoticeGate()
    testSafetyNoticeForceNeverWrites()
    testSafetyNoticeParentFallback()

    print('\n§7 提权分支循环防护')
    testFixFailedNoRestartLoopForAdmin()
    testFixFailedAsksWhenNotAdmin()
    testFixFailedOtherErrors()

    print('\n§8 回归专项')
    testConnectorStartSwallowsCredentialError()
    testMessageBoxCallSites()
    testLockConfigCardEmptyFolders()
    testSafetyNoticeNoParent()

    print('\n§9 isMinimized 的 Win32 语义')
    testIsMinimizedSemantics()

    print()

    # 先把控件清干净再决定退出码：否则 Qt 退出时的析构顺序偶发 segfault，
    # 会把「全过」的运行变成 EXIT=139
    disposeWidgets()

    if _failures:
        p(f'{len(_failures)}/{_checks} 项失败:')
        for name in _failures:
            p(f'  - {name}')
        sys.exit(1)

    p(f'全部通过（{_checks} 项断言）')


if __name__ == '__main__':
    main()
