# coding:utf-8
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import ctypes
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QTranslator, QTimer
from app.common.qfluentwidgets import FluentTranslator, setThemeColor
from qasync import QApplication, QEventLoop
import asyncio
import sys
from app.common.config import cfg, VERSION, BETA
from app.common import skin
from app.common import theme
from app.view.main_window import MainWindow


def main():
    args = sys.argv
    if len(args) == 2 and args[1] in ['--version', '-v']:
        print(BETA or VERSION)
        return

    # 任务栏按 AppUserModelID 分组，并记住第一次看到的图标。
    # 源码运行时进程是 pythonw.exe，不声明就会一直显示它缓存的旧图标
    # （换了 logo 也不刷新）；声明成自己的 ID，任务栏才读窗口图标。
    if sys.platform == 'win32':
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'gyw.MMO.Trainer')
        except Exception:
            pass

    if cfg.get(cfg.dpiScale) == "Auto":
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    else:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(cfg.get(cfg.dpiScale))
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    font = QFont()
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferFullHinting)
    QApplication.setFont(font)

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

    # 主题跟随设置里的「应用主题」(浅色 / 深色 / 跟随系统)。
    # 早期这里钉死深色, 是因为各页 qss 里写死了深色值、跟着系统走会白底白字;
    # 现在浅色也补齐了, 就没有理由再拦住用户的选择。
    # 走统一入口: 此时还没有可见窗口, 冻结重绘那段自然空转
    theme.applyTheme(cfg.get(cfg.themeMode))

    # 机能黄作为固定品牌色: 不写进配置, 免得用户在设置里改过一次之后,
    # 每次启动都带着旧色; 需要换肤时改这里一处即可
    setThemeColor('#E3B23C', save=False)

    # 库内部控件的圆角/蓝色只能从 qss 出口后处理, 必须在建窗口前装好
    skin.install()
    # 面板 / 战绩行 / 分页条这些跨页组件挂应用级, 只写一份样式
    skin.applyGlobal(app)

    eventLoop = QEventLoop(app)
    asyncio.set_event_loop(eventLoop)

    appCloseEvent = asyncio.Event()
    app.aboutToQuit.connect(appCloseEvent.set)

    locale = cfg.get(cfg.language).value
    fluentTranslator = FluentTranslator(locale)
    seraphineTranslator = QTranslator()
    seraphineTranslator.load(locale, "Seraphine", ".", "./app/resource/i18n")

    app.installTranslator(fluentTranslator)
    app.installTranslator(seraphineTranslator)

    w = MainWindow()
    w.show()

    # 主题初始化完了才装「跟随系统」的探针: 它只在 AUTO 下跑, 系统改深色/浅色
    # 时两秒内跟上(库自己只在赋值那一刻读一次 darkdetect, 要重启才生效)
    theme.startAutoWatch()

    # 启动器把"拖到 exe 上"的更新包路径放在环境变量里, 界面出来后弹确认
    pendingPackage = os.environ.pop('MMO_UPDATE_PACKAGE', None)

    if pendingPackage:
        QTimer.singleShot(
            900, lambda: w.applyUpdatePackage(pendingPackage))

    eventLoop.run_until_complete(appCloseEvent.wait())
    eventLoop.close()


if __name__ == '__main__':
    main()
