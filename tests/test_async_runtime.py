"""AsyncRuntime 退出时对长时间异步 I/O 的取消回归测试。"""

import asyncio
import threading
import unittest

from infrastructure.async_runtime import AsyncRuntime


class AsyncRuntimeTest(unittest.TestCase):
    """确认 Runtime 停止会取消仍在等待响应的协程，而不是强杀线程。"""

    def test_stop_cancels_long_running_coroutine(self) -> None:
        """应用退出时，未返回的异步请求必须收到正常 CancelledError。"""

        started = threading.Event()
        cancelled = threading.Event()

        async def wait_for_response() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        runtime = AsyncRuntime()
        runtime.start()
        try:
            runtime.submit(wait_for_response())
            self.assertTrue(started.wait(timeout=1.0))
        finally:
            runtime.stop()

        self.assertTrue(cancelled.wait(timeout=1.0))
