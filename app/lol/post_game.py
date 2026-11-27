# coding:utf-8
"""结算阶段的自动化: 自动点赞 + 自动回房间

两件事都在结算页, 但时机不同, 所以拆成两个独立任务:

- `PreEndOfGame` —— 点赞名单 (ballot) 只有这时候才拿得到, 此时点赞;
  顺便把 `missions-celebration` 序列播完, 否则结算页会卡在任务庆祝动画上
- `EndOfGame` —— 这才是"可以回大厅"的时刻, 延迟 N 秒后调 play-again

两条流程互不阻塞: 点赞没点完也可以先回房间, 所以用两个 task 槽位,
`onEndOfGame` 不会取消还在跑的点赞任务。

所有请求都是静默的 (失败返回 None/False), 因为"没有待点赞对局"、
"自定义房间不能再来一局"都属于正常情况, 不该弹提示打扰用户。
"""

import asyncio
import random

from app.common.config import cfg
from app.common.logger import logger
from app.lol.connector import connector

TAG = 'PostGame'

# 点赞类型: 中文文案 -> LCU 枚举值
HONOR_CATEGORIES = {
    '心态良好': 'HEART',
    '指挥有方': 'SHOTCALLER',
    '操作亮眼': 'COOL',
}

# 刚进 PreEndOfGame 时 ballot 可能还没生成, 轮询几次再放弃
BALLOT_RETRY = 5
BALLOT_RETRY_INTERVAL = 1.5

# 结算页上的序列动画, 不播完会挡住回房间
PRE_END_SEQUENCES = ('missions-celebration',)


class PostGameAutomation:
    """挂在 gameflow 状态机上的结算后动作

    使用方只需要在对应阶段调用 `onPreEndOfGame` / `onEndOfGame`,
    离开结算 (回到大厅、开下一局) 时调用 `abort()`。
    """

    def __init__(self):
        self.honorTask = None
        self.playAgainTask = None

        # 同一个 gameId 不重复点赞 (重连会再次触发 PreEndOfGame)
        self.honoredGameId = None

        # 观战不点赞也不回房间
        self.spectating = False

    # ------------------------------------------------------------------ 阶段入口

    def onPreEndOfGame(self):
        # 已经在点了就别重来, 否则会把点了一半的任务掐掉 (WSS 会重复推同一阶段)
        if self.honorTask and not self.honorTask.done():
            return

        if not cfg.get(cfg.enableAutoHonor):
            return

        self.honorTask = asyncio.create_task(self.__honor())

    def onEndOfGame(self):
        if self.playAgainTask and not self.playAgainTask.done():
            return

        if self.spectating or not cfg.get(cfg.enableAutoPlayAgain):
            return

        self.playAgainTask = asyncio.create_task(self.__playAgain())

    def abort(self):
        """离开结算阶段 / 开始新一局时调用"""
        self.__cancel('honorTask')
        self.__cancel('playAgainTask')

    def __cancel(self, name):
        task = getattr(self, name, None)

        if task and not task.done():
            task.cancel()

        setattr(self, name, None)

    # ------------------------------------------------------------------ 点赞

    async def __honor(self):
        try:
            ballot = await self.__waitBallot()

            if not ballot:
                logger.info('No honor ballot, skip', TAG)
                return

            gameId = ballot.get('gameId')

            if gameId and gameId == self.honoredGameId:
                return

            allies = self.__playable(ballot.get('eligibleAllies'))
            opponents = self.__playable(ballot.get('eligibleOpponents'))

            # 已经点过的人不再点 (重连后 ballot 会带上已投的票)
            honored = self.__honoredPuuids(ballot.get('honoredPlayers'))
            allies = [p for p in allies if p.get('puuid') not in honored]
            opponents = [p for p in opponents if p.get('puuid') not in honored]

            votes = self.__voteCount(ballot, allies, opponents)

            if votes <= 0:
                return

            # 队友优先: 一局通常只有 1 票, 队友比对手更值得
            targets = (allies + opponents)[:votes]

            if not targets:
                return

            category = self.__pickCategory()

            # 串行发送: LCU 并发额度很低, 并行反而会互相排队并报 429
            succeeded = 0

            for player in targets:
                if await connector.honorPlayer(player.get('puuid'),
                                              player.get('summonerId'),
                                              gameId, category):
                    succeeded += 1

            self.honoredGameId = gameId

            logger.info(f'Honored {succeeded}/{len(targets)} players '
                        f'with {category}', TAG)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f'Auto honor failed: {e}', TAG)

    async def __waitBallot(self):
        for i in range(BALLOT_RETRY):
            ballot = await connector.getHonorBallot()

            if ballot is not None:
                return ballot

            if i + 1 < BALLOT_RETRY:
                await asyncio.sleep(BALLOT_RETRY_INTERVAL)

        return None

    @staticmethod
    def __playable(players):
        """过滤电脑玩家和缺 puuid 的条目"""
        result = []

        for player in (players or []):
            if not isinstance(player, dict) or player.get('botPlayer'):
                continue

            if not player.get('puuid'):
                continue

            result.append(player)

        return result

    @staticmethod
    def __honoredPuuids(players):
        values = set()

        for player in (players or []):
            if isinstance(player, dict):
                values.add(player.get('puuid'))
            elif isinstance(player, str):
                values.add(player)

        values.discard(None)

        return values

    @staticmethod
    def __voteCount(ballot, allies, opponents):
        votes = (ballot.get('votePool') or {}).get('votes')

        try:
            votes = int(votes)
        except (TypeError, ValueError):
            votes = 0

        # 客户端没给票数时按 1 票处理, 但没有人可点就投 0 票
        if votes <= 0:
            votes = 1 if (allies or opponents) else 0

        return votes

    @staticmethod
    def __pickCategory():
        name = cfg.get(cfg.autoHonorCategory)

        if name in HONOR_CATEGORIES:
            return HONOR_CATEGORIES[name]

        return random.choice(list(HONOR_CATEGORIES.values()))

    # ------------------------------------------------------------------ 回房间

    async def __playAgain(self):
        try:
            delay = cfg.get(cfg.autoPlayAgainDelay)

            if delay and delay > 0:
                await asyncio.sleep(delay)

            # 先把结算页上的任务庆祝序列播完, 否则会卡在动画上
            for sequence in PRE_END_SEQUENCES:
                await connector.completePreEndOfGame(sequence)

            if await connector.playAgainQuiet():
                logger.info('Returned to lobby', TAG)
            else:
                logger.info('play-again rejected by client, skip', TAG)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f'Auto play again failed: {e}', TAG)
