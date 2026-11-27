# coding:utf-8
"""亮色 / 暗色主题的回归

不联网、不起真实窗口，只验色板本身和主题开关。

覆盖：
  - 两套调色板的键完全一致（少一个就是 `color()` 悄悄回退到正文色）
  - 正文 / 次要文字在各自底色上的对比度（深色 <-> 浅色都要能读）
  - 组排色板在浅色下压暗过，文字对比度达标
  - `setTheme` 之后 `color()` 真的换了值

运行: python _dev_tests/test_theme.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from app.common.qfluentwidgets import Theme, isDarkTheme, setTheme  # noqa: E402

failures = []


def check(name, cond, detail=''):
    print(f'{"PASS" if cond else "FAIL"}  {name}'
          + (f'  ({detail})' if detail and not cond else ''))

    if not cond:
        failures.append(name)


def luminance(hexColor: str) -> float:
    value = (hexColor or '').lstrip('#')[-6:]
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in channels)

    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)

    return (hi + 0.05) / (lo + 0.05)


from app.common.design_tokens import DARK, LIGHT, color  # noqa: E402
from app.lol.premade import (PREMADE_COLORS,  # noqa: E402
                             PREMADE_COLORS_LIGHT)

print('调色板')

check('两套调色板的键完全一致', set(DARK) == set(LIGHT),
      f'深色多 {set(DARK) - set(LIGHT)} / 浅色多 {set(LIGHT) - set(DARK)}')

for name in ('bg0', 'bg1', 'bg2', 'bg3', 'line', 'text', 'textSub',
             'textMuted', 'brand', 'accent', 'win', 'red'):
    if name not in DARK or name not in LIGHT:
        check(f'两套都有 {name}', False)

print('对比度')

for label, palette in (('深色', DARK), ('浅色', LIGHT)):
    ratio = contrast(palette['text'], palette['bg0'])
    check(f'{label}: 正文 / 页面底色 >= 7', ratio >= 7, f'{ratio:.1f}')

    ratio = contrast(palette['textSub'], palette['bg0'])
    check(f'{label}: 次要文字 / 页面底色 >= 4.5', ratio >= 4.5,
          f'{ratio:.1f}')

    ratio = contrast(palette['textMuted'], palette['bg0'])
    check(f'{label}: 弱化文字 / 页面底色 >= 3', ratio >= 3, f'{ratio:.1f}')

    ratio = contrast(palette['brand'], palette['bg0'])
    check(f'{label}: 品牌黄 / 页面底色 >= 3', ratio >= 3, f'{ratio:.1f}')

print('组排色')

check('两套色板不重复',
      not (set(PREMADE_COLORS) & set(PREMADE_COLORS_LIGHT)),
      str(set(PREMADE_COLORS) & set(PREMADE_COLORS_LIGHT)))

check('两套色板长度一致',
      len(PREMADE_COLORS) == len(PREMADE_COLORS_LIGHT),
      f'{len(PREMADE_COLORS)} vs {len(PREMADE_COLORS_LIGHT)}')

for index, hexColor in enumerate(PREMADE_COLORS_LIGHT):
    ratio = contrast(hexColor, LIGHT['bg2'])
    check(f'浅色组排色 {index} 在卡片底色上 >= 4', ratio >= 4,
          f'{hexColor} -> {ratio:.1f}')

for index, hexColor in enumerate(PREMADE_COLORS):
    ratio = contrast(hexColor, DARK['bg2'])
    check(f'深色组排色 {index} 在卡片底色上 >= 4', ratio >= 4,
          f'{hexColor} -> {ratio:.1f}')

print('切换')

setTheme(Theme.DARK, save=False)
darkBg = color('bg0')
check('切到深色后 isDarkTheme 为真', isDarkTheme())
check('切到深色后底色是近黑', luminance(darkBg) < 0.05, darkBg)

setTheme(Theme.LIGHT, save=False)
lightBg = color('bg0')
check('切到浅色后 isDarkTheme 为假', not isDarkTheme())
check('切到浅色后底色是近白', luminance(lightBg) > 0.8, lightBg)
check('两套底色确实不同', darkBg != lightBg, f'{darkBg} / {lightBg}')

setTheme(Theme.DARK, save=False)

print()
if failures:
    print(f'{len(failures)} 项失败: ' + ', '.join(failures))
    sys.exit(1)

print('主题色板全部通过')
