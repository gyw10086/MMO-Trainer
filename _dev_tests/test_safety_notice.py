# coding:utf-8
"""首启安全说明回归

「杀软误报被当成外挂」是这类助手最常见的差评来源，所以首启说明必须：
只说事实、只弹一次、设置页里能重看。这里守住这三点。

覆盖：
  - 配置里没记录过（0）→ 弹，并把版本写回配置
  - 已经是当前版本 → 不弹，也不重复写配置
  - 配置是人手改坏的脏值 → 当成没弹过（多弹一次不算事故，崩了才是）
  - force=True → 每次都弹，且**不写配置**（用户主动点进来不该改变首启状态）
  - 正文含四条边界说明与兜底那句，标题/按钮文案固定
  - 对话框尺寸按正文真实高度算：正文不被截断、按钮在框内（库里默认会截）
  - exec() 而不是 show()（模态，返回后才知道用户点了「我知道了」）

只读 app 模块、不写用户 config.json：配置换成 FakeCfg。

运行: python _dev_tests/test_safety_notice.py
"""
import functools
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

from app.common.config import SAFETY_NOTICE_VERSION  # noqa: E402
from app.view import safety_notice as S  # noqa: E402

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


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
    """只认 safetyNoticeVersion 一项，不碰用户真实的 config.json"""

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


class FakeBox:
    """替掉真的 MessageBox：只记 exec 有没有被调用"""

    def __init__(self):
        self.execCount = 0
        self.title = None
        self.content = None
        self.parent = None

    def exec(self):
        self.execCount += 1
        return 1


class Patcher:
    """临时替换 safety_notice 模块里的属性"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        self.orig = {k: getattr(S, k) for k in self.kwargs}

        for k, v in self.kwargs.items():
            setattr(S, k, v)

        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(S, k, v)

        return False


def makeBoxFactory():
    """返回 (工厂函数, 最近一次造出来的 box)"""
    box = FakeBox()

    def factory(parent=None):
        box.parent = parent
        box.title = S.TITLE
        box.content = S.buildContent()

        return box

    return factory, box


# ------------------------------------------------------------------ 版本门禁
def testFirstLaunch():
    fakeCfg = FakeCfg(safetyNoticeVersion=0)
    factory, box = makeBoxFactory()

    with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory):
        shown = S.showSafetyNotice(parent=object())

    check('配置里没有记录（0）→ 弹窗', shown is True)
    check('弹了一次', box.execCount == 1, str(box.execCount))
    check('弹完把版本写回配置',
          [w[1] for w in fakeCfg.writes] == [SAFETY_NOTICE_VERSION],
          str(fakeCfg.writes))


def testSameVersion():
    fakeCfg = FakeCfg(safetyNoticeVersion=SAFETY_NOTICE_VERSION)
    factory, box = makeBoxFactory()

    with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory):
        shown = S.showSafetyNotice(parent=object())

    check('已经是当前版本 → 不弹', shown is False)
    check('不弹就不 exec', box.execCount == 0, str(box.execCount))
    check('不重复写配置', not fakeCfg.writes, str(fakeCfg.writes))


def testFutureVersion():
    """用户装过更新版本又回退：配置里的版本更大，也不该再弹"""
    fakeCfg = FakeCfg(safetyNoticeVersion=SAFETY_NOTICE_VERSION + 5)
    factory, box = makeBoxFactory()

    with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory):
        shown = S.showSafetyNotice(parent=object())

    check('配置版本更高 → 不弹', shown is False)


def testDirtyVersion():
    fakeCfg = FakeCfg(safetyNoticeVersion='abc')
    factory, box = makeBoxFactory()

    with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory):
        shown = S.showSafetyNotice(parent=object())

    check('配置是脏值 → 当成没弹过', shown is True)
    check('脏值也会被写回正确版本',
          [w[1] for w in fakeCfg.writes] == [SAFETY_NOTICE_VERSION],
          str(fakeCfg.writes))


def testForce():
    fakeCfg = FakeCfg(safetyNoticeVersion=SAFETY_NOTICE_VERSION)
    factory, box = makeBoxFactory()

    with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory):
        shown = S.showSafetyNotice(parent=object(), force=True)

    check('force=True 每次都弹', shown is True)
    check('force=True 弹了一次', box.execCount == 1, str(box.execCount))
    check('force=True 不写配置', not fakeCfg.writes, str(fakeCfg.writes))


def testNoneParent():
    """parent 为 None 时要退化成当前活动窗口，而不是直接构造一个游离的模态框"""
    from PyQt5.QtWidgets import QWidget

    fakeCfg = FakeCfg(safetyNoticeVersion=0)
    factory, box = makeBoxFactory()

    active = QWidget()
    active.resize(400, 300)
    active.show()
    QApplication.processEvents()

    with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory):
        shown = S.showSafetyNotice(parent=None)

    check('parent=None 也能弹', shown is True)
    check('parent=None 时退化成当前活动窗口', box.parent is active,
          repr(box.parent))

    active.close()


# ------------------------------------------------------------------ 文案
def testContent():
    content = S.buildContent()
    lines = content.split('\n')

    check('标题固定', S.TITLE == '关于本程序的说明', S.TITLE)
    check('四条要点都在',
          all(f'• {bullet}' in content for bullet in S.BULLETS),
          str([b for b in S.BULLETS if b not in content]))
    check('要点是四条', len(S.BULLETS) == 4, str(len(S.BULLETS)))
    check('首行是第一条要点', lines[0] == f'• {S.BULLETS[0]}', lines[0])
    check('末行是兜底那句', lines[-1] == S.FOOTER, lines[-1])
    check('兜底提到信任区', '信任区' in content)
    check('兜底提到 SHA256', 'SHA256' in content)
    check('没有提到内存读取以外的处理器操作',
          '不读取游戏内存' in content and '不注入 DLL' in content)
    check('写明唯一会改动的是锁定配置',
          'PersistedSettings.json' in content and '默认关闭' in content)
    check('数据来源写清了',
          'LCU' in content and 'Live Client Data API' in content)


# ------------------------------------------------------------------ 无父窗口兜底
def testNoActiveWindow():
    """`parent=None` 且没有活动窗口时也要弹得出来

    以前这里 `QApplication.activeWindow()` 拿到 None 就直接传给 MessageBox，
    而 qfluentwidgets 的 mask_dialog_base 会 `parent.width()` → AttributeError，
    结果是「首启安全说明被静默跳过」—— 而让用户看到这段话正是这个功能的目的。
    """
    from PyQt5.QtWidgets import QWidget

    origActive = S._activeWindow
    S._activeWindow = lambda: None

    try:
        box = S.buildSafetyNoticeBox(None)
    finally:
        S._activeWindow = origActive

    check('没有活动窗口也能造出对话框', box is not None)

    if box is not None:
        parent = box.parent()

        check('兜底 parent 是一个 QWidget', isinstance(parent, QWidget),
              type(parent).__name__)
        check('兜底 parent 带 Qt.Tool（不进任务栏、不抢焦点）',
              bool(parent.windowFlags() & Qt.Tool), str(parent.windowFlags()))
        check('兜底 parent 没有被 show 出来（避免抢焦点）', not parent.isVisible())
        check('兜底 parent 被模块级变量引用住（不会被 GC）',
              S._fallbackParent is parent, repr(S._fallbackParent))
        check('兜底 parent 被保活（对话框没被连带 delete）',
              box.contentLabel.text() != '', repr(box.contentLabel.text()[:12]))
        check('兜底时正文尺寸依然不被截断',
              box.contentLabel.geometry().height() >= box.contentLabel.sizeHint().height(),
              f'{box.contentLabel.geometry().height()} vs '
              f'{box.contentLabel.sizeHint().height()}')
        box.close()

    # showSafetyNotice 这条路也不能炸，并且要真的弹
    fakeCfg = FakeCfg(safetyNoticeVersion=0)
    factory, fakeBox = makeBoxFactory()

    S._activeWindow = lambda: None

    try:
        with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory):
            shown = S.showSafetyNotice(parent=None)
    finally:
        S._activeWindow = origActive

    check('没有活动窗口时 showSafetyNotice 仍然弹窗', shown is True)
    check('没有活动窗口时 exec 被调到', fakeBox.execCount == 1,
          str(fakeBox.execCount))
    check('没有活动窗口时版本照样写回',
          [w[1] for w in fakeCfg.writes] == [SAFETY_NOTICE_VERSION],
          str(fakeCfg.writes))


class _NoApp:
    @staticmethod
    def instance():
        return None


def testNoApplication():
    """连 QApplication 都没有时安静返回 False，不抛"""
    fakeCfg = FakeCfg(safetyNoticeVersion=0)
    factory, fakeBox = makeBoxFactory()

    with Patcher(cfg=fakeCfg, buildSafetyNoticeBox=factory, QApplication=_NoApp):
        shown = S.showSafetyNotice(parent=None)

    check('没有 QApplication 时返回 False', shown is False, repr(shown))
    check('没有 QApplication 时不尝试造对话框', fakeBox.execCount == 0)
    check('没有 QApplication 时不写配置', not fakeCfg.writes, str(fakeCfg.writes))


# ------------------------------------------------------------------ 真实尺寸
def testRealBoxLayout():
    """真 MessageBox：库里按构造瞬间的 Label 几何定死尺寸，多行正文会被截"""
    from PyQt5.QtWidgets import QWidget

    parent = QWidget()
    parent.resize(1280, 800)

    box = S.buildSafetyNoticeBox(parent)

    check('按钮文案是「我知道了」', box.yesButton.text() == '我知道了',
          box.yesButton.text())
    check('取消按钮被藏起来', box.cancelButton.isHidden())

    def squash(text):
        # 库会按字符宽度重新换行，比较内容时把换行和空格都抹掉
        return text.replace('\n', '').replace(' ', '')

    check('正文写进了 contentLabel',
          squash(box.contentLabel.text()) == squash(S.buildContent()))
    check('四条要点都进了正文',
          all(squash(b) in squash(box.contentLabel.text()) for b in S.BULLETS))
    check('正文至少 5 行', box.contentLabel.text().count('\n') + 1 >= 5,
          str(box.contentLabel.text().count('\n') + 1))
    check('resize 时不再重新换行（钩子已摘掉）',
          not inspect.ismethod(box._adjustText), repr(box._adjustText))

    lab = box.contentLabel
    geo = lab.geometry()
    hint = lab.sizeHint()

    check('正文高度够放（不被截断）', geo.height() >= hint.height(),
          f'{geo.height()} >= {hint.height()}')
    check('正文宽度够放（不被截断）', geo.width() >= hint.width(),
          f'{geo.width()} >= {hint.width()}')

    widget = box.widget
    btn = box.buttonGroup.geometry()

    check('按钮组在对话框内', btn.bottom() <= widget.height(),
          f'{btn.bottom()} <= {widget.height()}')
    check('正文底部在按钮组上方', geo.bottom() <= btn.top(),
          f'{geo.bottom()} <= {btn.top()}')
    check('对话框宽度容得下正文', widget.width() >= geo.width() + 20,
          f'{widget.width()} vs {geo.width()}')
    check('对话框尺寸合理（不会撑爆 1366 宽的屏幕）',
          300 <= widget.width() <= 1200 and 150 <= widget.height() <= 900,
          f'{widget.width()}x{widget.height()}')

    box.close()
    parent.close()


def main():
    print(f'SAFETY_NOTICE_VERSION = {SAFETY_NOTICE_VERSION}')

    testFirstLaunch()
    testSameVersion()
    testFutureVersion()
    testDirtyVersion()
    testForce()
    testNoneParent()
    testNoActiveWindow()
    testNoApplication()
    testContent()
    testRealBoxLayout()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('全部通过')


if __name__ == '__main__':
    main()
