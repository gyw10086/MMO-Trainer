"""玩家标签与遇见记录 (本地库)

只存在本机 `%APPDATA%\\Seraphine\\players.json`, 不上传任何数据。

记录什么:

- 每局开始时, 把双方 10 人的 puuid 记一笔 (`ally` / `enemy` 各一次计数)
- 用户自己打的标签与备注

用来回答两个实战问题: "这人是不是又排到我了"、"上把是不是坑过我"。
数据量很小 (每人一条, 只保留最近 5 次), JSON 足够, 不引入 sqlite。
"""

import json
import os
import time

from app.common.config import LOCAL_PATH
from app.common.logger import logger

TAG = 'PlayerDB'

# 快捷标签; 顺序即展示顺序
TAG_PRESETS = ['好友', '大神', '谨慎', '拉黑']

TAG_TONES = {
    '好友': 'win',
    '大神': 'gold',
    '谨慎': 'brand',
    '拉黑': 'lose',
}

MAX_RECENT = 5


class PlayerDB:

    def __init__(self, path=None):
        self.path = path or f"{LOCAL_PATH}/players.json"
        self.players = {}

        self.load()

    # ------------------------------------------------------------------ 读写

    def load(self):
        if not os.path.exists(self.path):
            return

        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, dict) and isinstance(data.get('players'), dict):
                self.players = data['players']
        except Exception as e:
            logger.error(f"Load players.json failed: {e}", TAG)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)

            tmp = f"{self.path}.tmp"

            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'version': 1, 'players': self.players}, f,
                          ensure_ascii=False, indent=1)

            # 原子替换, 避免写一半断电把库写坏
            os.replace(tmp, self.path)
        except Exception as e:
            logger.error(f"Save players.json failed: {e}", TAG)

    # ------------------------------------------------------------------ 查询

    def entry(self, puuid: str):
        return self.players.get(puuid)

    def summary(self, puuid: str) -> dict:
        entry = self.players.get(puuid) or {}

        ally = entry.get('ally', 0)
        enemy = entry.get('enemy', 0)

        return {
            'tags': entry.get('tags') or [],
            'note': entry.get('note') or '',
            'ally': ally,
            'enemy': enemy,
            'total': ally + enemy,
            'lastSeen': entry.get('lastSeen') or 0,
            'name': entry.get('name') or '',
            'tag': entry.get('tag') or '',
        }

    # ------------------------------------------------------------------ 写入

    def __ensure(self, puuid, name='', tagLine='') -> dict:
        entry = self.players.get(puuid)

        if entry is None:
            entry = {
                'name': name,
                'tag': tagLine,
                'tags': [],
                'note': '',
                'ally': 0,
                'enemy': 0,
                'firstSeen': time.time(),
                'lastSeen': 0,
                'recent': [],
                'lastGameId': None,
            }
            self.players[puuid] = entry
        else:
            # 名字会改, 以最近一次看到的为准
            if name:
                entry['name'] = name
            if tagLine:
                entry['tag'] = tagLine

        return entry

    def recordTeam(self, team, side: str, gameId=None):
        """记录一队人的遇见

        @param side: 'ally' / 'enemy'
        @param gameId: 同一局重复调用时用来去重
        """
        now = time.time()
        changed = False

        for player in team or []:
            puuid = player.get('puuid')

            if not puuid or puuid == '00000000-0000-0000-0000-000000000000':
                continue

            name = (player.get('gameName') or player.get('summonerName')
                    or player.get('name') or '')
            tagLine = player.get('tagLine') or ''

            entry = self.__ensure(puuid, name, tagLine)

            if gameId and entry.get('lastGameId') == gameId:
                continue

            if gameId:
                entry['lastGameId'] = gameId

            entry[side] = entry.get(side, 0) + 1
            entry['lastSeen'] = now

            recent = entry.get('recent') or []
            recent.insert(0, {
                'ts': int(now),
                'side': side,
                'championId': player.get('championId') or 0,
            })
            entry['recent'] = recent[:MAX_RECENT]

            changed = True

        if changed:
            self.save()

    def setNote(self, puuid: str, note: str):
        if not puuid:
            return

        entry = self.__ensure(puuid)
        entry['note'] = (note or '').strip()[:120]
        self.save()

    def toggleTag(self, puuid: str, tag: str) -> bool:
        """加/删一个标签, 返回操作后是否存在"""
        if not puuid or not tag:
            return False

        entry = self.__ensure(puuid)
        tags = entry.setdefault('tags', [])

        if tag in tags:
            tags.remove(tag)
            exists = False
        else:
            tags.append(tag)
            exists = True

        self.save()

        return exists


playerDB = PlayerDB()
