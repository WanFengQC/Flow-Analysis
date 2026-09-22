from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row
from psycopg_pool import (
    AsyncConnectionPool,
    PoolClosed,
    PoolTimeout,
    TooManyRequests,
)

from config.database_settings import DatabaseSettings
from repositories.exceptions import (
    DatabaseAuthenticationError,
    DatabaseConnectionError,
    DatabasePoolBusyError,
    DatabasePoolTimeoutError,
    DatabaseQueryError,
    DatabaseQueryTimeoutError,
    DatabaseUnavailableError,
)

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
        从连接池安全获取一个 PostgreSQL 连接。

        此处同时作为数据库异常边界：
        将 psycopg / psycopg_pool 的底层异常，
        转换为 Flow Analysis 自己的数据库异常。
        """

        pool = self._require_pool()

        try:
            async with pool.connection(
                    timeout=self._settings.pool_timeout,
            ) as connection:
                # Repository 会在 yield 后使用该连接执行 SQL。
                #
                # 如果 Repository 内部执行 SQL 时发生异常，
                # 异常同样会重新回到当前 try 中，
                # 因此可以在这里统一转换。
                yield connection

        except TooManyRequests as exc:
            # 等待连接的任务已经达到 max_waiting，
            # 为防止请求无限堆积，连接池主动拒绝新请求。
            raise DatabasePoolBusyError(
                "数据库请求过多，连接池等待队列已满"
            ) from exc

        except PoolTimeout as exc:
            # 已经等待了一段时间，
            # 但仍然没有获得可用连接。
            raise DatabasePoolTimeoutError(
                "等待数据库连接超时"
            ) from exc

        except PoolClosed as exc:
            # 正常运行过程中不应该出现。
            # 通常意味着生命周期管理存在问题，
            # 或程序已经进入关闭阶段。
            raise DatabaseUnavailableError(
                "数据库连接池已经关闭"
            ) from exc

        except errors.InvalidPassword as exc:
            # PostgreSQL SQLSTATE 28P01：
            # 用户名或密码认证失败。
            raise DatabaseAuthenticationError(
                "PostgreSQL 用户名或密码错误"
            ) from exc

        except errors.QueryCanceled as exc:
            # 当前项目已经配置 statement_timeout，
            # 因此 SQL 超时会由 PostgreSQL 主动取消查询。
            #
            # 目前尚未实现用户主动取消 SQL，
            # 所以先统一解释为查询超时。
            raise DatabaseQueryTimeoutError(
                "SQL 执行超时"
            ) from exc

        except (
                errors.ConnectionTimeout,
                psycopg.InterfaceError,
        ) as exc:
            # 建立连接超时，或者客户端连接对象已经不可用。
            raise DatabaseConnectionError(
                "PostgreSQL 连接异常"
            ) from exc

        except psycopg.OperationalError as exc:
            # OperationalError 范围较广，例如：
            # - 网络中断
            # - PostgreSQL 关闭
            # - 连接突然断开
            # - 资源不足
            #
            # 更具体的异常已经在上面优先处理。
            raise DatabaseUnavailableError(
                "PostgreSQL 当前不可用"
            ) from exc

        except psycopg.DatabaseError as exc:
            # 兜底处理其它 PostgreSQL 数据库异常。
            #
            # ProgrammingError、IntegrityError 等后续随着
            # Repository 业务逐步细分。
            raise DatabaseQueryError(
                "数据库操作执行失败"
            ) from exc

    @asynccontextmanager
    async def transaction(
            self,
    ) -> AsyncIterator[AsyncConnection]:
        """
        在同一个 PostgreSQL 连接中执行由 Service 划定边界的事务。

        Repository 只能使用 yield 出去的 connection 执行 SQL，
        不自行决定提交或回滚。正常退出时自动提交；任何异常
        （包括任务取消）都会先回滚，再将原始异常交给上层处理。
        """

        # connection() 内部使用 psycopg_pool 的连接上下文：
        # 正常退出时由 psycopg 自动 COMMIT，异常退出时自动 ROLLBACK。
        # 复用它可避免手动提交后连接池退出阶段再次提交，并继续保留
        # 现有连接池、异常转换和生命周期逻辑。
        async with self.connection() as connection:
            # 同一个 connection 会被 Service 传递给多个 Repository。
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
