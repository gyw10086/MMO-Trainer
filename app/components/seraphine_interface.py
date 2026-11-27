from PyQt5.QtGui import QColor, QPalette
from PyQt5.sip import wrapper

from app.common.design_tokens import color
from app.common.qfluentwidgets import SmoothScrollArea


class SeraphineInterface(SmoothScrollArea):
    """所有一级页面的基类

    QScrollArea 的 viewport 用的是 `QPalette.Base`，Qt 默认是白色。
    页面内容哪怕差几个像素没铺满（窗口最大化、内容比视口矮），
    边上就会露出一条白底 —— 看起来像「软件底色是白的」。
    这里把 viewport 的底色钉成当前主题的 bg0。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        palette = self.viewport().palette()
        background = QColor(color('bg0'))
        palette.setColor(QPalette.Base, background)
        palette.setColor(QPalette.Window, background)
        self.viewport().setPalette(palette)
        self.viewport().setAutoFillBackground(True)

    def __str__(self):
        methods = [attr for attr in dir(self) if callable(getattr(self, attr))]
        attrs = [f"{k}({type(v).__name__})={v!r}" for k, v in self.__dict__.items() if
                 not isinstance(v, wrapper) and k not in methods]
        return f"{self.__class__.__name__}({', '.join(attrs)})"
