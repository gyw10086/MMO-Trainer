# coding:utf-8
"""对局分路判定：SGP 为准 + 本地缓存 + 算法兜底

## 为什么不再用 timeline.lane / role

那是 S5 时代的「玩家角色」字段（`SOLO / DUO / DUO_CARRY / DUO_SUPPORT`），
Riot 在 match-v5 里已经废弃。实测最近 15 场排位：

- `lane` 有 4 场是 `NONE`
- `role` 跟真实分路没有对应关系 —— 中路加里奥那局返回 `role = SUPPORT`，
  被旧逻辑判成「辅助」
- 另有 1 场 `lane = BOTTOM` 但 `role = DUO`，旧逻辑只认 `CARRY`/`SUPPORT`，
  直接判成空

也就是说错的不只是数据脏，是字段本身失效了。

## 三层判据，取到即止

1. SGP `teamPosition` → `individualPosition`（官方口径，实测最准）
2. 算法：带惩戒 = 打野、带辅助装 = 辅助（**只认这两个，其余不猜**）
3. 老 `lane` + `role`（收紧：`role = SUPPORT` 只在 lane 明确下路时才认辅助）

## 缓存

一局的分路打完就固定不变，按 `puuid|gameId` 落盘到
`%APPDATA%\\Seraphine\\positions.json`。首次多一次 SGP 请求（约 0.2 秒），
之后只有新对局才补，稳态零请求。

键必须是 `puuid|gameId` 而不是光 `gameId`：同一场对局会被多个人查询
（双排队友的生涯页、对局信息页的十个人），只用 gameId 的话后写的会把
先写的顶掉，「我的分路」变成队友的分路 —— 实测 48 条缓存里 18 条是队友的。

每次刷新还会做一次过期重校验（`FILL_TTL`）：即使判据变过、表里还留着按旧
判据算的错值，刷新生涯时最多 5 分钟就会被 SGP 的权威值覆盖纠正，不会再出现
「错值一落盘就永久错」。

**缓存必须带 schema 版本**：早期版本按 `participants[0]` 取玩家（SGP 给的是
完整十人，取到的常常是队友），写进去的分路是错的；而 `resolve()` 只在
「有 gameId 不在表里」时才去拉 SGP，于是这些错值一旦落盘就再也修不掉 ——
实测 30 场里 20 场和官方 `teamPosition` 对不上（ADC 显示成上单 / 打野）。
把 `CACHE_SCHEMA` 加一版就整表作废重拉，代价只有一次 0.2 秒。

非峡谷模式（大乱斗 / 斗魂）官方也不给位置，缓存里写哨兵 `NONE`，
避免每次刷新都重拉。
"""

import json
import os
import time

from app.common.config import LOCAL_PATH
from app.common.logger import logger
from app.lol.connector import connector
from app.lol.game_data import normalizePosition

TAG = 'Positions'

CACHE_PATH = f'{LOCAL_PATH}/positions.json'

# 缓存结构版本。判据改过就得加一版：
#   v1 —— 早期按 participants[0] 取玩家，SGP 的 participants 是完整十人，
#         取到的往往是队友，整表混了大量错值（实测 30 场错 20 场）
#   v2 —— SGP `teamPosition` 优先，且按 puuid 定位自己
#   v3 —— 键改成 f'{puuid}|{gameId}'。v2 只用 gameId，同一场对局被多人查询时
#         互相覆盖，"我的分路"会被双排队友的顶掉
CACHE_SCHEMA = 3

# 判据指纹。改了 fromSGP / fromItems / fromLegacy 的判法就 +1，
# 旧值会自动整表作废 —— 不必再靠人记得同步改 CACHE_SCHEMA
RULES_VERSION = 1

# 缓存条目上限。每局一条，长年累月会无限长；dict 保序，留最新的那些
MAX_ENTRIES = 2000

# 过期重校验间隔（秒）。到期就重新问一次 SGP，用来纠正历史毒值
FILL_TTL = 300

# 惩戒：唯一能直接判定「打野」的召唤师技能
SMITE_ID = 11

# 辅助装（世界树系列 3865~3870）。只在拿不到官方位置时才用
SUPPORT_ITEM = range(3865, 3871)

# 官方明确「没有位置」时存的哨兵，和「还没查过」区分开
NO_POSITION = 'NONE'

# 标准值 -> 界面文案。career_row / 对局信息条都用它，避免各写一份
POSITION_TEXT = {
    'TOP': '上单',
    'JUNGLE': '打野',
    'MIDDLE': '中路',
    'BOTTOM': '下路',
    'UTILITY': '辅助',
}

_cache = None

# puuid -> 上次成功向 SGP 补齐的时间戳，用来做过期重校验
_lastFill = {}


# ------------------------------------------------------------------ 单条判据
def fromSGP(participant) -> str:
    """SGP（match-v5）里取分路

    `teamPosition` 是官方统计口径，`individualPosition` 是位置检测结果；
    实测两者一致，前者优先。大乱斗 / 斗魂两个字段是空或 `Invalid`。
    """
    participant = participant or {}

    for key in ('teamPosition', 'individualPosition'):
        lane = normalizePosition(participant.get(key))

        if lane:
            return lane

    return ''


def fromItems(participant) -> str:
    """算法兜底：只能认出打野和辅助

    宁可不显示也不猜 —— 上单和中单在没有 `lane` 的情况下靠装备分不开，
    硬猜只会制造新的错。
    """
    participant = participant or {}
    spells = (participant.get('spell1Id'), participant.get('spell2Id'))

    if SMITE_ID in spells:
        return 'JUNGLE'

    stats = participant.get('stats') or {}

    for i in range(7):
        item = participant.get(f'item{i}') or stats.get(f'item{i}') or 0

        try:
            if int(item) in SUPPORT_ITEM:
                return 'UTILITY'
        except (TypeError, ValueError):
            continue

    return ''


def fromLegacy(participant) -> str:
    """老 lane + role，最后手段

    **关键修正**：`role == 'SUPPORT'` 不能无条件认成辅助 —— 中路局它照样
    返回 SUPPORT，这正是「中路显示辅助」的根因。只有 lane 明确在下路时才认。
    """
    participant = participant or {}
    timeline = participant.get('timeline') or {}

    lane = normalizePosition(participant.get('lane') or timeline.get('lane'))
    role = str(participant.get('role') or timeline.get('role') or '').upper()

    if lane == 'BOTTOM' and 'SUPPORT' in role:
        return 'UTILITY'

    return lane if lane in ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM') else ''


def resolveOne(participant, known: str = '') -> str:
    """单条兜底链：已知的优先，其次算法，最后老字段"""
    if known:
        return known

    return fromItems(participant) or fromLegacy(participant)


def positionLabel(value) -> str:
    """标准值 -> 两字中文，认不出返回空串"""
    return POSITION_TEXT.get(normalizePosition(value), '')


def lookup(mapping, gameId) -> str:
    """从 `{gameId: lane}` 里取，兼容 int / str 两种键"""
    if not mapping:
        return ''

    return mapping.get(gameId) or mapping.get(str(gameId)) or ''


# ------------------------------------------------------------------ 缓存
def _key(puuid: str, gameId) -> str:
    """缓存的键：`puuid|gameId`

    只按 gameId 存的话，同一场对局被双排队友 / 对局信息页的其他人查过之后，
    后写的分路会把先写的顶掉 —— 实测 48 条缓存里 18 条是队友的分路。
    """
    return f'{puuid}|{gameId}'


def _load() -> dict:
    global _cache

    if _cache is not None:
        return _cache

    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 版本对不上一律当空表: 旧判据写的错值留着就是永久错,
        # 因为 resolve() 只在「有局不在表里」时才去拉 SGP
        if data.get('version') != CACHE_SCHEMA:
            logger.info(f"positions.json 版本 {data.get('version')} -> "
                        f'{CACHE_SCHEMA}，整表作废重查', TAG)
            _cache = {}
            return _cache

        # 判据指纹对不上同样作废：值是按旧判法算出来的，留着等于永久错
        if data.get('rules') != RULES_VERSION:
            logger.info(f"positions.json 判据版本 {data.get('rules')} -> "
                        f'{RULES_VERSION}，整表作废重查', TAG)
            _cache = {}
            return _cache

        # 键统一成字符串：JSON 里 gameId 会被写成字符串，查的时候别踩类型不一致
        _cache = {str(k): v for k, v in (data.get('positions') or {}).items()}
    except FileNotFoundError:
        _cache = {}
    except Exception as e:
        # 缓存坏了不影响主流程，当空表重建
        logger.error(f'positions.json 读取失败，按空表处理: {e}', TAG)
        _cache = {}

    return _cache


def _save():
    if _cache is None:
        return

    # 长年累月每局一条，裁剪一下：dict 保序，留最后写入的那些
    while len(_cache) > MAX_ENTRIES:
        _cache.pop(next(iter(_cache)))

    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)

        # 先写临时文件再替换：写到一半崩了不会留下半个 JSON
        tmp = CACHE_PATH + '.tmp'

        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'version': CACHE_SCHEMA, 'rules': RULES_VERSION,
                       'positions': _cache}, f, ensure_ascii=False)

        os.replace(tmp, CACHE_PATH)
    except Exception as e:
        logger.error(f'positions.json 写入失败: {e}', TAG)


def cached(puuid: str, gameId) -> str:
    """读缓存：没查过返回空串，查过但官方没给返回空串（内部用哨兵记着）"""
    lane = _load().get(_key(puuid, str(gameId))) or ''

    return '' if lane == NO_POSITION else lane


# ------------------------------------------------------------------ 批量
async def _fillFromSGP(puuid: str, games: list) -> bool:
    """拉一次 SGP，把认得出的位置写进缓存

    失败不抛 —— 调用方还有算法和老字段兜底，分路不准总比整页加载失败好。
    返回值是「这次真的问过 SGP 了吗」，调用方据此重置过期计时。
    """
    if not puuid:
        logger.warning('puuid 为空，跳过分路缓存', TAG)
        return False

    count = max(len(games), 14)

    try:
        res = await connector.getSummonerGamesByPuuidViaSGP(puuid, 0, count - 1)
    except Exception as e:
        logger.error(f'SGP 位置拉取失败，退到算法兜底: {e}', TAG)
        return False

    wanted = {str(g['gameId']) for g in games}
    cache = _load()
    dirty = False

    for item in (res or {}).get('games') or []:
        raw = item.get('json') or {}
        gameId = str(raw.get('gameId'))

        if gameId not in wanted:
            continue

        mine = next((p for p in raw.get('participants') or []
                     if p.get('puuid') == puuid), None)

        if mine is None:
            # 查别人生涯时 SGP 会把「本人不在场」的对局一起返回。这种局写进缓存
            # 等于把别人的分路记到这个 puuid 名下（gameId 撞车还会顶掉真值），
            # 所以绝不落盘；但要留痕，否则分路不对时无从排查
            logger.warning(
                f'SGP 里找不到 puuid={puuid[:8]} 的参与者，跳过 {gameId}', TAG)
            continue

        lane = fromSGP(mine) or NO_POSITION
        key = _key(puuid, gameId)

        if cache.get(key) != lane:
            cache[key] = lane
            dirty = True

    if dirty:
        _save()

    return True


def _fromGames(game: dict, puuid: str) -> str:
    """从对局自带的 participants 里算一个分路（算法 + 老字段兜底）"""
    participants = game.get('participants') or []

    # 按 puuid 定位自己: SGP 和完整对局给的 participants 都是十个人,
    # 直接取 [0] 拿到的是队友, 分路会整个错位 (早期版本就栽在这)
    participant = next(
        (p for p in participants if p.get('puuid') == puuid),
        participants[0] if participants else {})

    return resolveOne(participant)


async def resolve(puuid: str, games: list) -> dict:
    """给一批对局补分路，返回 `{gameId: 'TOP' | ... | ''}`

    先读缓存，缺的一次 SGP 补齐；SGP 挂了就退到算法 + 老字段。
    非峡谷模式返回空串（本来也不显示分路）。
    """
    games = [g for g in (games or []) if g.get('gameId')]

    if not games:
        return {}

    # puuid 为空时一律走算法：写缓存会落 `|gameId` 这种脏键，
    # 而且根本分不清这些分路属于谁
    if not puuid:
        logger.warning('puuid 为空，分路只走算法兜底，不读写缓存', TAG)
        return {game['gameId']: _fromGames(game, '') for game in games}

    cache = _load()
    now = time.time()

    # 两个触发条件：有新对局没查过，或者距上次补齐超过 FILL_TTL。
    # 后者是自愈机制 —— 判据改过、表里还留着旧毒值时，不用等谁去手动删文件，
    # 刷新生涯时会被 SGP 权威值覆盖纠正
    missing = any(_key(puuid, str(g['gameId'])) not in cache for g in games)
    stale = now - _lastFill.get(puuid, 0) > FILL_TTL

    if missing or stale:
        if await _fillFromSGP(puuid, games):
            _lastFill[puuid] = time.time()

    result = {}

    for game in games:
        gameId = str(game['gameId'])
        lane = cache.get(_key(puuid, gameId))

        # 官方明确说这局没有位置（大乱斗 / 斗魂）→ 保持空。
        # 这时候再让算法去猜「惩戒=打野」只会凭空造一个错分路
        if lane == NO_POSITION:
            result[game['gameId']] = ''
            continue

        result[game['gameId']] = lane or (_fromGames(game, puuid) or '')

    return result
