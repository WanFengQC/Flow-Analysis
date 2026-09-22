# infrastructure/application_runtime.py

import threading
from concurrent.futures import Future
from enum import Enum, auto

from config.database_settings import DatabaseSettings
from infrastructure.async_runtime import AsyncRuntime
from repositories.database import DatabaseManager
from services.image_service import ImageService


class RuntimeState(Enum):
    """应用基础设施运行状态。"""

    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    FAILED = auto()


class ApplicationRuntime:
    """
    Flow Analysis 基础设施运行时协调器。

    负责统一管理：
    1. asyncio 后台运行环境；
    2. PostgreSQL 异步连接池；
    3. 基础设施的启动与关闭顺序。

    此类不负责任何 UI 或业务逻辑。
    """

    def __init__(
        self,
        database_settings: DatabaseSettings,
    ) -> None:
        # 长期运行的 asyncio 后台线程。
        self.async_runtime = AsyncRuntime()

        # PostgreSQL 数据库管理器。
        self.database = DatabaseManager(
            database_settings
        )

        # 商品图片使用独立、无认证的异步 HTTP Client。
        # 由应用运行时统一持有并在退出阶段关闭。
        self.image_service = ImageService()

        # 当前基础设施运行状态。
        self._state = RuntimeState.STOPPED

        # 保存启动任务，避免重复启动。
        self._startup_future: Future | None = None

        # 生命周期状态可能被不同线程访问，
        # 因此使用线程锁保护。
        self._state_lock = threading.Lock()

    @property
    def state(self) -> RuntimeState:
        """返回当前运行状态。"""

        with self._state_lock:
            return self._state

    def start(self) -> Future:
        """
        异步启动应用基础设施。

        此方法本身不会等待 PostgreSQL 完成初始化，
        而是立即返回一个 Future。

        调用方可以通过 Future 的完成回调获取启动结果，
        但 Qt 主线程禁止调用 future.result() 阻塞等待。
        """

        with self._state_lock:
            if self._state == RuntimeState.RUNNING:
                raise RuntimeError(
                    "ApplicationRuntime 已经启动"
                )

            if self._state == RuntimeState.STARTING:
                raise RuntimeError(
                    "ApplicationRuntime 正在启动"
                )

            if self._state == RuntimeState.STOPPING:
                raise RuntimeError(
                    "ApplicationRuntime 正在关闭"
                )

            self._state = RuntimeState.STARTING

        try:
            # 首先启动独立的 asyncio EventLoop。
            self.async_runtime.start()

            # PostgreSQL 连接池必须在这个 EventLoop 中创建，
            # 因此将真正的异步启动流程提交到后台 Runtime。
            future = self.async_runtime.submit(
                self._startup_async()
            )

            self._startup_future = future

            # 启动完成后更新 RuntimeState。
            future.add_done_callback(
                self._on_startup_done
            )

            return future

        except Exception:
            # 如果连异步运行环境都启动失败，
            # 立即将状态标记为 FAILED。
            with self._state_lock:
                self._state = RuntimeState.FAILED

            raise

    async def _startup_async(self) -> None:
        """
        真正运行在 asyncio 后台线程中的启动流程。
        """

        try:
            # 创建并打开 PostgreSQL 异步连接池。
            await self.database.open()

            # 启动完成后立即进行一次真实数据库健康检查。
            healthy = await self.database.health_check()

            if not healthy:
                raise RuntimeError(
                    "PostgreSQL 健康检查失败"
                )

        except Exception:
            # 如果连接池已经部分初始化，
            # 启动失败时也尽量释放数据库资源。
            await self.database.close()

            # 保留原始异常给上层处理。
            raise

    def _on_startup_done(
        self,
        future: Future,
    ) -> None:
        """
        基础设施启动任务完成后的内部回调。

        注意：
        此回调不是 Qt UI 回调，
        不允许在这里直接操作任何 QWidget。
        """

        with self._state_lock:
            # 如果程序此时已经进入关闭流程，
            # 不再把状态重新改成 RUNNING。
            if self._state != RuntimeState.STARTING:
                return

            if future.cancelled():
                self._state = RuntimeState.FAILED
                return

            if future.exception() is not None:
                self._state = RuntimeState.FAILED
                return

            self._state = RuntimeState.RUNNING

    def shutdown(
        self,
        timeout: float = 10.0,
    ) -> None:
        """
        按正确顺序关闭所有基础设施。

        此方法允许阻塞，因此只能用于程序退出阶段，
        不应用于正常 UI 操作。
        """

        with self._state_lock:
            if self._state == RuntimeState.STOPPED:
                return

            if self._state == RuntimeState.STOPPING:
                return

            self._state = RuntimeState.STOPPING

        try:
            # PostgreSQL 连接池和图片 HTTP Client 都运行在 asyncio EventLoop 中，
            # 因此必须先通过 AsyncRuntime 提交关闭任务。
            close_future = self.async_runtime.submit(
                self._shutdown_async_resources()
            )

            # 程序已经处于退出阶段，
            # 此时等待数据库资源释放是合理的。
            close_future.result(
                timeout=timeout
            )

        finally:
            # 必须先关闭数据库连接池，
            # 再停止 asyncio EventLoop。
            #
            # 顺序不能反过来，否则连接池可能无法正常执行
            # 自己的异步关闭逻辑。
            self.async_runtime.stop()

            with self._state_lock:
                self._state = RuntimeState.STOPPED

    async def _shutdown_async_resources(self) -> None:
        """在停止 EventLoop 前关闭全部异步基础设施资源。"""

        # 图片客户端不携带 SellerSprite 认证信息，但同样必须在 EventLoop 中关闭。
        await self.image_service.aclose()
        await self.database.close()
