import asyncio
import selectors
import threading
from concurrent.futures import Future
from typing import Coroutine, Any


class AsyncRuntime:
    """
    Flow Analysis 的异步 I/O 运行环境。

    在独立后台线程中长期运行一个 asyncio EventLoop，
    PostgreSQL 异步连接池以及其他异步 I/O 任务都运行在这里。
    """

    def __init__(self) -> None:
        # asyncio 事件循环。
        # start() 成功以后才会被赋值。
        self._loop: asyncio.AbstractEventLoop | None = None

        # 承载 asyncio EventLoop 的后台线程。
        self._thread: threading.Thread | None = None

        # 用于通知主线程：
        # “后台 EventLoop 已经创建完成，可以提交任务了”。
        self._ready_event = threading.Event()

        # 防止 start() / stop() 在多个地方同时执行。
        self._lifecycle_lock = threading.Lock()

    def start(self) -> None:
        """启动长期运行的异步 I/O 线程。"""

        with self._lifecycle_lock:
            # 已经运行时不重复创建线程。
            if self._thread is not None and self._thread.is_alive():
                return

            # 每次重新启动前清除旧状态。
            self._ready_event.clear()

            self._thread = threading.Thread(
                target=self._run_event_loop,
                name="FlowAnalysis-AsyncIO",
                daemon=True,
            )

            self._thread.start()

        # 等待后台线程完成 EventLoop 初始化。
        #
        # 这里加入超时，避免因为后台初始化异常导致主程序永久卡死。
        if not self._ready_event.wait(timeout=5.0):
            raise RuntimeError(
                "异步 I/O Runtime 启动超时"
            )

    def _run_event_loop(self) -> None:
        """
        后台线程入口。

        创建一个独立 asyncio EventLoop，
        并让它持续运行直到 stop() 被调用。
        """

        # Psycopg 的异步模式在 Windows 下不能运行在 ProactorEventLoop。
        # 因此为后台 I/O Runtime 显式创建 SelectorEventLoop。
        #
        # 只影响 Flow Analysis 自己的异步后台线程，
        # 不修改整个 Python 进程的全局 EventLoop 策略。
        loop = asyncio.SelectorEventLoop(
            selectors.SelectSelector()
        )

        try:
            # 把这个 EventLoop 设置为当前后台线程的事件循环。
            asyncio.set_event_loop(loop)

            self._loop = loop

            # 通知 start()：
            # EventLoop 已经准备完成。
            self._ready_event.set()

            # 长期运行，等待其他线程提交协程。
            loop.run_forever()

        finally:
            # EventLoop 停止以后，清理尚未结束的异步任务。
            pending_tasks = asyncio.all_tasks(loop)

            for task in pending_tasks:
                task.cancel()

            if pending_tasks:
                loop.run_until_complete(
                    asyncio.gather(
                        *pending_tasks,
                        return_exceptions=True,
                    )
                )

            # 清理异步生成器。
            loop.run_until_complete(
                loop.shutdown_asyncgens()
            )

            loop.close()

            self._loop = None

    def submit(
        self,
        coroutine: Coroutine[Any, Any, Any],
    ) -> Future:
        """
        把协程提交到后台 asyncio EventLoop。

        此方法可以从 Qt 主线程调用，但绝不能直接等待 Future.result()，
        否则仍然会阻塞 UI。
        """

        loop = self._require_loop()

        return asyncio.run_coroutine_threadsafe(
            coroutine,
            loop,
        )

    def stop(self) -> None:
        """停止后台 EventLoop，并等待线程安全退出。"""

        with self._lifecycle_lock:
            thread = self._thread

            if thread is None:
                return

            loop = self._loop

            if loop is not None and loop.is_running():
                # call_soon_threadsafe() 可以安全地从其他线程
                # 要求后台 EventLoop 停止。
                loop.call_soon_threadsafe(
                    loop.stop
                )

            # 等待后台线程真正结束。
            thread.join(timeout=5.0)

            if thread.is_alive():
                raise RuntimeError(
                    "异步 I/O Runtime 未能在规定时间内停止"
                )

            self._thread = None
            self._ready_event.clear()

    def _require_loop(
        self,
    ) -> asyncio.AbstractEventLoop:
        """取得已经启动的 asyncio EventLoop。"""

        loop = self._loop

        if loop is None or not loop.is_running():
            raise RuntimeError(
                "异步 I/O Runtime 尚未启动"
            )

        return loop