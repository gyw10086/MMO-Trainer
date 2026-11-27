# coding:utf-8
"""LOL 官方版本改动（国服公告）

总览页的「近期改动」说的是 **游戏** 的改动（英雄 / 装备 / 系统），
不是本软件的更新日志 —— 后者在设置页「关于」里看 CHANGELOG。

数据链路，全部来自官方页面，不做二次加工、不编造数值：
    1. 官方内容列表接口 target=24，筛标题形如 `26.23版本更新公告` 的最新一条
    2. 打开公告页（GBK 编码），按 <h3> 分区、<h4> 条目切块
    3. 每个条目取第一处 `⇒` 数值改动，压成「技能 · 属性 旧 → 新」

**远端兜底**：解析规则一旦写死在客户端，官网改版就只能等发版修。所以再加一路
GitHub 上的成品 JSON（仓库 `gyw10086/MMO-Trainer`，由 Actions 每 6 小时重新生成）：
官方页和远端并发拉，谁新用谁。官网改版时旧客户端靠远端继续拿到数据，不用升级。

网络失败一律退回缓存；缓存也没有就抛 PatchNotesUnavailable，
由界面显示「离线 / 重试」，绝不拿假数据顶上。

缓存：%APPDATA%\\Seraphine\\patch_notes.json，45 分钟有效。
"""
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from app.common.config import LOCAL_PATH
from app.common.logger import logger

TAG = 'PatchNotes'

LIST_API = 'https://apps.game.qq.com/cmc/zmMcnTargetContentList'
# 公告详情页兜底地址：正常走列表里的 sRedirectURL，拿不到时用 iNewsId 拼
DETAIL_API = 'https://lol.qq.com/gicp/news/410/{newsId}.html'
# 公告列表页，给「官方公告」角标做跳转
NEWS_HOME = 'https://lol.qq.com/news/'

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
    'Referer': 'https://lol.qq.com/',
}

CACHE_FILE = Path(LOCAL_PATH) / 'patch_notes.json'
CACHE_TTL = 45 * 60

# 远端成品 JSON：仓库里由 GitHub Actions 定时用同一套规则生成并提交。
# 顺序有讲究：国内直连 raw.githubusercontent 基本不通（实测超时），
# jsDelivr 走 CDN 能通；raw 留给挂了代理的环境兜底。
REMOTE_URLS = (
    'https://cdn.jsdelivr.net/gh/gyw10086/MMO-Trainer@main/data/patch_notes.json',
    'https://raw.githubusercontent.com/gyw10086/MMO-Trainer/main/data/patch_notes.json',
)
# 客户端能读懂的格式版本；远端比它大说明是自己太旧，直接不用
REMOTE_SCHEMA = 1
REMOTE_TIMEOUT = 6

# 远端 JSON 多少天没更新就认为它已经停了（官网改版 / Actions 被停用）。
# 这种情况下数据还能读到，但已经不代表当前版本，必须让用户看出来。
REMOTE_STALE_DAYS = 7

# `26.23版本更新公告`；云顶之弈有自己的版本号，不能混进来
_TITLE = re.compile(r'^(\d{1,2}\.\d{1,2})\s*版本更新公告$')

# 这三类之外的区块（版本要闻 / 皮肤 / 活动）不是平衡性改动，跳过
_KIND_HERO = '英雄'
_KIND_ITEM = '装备'
_KIND_SYSTEM = '系统'

# 出现在 h3 里、且确实包含玩法改动的分区；顺序即面板里的优先级
SYSTEM_SECTIONS = ('经典模式', '海克斯大乱斗', '极地大乱斗', '斗魂竞技场', 'BUG修复及品质级改动')

_NUMBER = re.compile(r'-?\d+(?:\.\d+)?%?')
_SKILL = re.compile(r'^([QRWEP]\s*[-–—]|被动|基础属性|终极技能|唯一被动)')

# 系统分区里 h4 只起分类作用（英雄 / 强化符文 / BUG修复），不是条目名
_CATEGORY = {'英雄', '装备', '系统', '强化符文', '赛季之星', 'BUG修复', '综合'}

# 标题里用短名，`BUG修复及品质级改动：…` 太长，会把属性挤没
_SHORT_SECTION = {'BUG修复及品质级改动': 'BUG修复'}


class PatchNotesUnavailable(Exception):
    """拉不到官方公告，且本地没有可用缓存"""


def _inline(fragment: str) -> str:
    """把一小段 HTML 压成一行纯文本"""
    text = re.sub(r'<[^>]+>', '', fragment or '')
    text = html.unescape(text)
    text = (text.replace('\u2003', ' ').replace('\u2002', ' ')
                .replace('\u00a0', ' ').replace('\u3000', ' '))
    text = text.replace('⇒', '→').replace('=&gt;', '→')
    return re.sub(r'\s+', ' ', text).strip()


def _lines(fragment: str) -> list:
    """把 HTML 片段按视觉换行拆开

    公告正文只用 <br> 和 </p> 表意，直接去标签会把「技能名」和「数值」
    粘成一行，所以先把换行标签替换成 \\n 再洗标签。
    """
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
    """从 `A ⇒ B` 里挑出第一对不一样的数字，压成 `旧 → 新`

    技能数值常常是 `80 / 120 / 160 / 200 / 240` 这种序列，取首尾会得到
    「80 → 80」这种看不出变化的结果，所以逐位比对，取第一个真正变了的。
    一侧是「全等级统一成一个值」时（`45 / 50 / 55 → 45`），保留区间感。
    """
    if '→' not in stat:
        return ''

    left, _, right = stat.partition('→')
    before, after = _numbers(left), _numbers(right)

    # 先逐位比对：这是唯一能确定「哪个数值变了」的方法
    for old, new in zip(before, after):
        if old != new:
            return f'{old} → {new}'

    # 逐位都相同、但两侧个数不同：多半是「全等级序列 → 统一值」，
    # 这时首值没变，有意义的是末值（`45 / 50 / 55 → 45`）
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
    """从数值行里抽属性名

    `唯一被动-苍穹 额外伤害：…` → `额外伤害`
    `法术强度收益率(初始和标记)40%法术强度 ⇒ …` → `法术强度收益率`
    `提伯斯生命值：…` → `提伯斯生命值`
    """
    head = stat.split('：')[0].strip() if '：' in stat else stat.split('→')[0].strip()
    head = re.sub(r'[（(][^）)]*[）)]', '', head)
    head = _UNIT.sub('', head).strip()
    if not head:
        return ''
    return head.split(' ')[-1]


def _cleanSkill(line: str) -> str:
    line = re.sub(r'^([QRWEP])\s*[-–—]\s*', r'\1 ', line)
    return line.strip()


def _skillMark(line: str) -> str:
    """把技能行压成一个标记，对齐设计稿的「Q 基础伤害」

    `R - 提伯斯之怒` → `R`（技能名放进标题会把 620px 的列撑爆）
    `基础属性` → `基础属性`
    `唯一被动-苍穹` → ``（装备行不需要被动名）
    """
    if not line:
        return ''

    matched = re.match(r'^([QRWEP])\s*[-–—]', line)
    if matched:
        return matched.group(1)

    if line.startswith('基础属性'):
        return '基础属性'

    return ''


def _entryTitle(name: str, skill: str, stat: str, showSkill: bool = True) -> str:
    """条目名 + 技能标记 + 属性 + 首对变化数值

    设计稿的写法是「亚索：Q 基础伤害 20 → 25」，一行说清谁、哪招、从多少到多少。
    """
    pair = _diffPair(stat)
    if not pair:
        return f'{name}：{stat}' if stat else name

    mark = _skillMark(skill) if showSkill else ''
    parts = [part for part in (mark, _attr(stat), pair) if part]
    return f'{name}：{" ".join(parts)}'


def _statLine(lines: list) -> tuple:
    """取第一处带箭头的数值行，以及紧跟它上面的技能名"""
    for index, line in enumerate(lines):
        if '→' not in line:
            continue

        skill = ''
        if index:
            prev = lines[index - 1]
            if _SKILL.match(prev) and len(prev) <= 30:
                skill = prev
        return line, skill

    # 本版本没动数值（重做、修 bug），退回说明的第一句
    for line in lines:
        if len(line) >= 8 and not line.startswith(('注：', '&')):
            return line, ''
    return '', ''


def _brief(text: str, limit: int = 30) -> str:
    """取一句话的前半截：公告里的说明动辄上百字，列表行放不下"""
    text = re.split(r'[。！？；]', text or '', maxsplit=1)[0].strip()
    return text if len(text) <= limit else text[:limit] + '…'


def _systemEntry(section: str, rest: str) -> dict:
    """模式 / 系统类改动

    这部分没有 <h4> 条目名，正文是「英雄名 → 属性：旧 ⇒ 新」交替排列，
    所以按「数值行 + 上一行的名字」配对；整块只剩分类标题时，
    退回「分类清单」（`经典模式：英雄、BUG修复`），至少说清这个版本动了哪几块。
    """
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

    # 经典模式这类整块重做：把 `新增 - 刀锋之影 泰隆` 的短名挑出来
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

        if section == _KIND_HERO or section == _KIND_ITEM:
            kind = _KIND_HERO if section == _KIND_HERO else _KIND_ITEM
            blocks = re.split(r'<h4[^>]*>', rest)
            for block in blocks[1:]:
                rawName, _, inner = block.partition('</h4>')
                # `黑暗之女 安妮` → `安妮`；`电震涡流剑` 原样
                name = _inline(rawName).split(' ')[-1]
                if not name:
                    continue

                stat, skill = _statLine(_lines(inner))
                heroesOrItems = heroes if kind == _KIND_HERO else items
                heroesOrItems.append({
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
    """三类轮流取，保证首屏就能看到「英雄 / 装备 / 系统」都有

    面板一屏只放得下 5~9 行，如果按公告顺序排，装备和系统永远在折叠区里，
    看上去就像「这个版本只改了英雄」。
    """
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

    payload = resp.json() or {}
    rows = ((payload.get('data') or {}).get('result')) or []

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

    raise PatchNotesUnavailable('官方公告列表里没有版本更新条目')


def _fetchBody(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    raw = resp.content
    # 公告页 meta 写的是 gbk；个别页面混了 utf-8 片段，用 gb18030 兜住超集
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
    # 正文里嵌套 div 极少，但公告尾部有分享/推荐块，砍到下一个 h3 群之前更安全
    return page[start:end] if end > start else page[start:]


def fetchPatchNotes() -> dict:
    """联网抓最新版本公告并解析，失败抛异常"""
    meta = _fetchList()
    page = _fetchBody(meta['url'])
    body = _extractArticle(page)
    if not body:
        raise PatchNotesUnavailable('公告正文结构无法识别')

    items = _entries(meta['version'], meta['date'], meta['url'], body)
    if not items:
        raise PatchNotesUnavailable('公告里没有解析到改动条目')

    logger.info(f"patch notes {meta['version']}: {len(items)} items", TAG)
    return {
        'version': meta['version'],
        'date': meta['date'],
        'url': meta['url'],
        'source': 'official',
        'fetchedAt': int(time.time()),
        'items': items,
    }


def _versionKey(value) -> tuple:
    """`26.23` → (26, 23)；认不出来给 (0, 0)，排序时永远垫底"""
    matched = re.match(r'^(\d{1,2})\.(\d{1,2})$', str(value or '').strip())
    return (int(matched.group(1)), int(matched.group(2))) if matched else (0, 0)


def _validRemote(data) -> bool:
    """远端 JSON 的自检

    这里必须挑剔一点：一份格式错的数据比没有数据更糟，会让所有客户端
    同时显示错误内容。只接受「版本号能认 + 有若干条带标题的改动」。
    """
    if not isinstance(data, dict) or _versionKey(data.get('version')) == (0, 0):
        return False

    items = data.get('items')
    if not isinstance(items, list) or not items:
        return False

    for item in items:
        if not isinstance(item, dict) or not item.get('title'):
            return False

    schema = data.get('schema')
    if schema is not None:
        try:
            if int(schema) > REMOTE_SCHEMA:
                return False
        except (TypeError, ValueError):
            return False

    return True


def fetchRemote() -> dict:
    """拉仓库里已经解析好的公告，失败返回 None

    `t` 参数按 5 分钟取整：既能穿过 Raw/jsDelivr 的短缓存拿到新文件，
    又不会每次刷新都击穿 CDN。
    """
    stamp = int(time.time() // 300)

    for url in REMOTE_URLS:
        try:
            resp = requests.get(url, params={'t': stamp}, headers=HEADERS,
                                timeout=REMOTE_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as error:
            logger.warning(f'remote patch notes failed: {error}', TAG)
            continue

        if not _validRemote(data):
            logger.warning(f'remote patch notes invalid: {url}', TAG)
            continue

        data['source'] = 'remote'
        logger.info(
            f"remote patch notes {data.get('version')}: {len(data['items'])} items", TAG)
        return data

    return None


def _fetchFresh() -> dict:
    """同时问官方和远端，返回较新的那份

    官方是源头，但解析规则写死在客户端；远端是「提前解析好的成品」。
    两路并发，按 (版本号, 来源优先级) 取新：
      - 官网刚发版本、远端还没跟上 → 用官方
      - 官网改版、本地解析挂了 → 用远端
      - 同版本 → 用远端（规则可以在仓库里改，不用发版）
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(fetchPatchNotes), pool.submit(fetchRemote)]
        results = []

        for job in jobs:
            try:
                results.append(job.result())
            except Exception as error:
                results.append(error)

    candidates = [item for item in results if isinstance(item, dict)]
    errors = [item for item in results if isinstance(item, Exception)]

    if not candidates:
        detail = errors[0] if errors else '没有可用的公告数据'
        raise PatchNotesUnavailable(str(detail))

    return max(candidates, key=lambda item: (
        _versionKey(item.get('version')),
        1 if item.get('source') == 'remote' else 0,
        int(item.get('fetchedAt') or 0),
    ))


def readCache() -> dict:
    """读本地缓存；没有或损坏返回 None"""
    try:
        data = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict) or not data.get('items'):
        return None
    return data


def cacheAge(data: dict) -> float:
    """本地这份数据有多久没确认过了

    用 `checkedAt` 而不是 `fetchedAt`：远端 JSON 的 `fetchedAt` 是它自己生成
    的时间（可能是几小时前），拿它算 TTL 会让缓存刚写进来就被判成过期，
    于是每次切页都去打一次接口。
    """
    stamp = int(data.get('checkedAt') or data.get('fetchedAt') or 0)
    return max(0, time.time() - stamp)


def writeCache(data: dict):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    except OSError as error:
        logger.warning(f'write patch cache failed: {error}', TAG)


def _staleReason(data: dict) -> str:
    """数据本身是不是已经过时了

    只看远端来源：官方页每次抓都是当次时间戳，不存在「拿到一份旧快照」。
    远端被冻结时（官网改版解析不出来、Actions 被停用），客户端每轮都能
    成功读到同一份旧数据 —— 不标出来的话，界面会把它当成最新的显示。
    """
    if data.get('source') != 'remote':
        return ''

    fetchedAt = int(data.get('fetchedAt') or 0)

    if fetchedAt and time.time() - fetchedAt > REMOTE_STALE_DAYS * 86400:
        return 'outdated'

    return ''


def loadPatchNotes(force: bool = False) -> dict:
    """给界面用：优先返回可用的最新数据

    命中 45 分钟缓存直接返回（不打扰官方接口）；过期则并发问官方和远端，
    取较新的那份。两路都失败但还有旧缓存时，退回旧缓存并标 `stale`，
    界面加「缓存」角标。

    `staleReason` 区分两种「旧」：
      - `outdated` 远端多日没更新，数字还在但可能不是当前版本
      - `offline`  网络挂了，显示的是本地最后一次成功的数据
    """
    cached = readCache()

    if cached and not force and cacheAge(cached) < CACHE_TTL:
        reason = _staleReason(cached)
        cached['stale'] = bool(reason)
        cached['staleReason'] = reason
        return cached

    try:
        fresh = _fetchFresh()
        fresh['checkedAt'] = int(time.time())

        reason = _staleReason(fresh)
        fresh['stale'] = bool(reason)
        fresh['staleReason'] = reason

        writeCache(fresh)
        return fresh
    except Exception as error:
        if cached:
            logger.warning(f'patch notes offline, using cache: {error}', TAG)
            cached['stale'] = True
            cached['staleReason'] = 'offline'
            return cached
        raise PatchNotesUnavailable(str(error)) from error
