"""第三方商品图片的异步加载服务。"""

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from config.settings import REQUEST_TIMEOUT


logger = logging.getLogger(__name__)


class ImageService:
    """使用独立无认证客户端加载商品图片，并维护运行期内存缓存。"""

    _MAX_CONCURRENT_REQUESTS = 4

    def __init__(self) -> None:
        # 图片可能来自 Amazon 或 CDN，不能复用携带 SellerSprite Cookie 的客户端。
        self._client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        self._semaphore = asyncio.Semaphore(
            self._MAX_CONCURRENT_REQUESTS
        )
        self._cache: dict[str, bytes] = {}
        self._inflight: dict[str, asyncio.Task[bytes | None]] = {}

    async def get_image(self, image_url: str) -> bytes | None:
        """获取图片字节；相同 URL 在运行期内只会执行一次下载。"""

        cached_image = self._cache.get(image_url)
        if cached_image is not None:
            return cached_image

        task = self._inflight.get(image_url)
        if task is None:
            task = asyncio.create_task(
                self._download_image(image_url)
            )
            self._inflight[image_url] = task

        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._inflight.get(image_url) is task:
                self._inflight.pop(image_url, None)

    async def aclose(self) -> None:
        """在应用退出阶段关闭无认证图片客户端。"""

        await self._client.aclose()
        self._cache.clear()
        self._inflight.clear()

    async def _download_image(self, image_url: str) -> bytes | None:
        """在受控并发范围内下载单张图片，失败时只返回空结果。"""

        try:
            async with self._semaphore:
                response = await self._client.get(image_url)
                response.raise_for_status()

            image_bytes = response.content
            if not image_bytes:
                logger.warning(
                    "商品图片为空：domain=%s",
                    urlparse(image_url).netloc,
                )
                return None

            self._cache[image_url] = image_bytes
            return image_bytes

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "商品图片请求失败：domain=%s status=%s",
                urlparse(image_url).netloc,
                exc.response.status_code,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "商品图片请求异常：domain=%s type=%s",
                urlparse(image_url).netloc,
                type(exc).__name__,
            )

        return None
