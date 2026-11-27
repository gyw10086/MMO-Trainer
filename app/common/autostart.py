"""开机自启: 只写 HKCU 下的 Run 项, 不需要管理员权限

为什么不用任务计划 / 启动文件夹:
- Run 项在任务管理器的"启动"页可见可关, 用户随时能自己禁用, 不会变成流氓软件
- 启动文件夹要往 %APPDATA% 丢 lnk, 卸载时容易留垃圾
- 任何写操作都要能在用户关掉开关时**干净删除**, 所以只用一个固定值名

启动目标:
- 打包成 exe 运行时 -> 直接指向这个 exe (launcher 会自己找项目目录和 Python)
- 源码运行时 -> pythonw.exe + main.py, 避免弹黑框
"""

import os
import sys
import winreg
from pathlib import Path

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'MMO战队训练助手'

# 老版本(改名前后)可能留下的值名, 切换开关时一并清掉, 免得开机弹两个
LEGACY_NAMES = ('MMO战队训练帮手', 'Seraphine')


def launchCommand() -> str:
    """开机时要执行的命令行"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打出来的 exe: sys.executable 就是它自己
        return f'"{os.path.abspath(sys.executable)}"'

    # 源码运行: 用 pythonw 避免控制台窗口一闪
    interpreter = Path(sys.executable)
    pythonw = interpreter.with_name('pythonw.exe')

    if not pythonw.is_file():
        pythonw = interpreter

    # app/common/autostart.py -> 项目根
    mainPy = Path(__file__).resolve().parents[2] / 'main.py'

    return f'"{pythonw}" "{mainPy}"'


def isEnabled() -> bool:
    """注册表里是否已经有本程序的自启项 (不读配置, 读真实状态)"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def apply(enabled: bool) -> bool:
    """写入或删除自启项, 返回是否成功"""
    try:
        # KEY_SET_VALUE 就够, 不需要管理员
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                winreg.KEY_SET_VALUE) as key:
            for legacy in LEGACY_NAMES:
                try:
                    winreg.DeleteValue(key, legacy)
                except FileNotFoundError:
                    pass

            if enabled:
                winreg.SetValueEx(
                    key, VALUE_NAME, 0, winreg.REG_SZ, launchCommand())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass

        return True
    except OSError:
        return False


def sync(enabled: bool):
    """启动时对账: 配置说要自启就确保写入, 说要关就确保删掉

    用户可能手动在任务管理器里禁用了启动项, 这时注册表值还在但被系统标记为禁用。
    这里只在"值是否存在"层面做对账, 不去覆盖系统的禁用标记 —— 尊重用户的选择。
    """
    if enabled and not isEnabled():
        apply(True)
    elif not enabled and isEnabled():
        apply(False)
