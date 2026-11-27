# coding:utf-8
"""近期改动（本程序自己的更新日志）

原来这块读的是 GitHub 仓库里的 `document/notice.md` —— 上游仓库已清空，
拉不到就永远空着。改成读本地的 `CHANGELOG.md`：离线可用，
也跟「离线更新包」这条主线一致（更新包里带的就是这份文件）。

只做解析，不做网络请求；解析失败返回空列表，界面显示「暂无记录」。
"""
import re
from pathlib import Path

# 项目根：app/common/notice.py -> 上溯两级
ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / 'CHANGELOG.md'

# `# v2.1.0 · 2026-11-26` 或 `## 2.1.0 (2026-11-26)`
_HEADER = re.compile(r'^#{1,3}\s*v?([0-9][\w.]*)\s*(?:[·|\-–]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}))?')
# `- [新增] xxx` / `- 新增: xxx` / `* xxx`
_ITEM = re.compile(r'^[-*]\s*(?:\[([^\]]+)\]\s*)?(.+)$')

MAX_ITEMS = 30


def recentChanges(limit: int = 5) -> list:
    """返回最新的若干条改动

    每条是 dict: version / date / tag / text
    文件不存在或没有可用条目时返回 []，调用方负责显示空态。
    """
    if not CHANGELOG.exists():
        return []

    try:
        text = CHANGELOG.read_text(encoding='utf-8')
    except OSError:
        return []

    entries = []
    version = ''
    date = ''

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('>'):
            continue

        header = _HEADER.match(line)
        if header:
            version = header.group(1)
            date = header.group(2) or ''
            continue

        item = _ITEM.match(line)
        if not item:
            continue

        tag = (item.group(1) or '').strip()
        body = item.group(2).strip()
        if not body:
            continue

        entries.append({
            'version': version,
            'date': date,
            'tag': tag,
            'text': body,
        })

        if len(entries) >= MAX_ITEMS:
            break

    return entries[:limit]


def latestVersion() -> str:
    changes = recentChanges(1)
    return changes[0]['version'] if changes else ''
