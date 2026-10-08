"""Repository 连接复用的基础支持。"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg import AsyncConnection

from repositories.database import DatabaseManager


class BaseRepository:
    """为具体 Repository 提供连接池借用和外部事务连接复用能力。"""

    def __init__(
        self,
        database: DatabaseManager,
        connection: AsyncConnection | None = None,
    ) -> None:
        """
        保存数据库管理器和可选的外部连接。

        Service 在跨 Repository 的事务中传入 transaction() yield 的连接；
        普通单次查询不传入连接，Repository 则通过 DatabaseManager 借用连接。
        """
        self._database = database
        self._external_connection = connection

    @asynccontextmanager
    async def connection_scope(self) -> AsyncIterator[AsyncConnection]:
        """优先复用外部事务连接，否则为当前 Repository 操作借用连接。"""
        if self._external_connection is not None:
            # 外部连接的事务边界属于 Service，Repository 不提交、不回滚、不关闭。
            yield self._external_connection
            return

        # 普通查询仍按原有连接池策略独立借还连接，无需显式事务。
        async with self._database.connection() as connection:
            yield connection
