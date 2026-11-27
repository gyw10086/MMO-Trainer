"""更新替换脚本

流程:
1. 主程序把新版 zip 解压到 %APPDATA%\\Seraphine\\temp
2. 这里生成一个 PowerShell 脚本, 等主进程退出后把 temp 覆盖到程序目录
3. 重新启动程序, 脚本自删

为什么不用上游那套 filelist.txt + Move-Item:
- Move-Item 往已存在的目录移动时会变成 "app/app" 这种嵌套
- filelist.txt 要手动维护删除清单, 漏一个就留下旧模块
- robocopy /E 是"覆盖 + 新增", 不删除目标目录里多出来的文件,
  用户自己放在程序目录里的东西不会被误删
"""

import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from app.common.config import LOCAL_PATH, cfg

EXE_NAME = 'MMO-Trainer.exe'

# GitHub 上的同一个文件有两套地址，国内的可达性正好互补：
#   jsDelivr 走国内 CDN，但仓库/分支刚推完会有缓存滞后
#   raw.githubusercontent 没有缓存，但部分网络环境直接连不上
# 所以任何一条地址都能推出另一条，下载时轮流试。
RAW_PATTERN = re.compile(
    r'^https?://raw\.githubusercontent\.com/(?P<user>[^/]+)/(?P<repo>[^/]+)'
    r'/(?P<ref>[^/]+)/(?P<path>.+)$')
JSD_PATTERN = re.compile(
    r'^https?://cdn\.jsdelivr\.net/gh/(?P<user>[^/]+)/(?P<repo>[^/]+)'
    r'@(?P<ref>[^/]+)/(?P<path>.+)$')


def mirrorOf(url: str) -> str:
    """raw <-> jsDelivr 互换，认不出返回空串"""
    url = (url or '').strip()

    match = RAW_PATTERN.match(url)

    if match:
        g = match.groupdict()
        return (f"https://cdn.jsdelivr.net/gh/{g['user']}/{g['repo']}"
                f"@{g['ref']}/{g['path']}")

    match = JSD_PATTERN.match(url)

    if match:
        g = match.groupdict()
        return (f"https://raw.githubusercontent.com/{g['user']}/{g['repo']}"
                f"/{g['ref']}/{g['path']}")

    return ''


def candidateUrls(info: dict) -> list:
    """把更新信息里的 assets 展开成候选下载地址

    兼容三种写法，因为清单是手写的、历史上换过形状：
      - `{"assets": [{"browser_download_url": "...", "mirrors": [...]}]}`  Releases 那套
      - `{"assets": ["https://..."]}`                                    纯字符串
      - `"download": "https://..."`                                     早期清单

    每条地址都会补一个镜像，去重后保序 —— 顺序就是下载时的尝试顺序。
    """
    info = info or {}
    raw = []

    for asset in info.get('assets') or []:
        if isinstance(asset, str):
            raw.append(asset)
            continue

        if not isinstance(asset, dict):
            continue

        if asset.get('browser_download_url'):
            raw.append(asset['browser_download_url'])

        for mirror in asset.get('mirrors') or []:
            raw.append(mirror)

    if info.get('download'):
        raw.append(info['download'])

    result = []

    for url in raw:
        url = (url or '').strip()

        if not url:
            continue

        for candidate in (url, mirrorOf(url)):
            if candidate and candidate not in result:
                result.append(candidate)

    return result


def proxyUrl() -> str:
    """设置里的 GitHub 代理，补成 aiohttp 认的完整 URL

    设置项默认存的是 `127.0.0.1:10809`（没 scheme），requests 能容忍，
    aiohttp 会直接抛 InvalidURL。所以这里统一补齐。
    """
    if not cfg.get(cfg.enableGithubProxy):
        return ''

    addr = (cfg.get(cfg.githubProxyAddr) or '').strip()

    if not addr:
        return ''

    if '://' not in addr:
        addr = f'http://{addr}'

    return addr


def verifyPackage(path: str, expectedSize: int = 0) -> tuple:
    """下载完整性校验，返回 `(ok, 说明)`

    只校验「能不能用」：大小对得上、zip 目录读得出来、每个成员都能解压。
    不比对哈希 —— 清单是手写的，多一个字段就多一处会写错的地方。

    半个文件最坑：解压到一半才报错，那时主程序已经把旧版本关了。
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return False, f'读不到文件: {e}'

    if size <= 0:
        return False, '文件是空的'

    # 允许 2% 误差：清单里的 size 可能是打包时算的，压缩参数不同会差几 KB
    if expectedSize and abs(size - expectedSize) > max(4096, expectedSize * 0.02):
        return False, f'大小不符: 实际 {size} 字节, 清单写的是 {expectedSize}'

    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()

            if bad:
                return False, f'压缩包内文件损坏: {bad}'

            if not z.namelist():
                return False, '压缩包是空的'
    except zipfile.BadZipFile:
        return False, '不是有效的 zip（可能下载被截断）'
    except Exception as e:
        return False, f'解压校验失败: {e}'

    return True, f'{size / (1024 * 1024):.2f} MB'


SCRIPT_TEMPLATE = r'''$ErrorActionPreference = 'SilentlyContinue'
$targetPid = {pid}
$src = "{src}"
$dest = "{dest}"

# 等主进程真的退出, 否则文件还被占用
while (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {{
    Start-Sleep -Milliseconds 400
}}

# 再给句柄释放留一点时间
Start-Sleep -Milliseconds 900

# /E 遍历子目录(含空目录) /IS 覆盖同名文件 /IT 覆盖内容有变化的文件
# 不加 /MIR —— 镜像会删掉用户自己放在目录里的东西
robocopy $src $dest /E /IS /IT /NFL /NDL /NJH /NJS /R:2 /W:1 | Out-Null

# 清掉解压出来的临时文件
Remove-Item -Path $src -Recurse -Force

# 优先重新拉起启动器 exe; 没有 exe(纯源码运行)就用 pythonw 跑 main.py
$exe = Join-Path $dest "{exe}"
if (Test-Path $exe) {{
    Start-Process -FilePath $exe -WorkingDirectory $dest
}} else {{
    $pyw = Join-Path $dest "pythonw.exe"
    if (-not (Test-Path $pyw)) {{ $pyw = "pythonw.exe" }}
    Start-Process -FilePath $pyw -ArgumentList "main.py" -WorkingDirectory $dest
}}

# 脚本自删
Remove-Item -Path $MyInvocation.MyCommand.Definition -Force
'''


def appDir() -> Path:
    """程序根目录 (main.py 所在处)"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent

    # app/common/update.py -> 上两层就是项目根
    return Path(__file__).resolve().parents[2]


def runUpdater() -> bool:
    """生成脚本并后台执行, 调用方随后应立即退出程序"""
    src = Path(LOCAL_PATH) / 'temp'

    if not src.is_dir():
        return False

    script = SCRIPT_TEMPLATE.format(
        pid=os.getpid(),
        src=str(src),
        dest=str(appDir()),
        exe=EXE_NAME,
    )

    scriptPath = Path(tempfile.gettempdir()) / 'mmo_trainer_update.ps1'

    # utf-8-sig: PowerShell 5.1 只有看到 BOM 才会按 UTF-8 解析,
    # 否则脚本里的中文 exe 名会变成乱码
    with open(scriptPath, 'w', encoding='utf-8-sig') as f:
        f.write(script)

    flags = 0
    if hasattr(subprocess, 'CREATE_NO_WINDOW'):
        flags = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(
        ['PowerShell.exe', '-ExecutionPolicy', 'Bypass', '-NoProfile',
         '-File', str(scriptPath)],
        creationflags=flags)

    return True
