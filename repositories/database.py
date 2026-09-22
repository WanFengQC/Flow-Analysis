from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from config.database_settings import DatabaseSettings


class DatabaseManager:
    """负责 PostgreSQL 异步连接池的生命周期管理。"""

    def __init__(self, settings: DatabaseSettings):
        """
        初始化数据库管理器。

        此时只保存配置，不立即连接 PostgreSQL。
        真正的连接池会在 open() 中创建。
        """
        self._settings = settings

        # 连接池启动前为 None。
        # 这样可以明确区分“数据库管理器已创建”和“连接池已启动”。
        self._pool: AsyncConnectionPool | None = None

    async def open(self) -> None:
        """创建并启动 PostgreSQL 异步连接池。"""

        # 防止重复启动连接池。
        if self._pool is not None:
            return

        # 启动前再次检查配置。
        self._settings.validate()

        pool = AsyncConnectionPool(
            # 数据库基础连接参数。
            kwargs={
                "host": self._settings.host,
                "port": self._settings.port,
                "dbname": self._settings.database,
                "user": self._settings.user,
                "password": self._settings.password,
                "sslmode": self._settings.sslmode,

                # 建立 TCP / PostgreSQL 连接时的超时时间。
                "connect_timeout": self._settings.connect_timeout,

                # PostgreSQL 会话级 SQL 执行超时。
                "options": (
                    "-c statement_timeout="
                    f"{self._settings.statement_timeout_ms}"
                ),

                # 查询结果默认返回类似字典的 Row。
                "row_factory": dict_row,
            },

            # 连接池容量。
            min_size=self._settings.pool_min_size,
            max_size=self._settings.pool_max_size,

            # 从连接池获取连接最多等待多久。
            timeout=self._settings.pool_timeout,

            # 等待连接的任务数量上限。
            max_waiting=self._settings.pool_max_waiting,

            # 连接生命周期管理。
            max_lifetime=self._settings.pool_max_lifetime,
            max_idle=self._settings.pool_max_idle,

            # PostgreSQL 暂时不可用时的重连窗口。
            reconnect_timeout=self._settings.pool_reconnect_timeout,

            # 给连接池命名，后续日志和监控更容易识别。
            name="flow-analysis-postgresql",

            # 明确禁止构造对象时自动联网。
            open=False,
        )

        # 显式启动连接池，并等待 min_size 个连接准备完成。
        try:
            await pool.open(
                wait=True,
                timeout=self._settings.pool_timeout,
            )
        except Exception:
            await pool.close()
            raise

        # 只有启动成功以后才保存到实例属性。
        self._pool = pool

    async def close(self) -> None:
        """安全关闭 PostgreSQL 连接池。"""

        pool = self._pool

        if pool is None:
            return

        # 先清空引用，阻止后续代码继续取得这个连接池。
        self._pool = None

        # 等待连接池完成关闭。
        await pool.close()

    @asynccontextmanager
    async def connection(
        self,
    ) -> AsyncIterator[AsyncConnection]:
        """
        从连接池中获取一个数据库连接。

        使用完成后自动把连接归还给连接池。
        """

        pool = self._require_pool()

        async with pool.connection(
            timeout=self._settings.pool_timeout,
        ) as connection:
            yield connection

    async def health_check(self) -> bool:
        """
        执行最轻量的数据库健康检查。

        如果数据库不可用，让 psycopg 异常继续向上抛出，
        由上层统一决定如何记录日志和更新 UI 状态。
        """

        async with self.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()

        return row is not None

    def _require_pool(self) -> AsyncConnectionPool:
        """
        获取已启动的连接池。

        如果数据库尚未初始化，立即报错，
        避免出现难以定位的 NoneType 错误。
        """

        if self._pool is None:
            raise RuntimeError(
                "PostgreSQL 连接池尚未启动"
            )

        return self._pool