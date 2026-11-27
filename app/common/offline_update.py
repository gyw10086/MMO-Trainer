"""离线更新包: 把 zip 拖进窗口就完成更新

为什么改成这样(而不是从 GitHub 在线拉):
- 国内直连 github.com 不稳定, 在线更新失败率高, 失败后用户还得手动找包
- 用户手里已经有包, 真正缺的是"怎么把它装进去", 不是"怎么下载"
- 顺带没有了 rate limit / 代理 / token 这些额外变量

包结构必须与程序目录一致:
    MMO-Trainer-2.2.0.zip
    ├── app/
    ├── main.py
    └── requirements.txt

校验四件事, 任何一条不过就直接拒绝(错误文案给用户看):
1. 是 zip 且 testzip 能过 (防止下载中断的半截包)
2. 含 app/common/config.py (确认是本程序的包, 而不是随便一个压缩包)
3. 不含绝对路径和 .. (防止解压时写到程序目录之外)
4. 读得出包内 VERSION (用于和当前版本对比)
"""

import re
import shutil
import zipfile
from pathlib import Path

from app.common.config import LOCAL_PATH, VERSION

# 误拖一个电影进来时别去解压它
MAX_SIZE = 300 * 1024 * 1024

# 允许用户拖"多套了一层目录"的包 (有些压缩软件默认会包一层)
INNER_MARKERS = ('app', 'main.py')


class PackageError(Exception):
    """更新包不可用; 文案直接展示给用户"""


def _readVersion(zf: zipfile.ZipFile) -> str:
    try:
        raw = zf.read('app/common/config.py').decode('utf-8', 'ignore')
    except KeyError:
        raise PackageError('包里没有 app/common/config.py，不像是本程序的更新包')

    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', raw, re.M)

    return match.group(1) if match else '未知'


def _checkSafe(zf: zipfile.ZipFile):
    for name in zf.namelist():
        path = Path(name)

        if path.is_absolute() or '..' in path.parts:
            raise PackageError(f'包内有可疑路径，已拒绝解压：{name}')


def _readChangelog(zf: zipfile.ZipFile) -> list:
    """读包里的 CHANGELOG.md, 取前几条 - 开头的行

    没有这个文件就返回空列表, 不算错误 —— 只是确认框少显示一块
    """
    try:
        raw = zf.read('CHANGELOG.md').decode('utf-8', 'ignore')
    except KeyError:
        return []

    items = []

    for line in raw.splitlines():
        line = line.strip()

        if line.startswith(('-', '*', '+')) and len(line) > 2:
            items.append(line[1:].strip())

        if len(items) >= 5:
            break

    return items


def inspect(zipPath) -> dict:
    """只校验不落盘, 返回包信息"""
    path = Path(zipPath)

    if not path.is_file():
        raise PackageError('找不到这个文件')

    if path.suffix.lower() != '.zip':
        raise PackageError('只接受 .zip 格式的更新包')

    size = path.stat().st_size

    if size > MAX_SIZE:
        raise PackageError('这个文件超过 300 MB，可能选错了')

    try:
        with zipfile.ZipFile(path) as zf:
            broken = zf.testzip()

            if broken:
                raise PackageError(f'压缩包已损坏（{broken} 校验失败），可能没下载完')

            _checkSafe(zf)

            files = [n for n in zf.namelist() if not n.endswith('/')]

            if not files:
                raise PackageError('压缩包是空的')

            return {
                'path': str(path),
                'version': _readVersion(zf),
                'files': len(files),
                'sizeMB': size / (1024 * 1024),
                'isNewer': _isNewer(_readVersion(zf), VERSION),
                'changelog': _readChangelog(zf),
            }
    except zipfile.BadZipFile:
        raise PackageError('这个文件不是有效的 zip')


def _parse(ver: str) -> tuple:
    parts = []

    for chunk in str(ver or '').lstrip('vV').split('.'):
        match = re.match(r'\d+', chunk)
        parts.append(int(match.group()) if match else 0)

    return tuple(parts) or (0, )


def _isNewer(remote: str, local: str) -> bool:
    return _parse(remote) > _parse(local)


def extract(zipPath) -> Path:
    """解压到 %APPDATA%\\Seraphine\\temp, 返回该目录"""
    dest = Path(LOCAL_PATH) / 'temp'

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)

    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zipPath) as zf:
        zf.extractall(dest)

    return _flatten(dest)


def _flatten(dest: Path) -> Path:
    """把「多包了一层目录」的包拍平, 否则覆盖时会变成 app/app"""
    entries = list(dest.iterdir())

    if len(entries) != 1 or not entries[0].is_dir():
        return dest

    inner = entries[0]

    if not any((inner / marker).exists() for marker in INNER_MARKERS):
        return dest

    for item in list(inner.iterdir()):
        shutil.move(str(item), str(dest / item.name))

    inner.rmdir()

    return dest
