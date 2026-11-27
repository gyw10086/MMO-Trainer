"""Live Client Data API (127.0.0.1:2999)

游戏进程自己在本地开的一个 HTTPS 服务, 只要"正在打"或"正在观战"就有响应,
不需要 LCU 连接, 也不需要管理员权限。

关键点:

- 只能在 127.0.0.1 访问, 证书是自签的, 必须 `ssl=False`
- 观战 / 回放时 LCU 的 gameflow 读作 `None`, 但这个接口照常可用
- 所有方法失败都返回 `None` 而不是抛异常, 上层按"没有数据"处理即可
"""

import aiohttp

TAG = 'LiveClient'


class LiveClient:
    BASE = 'https://127.0.0.1:2999/liveclientdata'
    TIMEOUT = aiohttp.ClientTimeout(total=2.5, connect=1.5)

    def __init__(self):
        self.__session: aiohttp.ClientSession = None

    async def __get(self, path: str):
        if self.__session is None or self.__session.closed:
            self.__session = aiohttp.ClientSession(timeout=self.TIMEOUT)

        try:
            # proxy=None: 用户的系统代理经常把 127.0.0.1 也劫持走
            async with self.__session.get(f"{self.BASE}{path}", ssl=False,
                                          proxy=None) as res:
                if res.status != 200:
                    return None

                return await res.json(content_type=None)
        except Exception:
            return None

    async def isAvailable(self) -> bool:
        return await self.__get('/gamestats') is not None

    async def getGameStats(self):
        """对局基础信息: gameTime / gameMode / mapName / gameVersion"""
        return await self.__get('/gamestats')

    async def getPlayerList(self):
        """10 名玩家的列表, 每项含 team / championName / scores / items"""
        data = await self.__get('/playerlist')

        return data if isinstance(data, list) else None

    async def getActivePlayer(self):
        """当前玩家 (观战时是观察对象) 的详细信息"""
        return await self.__get('/activeplayer')

    async def getAllGameData(self):
        """一次性拿到全部数据 (含 events, 后续做野怪计时用)"""
        return await self.__get('/allgamedata')

    async def getEventData(self):
        data = await self.__get('/eventdata')

        if not data:
            return None

        return data.get('Events') or []

    async def close(self):
        if self.__session and not self.__session.closed:
            await self.__session.close()

        self.__session = None


liveClient = LiveClient()
