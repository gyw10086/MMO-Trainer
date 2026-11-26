# coding:utf-8
"""把英雄联盟国服「版本更新公告」抓下来，解析成 data/patch_notes.json

这个脚本是 `MMO-Trainer` 客户端的远端兜底数据源，由 GitHub Actions 每 6 小时
跑一次。它和客户端里的 `app/lol/patch_notes.py` 是**同一套解析规则**，改规则时
两边都要改 —— 区别只在这里不读本地缓存、不做版本合并。

用法：
    python tools/refresh_patch_notes.py --out data/patch_notes.json

抓不到就非零退出（Actions 会标红），不写空文件、不覆盖旧数据。
"""
import argparse
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

LIST_API = 'https://apps.game.qq.com/cmc/zmMcnTargetContentList'
DETAIL_API = 'https://lol.qq.com/gicp/news/410/{newsId}.html'

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
    'Referer': 'https://lol.qq.com/',
}

# 客户端能读懂的格式版本，加字段时递增
SCHEMA = 1

_TITLE = re.compile(r'^(\d{1,2}\.\d{1,2})\s*版本更新公告$')

_KIND_HERO = '英雄'
_KIND_ITEM = '装备'
_KIND_SYSTEM = '系统'

SYSTEM_SECTIONS = ('经典模式', '海克斯大乱斗', '极地大乱斗', '斗魂竞技场', 'BUG修复及品质级改动')

_NUMBER = re.compile(r'-?\d+(?:\.\d+)?%?')
_SKILL = re.compile(r'^([QRWEP]\s*[-–—]|被动|基础属性|终极技能|唯一被动)')

_CATEGORY = {'英雄', '装备', '系统', '强化符文', '赛季之星', 'BUG修复', '综合'}

_SHORT_SECTION = {'BUG修复及品质级改动': 'BUG修复'}


class FetchError(Exception):
    """抓不到可用的公告"""


def _inline(fragment: str) -> str:
    text = re.sub(r'<[^>]+>', '', fragment or '')
    text = html.unescape(text)
    text = (text.replace('\u2003', ' ').replace('\u2002', ' ')
                .replace('\u00a0', ' ').replace('\u3000', ' '))
    text = text.replace('⇒', '→').replace('=&gt;', '→')
    return re.sub(r'\s+', ' ', text).strip()


def _lines(fragment: str) -> list:
    text = re.sub(r'<br\s*/?>', '\n', fragment or '', flags=re.I)
    text = re.sub(r'</(?:p|blockquote|h4|h3|li|tr)>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = (text.replace('\u2003', ' ').replace('\u2002', ' ')
                .replace('\u00a0', ' ').replace('\u3000', ' '))
    text = text.replace('⇒', '→')

    out = []
    for line in text.split('\n'):
        line = re.sub(r'[ \t]+', ' ', line).strip()
        if line:
            out.append(line)
    return out


def _numbers(text: str) -> list:
    return _NUMBER.findall(text or '')


def _diffPair(stat: str) -> str:
    if '→' not in stat:
        return ''

    left, _, right = stat.partition('→')
    before, after = _numbers(left), _numbers(right)

    for old, new in zip(before, after):
        if old != new:
            return f'{old} → {new}'

    if len(before) != len(after):
        if not before or not after:
            return ''
        head = f'{before[0]}…{before[-1]}' if len(before) > 1 else before[0]
        return f'{head} → {after[-1]}'

    if before and after:
        return f'{before[0]} → {after[0]}'
    return ''


_UNIT = re.compile(r'\d+(?:\.\d+)?%?\s*(?:法术强度|攻击力|生命值|最大生命值|法力值|护甲|魔抗|'
                   r'移动速度|攻击速度|技能急速|金币|秒)?\s*$')


def _attr(stat: str) -> str:
    head = stat.split('：')[0].strip() if '：' in stat else stat.split('→')[0].strip()
    head = re.sub(r'[（(][^）)]*[）)]', '', head)
    head = _UNIT.sub('', head).strip()
    if not head:
        return ''
    return head.split(' ')[-1]


def _skillMark(line: str) -> str:
    if not line:
        return ''

    matched = re.match(r'^([QRWEP])\s*[-–—]', line)
    if matched:
        return matched.group(1)

    if line.startswith('基础属性'):
        return '基础属性'

    return ''


def _entryTitle(name: str, skill: str, stat: str, showSkill: bool = True) -> str:
    pair = _diffPair(stat)
    if not pair:
        return f'{name}：{stat}' if stat else name

    mark = _skillMark(skill) if showSkill else ''
    parts = [part for part in (mark, _attr(stat), pair) if part]
    return f'{name}：{" ".join(parts)}'


def _statLine(lines: list) -> tuple:
    for index, line in enumerate(lines):
        if '→' not in line:
            continue

        skill = ''
        if index:
            prev = lines[index - 1]
            if _SKILL.match(prev) and len(prev) <= 30:
                skill = prev
        return line, skill

    for line in lines:
        if len(line) >= 8 and not line.startswith(('注：', '&')):
            return line, ''
    return '', ''


def _brief(text: str, limit: int = 30) -> str:
    text = re.split(r'[。！？；]', text or '', maxsplit=1)[0].strip()
    return text if len(text) <= limit else text[:limit] + '…'


def _systemEntry(section: str, rest: str) -> dict:
    lines = _lines(rest)
    lines = [line for line in lines if line and line != section]
    label = _SHORT_SECTION.get(section, section)

    categories = [line for line in lines if line in _CATEGORY]

    for index, line in enumerate(lines):
        if '→' not in line:
            continue

        pair = _diffPair(line)
        if not pair:
            continue

        who = ''
        if index:
            prev = lines[index - 1]
            if 2 <= len(prev) <= 10 and prev not in _CATEGORY and not _SKILL.match(prev):
                who = prev

        attr = _attr(line).split(' ')[-1]
        title = f'{label}：{who} {attr} {pair}' if who else f'{label}：{attr} {pair}'
        return {
            'kind': _KIND_SYSTEM,
            'name': label,
            'title': re.sub(r'\s+', ' ', title).strip(),
            'detail': line,
        }

    added = []
    for line in lines:
        matched = re.match(r'^新增\s*[-–—]\s*(.+)$', line)
        if matched:
            added.append(matched.group(1).split(' ')[-1])

    if added:
        return {
            'kind': _KIND_SYSTEM,
            'name': label,
            'title': f'{label}：新增 {("、".join(added))}',
            'detail': next((line for line in lines if line.startswith('新增')), ''),
        }

    if categories:
        return {
            'kind': _KIND_SYSTEM,
            'name': label,
            'title': f'{label}：{"、".join(dict.fromkeys(categories))} 调整',
            'detail': '',
        }

    prose = [line for line in lines if len(line) >= 8]
    if not prose:
        return {}

    return {
        'kind': _KIND_SYSTEM,
        'name': label,
        'title': f'{label}：{_brief(prose[0])}',
        'detail': prose[0],
    }


def _entries(version: str, date: str, url: str, body: str) -> list:
    heroes, items, systems = [], [], []

    for chunk in re.split(r'<h3[^>]*>', body)[1:]:
        head, _, rest = chunk.partition('</h3>')
        section = _inline(head)
        if not section:
            continue

        if section in (_KIND_HERO, _KIND_ITEM):
            kind = section
            blocks = re.split(r'<h4[^>]*>', rest)
            for block in blocks[1:]:
                rawName, _, inner = block.partition('</h4>')
                name = _inline(rawName).split(' ')[-1]
                if not name:
                    continue

                stat, skill = _statLine(_lines(inner))
                bucket = heroes if kind == _KIND_HERO else items
                bucket.append({
                    'kind': kind,
                    'name': name,
                    'title': _entryTitle(name, skill, stat,
                                         showSkill=(kind == _KIND_HERO)),
                    'detail': stat,
                    'version': version,
                    'date': date,
                    'url': url,
                })
            continue

        if section in SYSTEM_SECTIONS:
            entry = _systemEntry(section, rest)
            if entry:
                entry.update({'version': version, 'date': date, 'url': url})
                systems.append(entry)

    return _interleave(heroes, items, systems)


def _interleave(heroes: list, items: list, systems: list) -> list:
    queues = [queue for queue in (heroes, items, systems) if queue]
    merged, cursor = [], 0

    while any(queues):
        queue = queues[cursor % len(queues)]
        if queue:
            merged.append(queue.pop(0))
        cursor += 1
        queues = [queue for queue in queues if queue]

    return merged


def _fetchList() -> dict:
    resp = requests.get(
        LIST_API,
        params={'r0': 'json', 'page': 1, 'num': 60, 'target': 24, 'source': 'web_pc'},
        headers=HEADERS, timeout=12)
    resp.raise_for_status()

    rows = ((resp.json() or {}).get('data') or {}).get('result') or []

    for row in rows:
        title = (row.get('sTitle') or '').strip()
        matched = _TITLE.match(title)
        if not matched:
            continue

        newsId = row.get('iNewsId') or ''
        url = row.get('sRedirectURL') or (DETAIL_API.format(newsId=newsId) if newsId else '')
        return {
            'version': matched.group(1),
            'date': (row.get('sCreated') or '')[:10],
            'url': url,
        }

    raise FetchError('公告列表里没有版本更新条目')


def _fetchBody(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    raw = resp.content
    for encoding in ('gbk', 'gb18030', 'utf-8'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('gb18030', 'ignore')


def _extractArticle(page: str) -> str:
    start = page.find('<div class="article"')
    if start < 0:
        start = page.find('id="article"')
    if start < 0:
        return ''

    end = page.find('</div>', start)
    return page[start:end] if end > start else page[start:]


def fetch() -> dict:
    meta = _fetchList()
    body = _extractArticle(_fetchBody(meta['url']))
    if not body:
        raise FetchError('公告正文结构无法识别')

    items = _entries(meta['version'], meta['date'], meta['url'], body)
    if not items:
        raise FetchError('公告里没有解析到改动条目')

    return {
        'schema': SCHEMA,
        'version': meta['version'],
        'date': meta['date'],
        'url': meta['url'],
        'fetchedAt': int(time.time()),
        'items': items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='抓取 LOL 国服版本更新公告')
    parser.add_argument('--out', default='data/patch_notes.json')
    parser.add_argument('--allow-empty', action='store_true',
                        help='抓取失败时也退出 0（默认直接失败，保留旧文件）')
    args = parser.parse_args()

    try:
        data = fetch()
    except Exception as error:
        print(f'fetch failed: {error}', file=sys.stderr)
        return 0 if args.allow_empty else 1

    # 只写解析成功的完整数据；条数太少基本意味着页面结构变了，宁可不更新
    if len(data['items']) < 3:
        print(f"too few items: {len(data['items'])}, refuse to publish", file=sys.stderr)
        return 1

    out = Path(args.out)

    # 内容没变就别动文件：Actions 每 6 小时跑一次，否则每次都会写一个新的
    # fetchedAt，仓库里全是「只有时间戳变了」的提交
    try:
        previous = json.loads(out.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = None

    if previous and all(previous.get(key) == data.get(key)
                        for key in ('version', 'date', 'url', 'items')):
        print(f"no change: {data['version']} ({data['date']}) {len(data['items'])} items")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    print(f"wrote {out}: {data['version']} ({data['date']}) {len(data['items'])} items")
    return 0


if __name__ == '__main__':
    sys.exit(main())
