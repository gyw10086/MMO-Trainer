# bin

这里曾经有一个 58KB 的 C 小程序，用于把客户端窗口按 `1280 × zoom` 居中重设。

运行时代码已迁到 `app/common/win_window.py`，仓库里不再保留任何 C 源码与 exe —
别在这里重新编译任何二进制。

保留这段说明，只是为了让后来的人知道 `win_window.py` 为什么要存在（它替代了什么），
而不是留一份实现。`make.ps1` 打包时会把整个 `app/resource/bin` 从安装包里排除。
