"""SellerSprite reversing 预热与重试顺序测试。"""

import asyncio
import unittest
from typing import Any

from services.crawler_service import (
    CrawlerService,
    ReversingDataSourcePermissionError,
    ReversingPreparationRetriesExhaustedError,
)
from services.api_service import SellerSpriteDataSourcePermissionError


class _ReversingApiStub:
    """记录预热与 reversing 调用，不访问真实 SellerSprite。"""

    def __init__(self, responses: list[dict[str, Any]], events: list[str]) -> None:
        self._responses = list(responses)
        self.events = events
        self.prewarm_count = 0
        self.reversing_count = 0

    async def prewarm_relation_monthly(self, **_kwargs: Any) -> dict[str, str]:
        self.prewarm_count += 1
        self.events.append("prewarm")
        return {"status": "accepted"}

    async def get_relation_reversing(self, **_kwargs: Any) -> dict[str, Any]:
        self.reversing_count += 1
        self.events.append("reversing")
        return self._responses.pop(0)


class CrawlerServicePrewarmRetryTest(unittest.IsolatedAsyncioTestCase):
    """确认每次 reversing 尝试都先执行预热并等待异步缓存就绪。"""

    async def test_retry_reprewarms_before_next_reversing_request(self) -> None:
        """空响应重试必须是预热、等待、请求的完整新一轮。"""

        events: list[str] = []
        api_service = _ReversingApiStub(
            [
                {"total": 0, "items": []},
                {"total": 1, "items": [{"keywords": "weighted plush"}]},
            ],
            events,
        )

        async def record_sleep(_seconds: float) -> None:
            events.append("sleep")

        service = CrawlerService(api_service, sleep=record_sleep)  # type: ignore[arg-type]
        result = await service.get_relation_reversing_for_asin_month(
            asin="B0TEST",
            month="202503",
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(api_service.prewarm_count, 2)
        self.assertEqual(api_service.reversing_count, 2)
        self.assertEqual(
            events,
            [
                "prewarm",
                "sleep",
                "reversing",
                "sleep",
                "prewarm",
                "sleep",
                "reversing",
            ],
        )

    async def test_every_automatic_retry_starts_with_prewarm(self) -> None:
        """四轮均失败时，不能退化成只重发 reversing 请求。"""

        events: list[str] = []
        api_service = _ReversingApiStub(
            [{"total": 0, "items": []}] * 4,
            events,
        )

        async def record_sleep(_seconds: float) -> None:
            events.append("sleep")

        service = CrawlerService(api_service, sleep=record_sleep)  # type: ignore[arg-type]
        with self.assertRaises(ReversingPreparationRetriesExhaustedError) as ctx:
            await service.get_relation_reversing_for_asin_month(
                asin="B0TEST",
                month="202503",
            )

        self.assertEqual(ctx.exception.attempts, 4)
        self.assertEqual(api_service.prewarm_count, 4)
        self.assertEqual(api_service.reversing_count, 4)
        self.assertEqual(
            events,
            [
                "prewarm",
                "sleep",
                "reversing",
                "sleep",
                "prewarm",
                "sleep",
                "reversing",
                "sleep",
                "prewarm",
                "sleep",
                "reversing",
                "sleep",
                "prewarm",
                "sleep",
                "reversing",
            ],
        )

    async def test_cancelling_prewarm_settle_wait_does_not_send_reversing(self) -> None:
        """用户取消时，等待中的任务必须取消，不能再继续发 reversing。"""

        events: list[str] = []
        api_service = _ReversingApiStub(
            [{"total": 1, "items": [{"keywords": "weighted plush"}]}],
            events,
        )
        wait_started = asyncio.Event()

        async def wait_until_cancelled(_seconds: float) -> None:
            events.append("sleep")
            wait_started.set()
            await asyncio.Event().wait()

        service = CrawlerService(
            api_service,
            sleep=wait_until_cancelled,  # type: ignore[arg-type]
        )
        task = asyncio.create_task(
            service.get_relation_reversing_for_asin_month(
                asin="B0TEST",
                month="202503",
            )
        )
        await wait_started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(events, ["prewarm", "sleep"])
        self.assertEqual(api_service.reversing_count, 0)

    async def test_permission_denial_refreshes_once_then_reprewarms(self) -> None:
        """数据源权限拒绝时，刷新 Cookie 后必须从预热重新开始。"""

        events: list[str] = []

        class PermissionThenSuccessApiStub(_ReversingApiStub):
            async def prewarm_relation_monthly(self, **_kwargs: Any) -> dict[str, str]:
                self.prewarm_count += 1
                self.events.append("prewarm")
                if self.prewarm_count == 1:
                    raise SellerSpriteDataSourcePermissionError("no permission")
                return {"status": "accepted"}

        api_service = PermissionThenSuccessApiStub(
            [{"total": 1, "items": [{"keywords": "weighted plush"}]}],
            events,
        )

        async def record_sleep(_seconds: float) -> None:
            events.append("sleep")

        async def refresh_cookie() -> None:
            events.append("refresh_cookie")

        service = CrawlerService(
            api_service,  # type: ignore[arg-type]
            sleep=record_sleep,
            refresh_cookie=refresh_cookie,
        )
        result = await service.get_relation_reversing_for_asin_month(
            asin="B0TEST",
            month="202503",
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(
            events,
            ["prewarm", "refresh_cookie", "prewarm", "sleep", "reversing"],
        )

    async def test_permission_denial_after_refresh_is_not_blindly_retried(self) -> None:
        """同一次任务中刷新 Cookie 后仍无权限时必须立即停止。"""

        events: list[str] = []

        class AlwaysPermissionDeniedApiStub(_ReversingApiStub):
            async def prewarm_relation_monthly(self, **_kwargs: Any) -> dict[str, str]:
                self.prewarm_count += 1
                self.events.append("prewarm")
                raise SellerSpriteDataSourcePermissionError("no permission")

        api_service = AlwaysPermissionDeniedApiStub([], events)

        async def refresh_cookie() -> None:
            events.append("refresh_cookie")

        service = CrawlerService(
            api_service,  # type: ignore[arg-type]
            refresh_cookie=refresh_cookie,
        )
        with self.assertRaises(ReversingDataSourcePermissionError):
            await service.get_relation_reversing_for_asin_month(
                asin="B0TEST",
                month="202503",
            )

        self.assertEqual(events, ["prewarm", "refresh_cookie", "prewarm"])
        self.assertEqual(api_service.reversing_count, 0)
