# coding:utf-8
"""BP 浮窗（5 张卡横排）吸附定位回归

不依赖 LCU 和真实客户端：用假桌面 + 假客户端矩形跑 offscreen Qt，只验几何。

覆盖：
  - 空间充足：5 张卡贴客户端上沿，间距 8px，水平居中
  - 1080p 上方只有 90px：**不自动折叠**（折叠等于把 5+5 全藏起来），
    改成压到客户端内侧顶部
  - 客户端贴着屏幕顶：同上，且不能跑出屏幕
  - 折叠态：按 5 条的高度算，仍然贴上沿
  - 拖过的自定义偏移：按偏移走，且被 clamp 在屏幕内
  - 小客户端：k 有下限，5 张卡不会缩成一条
  - 窗口宽度跟着 k 走

运行: python _dev_tests/test_bp_place.py
"""
import functools
import os
import sys
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

p = functools.partial(print, flush=True)

app = QApplication(sys.argv)

from app.common.qfluentwidgets import setTheme, Theme  # noqa: E402

setTheme(Theme.DARK, save=False)

from app.view import bp_overlay as O  # noqa: E402
from app.view.bp_overlay import (  # noqa: E402
    BpOverlay, GAP, MIN_K, MAX_K, PANEL_H, BAR_H, BASE_W, BAR_TOTAL_W)


class _Sig:
    def connect(self, *args, **kwargs):
        pass


class _Item:
    def __init__(self, value=None):
        self.value = value
        self.valueChanged = _Sig()


class FakeCfg:
    """构造浮窗时会读 collapsed / offset / tier，用假的顶上，不碰用户配置"""

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


O.cfg = FakeCfg(bpOverlayCollapsed=False, bpOverlayOffset='',
                bpOverlayOffsetSchema=O.OFFSET_SCHEMA, opggTier='emerald_plus')

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


class FakeDesktop:
    def __init__(self, rect):
        self.rect = rect

    def availableGeometry(self):
        return self.rect


def place(screen, client, collapsed=False, offset=None):
    """造一个浮窗，按假桌面算位置，返回 (pos, w, h, collapsed)"""
    ov = BpOverlay()
    ov.k = max(MIN_K, min(MAX_K, min(client.width() / 1600.0, MAX_K)))
    ov.collapsed = collapsed
    ov._BpOverlay__offset = offset
    ov.resizeToFit()

    desktop = FakeDesktop(screen)

    with patch.object(QApplication, 'desktop', staticmethod(lambda: desktop)):
        pos = ov._BpOverlay__positionFor(client)

    return pos, ov.width(), ov.height(), ov.collapsed


def testPlenty():
    """1440p：上方 287px，5 张卡放得下"""
    pos, w, h, collapsed = place(QRect(0, 0, 2560, 1392), QRect(533, 287, 1600, 900))

    check('空间充足不折叠', not collapsed)
    check('卡片高度', h == PANEL_H, str(h))
    check('水平居中', pos.x() == 533 + (1600 - w) // 2, str(pos.x()))
    check('贴客户端上沿间距 8px', 287 - (pos.y() + h) == GAP,
          str(287 - (pos.y() + h)))
    check('5 张卡放得下 1600 宽客户端', w < 1600, str(w))


def testTight():
    """1080p：上方只有 90px -> 不折叠，压到客户端内侧顶部"""
    pos, w, h, collapsed = place(QRect(0, 0, 1920, 1040), QRect(160, 90, 1600, 900))

    check('上方放不下也不自动折叠', not collapsed)
    check('保持完整卡片高度', h == PANEL_H, str(h))
    check('压到客户端内侧顶部', pos.y() == 90, str(pos.y()))
    check('不越出屏幕顶', pos.y() >= 0, str(pos.y()))


def testScreenTop():
    """客户端顶在屏幕最上方：贴 y=0，不能跑到屏幕外"""
    pos, w, h, collapsed = place(QRect(0, 0, 1920, 1040), QRect(160, 0, 1600, 900))

    check('客户端贴屏幕顶时不折叠', not collapsed)
    check('位置不越出屏幕顶', pos.y() == 0, str(pos.y()))


def testCollapsed():
    """用户主动收起：5 条细条，仍贴上沿"""
    pos, w, h, collapsed = place(QRect(0, 0, 2560, 1392), QRect(533, 287, 1600, 900),
                                 collapsed=True)

    check('折叠后是一条的高度', h == BAR_H, str(h))
    check('折叠后宽度是 5 条', w == BAR_TOTAL_W, str(w))
    check('折叠后仍贴客户端上沿', 287 - (pos.y() + h) == GAP,
          str(287 - (pos.y() + h)))


def testOffset():
    """拖过之后按保存的偏移走"""
    pos, w, h, collapsed = place(QRect(0, 0, 2560, 1392), QRect(533, 287, 1600, 900),
                                 offset=(120.0, -200.0))

    check('自定义偏移生效', pos.x() == 533 + 120 and pos.y() == 287 - 200,
          f'({pos.x()}, {pos.y()})')

    pos, w, h, _ = place(QRect(0, 0, 2560, 1392), QRect(533, 287, 1600, 900),
                         offset=(-9999.0, -9999.0))

    check('偏移越界被拉回屏幕内', pos.x() >= 0 and pos.y() >= 0,
          f'({pos.x()}, {pos.y()})')


def testSmallClient():
    """小客户端：k 有下限，5 张卡不会无限缩，但也不能撑出客户端"""
    pos, w, h, collapsed = place(QRect(0, 0, 1366, 728), QRect(63, 30, 1280, 720))

    check('k 不低于下限', w >= int(round(BASE_W * MIN_K)), str(w))
    check('小屏也不自动折叠', not collapsed)
    check('小屏 5 张卡仍放得下 1280 宽客户端', w < 1280, str(w))


def testScale():
    """窗口宽度跟着客户端宽度等比走"""
    _, w1, _, _ = place(QRect(0, 0, 2560, 1392), QRect(533, 287, 1600, 900))
    _, w2, _, _ = place(QRect(0, 0, 2560, 1392), QRect(433, 287, 1800, 900))
    _, w3, _, _ = place(QRect(0, 0, 2800, 1500), QRect(200, 287, 2200, 900))

    check('1600 宽客户端 k = 1', w1 == BASE_W, str(w1))
    check('1800 宽客户端放大', w2 > w1, f'{w2} vs {w1}')
    check('放大有上限', w3 == int(round(BASE_W * MAX_K)), str(w3))


def main():
    testPlenty()
    testTight()
    testScreenTop()
    testCollapsed()
    testOffset()
    testSmallClient()
    testScale()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('吸附定位全部通过')


if __name__ == '__main__':
    main()
