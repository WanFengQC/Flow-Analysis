"""以 async Playwright 从 Amazon 商品页 pqv 区域获取临时 ProductContext。"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config.settings import (
    AMAZON_PLAYWRIGHT_PROFILE_DIR,
    AMAZON_ZIP_CODE,
)
from models.product_context import (
    AmazonProductContextResult,
    AmazonProductSummaryStatus,
)
from services.amazon_product_summary_parser import (
    AmazonProductSummaryParseError,
    AmazonProductSummaryParser,
)


class AmazonProductContextProvider:
    """一个 tagging generation 内复用 ASIN 结果，并在批次末关闭浏览器。"""

    _MAX_ATTEMPTS = 3
    _PAGE_TIMEOUT_MILLISECONDS = 20_000
    _AMAZON_URL_TEMPLATE = "https://www.amazon.com/dp/{asin}?th=1"

    def __init__(
        self,
        profile_dir: Path = AMAZON_PLAYWRIGHT_PROFILE_DIR,
        zip_code: str = AMAZON_ZIP_CODE,
        parser: AmazonProductSummaryParser | None = None,
    ) -> None:
        """保存稳定配置；不在构造时启动浏览器或创建额外 EventLoop。"""

        self._profile_dir = Path(profile_dir)
        self._zip_code = zip_code.strip()
        self._parser = parser or AmazonProductSummaryParser()
        self._generation_cache: dict[str, AmazonProductContextResult] = {}
        # 信号同时供 AsyncRuntime 与 Windows Proactor bridge 线程读取；只影响
        # 当前 generation，绝不污染用户之后显式启动的新任务。
        self._cancel_requested = threading.Event()
        self._bridge_stopped = threading.Event()
        self._bridge_stopped.set()

    @property
    def profile_dir(self) -> Path:
        """暴露专用 Profile 路径，便于诊断而不涉及浏览器实例。"""

        return self._profile_dir

    def clear_generation_cache(self) -> None:
        """在一轮 tagging 结束时释放 ASIN Context 引用，不写入任何持久化介质。"""

        self._generation_cache.clear()

    def begin_generation(self) -> None:
        """显式开始一轮可取消的 Amazon 背景获取。"""

        self._cancel_requested.clear()

    def cancel_current_batch(self) -> None:
        """请求停止尚未开始的 ASIN，并让浏览器流程尽快释放资源。"""

        self._cancel_requested.set()

    async def wait_for_bridge_cleanup(self, timeout: float) -> bool:
        """退出阶段等待 Windows Proactor bridge 释放浏览器资源。"""

        return await asyncio.to_thread(self._bridge_stopped.wait, timeout)

    async def fetch_contexts(
        self,
        asins: Iterable[str],
    ) -> Mapping[str, AmazonProductContextResult]:
        """在一个 Context 和一个 Page 内顺序处理本批次去重后的 ASIN。"""

        unique_asins = self._normalize_unique_asins(asins)
        self._raise_if_cancelled()
        missing_asins = [
            asin for asin in unique_asins if asin not in self._generation_cache
        ]
        if not missing_asins:
            return {
                asin: self._generation_cache[asin]
                for asin in unique_asins
            }

        # psycopg 要求的 Windows SelectorEventLoop 无法创建 Playwright Node
        # driver 所需的子进程。仍由 AsyncRuntime 调度本方法；仅把一个完整
        # Amazon 批次桥接到后台 Proactor loop，绝不在 Qt UI 线程执行，也不
        # 为每个 ASIN 单独创建浏览器、线程或 EventLoop。
        if self._requires_windows_playwright_bridge():
            self._bridge_stopped.clear()
            await asyncio.to_thread(
                self._fetch_missing_contexts_in_playwright_thread,
                missing_asins,
            )
        else:
            await self._fetch_missing_contexts(missing_asins)

        return self._results_for(unique_asins)

    def _fetch_missing_contexts_in_playwright_thread(
        self,
        missing_asins: list[str],
    ) -> None:
        """在非 Qt 工作线程的 Windows Proactor loop 中运行 async Playwright。"""

        try:
            asyncio.run(self._fetch_missing_contexts(missing_asins))
        finally:
            self._bridge_stopped.set()

    async def _fetch_missing_contexts(
        self,
        missing_asins: list[str],
    ) -> None:
        """在当前可创建子进程的 loop 内启动一次浏览器并顺序抓取缺失 ASIN。"""

        try:
            from playwright.async_api import (  # type: ignore[import-not-found]
                Error as PlaywrightError,
                TimeoutError as PlaywrightTimeoutError,
                async_playwright,
            )
        except ImportError:
            # 依赖或浏览器未安装时以明确状态返回，TaggingService 不得发送
            # 空 ProductContext 给 AI。
            self._store_uniform_failure(
                missing_asins,
                AmazonProductSummaryStatus.PAGE_LOAD_FAILED,
                "Playwright 未安装或不可用",
            )
            return

        playwright: Any | None = None
        context: Any | None = None
        try:
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            playwright = await async_playwright().start()
            # Windows 目标机通常已有 Edge；指定 msedge 可避免每台桌面端额外
            # 下载一份 Chromium，同时仍使用 Flow Analysis 自己的专用 Profile。
            context = await playwright.chromium.launch_persistent_context(
                str(self._profile_dir),
                channel="msedge",
                headless=True,
                locale="en-US",
                timezone_id="America/Los_Angeles",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            await context.add_cookies(
                [
                    {
                        "name": "lc-main",
                        "value": "en_US",
                        "domain": ".amazon.com",
                        "path": "/",
                    },
                    {
                        "name": "i18n-prefs",
                        "value": "USD",
                        "domain": ".amazon.com",
                        "path": "/",
                    },
                ]
            )
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(self._PAGE_TIMEOUT_MILLISECONDS)
            for asin in missing_asins:
                self._raise_if_cancelled()
                self._generation_cache[asin] = await self._fetch_one(
                    page,
                    asin,
                    PlaywrightTimeoutError,
                )
        except Exception as exc:
            status = (
                AmazonProductSummaryStatus.PROFILE_IN_USE
                if self._is_profile_in_use_error(exc)
                else AmazonProductSummaryStatus.PAGE_LOAD_FAILED
            )
            self._store_uniform_failure(missing_asins, status, str(exc))
        finally:
            # 持久化的是 Profile 内 Amazon 状态，而不是 Chromium 进程；每批
            # 完成后都必须释放 Context 和 Playwright，不能常驻后台浏览器。
            if context is not None:
                await context.close()
            if playwright is not None:
                await playwright.stop()

        return

    @staticmethod
    def _requires_windows_playwright_bridge() -> bool:
        """只针对 SelectorEventLoop 的 Windows 子进程限制启用兼容桥接。"""

        return (
            os.name == "nt"
            and isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
        )

    def _raise_if_cancelled(self) -> None:
        """将线程安全取消信号转换为标准 asyncio 取消语义。"""

        if self._cancel_requested.is_set():
            raise asyncio.CancelledError()

    async def _fetch_one(
        self,
        page: Any,
        asin: str,
        timeout_exception: type[Exception],
    ) -> AmazonProductContextResult:
        """顺序访问单 ASIN，有限重试可恢复页面问题而不碰 CAPTCHA。"""

        last_result: AmazonProductContextResult | None = None
        for attempt in range(self._MAX_ATTEMPTS):
            self._raise_if_cancelled()
            try:
                await page.goto(
                    self._AMAZON_URL_TEMPLATE.format(asin=asin),
                    wait_until="domcontentloaded",
                )
                # CAPTCHA 或登录页未必有 pqv-title；先检查当前源码，不能让
                # wait_for_selector 超时掩盖真实的页面状态。
                initial_html = await page.content()
                page_status = self._page_status(page.url, initial_html, asin)
                if page_status is not None:
                    return AmazonProductContextResult(
                        asin=asin,
                        status=page_status,
                        context=None,
                    )
                if self._needs_us_delivery_correction(initial_html):
                    await self._try_set_us_delivery_zip(page)
                await page.locator("#pqv-title").wait_for(
                    state="attached",
                    timeout=self._PAGE_TIMEOUT_MILLISECONDS,
                )
                html = await page.content()
                page_status = self._page_status(page.url, html, asin)
                if page_status is not None:
                    return AmazonProductContextResult(
                        asin=asin,
                        status=page_status,
                        context=None,
                    )
                context = self._parser.parse(html, asin)
                return AmazonProductContextResult(
                    asin=asin,
                    status=AmazonProductSummaryStatus.READY,
                    context=context,
                )
            except timeout_exception:
                # pqv 节点未出现时仍检查最终源码：CAPTCHA、错误 ASIN 和
                # PRODUCT_SUMMARY_NOT_FOUND 不能统统被折叠成 TIMEOUT。
                timed_out_html = await page.content()
                page_status = self._page_status(page.url, timed_out_html, asin)
                if page_status is not None:
                    return AmazonProductContextResult(
                        asin=asin,
                        status=page_status,
                        context=None,
                    )
                try:
                    context = self._parser.parse(timed_out_html, asin)
                except AmazonProductSummaryParseError as exc:
                    last_result = AmazonProductContextResult(
                        asin=asin,
                        status=exc.status,
                        context=None,
                        detail=str(exc),
                    )
                else:
                    return AmazonProductContextResult(
                        asin=asin,
                        status=AmazonProductSummaryStatus.READY,
                        context=context,
                    )
            except AmazonProductSummaryParseError as exc:
                last_result = AmazonProductContextResult(
                    asin=asin,
                    status=exc.status,
                    context=None,
                    detail=str(exc),
                )
            except Exception as exc:
                last_result = AmazonProductContextResult(
                    asin=asin,
                    status=AmazonProductSummaryStatus.PAGE_LOAD_FAILED,
                    context=None,
                    detail=str(exc),
                )

            if (
                last_result.status
                in {
                    AmazonProductSummaryStatus.AMAZON_CAPTCHA,
                    AmazonProductSummaryStatus.ASIN_MISMATCH,
                    AmazonProductSummaryStatus.AMAZON_LOGIN_REQUIRED,
                }
            ):
                return last_result
            if attempt < self._MAX_ATTEMPTS - 1:
                await asyncio.sleep(0.5 * (attempt + 1))

        return last_result or AmazonProductContextResult(
            asin=asin,
            status=AmazonProductSummaryStatus.PAGE_LOAD_FAILED,
            context=None,
        )

    @staticmethod
    def _page_status(
        page_url: str,
        html: str,
        asin: str,
    ) -> AmazonProductSummaryStatus | None:
        """先拦截登录/CAPTCHA/ASIN 跳转，禁止把错误页面当作 Product Summary。"""

        lowered_html = html.lower()
        if any(
            marker in lowered_html
            for marker in (
                "opfcaptcha.amazon.com",
                "amazon captcha",
                "robot check",
                "continue shopping",
            )
        ):
            return AmazonProductSummaryStatus.AMAZON_CAPTCHA
        if "sign in" in lowered_html and "ap/signin" in page_url.lower():
            return AmazonProductSummaryStatus.AMAZON_LOGIN_REQUIRED

        parsed_path = urlparse(page_url).path.upper()
        actual_asin_match = re.search(r"/DP/(B0[A-Z0-9]{8})", parsed_path)
        if actual_asin_match is None:
            return AmazonProductSummaryStatus.ASIN_MISMATCH
        if actual_asin_match.group(1) != asin:
            return AmazonProductSummaryStatus.ASIN_MISMATCH
        return None

    @staticmethod
    def _needs_us_delivery_correction(html: str) -> bool:
        """仅在页面明确处于非美国配送/CNY 环境时，才尝试一次正常 UI 设置。"""

        lowered_html = html.lower()
        return "cny" in lowered_html or any(
            marker in lowered_html
            for marker in (
                "deliver to china",
                "ship to china",
                "配送至中国",
            )
        )

    async def _try_set_us_delivery_zip(self, page: Any) -> None:
        """按 Amazon 正常配送地 UI 尝试设置 ZIP；控件缺失时保持原页面继续解析。"""

        try:
            await page.locator("#nav-global-location-popover-link").click()
            await page.locator("#GLUXZipUpdateInput").fill(self._zip_code)
            await page.locator("#GLUXZipUpdate").click()
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            # 配送地是辅助环境设置，不得因为 Amazon UI selector 变化而中断
            # Product Summary 主流程；后续页面状态仍会按真实结果判断。
            return

    @staticmethod
    def _is_profile_in_use_error(error: Exception) -> bool:
        """仅识别明确的专用 Profile 锁，不杀任何用户浏览器进程。"""

        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "user data directory is already in use",
                "processsingleton",
                "profile appears to be in use",
            )
        )

    def _store_uniform_failure(
        self,
        asins: Iterable[str],
        status: AmazonProductSummaryStatus,
        detail: str,
    ) -> None:
        """Context 启动失败时为未处理 ASIN 记录同一明确状态。"""

        for asin in asins:
            self._generation_cache.setdefault(
                asin,
                AmazonProductContextResult(
                    asin=asin,
                    status=status,
                    context=None,
                    detail=detail,
                ),
            )

    def _results_for(
        self,
        asins: Iterable[str],
    ) -> dict[str, AmazonProductContextResult]:
        """复制本轮所需 ASIN 的结果映射，避免上层修改内部 cache。"""

        return {
            asin: self._generation_cache[asin]
            for asin in asins
            if asin in self._generation_cache
        }

    @staticmethod
    def _normalize_unique_asins(asins: Iterable[str]) -> list[str]:
        """按首次出现顺序去重并严格验证 ASIN，避免隐式请求错误商品。"""

        unique_asins: list[str] = []
        for asin in asins:
            normalized_asin = asin.strip().upper() if isinstance(asin, str) else ""
            if not re.fullmatch(r"B0[A-Z0-9]{8}", normalized_asin):
                raise ValueError("Amazon fallback 包含无效 ASIN")
            if normalized_asin not in unique_asins:
                unique_asins.append(normalized_asin)
        return unique_asins
