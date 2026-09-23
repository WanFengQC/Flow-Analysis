from concurrent.futures import Future
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from infrastructure.application_runtime import ApplicationRuntime
from services.api_service import (
    ApiService,
    SellerSpriteAuthenticationError,
    SellerSpriteBusinessError,
    SellerSpriteHttpError,
    SellerSpriteResponseError,
    SellerSpriteTransientError,
)
from services.crawler_service import (
    CrawlerService,
    ReversingPreparationRetriesExhaustedError,
)
from workers.cookie_worker import CookieWorker


class MainController(QObject):
    """协调主窗口、异步基础设施、Cookie 与关联 ASIN 查询流程。"""

    # Future 回调运行在 asyncio 后台线程，因此必须经由 Qt Signal 回到 UI 线程。
    database_ready = Signal()
    database_failed = Signal(str)
    relation_query_succeeded = Signal(object, str, str, str)
    relation_query_failed = Signal(str)
    relation_image_ready = Signal(str, object)
    reversing_preparation_succeeded = Signal(str, object)
    reversing_preparation_failed = Signal(str, object)

    def __init__(
        self,
        window,
        runtime: ApplicationRuntime,
    ):
        super().__init__()

        # 保存 View 和应用级异步基础设施。
        self.window = window
        self.runtime = runtime

        # Cookie 成功后统一创建并复用 SellerSprite API Client 与分页 Service。
        self.cookie = None
        self.api_service: ApiService | None = None
        self.crawler_service: CrawlerService | None = None

        # Cookie 获取仍是现有同步 Worker 的职责。
        self.cookie_thread = None
        self.cookie_worker = None

        # 关联查询与图片加载均由 AsyncRuntime 返回 Future。
        self.relation_query_future: Future | None = None
        self.reversing_preparation_future: Future | None = None
        self._image_futures: set[Future] = set()

        # relation 查询成功时固定其实际请求月份；后续 reversing 必须沿用
        # 这一上下文，不能在点击“开始分析”时重新读取当前下拉框。
        self._relation_query_context: dict[str, str] | None = None
        self._reversing_results: dict[str, Any] = {}
        self._preparation_asins: list[str] = []
        self._preparation_month: str | None = None
        self._preparation_index = 0
        self._reversing_preparation_active = False
        self._reversing_preparation_status = "idle"

        self._database_ready = False
        self._api_ready = False

        self.database_ready.connect(self._on_database_ready)
        self.database_failed.connect(self._on_database_failed)
        self.relation_query_succeeded.connect(
            self._on_relation_query_succeeded
        )
        self.relation_query_failed.connect(
            self._on_relation_query_failed
        )
        self.relation_image_ready.connect(
            self._on_relation_image_ready
        )
        self.reversing_preparation_succeeded.connect(
            self._on_reversing_preparation_succeeded
        )
        self.reversing_preparation_failed.connect(
            self._on_reversing_preparation_failed
        )

        # Controller 只订阅 View 的公开信号，不连接 Designer 生成控件。
        self.window.relation_query_requested.connect(
            self._start_relation_query
        )
        self.window.analysis_preparation_requested.connect(
            self._start_analysis_preparation
        )

    def start(self):
        """并行启动 PostgreSQL 基础设施与 Cookie 获取流程。"""

        self._start_database()
        self._start_cookie_task()

    def shutdown(self) -> None:
        """在程序退出阶段取消业务任务并关闭 SellerSprite API Client。"""

        if self.relation_query_future is not None:
            self.relation_query_future.cancel()

        if self.reversing_preparation_future is not None:
            self.reversing_preparation_future.cancel()
        self._reversing_preparation_active = False
        self._reversing_preparation_status = "aborted"

        self._cancel_relation_image_tasks()

        if self.api_service is None:
            return

        try:
            close_future = self.runtime.async_runtime.submit(
                self.api_service.aclose()
            )
            # 程序退出时允许带超时等待，不能将该行为用于普通 UI 流程。
            close_future.result(timeout=5.0)
        except Exception:
            # 退出阶段继续执行后续基础设施清理，避免关闭顺序被单个客户端阻断。
            pass

    def _start_database(self):
        """启动 PostgreSQL 基础设施。"""

        self.window.set_database_status("连接中")

        try:
            future = self.runtime.start()
            future.add_done_callback(
                self._on_database_future_done
            )
        except Exception as exc:
            self.database_failed.emit(str(exc))

    def _on_database_future_done(
        self,
        future: Future,
    ):
        """在后台回调中读取已完成结果，并通过 Signal 交回 UI 线程。"""

        try:
            future.result()
        except Exception as exc:
            self.database_failed.emit(str(exc))
            return

        self.database_ready.emit()

    @Slot()
    def _on_database_ready(self):
        """更新数据库准备完成状态。"""

        self._database_ready = True
        self.window.set_database_status("正常")
        self._update_initialization_status()

    @Slot(str)
    def _on_database_failed(
        self,
        message: str,
    ):
        """更新数据库初始化失败状态。"""

        self._database_ready = False
        self.window.set_database_status("连接失败")
        self.window.set_status(
            f"PostgreSQL 连接失败：{message}"
        )

    def _start_cookie_task(self):
        """在现有 QThread 中启动同步 Cookie 获取任务。"""

        if self.cookie_thread is not None:
            return

        self.window.set_status("正在获取 Cookie...")
        self.cookie_thread = QThread()
        self.cookie_worker = CookieWorker()
        self.cookie_worker.moveToThread(self.cookie_thread)

        self.cookie_thread.started.connect(self.cookie_worker.run)
        self.cookie_worker.cookie_ready.connect(self._on_cookie_ready)
        self.cookie_worker.error.connect(self._on_cookie_error)
        self.cookie_worker.finished.connect(self.cookie_thread.quit)
        self.cookie_worker.finished.connect(
            self.cookie_worker.deleteLater
        )
        self.cookie_thread.finished.connect(
            self.cookie_thread.deleteLater
        )
        self.cookie_thread.finished.connect(
            self._on_cookie_thread_finished
        )
        self.cookie_thread.start()

    @Slot(object)
    def _on_cookie_ready(
        self,
        cookie,
    ):
        """使用当前 Cookie 创建唯一的 SellerSprite API Client。"""

        self.cookie = cookie

        # Cookie 更新时，先取消依赖旧 Cookie 的后台任务。
        self._cancel_relation_image_tasks()
        if self.relation_query_future is not None:
            self.relation_query_future.cancel()
        self._stop_reversing_preparation(
            "Cookie 已更新，已停止当前数据准备"
        )

        if self.api_service is not None:
            # 旧客户端只能在 AsyncRuntime 中关闭，不能在 Qt 主线程调用同步 close。
            try:
                self.runtime.async_runtime.submit(
                    self.api_service.aclose()
                )
            except RuntimeError:
                # Runtime 启动失败时没有可提交的 EventLoop；新客户端不会被创建。
                self._api_ready = False
                self.window.set_relation_query_available(False)
                return

        self.api_service = ApiService(cookie)
        self.crawler_service = CrawlerService(self.api_service)
        self._api_ready = True

        self.window.set_cookie_status("正常")
        self.window.set_api_status("就绪")
        self.window.set_relation_query_available(True)
        self._update_initialization_status()

    @Slot(str)
    def _on_cookie_error(
        self,
        message,
    ):
        """处理 Cookie 获取失败，并关闭关联查询入口。"""

        self.cookie = None
        self._api_ready = False
        self.crawler_service = None
        self.window.set_cookie_status("获取失败")
        self.window.set_api_status("未初始化")
        self.window.set_relation_query_available(False)
        self.window.set_status(
            f"Cookie 获取失败：{message}"
        )

    @Slot()
    def _on_cookie_thread_finished(self):
        """清理已退出 Cookie Worker 对应的 Python 引用。"""

        self.cookie_worker = None
        self.cookie_thread = None

    @Slot()
    def _start_relation_query(self):
        """读取 View 参数并将完整分页协程提交到 AsyncRuntime。"""

        if (
            not self._api_ready
            or self.crawler_service is None
        ):
            self.window.set_status("SellerSprite API 尚未就绪")
            return

        if (
            self.relation_query_future is not None
            and not self.relation_query_future.done()
        ):
            return

        parameters = self.window.relation_query_parameters()
        asin = parameters["asin"]
        month = parameters["month"]
        display_time_range = parameters["display_time_range"]

        if not asin:
            self.window.set_status("请输入 ASIN")
            return

        if month is None or display_time_range is None:
            self.window.set_status("请选择时间范围")
            return

        self._cancel_relation_image_tasks()
        self._relation_query_context = None
        self.window.set_relation_query_running(True)
        self.window.show_relation_loading()
        self.window.set_task_summary(
            f"{asin} · {display_time_range}",
            0,
        )
        self.window.set_status("正在查询关联 ASIN...")

        try:
            future = self.runtime.async_runtime.submit(
                self._fetch_relation_sources(
                    asin=asin,
                    month=month,
                )
            )
        except Exception:
            self.window.set_relation_query_running(False)
            self.window.show_relation_query_error(
                "异步查询服务不可用"
            )
            self.window.set_status("关联 ASIN 查询无法启动")
            return

        self.relation_query_future = future
        future.add_done_callback(
            lambda completed_future: self._on_relation_query_done(
                completed_future,
                asin,
                month,
                display_time_range,
            )
        )

    async def _fetch_relation_sources(
        self,
        asin: str,
        month: str,
    ) -> list[Any]:
        """在 AsyncRuntime 中执行指定 ASIN 与月份的完整分页查询。"""

        if self.crawler_service is None:
            raise RuntimeError("CrawlerService 尚未初始化")

        return await self.crawler_service.get_all_relation_sources_for_asin_month(
            asin=asin,
            market="COM",
            month=month,
            page_size=100,
        )

    def _on_relation_query_done(
        self,
        future: Future,
        asin: str,
        month: str,
        display_time_range: str,
    ):
        """在后台 Future 回调中转换结果，再通过 Signal 返回 Qt 主线程。"""

        try:
            items = future.result()
        except Exception as exc:
            self.relation_query_failed.emit(
                self._relation_query_error_message(exc)
            )
            return

        self.relation_query_succeeded.emit(
            items,
            asin,
            month,
            display_time_range,
        )

    @Slot(object, str, str, str)
    def _on_relation_query_succeeded(
        self,
        items: list[Any],
        asin: str,
        month: str,
        display_time_range: str,
    ):
        """在 Qt 主线程展示文字卡片，并启动独立的图片加载任务。"""

        self.relation_query_future = None
        self._relation_query_context = {
            "asin": asin,
            "month": month,
            "display_time_range": display_time_range,
        }
        self.window.set_relation_query_running(False)
        self.window.show_relation_results(items, asin)
        self.window.set_task_summary(
            f"{asin} · {display_time_range}",
            len(items),
        )
        self.window.set_status("关联 ASIN 查询完成")

        for image_url in self.window.relation_image_urls():
            self._start_relation_image_load(image_url)

    @Slot(str)
    def _on_relation_query_failed(self, message: str):
        """在 Qt 主线程恢复控件并显示查询失败状态。"""

        self.relation_query_future = None
        self._relation_query_context = None
        self.window.set_relation_query_running(False)
        self.window.show_relation_query_error(message)
        self.window.set_status(f"关联 ASIN 查询失败：{message}")

    @Slot()
    def _start_analysis_preparation(self):
        """以当前选择和已保存的 relation 查询上下文启动 reversing 预处理。"""

        if (
            not self._api_ready
            or self.crawler_service is None
        ):
            self.window.set_status("SellerSprite API 尚未就绪")
            return

        if self._reversing_preparation_active:
            return

        selected_items = self.window.selected_relation_items()
        if not selected_items:
            self.window.set_status("请先选择至少一个关联 ASIN")
            return

        query_context = self._relation_query_context
        if query_context is None:
            self.window.set_status("当前关联 ASIN 查询上下文不可用")
            return

        month = query_context.get("month")
        if month is None:
            self.window.set_status("当前关联 ASIN 查询时间范围不可用")
            return

        asins = self._selected_relation_asins(selected_items)
        if not asins:
            self.window.set_status("未找到可处理的关联 ASIN")
            return

        # 每次新的“开始分析”任务建立独立内存结果；手动重试则不会经过
        # 此入口，因此已经成功的 ASIN 结果不会被清空或重新处理。
        self._reversing_results = {}
        self._preparation_asins = asins
        self._preparation_month = month
        self._preparation_index = 0
        self._reversing_preparation_active = True
        self._reversing_preparation_status = "running"

        self.window.set_relation_preparation_running(True)
        self._submit_current_reversing_preparation()

    @staticmethod
    def _selected_relation_asins(selected_items: list[Any]) -> list[str]:
        """按卡片选择结果的原始顺序读取 item["asin"]，并忽略重复 ASIN。"""

        asins: list[str] = []
        seen: set[str] = set()
        for item in selected_items:
            if not isinstance(item, Mapping):
                continue

            if "asin" not in item:
                continue

            asin = str(item["asin"]).strip().upper()
            if asin and asin not in seen:
                asins.append(asin)
                seen.add(asin)

        return asins

    def _submit_current_reversing_preparation(self):
        """提交当前序号的单个 ASIN；只有成功后才会提交下一个。"""

        if not self._reversing_preparation_active:
            return

        if self._preparation_index >= len(self._preparation_asins):
            self._finish_reversing_preparation_success()
            return

        if self.crawler_service is None or self._preparation_month is None:
            self._stop_reversing_preparation("分析数据准备失败")
            return

        asin = self._preparation_asins[self._preparation_index]
        total = len(self._preparation_asins)
        current = self._preparation_index + 1
        self.window.set_analysis_progress(
            int((self._preparation_index / total) * 100),
            f"正在准备分析数据：{current} / {total}",
        )

        try:
            future = self.runtime.async_runtime.submit(
                self.crawler_service.get_relation_reversing_for_asin_month(
                    asin=asin,
                    month=self._preparation_month,
                    market="COM",
                    limit=100,
                    skip=0,
                )
            )
        except RuntimeError:
            self._stop_reversing_preparation("分析数据准备失败")
            return

        self.reversing_preparation_future = future
        future.add_done_callback(
            lambda completed_future: self._on_reversing_preparation_done(
                completed_future,
                asin,
            )
        )

    def _on_reversing_preparation_done(
        self,
        future: Future,
        asin: str,
    ):
        """将 AsyncRuntime 中单个 ASIN 的结果经 Signal 交回 Qt 主线程。"""

        try:
            data = future.result()
        except Exception as exc:
            self.reversing_preparation_failed.emit(asin, exc)
            return

        self.reversing_preparation_succeeded.emit(asin, data)

    @Slot(str, object)
    def _on_reversing_preparation_succeeded(
        self,
        asin: str,
        data: Any,
    ):
        """保存成功的内存结果，并严格串行推进到下一个选择 ASIN。"""

        if (
            not self._reversing_preparation_active
            or self._current_preparation_asin() != asin
        ):
            return

        self.reversing_preparation_future = None
        self._reversing_results[asin] = data
        self._preparation_index += 1

        if self._preparation_index >= len(self._preparation_asins):
            self._finish_reversing_preparation_success()
            return

        self._submit_current_reversing_preparation()

    @Slot(str, object)
    def _on_reversing_preparation_failed(
        self,
        asin: str,
        exc: Exception,
    ):
        """在主线程处理可人工重试的最终失败或直接终止不可恢复失败。"""

        if (
            not self._reversing_preparation_active
            or self._current_preparation_asin() != asin
        ):
            return

        self.reversing_preparation_future = None
        if isinstance(exc, ReversingPreparationRetriesExhaustedError):
            # 弹窗只能由主线程的 View 创建；用户选择重试时保持当前索引，
            # 因而只会从当前失败 ASIN 的 monthly 预热重新开始。
            if self.window.ask_reversing_data_retry(asin):
                self._submit_current_reversing_preparation()
                return

            self._stop_reversing_preparation("分析数据准备失败")
            return

        self._stop_reversing_preparation(
            self._preparation_error_message(exc)
        )

    def _finish_reversing_preparation_success(self):
        """在所有选择 ASIN 均成功后恢复 UI，暂不进入后续分析步骤。"""

        total = len(self._preparation_asins)
        self._reversing_preparation_active = False
        self._reversing_preparation_status = "completed"
        self.reversing_preparation_future = None
        self.window.set_relation_preparation_running(False)
        self.window.set_status(f"分析数据准备完成：{total} / {total}")

    def _stop_reversing_preparation(self, status_message: str):
        """终止当前预处理任务并保留已成功的内存结果用于诊断。"""

        if (
            self.reversing_preparation_future is not None
            and not self.reversing_preparation_future.done()
        ):
            self.reversing_preparation_future.cancel()

        self.reversing_preparation_future = None
        self._reversing_preparation_active = False
        self._reversing_preparation_status = "failed"
        self.window.set_relation_preparation_running(False)
        self.window.set_status(status_message)

    def _current_preparation_asin(self) -> str | None:
        """返回当前暂停或执行中的 ASIN，防止旧 Future 回调推进新任务。"""

        if 0 <= self._preparation_index < len(self._preparation_asins):
            return self._preparation_asins[self._preparation_index]

        return None

    def _start_relation_image_load(self, image_url: str):
        """提交单个去重图片 URL 的异步加载，不阻塞文字结果展示。"""

        try:
            future = self.runtime.async_runtime.submit(
                self.runtime.image_service.get_image(image_url)
            )
        except RuntimeError:
            return

        self._image_futures.add(future)
        future.add_done_callback(
            lambda completed_future: self._on_relation_image_done(
                completed_future,
                image_url,
            )
        )

    def _on_relation_image_done(
        self,
        future: Future,
        image_url: str,
    ):
        """将图片下载结果经 Signal 回传；单张失败不影响本次查询。"""

        self._image_futures.discard(future)

        try:
            image_bytes = future.result()
        except Exception:
            return

        if image_bytes:
            self.relation_image_ready.emit(
                image_url,
                image_bytes,
            )

    @Slot(str, object)
    def _on_relation_image_ready(
        self,
        image_url: str,
        image_bytes: bytes,
    ):
        """在 Qt 主线程回填已下载图片。"""

        self.window.update_relation_product_image(
            image_url,
            image_bytes,
        )

    def _cancel_relation_image_tasks(self):
        """取消不再属于当前结果页的图片 Future。"""

        for future in tuple(self._image_futures):
            future.cancel()

        self._image_futures.clear()

    def _update_initialization_status(self):
        """在数据库与 API 均完成初始化后更新底部状态。"""

        if self._database_ready and self._api_ready:
            self.window.set_status("初始化完成")

    @staticmethod
    def _relation_query_error_message(exc: Exception) -> str:
        """将底层异常转换成不会暴露认证信息的简洁用户提示。"""

        if isinstance(exc, SellerSpriteAuthenticationError):
            return "Cookie 已失效，请重新获取"

        if isinstance(
            exc,
            (SellerSpriteHttpError, SellerSpriteTransientError),
        ):
            return "SellerSprite 网络请求失败"

        if isinstance(exc, SellerSpriteBusinessError):
            return "SellerSprite 拒绝了当前请求"

        if isinstance(exc, SellerSpriteResponseError):
            return "SellerSprite 返回的数据结构异常"

        return "查询过程中发生未预期错误"

    @staticmethod
    def _preparation_error_message(exc: Exception) -> str:
        """返回预处理失败的安全状态提示，不透传认证或请求敏感信息。"""

        if isinstance(exc, SellerSpriteAuthenticationError):
            return "分析数据准备失败：Cookie 已失效"

        if isinstance(exc, SellerSpriteBusinessError):
            return "分析数据准备失败：SellerSprite 拒绝了当前请求"

        if isinstance(exc, SellerSpriteHttpError):
            return "分析数据准备失败：SellerSprite HTTP 请求异常"

        if isinstance(exc, SellerSpriteResponseError):
            return "分析数据准备失败：SellerSprite 返回的数据结构异常"

        return "分析数据准备失败"
