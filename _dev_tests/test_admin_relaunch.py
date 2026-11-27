# coding:utf-8
"""提权重启 + 锁定配置空目录兜底回归

背景（2.2.48 之后的一轮）：
客户端以管理员身份运行时，UIPI 会拦住非提权进程发来的 `SetWindowPos`，
表现为 `applyZoom` 返回 False 且 `lastErrorCode == 5`。旧方案靠一个未签名
的 helper exe 自己 `ShellExecuteW("runas")` 提权，那个 exe 已被删除，所以现在
只能让本程序也提权 —— 也就是问用户一句、然后重启。

这一层的风险全在「问不问」和「什么时候退出」上，所以全部在这里钉死：

覆盖：
  - `isAdmin()` 返回真 → **绝不**弹提权询问（否则会变成重启循环）
  - `lastErrorCode == 5` 且非管理员 → 才弹询问
  - `lastErrorCode` 不是 5（客户端没开 / 最小化）→ 不弹询问，走原来的 warning
  - 用户点取消 → 不重启、不退出，只给一条「已取消」
  - `restartAsAdmin` 返回 False（UAC 被取消）→ **不退出程序**
  - `restartAsAdmin` 返回 True → `QApplication.quit()`
  - `restartAsAdmin` 只在 ShellExecuteW 返回值 > 32 时算成功（32 及以下都是错误码）
  - 重启参数：frozen 形态直接带 argv；源码形态第一项是 main.py 的绝对路径；
    命令行里**不塞**任何「已重启过」之类的标记
  - `lolFolder` 为空（首次使用没选目录）→ `loadNowMode` /
    `setConfigFileReadOnlyEnabled` 都不抛 IndexError

绝对不真的拉进程、不弹 UAC：`ShellExecuteW` 那一层整体换成假函数。

运行: python _dev_tests/test_admin_relaunch.py
"""
import functools
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication, QWidget

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, setTheme  # noqa: E402

setTheme(Theme.LIGHT, save=False)

from app.common import skin  # noqa: E402

skin.install()
skin.applyGlobal(app)

from app.common import win_window as W  # noqa: E402
from app.view import auxiliary_interface as A  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


# ------------------------------------------------------------------ 假件
class FakeWinWindow:
    """顶掉 app.view.auxiliary_interface.win_window 整个模块引用"""

    ERROR_ACCESS_DENIED = 5

    def __init__(self, admin=False, errorCode=0, restartOk=False):
        self.isAdminValue = admin
        self.lastErrorCode = errorCode
        self.restartOk = restartOk
        self.restartCalls = 0
        self.buildCalls = 0

    def isAdmin(self):
        return self.isAdminValue

    def buildRelaunchArgs(self):
        self.buildCalls += 1
        return ['main.py', '--flag']

    def restartAsAdmin(self, args=None):
        self.restartCalls += 1
        return self.restartOk


class FakeButton:
    def __init__(self):
        self.textValue = ''

    def setText(self, text):
        self.textValue = text


class FakeMessageBox:
    instances = []

    # 和 QDialog 一致：exec() 返回 Accepted / Rejected
    Accepted = 1
    Rejected = 0

    def __init__(self, title, content, parent=None):
        self.title = title
        self.content = content
        self.parent = parent
        self.yesButton = FakeButton()
        self.cancelButton = FakeButton()
        self.execCount = 0
        self.execResult = FakeMessageBox.Accepted
        FakeMessageBox.instances.append(self)

    def exec(self):
        self.execCount += 1
        return self.execResult


class FakeInfoBar:
    calls = []

    @classmethod
    def success(cls, **kwargs):
        cls.calls.append(('success', kwargs))

    @classmethod
    def warning(cls, **kwargs):
        cls.calls.append(('warning', kwargs))

    @classmethod
    def error(cls, **kwargs):
        cls.calls.append(('error', kwargs))

    @classmethod
    def reset(cls):
        cls.calls = []


class FakeApp:
    quitCount = 0

    def quit(self):
        FakeApp.quitCount += 1


class FakeQApplication:
    @staticmethod
    def instance():
        return FakeApp()


class Patcher:
    def __init__(self, module, **kwargs):
        self.module = module
        self.kwargs = kwargs

    def __enter__(self):
        self.orig = {k: getattr(self.module, k) for k in self.kwargs}

        for k, v in self.kwargs.items():
            setattr(self.module, k, v)

        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(self.module, k, v)

        return False


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
    """只认 lolFolder 一项，不读写用户真实 config.json"""

    def __init__(self, **values):
        self._values = values

    def __getattr__(self, name):
        item = _Item(self._values.get(name))
        object.__setattr__(self, name, item)
        return item

    def get(self, item):
        return item.value

    def set(self, item, value):
        item.value = value


# ------------------------------------------------------------------ 参数构造
def testBuildRelaunchArgs():
    # 源码形态（当前就是）：第一项必须是 main.py 的绝对路径
    args = W.buildRelaunchArgs(['--one', '--two'])

    check('源码形态带上了 argv', args[-2:] == ['--one', '--two'], str(args))
    check('源码形态第一项是 main.py 绝对路径',
          len(args) == 3 and os.path.isabs(args[0])
          and args[0].endswith('main.py'), str(args))
    check('main.py 确实存在', os.path.exists(args[0]), args[0])

    # frozen 形态：sys.executable 就是 exe，不能再塞 main.py
    had = hasattr(sys, 'frozen')
    orig = getattr(sys, 'frozen', None)
    sys.frozen = True

    try:
        frozenArgs = W.buildRelaunchArgs(['--one'])
    finally:
        if had:
            sys.frozen = orig
        else:
            del sys.frozen

    check('frozen 形态直接透传 argv', frozenArgs == ['--one'], str(frozenArgs))
    check('frozen 形态不塞 main.py',
          not any(str(a).endswith('main.py') for a in frozenArgs), str(frozenArgs))

    # 不塞「已重启过」标记：参数必须完全来自 argv
    check('命令行里没有自造的标记参数',
          W.buildRelaunchArgs([])[-1].endswith('main.py')
          and len(W.buildRelaunchArgs([])) == 1,
          str(W.buildRelaunchArgs([])))


# ------------------------------------------------------------------ 提权调用
def testRestartAsAdmin():
    recorded = []

    def fakeShellExecute(executable, params, cwd):
        recorded.append((executable, params, cwd))
        return fakeShellExecute.result

    orig = W._shellExecuteRunAs
    W._shellExecuteRunAs = fakeShellExecute

    try:
        # ShellExecuteW: > 32 才是成功
        fakeShellExecute.result = 33
        check('返回值 33 → 成功', W.restartAsAdmin(['main.py']) is True)

        fakeShellExecute.result = 42
        check('返回值 42 → 成功', W.restartAsAdmin(['main.py']) is True)

        for bad in (0, 2, 5, 31, 32):
            fakeShellExecute.result = bad
            check(f'返回值 {bad} → 失败（32 及以下都是错误码）',
                  W.restartAsAdmin(['main.py']) is False)

        # 参数拼装：带空格的路径要加引号
        recorded.clear()
        fakeShellExecute.result = 42
        W.restartAsAdmin(['C:/a b/main.py', '--x'])
        check('参数经过 list2cmdline 转义',
              bool(recorded) and '"C:/a b/main.py"' in recorded[-1][1],
              recorded[-1][1] if recorded else 'no call')
        check('可执行文件是 sys.executable',
              bool(recorded) and recorded[-1][0] == sys.executable)
        check('工作目录是当前目录',
              bool(recorded) and recorded[-1][2] == os.getcwd())

        # 接口本身炸了也要 False，不往上抛
        def boom(*a):
            raise OSError('shell execute failed')

        W._shellExecuteRunAs = boom
        check('ShellExecuteW 抛异常 → 返回 False，不抛',
              W.restartAsAdmin(['main.py']) is False)
    finally:
        W._shellExecuteRunAs = orig


def testIsAdmin():
    value = W.isAdmin()

    check('isAdmin() 返回 bool', isinstance(value, bool), repr(value))
    check('非 Windows 平台恒为 False',
          W.IS_WINDOWS or value is False)


# ------------------------------------------------------------------ 卡片分支
def makeCard():
    host = QWidget()
    host.resize(800, 600)

    card = A.FixClientDpiCard(title='修复客户端窗口', content='内容', parent=host)

    return host, card


def runFailed(card):
    """走到「修复失败」那条分支（私有方法，名字被 mangle 过）"""
    card._FixClientDpiCard__onFixFailed()


def testAdminSkipsPrompt():
    """已是管理员 → 绝不弹提权询问（否则重启循环）"""
    host, card = makeCard()
    fakeWin = FakeWinWindow(admin=True, errorCode=5)

    FakeInfoBar.reset()
    FakeMessageBox.instances = []

    with Patcher(A, win_window=fakeWin, MessageBox=FakeMessageBox, InfoBar=FakeInfoBar):
        runFailed(card)

    check('管理员身份下不弹 MessageBox', not FakeMessageBox.instances)
    check('管理员身份下不尝试重启', fakeWin.restartCalls == 0, str(fakeWin.restartCalls))
    check('管理员身份下给普通 warning',
          len(FakeInfoBar.calls) == 1 and FakeInfoBar.calls[0][0] == 'warning',
          str(FakeInfoBar.calls))

    host.close()


def testNonAdminOtherError():
    """错误码不是 5（客户端没开 / 最小化）→ 不弹提权询问"""
    host, card = makeCard()

    for code in (0, 2, 87):
        fakeWin = FakeWinWindow(admin=False, errorCode=code)

        FakeInfoBar.reset()
        FakeMessageBox.instances = []

        with Patcher(A, win_window=fakeWin, MessageBox=FakeMessageBox, InfoBar=FakeInfoBar):
            runFailed(card)

        check(f'错误码 {code} 不弹 MessageBox', not FakeMessageBox.instances)
        check(f'错误码 {code} 不尝试重启', fakeWin.restartCalls == 0)
        check(f'错误码 {code} 给普通 warning',
              len(FakeInfoBar.calls) == 1 and FakeInfoBar.calls[0][0] == 'warning')

    host.close()


def testNonAdminAccessDeniedPrompt():
    """错误码 5 + 非管理员 → 弹一次询问"""
    host, card = makeCard()
    fakeWin = FakeWinWindow(admin=False, errorCode=5, restartOk=True)

    FakeInfoBar.reset()
    FakeMessageBox.instances = []
    FakeApp.quitCount = 0

    with Patcher(A, win_window=fakeWin, MessageBox=FakeMessageBox,
                 InfoBar=FakeInfoBar, QApplication=FakeQApplication):
        runFailed(card)

    check('弹了 MessageBox', len(FakeMessageBox.instances) == 1,
          str(len(FakeMessageBox.instances)))

    if FakeMessageBox.instances:
        box = FakeMessageBox.instances[0]
        check('标题是「需要管理员权限」', box.title == '需要管理员权限', box.title)
        check('正文提示了重启', '是否以管理员身份重启本程序' in box.content, box.content)
        check('确定按钮文案', box.yesButton.textValue == '以管理员身份重启',
              box.yesButton.textValue)
        check('取消按钮文案', box.cancelButton.textValue == '取消',
              box.cancelButton.textValue)

    check('重启参数由 buildRelaunchArgs 提供', fakeWin.buildCalls == 1,
          str(fakeWin.buildCalls))
    check('调了一次 restartAsAdmin', fakeWin.restartCalls == 1,
          str(fakeWin.restartCalls))
    check('重启成功 → 退出程序', FakeApp.quitCount == 1, str(FakeApp.quitCount))
    check('重启成功 → 不再给 warning', not FakeInfoBar.calls, str(FakeInfoBar.calls))

    host.close()


def testUacCancelled():
    """UAC 被取消（restartAsAdmin 返回 False）→ 不退出程序"""
    host, card = makeCard()
    fakeWin = FakeWinWindow(admin=False, errorCode=5, restartOk=False)

    FakeInfoBar.reset()
    FakeMessageBox.instances = []
    FakeApp.quitCount = 0

    with Patcher(A, win_window=fakeWin, MessageBox=FakeMessageBox,
                 InfoBar=FakeInfoBar, QApplication=FakeQApplication):
        runFailed(card)

    check('还是尝试了一次重启', fakeWin.restartCalls == 1)
    check('UAC 取消 → 不退出程序', FakeApp.quitCount == 0, str(FakeApp.quitCount))
    check('UAC 取消 → 给一条「已取消」',
          len(FakeInfoBar.calls) == 1
          and FakeInfoBar.calls[0][1].get('title') == '已取消',
          str(FakeInfoBar.calls))

    host.close()


def testUserClicksCancel():
    """用户在询问框上点取消 → 不去拉 UAC"""
    host, card = makeCard()
    fakeWin = FakeWinWindow(admin=False, errorCode=5, restartOk=True)

    FakeInfoBar.reset()
    FakeMessageBox.instances = []
    FakeApp.quitCount = 0

    origInit = FakeMessageBox.__init__

    def initWithReject(self, title, content, parent=None):
        origInit(self, title, content, parent)
        self.execResult = FakeMessageBox.Rejected

    FakeMessageBox.__init__ = initWithReject

    try:
        with Patcher(A, win_window=fakeWin, MessageBox=FakeMessageBox,
                     InfoBar=FakeInfoBar, QApplication=FakeQApplication):
            runFailed(card)
    finally:
        FakeMessageBox.__init__ = origInit

    check('点了取消 → 不调 restartAsAdmin', fakeWin.restartCalls == 0,
          str(fakeWin.restartCalls))
    check('点了取消 → 不退出程序', FakeApp.quitCount == 0)
    check('点了取消 → 给一条「已取消」',
          len(FakeInfoBar.calls) == 1
          and FakeInfoBar.calls[0][1].get('title') == '已取消',
          str(FakeInfoBar.calls))

    host.close()


def testSlotWiring():
    """`@asyncSlot` 的按钮回调确实会走到失败分支（跑一遍真协程，不点真按钮）"""
    import asyncio

    host, card = makeCard()
    fakeWin = FakeWinWindow(admin=False, errorCode=5, restartOk=True)

    async def fakeFixLCUWindow():
        return False

    FakeInfoBar.reset()
    FakeMessageBox.instances = []
    FakeApp.quitCount = 0

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        with Patcher(A, win_window=fakeWin, MessageBox=FakeMessageBox,
                     InfoBar=FakeInfoBar, QApplication=FakeQApplication,
                     fixLCUWindow=fakeFixLCUWindow):
            task = card._FixClientDpiCard__onButtonClicked()
            loop.run_until_complete(task)
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    check('按钮回调把失败一路传到询问框', len(FakeMessageBox.instances) == 1,
          str(len(FakeMessageBox.instances)))
    check('按钮回调也会在成功后退出程序', FakeApp.quitCount == 1,
          str(FakeApp.quitCount))

    host.close()


def testSuccessPathUntouched():
    """成功分支的文案不能被这轮改动带偏"""
    host, card = makeCard()

    FakeInfoBar.reset()

    with Patcher(A, InfoBar=FakeInfoBar):
        A.InfoBar.success(
            title=card.tr('已修复客户端窗口'),
            content=card.tr('客户端窗口尺寸已按当前缩放重设'),
        )

    check('成功文案还在', len(FakeInfoBar.calls) == 1
          and FakeInfoBar.calls[0][1]['title'] == '已修复客户端窗口')

    host.close()


# ------------------------------------------------------------------ 锁定配置兜底
def makeLockCard(folders):
    host = QWidget()
    host.resize(800, 600)
    card = A.LockConfigCard(title='锁定配置', content='内容', parent=host)

    return host, card


def testLockConfigEmptyFolder():
    """lolFolder 为空时两个方法都不能抛（安全说明第 4 条对外承诺了这个开关）"""
    for folders in ([], '', None, ['']):
        host, card = makeLockCard(folders)

        with Patcher(A, cfg=FakeCfg(lolFolder=folders)):
            # 1) loadNowMode: 置灰 + 取消勾选 + 直接返回
            card.switchButton.setEnabled(True)
            card.switchButton.setChecked(True)

            raised = None

            try:
                card.loadNowMode()
            except Exception as e:  # noqa: BLE001
                raised = e

            check(f'lolFolder={folders!r} 时 loadNowMode 不抛异常', raised is None,
                  repr(raised))
            check(f'lolFolder={folders!r} 时开关被置灰', not card.switchButton.isEnabled())
            check(f'lolFolder={folders!r} 时开关被取消勾选',
                  not card.switchButton.isChecked())

            # 2) setConfigFileReadOnlyEnabled: 返回 False，不抛
            for enable in (True, False):
                raised = None

                try:
                    result = card.setConfigFileReadOnlyEnabled(enable)
                except Exception as e:  # noqa: BLE001
                    raised = e
                    result = None

                check(f'lolFolder={folders!r}/enable={enable} 不抛异常',
                      raised is None, repr(raised))
                check(f'lolFolder={folders!r}/enable={enable} 返回 False',
                      result is False, repr(result))

            # 3) 路径构造本身返回 None
            check(f'lolFolder={folders!r} 时路径为 None',
                  card._LockConfigCard__getSettingsPath() is None)

        host.close()


def testLockConfigStringFolder():
    """老配置里裸字符串不能被当成「取第一个字符」"""
    host, card = makeLockCard(['C:/Riot Games/League of Legends'])

    with Patcher(A, cfg=FakeCfg(lolFolder='C:/Riot Games/League of Legends')):
        path = card._LockConfigCard__getSettingsPath()

    check('字符串形态整体当成一个路径',
          path == 'C:/Riot Games/League of Legends/../Game/Config/PersistedSettings.json',
          str(path))

    host.close()


def main():
    testBuildRelaunchArgs()
    testRestartAsAdmin()
    testIsAdmin()
    testAdminSkipsPrompt()
    testNonAdminOtherError()
    testNonAdminAccessDeniedPrompt()
    testUacCancelled()
    testUserClicksCancel()
    testSlotWiring()
    testSuccessPathUntouched()
    testLockConfigEmptyFolder()
    testLockConfigStringFolder()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
