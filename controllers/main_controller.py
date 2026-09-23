from concurrent.futures import Future
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, QThread, Signal, Slot

from infrastructure.application_runtime import ApplicationRuntime
from infrastructure.diagnostics import (
    write_normalization_candidates_snapshot,
)
from services.analysis_service import AnalysisService
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
from services.normalization_candidate_service import (
    NormalizationCandidateService,
)
from services.normalization_rule_service import NormalizationRuleService
from services.normalization_review_service import NormalizationReviewService
from models.normalization_review_decision import NormalizationReviewDecision
from repositories.normalization_review_repository import (
    NormalizationReviewRepository,
)
from workers.analysis_worker import AnalysisWorker
from workers.cookie_worker import CookieWorker
from workers.normalization_apply_worker import NormalizationApplyWorker


class MainController(QObject):
    """协调主窗口、异步基础设施、Cookie 与关联 ASIN 查询流程。"""

    # Future 回调运行在 asyncio 后台线程，因此必须经由 Qt Signal 回到 UI 线程。
    database_ready = Signal()
    database_failed = Signal(str)
    relation_query_succeeded = Signal(object, str, str, str, object)
    relation_query_failed = Signal(str)
    relation_image_ready = Signal(str, object)
    reversing_preparation_succeeded = Signal(str, str, object)
    reversing_preparation_failed = Signal(str, str, object)
    normalization_decision_saved = Signal(str)
    normalization_decision_save_failed = Signal(str)
    normalization_history_references_loaded = Signal(int, object)
    normalization_history_references_failed = Signal(int)

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
        self.analysis_service = AnalysisService()
        self.normalization_candidate_service = (
            NormalizationCandidateService()
        )
        self.normalization_rule_service = NormalizationRuleService()
        database = getattr(runtime, "database", None)
        self.normalization_review_service = (
            NormalizationReviewService(
                NormalizationReviewRepository(database)
            )
            if database is not None
            else None
        )

        # Cookie 获取仍是现有同步 Worker 的职责。
        self.cookie_thread = None
        self.cookie_worker = None
        self.analysis_thread = None
        self.analysis_worker = None
        self.normalization_apply_thread = None
        self.normalization_apply_worker = None

        # 关联查询与图片加载均由 AsyncRuntime 返回 Future。
        self.relation_query_future: Future | None = None
        self.reversing_preparation_future: Future | None = None
        self._image_futures: set[Future] = set()

        # relation 查询成功时固定其实际请求月份；后续 reversing 必须沿用
        # 这一上下文，不能在点击“开始分析”时重新读取当前下拉框。
        self._relation_query_context: dict[str, Any] | None = None
        # 月份必须是所有业务数据最外层的物理边界。即使关键词相同，
        # 也绝不允许不同月份的 reversing / RAW / RESULT 混入同一列表。
        self._reversing_results: dict[str, dict[str, Any]] = {}
        self._analysis_raw: dict[str, list[dict[str, Any]]] = {}
        self._analysis_results: dict[str, list[dict[str, Any]]] = {}
        # 审核前预览只做基础分词，正式词结果只能在明确应用人工 APPROVED
        # 规则后产生；本阶段没有“应用审核结果”动作，因此保持为空。
        self._analysis_word_preview_results: dict[
            str,
            list[dict[str, Any]],
        ] = {}
        self._analysis_word_results: dict[str, list[dict[str, Any]]] = {}
        self._analysis_word_result_meta: dict[str, Any] = {}
        # 候选独立于正式规则和词统计，只保留给后续人工审核流程读取。
        self._normalization_candidates: list[dict[str, Any]] = []
        self._analysis_diagnostics: dict[str, dict[str, Any]] = {}
        self._analysis_processing_active = False
        self._normalization_apply_active = False
        self._normalization_apply_context: dict[str, Any] = {}
        self._normalization_revision = 0
        self._normalization_result_state = "NOT_GENERATED"
        # 每次人工动作都有独立待保存快照；失败事件保留，以便用户手动重试。
        self._normalization_persistence_state = "CLEAN"
        self._normalization_decision_events: dict[str, dict[str, Any]] = {}
        self._normalization_save_futures: dict[str, Future] = {}
        # 历史审核仅是当前候选的只读参考；generation 防止旧任务异步结果覆盖新任务。
        self._normalization_candidate_generation = 0
        self._normalization_history_references: dict[str, dict[str, Any]] = {}
        self._normalization_history_state = "IDLE"
        self._normalization_history_futures: set[Future] = set()
        self._preparation_asins: list[str] = []
        self._preparation_months: list[str] = []
        self._preparation_month_index = 0
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
        self.normalization_decision_saved.connect(
            self._on_normalization_decision_saved
        )
        self.normalization_decision_save_failed.connect(
            self._on_normalization_decision_save_failed
        )
        self.normalization_history_references_loaded.connect(
            self._on_normalization_history_references_loaded
        )
        self.normalization_history_references_failed.connect(
            self._on_normalization_history_references_failed
        )

        # Controller 只订阅 View 的公开信号，不连接 Designer 生成控件。
        self.window.relation_query_requested.connect(
            self._start_relation_query
        )
        self.window.analysis_preparation_requested.connect(
            self._start_analysis_preparation
        )
        self.window.normalization_approve_requested.connect(
            self._approve_normalization_candidate
        )
        self.window.normalization_reject_requested.connect(
            self._reject_normalization_candidate
        )
        self.window.normalization_skip_requested.connect(
            self._skip_normalization_candidate
        )
        self.window.normalization_apply_requested.connect(
            self._apply_normalization_rules
        )
        self.window.normalization_persistence_retry_requested.connect(
            self._retry_normalization_decision_persistence
        )

    def start(self):
        """并行启动 PostgreSQL 基础设施与 Cookie 获取流程。"""

        self._start_database()
        self._start_cookie_task()

    def shutdown(self) -> None:
        """在程序退出阶段取消业务任务并关闭 SellerSprite API Client。"""

        # 审核记录属于用户已经确认的操作。退出阶段允许有限同步等待，尽量让
        # 已提交到现有 AsyncRuntime 的 INSERT 在数据库连接池关闭前完成。
        self._wait_for_normalization_persistence_on_shutdown()

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
        analysis_months = parameters["analysis_months"]

        if not asin:
            self.window.set_status("请输入 ASIN")
            return

        if (
            month is None
            or display_time_range is None
            or not isinstance(analysis_months, list)
            or not analysis_months
        ):
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
                analysis_months,
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
        analysis_months: list[str],
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
            analysis_months,
        )

    @Slot(object, str, str, str, object)
    def _on_relation_query_succeeded(
        self,
        items: list[Any],
        asin: str,
        month: str,
        display_time_range: str,
        analysis_months: list[str],
    ):
        """在 Qt 主线程展示文字卡片，并启动独立的图片加载任务。"""

        self.relation_query_future = None
        self._relation_query_context = {
            "asin": asin,
            "month": month,
            "display_time_range": display_time_range,
            "analysis_months": list(analysis_months),
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

        analysis_months = query_context.get("analysis_months")
        if (
            not isinstance(analysis_months, list)
            or not analysis_months
            or not all(isinstance(month, str) for month in analysis_months)
        ):
            self.window.set_status("当前关联 ASIN 查询时间范围不可用")
            return

        asins = self._selected_relation_asins(selected_items)
        if not asins:
            self.window.set_status("未找到可处理的关联 ASIN")
            return

        # 每次新的“开始分析”任务建立独立内存结果；手动重试则不会经过
        # 此入口，因此已经成功的 ASIN 结果不会被清空或重新处理。
        # 预先按 UI 已固定的“最新到最旧”顺序建立月份桶，彻底阻断
        # 不同月份之间的隐式聚合。
        self._reversing_results = {
            month: {} for month in analysis_months
        }
        self._analysis_raw = {}
        self._analysis_results = {}
        self._analysis_word_preview_results = {}
        self._analysis_word_results = {}
        self._analysis_word_result_meta = {}
        self._normalization_candidates = []
        self._normalization_candidate_generation += 1
        self._normalization_history_references = {}
        self._normalization_history_state = "IDLE"
        self._analysis_diagnostics = {}
        self._normalization_revision = 0
        self._normalization_apply_context = {}
        self._normalization_result_state = "NOT_GENERATED"
        # 旧任务已明确触发的审计写入仍可能在 AsyncRuntime 中执行，不能因为
        # 用户启动新分析就丢弃其跟踪或取消 INSERT。
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self._refresh_normalization_persistence_status()
        self.window.set_normalization_candidates(
            self._normalization_candidates
        )
        self.window.set_normalization_history_references({})
        self.window.set_normalization_history_load_status("IDLE")
        self._preparation_asins = asins
        self._preparation_months = list(analysis_months)
        self._preparation_month_index = 0
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

        if self._preparation_month_index >= len(self._preparation_months):
            self._finish_reversing_preparation_success()
            return

        month = self._current_preparation_month()
        if self.crawler_service is None or month is None:
            self._stop_reversing_preparation("分析数据准备失败")
            return

        asin = self._preparation_asins[self._preparation_index]
        total = len(self._preparation_asins)
        current = self._preparation_index + 1
        self.window.set_analysis_progress(
            int((self._preparation_index / total) * 100),
            f"正在准备分析数据：{month}，{current} / {total}",
        )

        try:
            future = self.runtime.async_runtime.submit(
                self.crawler_service.get_relation_reversing_for_asin_month(
                    asin=asin,
                    month=month,
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
                month,
            )
        )

    def _on_reversing_preparation_done(
        self,
        future: Future,
        asin: str,
        month: str,
    ):
        """将 AsyncRuntime 中单个 ASIN 的结果经 Signal 交回 Qt 主线程。"""

        try:
            data = future.result()
        except Exception as exc:
            self.reversing_preparation_failed.emit(month, asin, exc)
            return

        self.reversing_preparation_succeeded.emit(month, asin, data)

    @Slot(str, str, object)
    def _on_reversing_preparation_succeeded(
        self,
        month: str,
        asin: str,
        data: Any,
    ):
        """保存成功的内存结果，并严格串行推进到下一个选择 ASIN。"""

        if (
            not self._reversing_preparation_active
            or self._current_preparation_month() != month
            or self._current_preparation_asin() != asin
        ):
            return

        self.reversing_preparation_future = None
        self._reversing_results[month][asin] = data
        self._preparation_index += 1

        if self._preparation_index >= len(self._preparation_asins):
            # 当前月全部 ASIN 成功后，才允许开始下一月的第一个 ASIN。
            self._preparation_month_index += 1
            self._preparation_index = 0

        self._submit_current_reversing_preparation()

    @Slot(str, str, object)
    def _on_reversing_preparation_failed(
        self,
        month: str,
        asin: str,
        exc: Exception,
    ):
        """在主线程处理可人工重试的最终失败或直接终止不可恢复失败。"""

        if (
            not self._reversing_preparation_active
            or self._current_preparation_month() != month
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
        """在所有选择 ASIN 均成功后启动后台 RAW / RESULT 数据处理。"""

        total = len(self._preparation_asins) * len(
            self._preparation_months
        )
        self._reversing_preparation_active = False
        self._reversing_preparation_status = "processing"
        self.reversing_preparation_future = None
        self.window.set_status(f"分析数据准备完成：{total} / {total}")
        self._start_reversing_result_analysis()

    def _start_reversing_result_analysis(self):
        """用现有 QThread 执行纯 Python 聚合，避免在 Qt 主线程循环大量 RAW。"""

        if self._analysis_processing_active:
            return

        if not self._preparation_months:
            self._stop_reversing_preparation("数据处理失败")
            return

        self._analysis_processing_active = True
        self.window.set_status("正在分析关键词数据...")
        self.analysis_thread = QThread()
        self.analysis_worker = AnalysisWorker(
            self.analysis_service,
            self.normalization_candidate_service,
            self._reversing_results,
            self._preparation_months,
        )
        self.analysis_worker.moveToThread(self.analysis_thread)

        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_worker.processed.connect(
            self._on_reversing_result_analysis_succeeded
        )
        self.analysis_worker.error.connect(
            self._on_reversing_result_analysis_failed
        )
        self.analysis_worker.finished.connect(self.analysis_thread.quit)
        self.analysis_worker.finished.connect(
            self.analysis_worker.deleteLater
        )
        self.analysis_thread.finished.connect(
            self.analysis_thread.deleteLater
        )
        self.analysis_thread.finished.connect(
            self._on_analysis_thread_finished
        )
        self.analysis_thread.start()

    @Slot(object)
    def _on_reversing_result_analysis_succeeded(
        self,
        processed_data: dict[str, Any],
    ):
        """保存后台生成的 RAW、正式词结果、候选与诊断信息。"""

        if not self._analysis_processing_active:
            return

        self._analysis_raw = processed_data["raw"]
        self._analysis_results = processed_data["results"]
        self._analysis_word_preview_results = processed_data[
            "word_preview_results"
        ]
        # 新任务先只保存未归一预览；正式词结果必须等人工明确点击应用。
        self._analysis_word_results = processed_data["word_results"]
        self._analysis_word_result_meta = {}
        self._normalization_candidates = processed_data[
            "normalization_candidates"
        ]
        # 仅将当前任务生成的候选交给 View 审核；View 不持有业务写权限。
        self.window.set_normalization_candidates(
            self._normalization_candidates
        )
        # 候选必须先立即可审核，再异步读取精确 fingerprint 的历史参考。
        self._normalization_history_references = {}
        self.window.set_normalization_history_references({})
        self._load_normalization_history_references(
            self._normalization_candidate_generation,
            self._normalization_candidates,
        )
        # 候选完整生成后才写一次只读快照；诊断失败由基础设施内部吞吐，
        # 不得让正式分析结果或 UI 状态回退为失败。
        write_normalization_candidates_snapshot(
            self._normalization_candidates,
            self._preparation_months,
        )
        self._analysis_diagnostics = processed_data["diagnostics"]
        self._normalization_result_state = "NOT_GENERATED"
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self._analysis_processing_active = False
        self._reversing_preparation_status = "completed"
        self.window.set_relation_preparation_running(False)
        self.window.set_status("关键词数据分析完成（未归一预览）")

    @Slot(str, str)
    def _approve_normalization_candidate(
        self,
        candidate_id: str,
        approved_canonical: str,
    ) -> None:
        """保存当前会话的人工批准结果，不写入任何正式 alias 或数据库。"""

        if self._normalization_apply_active:
            return

        canonical = approved_canonical.strip()
        if not canonical:
            self.window.set_status("请输入标准词")
            return
        candidate = self._find_normalization_candidate(candidate_id)
        if candidate is None:
            self.window.set_status("归一候选不存在或已更新")
            return

        # suggestedCanonical 是机器建议和未来审计依据，人工值只能单独保存。
        changed = (
            candidate.get("decision") != "APPROVED"
            or candidate.get("approvedCanonical") != canonical
        )
        candidate["decision"] = "APPROVED"
        candidate["approvedCanonical"] = canonical
        self.window.update_normalization_candidate(candidate)
        if changed:
            self._record_normalization_review_change()
        self._queue_normalization_decision_persistence(
            candidate,
            NormalizationReviewDecision.APPROVED,
        )
        self.window.set_status("已批准归一候选")

    @Slot(str)
    def _reject_normalization_candidate(self, candidate_id: str) -> None:
        """标记当前候选不应合并，允许同一会话稍后重新审核。"""

        if self._normalization_apply_active:
            return

        candidate = self._find_normalization_candidate(candidate_id)
        if candidate is None:
            self.window.set_status("归一候选不存在或已更新")
            return

        changed = (
            candidate.get("decision") != "REJECTED"
            or "approvedCanonical" in candidate
        )
        candidate["decision"] = "REJECTED"
        candidate.pop("approvedCanonical", None)
        self.window.update_normalization_candidate(candidate)
        if changed:
            self._record_normalization_review_change()
        self._queue_normalization_decision_persistence(
            candidate,
            NormalizationReviewDecision.REJECTED,
        )
        self.window.set_status("已拒绝归一候选")

    @Slot(str)
    def _skip_normalization_candidate(self, candidate_id: str) -> None:
        """标记当前候选暂不判断，既不批准也不形成拒绝规则。"""

        if self._normalization_apply_active:
            return

        candidate = self._find_normalization_candidate(candidate_id)
        if candidate is None:
            self.window.set_status("归一候选不存在或已更新")
            return

        changed = (
            candidate.get("decision") != "SKIPPED"
            or "approvedCanonical" in candidate
        )
        candidate["decision"] = "SKIPPED"
        candidate.pop("approvedCanonical", None)
        self.window.update_normalization_candidate(candidate)
        if changed:
            self._record_normalization_review_change()
        self._queue_normalization_decision_persistence(
            candidate,
            NormalizationReviewDecision.SKIPPED,
        )
        self.window.set_status("已跳过归一候选")

    @Slot()
    def _apply_normalization_rules(self) -> None:
        """显式编译当前审核快照，并在后台原子重算所有月份正式词结果。"""

        if self._normalization_apply_active:
            return
        if not self._analysis_results or not self._preparation_months:
            self.window.set_status("当前没有可重算的月度分析结果")
            return

        # 点击瞬间复制候选，之后用户任何操作均不能影响本轮规则集。
        candidate_snapshot = deepcopy(self._normalization_candidates)
        approved_candidate_count = sum(
            candidate.get("decision") == "APPROVED"
            for candidate in candidate_snapshot
        )
        if approved_candidate_count == 0:
            self.window.set_status("当前没有已批准的归一规则")
            return

        build_result = self.normalization_rule_service.build_approved_rules(
            candidate_snapshot
        )
        if not build_result.rules:
            # 防御性处理旧会话或外部测试构造的空 canonical，不能让“批准”
            # 状态在没有任何可执行规则时启动一轮无意义重算。
            self.window.set_status("当前没有可执行的已批准归一规则")
            return
        blocking_conflicts = (
            *build_result.conflicts,
            *build_result.chain_references,
        )
        if blocking_conflicts or build_result.approved_rules is None:
            self._normalization_result_state = "CONFLICT"
            self._refresh_normalization_result_status()
            self.window.show_normalization_rule_conflicts(
                [
                    {
                        "conflict_type": conflict.conflict_type,
                        "variant": conflict.variant,
                        "canonical_values": conflict.canonical_values,
                        "source_candidate_ids": conflict.source_candidate_ids,
                    }
                    for conflict in blocking_conflicts
                ]
            )
            return

        months = list(self._preparation_months)
        monthly_result_snapshot = deepcopy(
            {
                month: self._analysis_results.get(month, [])
                for month in months
            }
        )
        self._normalization_apply_context = {
            "normalization_revision": self._normalization_revision,
            "approved_rule_count": len(build_result.rules),
            "approved_candidate_ids": [
                rule.source_candidate_id for rule in build_result.rules
            ],
            "pending_candidate_count": self._normalization_decision_count(
                "PENDING",
                candidate_snapshot,
            ),
            "skipped_candidate_count": self._normalization_decision_count(
                "SKIPPED",
                candidate_snapshot,
            ),
            "months": months,
        }
        self._normalization_apply_active = True
        self._normalization_result_state = "RUNNING"
        self.window.set_normalization_review_editable(False)
        self._refresh_normalization_result_status()
        self.window.set_status("正在按已批准规则重算...")

        self.normalization_apply_thread = QThread()
        self.normalization_apply_worker = NormalizationApplyWorker(
            self.analysis_service,
            monthly_result_snapshot,
            months,
            build_result.approved_rules,
        )
        self.normalization_apply_worker.moveToThread(
            self.normalization_apply_thread
        )
        self.normalization_apply_thread.started.connect(
            self.normalization_apply_worker.run
        )
        self.normalization_apply_worker.processed.connect(
            self._on_normalization_apply_succeeded
        )
        self.normalization_apply_worker.error.connect(
            self._on_normalization_apply_failed
        )
        self.normalization_apply_worker.finished.connect(
            self.normalization_apply_thread.quit
        )
        self.normalization_apply_worker.finished.connect(
            self.normalization_apply_worker.deleteLater
        )
        self.normalization_apply_thread.finished.connect(
            self.normalization_apply_thread.deleteLater
        )
        self.normalization_apply_thread.finished.connect(
            self._on_normalization_apply_thread_finished
        )
        self.normalization_apply_thread.start()

    @Slot(object)
    def _on_normalization_apply_succeeded(
        self,
        processed_data: Mapping[str, Any],
    ) -> None:
        """仅在全部月份成功后，一次性替换正式词结果和对应元数据。"""

        if not self._normalization_apply_active:
            return

        word_results = processed_data.get("word_results")
        diagnostics = processed_data.get("diagnostics")
        months = self._normalization_apply_context.get("months", [])
        if (
            not isinstance(word_results, Mapping)
            or list(word_results) != months
            or not isinstance(diagnostics, Mapping)
        ):
            self._on_normalization_apply_failed("正式词结果重算失败")
            return

        # 所有月份都已在 Worker 内成功完成，才允许替换旧正式快照。
        self._analysis_word_results = dict(word_results)
        self._analysis_word_result_meta = {
            "applied_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            **self._normalization_apply_context,
        }
        for month, month_diagnostics in diagnostics.items():
            if isinstance(month_diagnostics, Mapping):
                self._analysis_diagnostics.setdefault(month, {})[
                    "word_result"
                ] = dict(month_diagnostics)

        self._normalization_apply_active = False
        self._normalization_result_state = "CURRENT"
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self.window.set_status("人工归一结果已生成")

    @Slot(str)
    def _on_normalization_apply_failed(self, _message: str) -> None:
        """失败时不覆盖旧正式结果，并解除审核编辑锁定。"""

        if not self._normalization_apply_active:
            return

        self._normalization_apply_active = False
        self._normalization_result_state = "FAILED"
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self.window.set_status("正式词结果重算失败，已保留上一份正式结果")

    @Slot()
    def _on_normalization_apply_thread_finished(self) -> None:
        """清理正式重算专用线程引用，避免与初始分析 Worker 混用。"""

        self.normalization_apply_worker = None
        self.normalization_apply_thread = None

    def _record_normalization_review_change(self) -> None:
        """递增审核 revision；旧正式结果保留但不能再被标记为最新。"""

        self._normalization_revision += 1
        self._normalization_result_state = (
            "STALE"
            if self._analysis_word_results
            else "NOT_GENERATED"
        )
        self._refresh_normalization_result_status()

    def _queue_normalization_decision_persistence(
        self,
        candidate: Mapping[str, Any],
        decision: NormalizationReviewDecision,
    ) -> None:
        """保留人工动作快照并异步写库；内存审核状态绝不因失败回滚。"""

        event_id = str(uuid4())
        self._normalization_decision_events[event_id] = {
            "candidate": deepcopy(dict(candidate)),
            "decision": decision,
            "context_snapshot": self._normalization_context_snapshot(),
            "normalization_revision": self._normalization_revision,
            "candidate_generation": self._normalization_candidate_generation,
        }
        self._submit_normalization_decision_event(event_id)

    def _submit_normalization_decision_event(self, event_id: str) -> None:
        """将单条已确认决定交给既有 AsyncRuntime，Qt 主线程不等待结果。"""

        event = self._normalization_decision_events.get(event_id)
        if event is None or event_id in self._normalization_save_futures:
            return
        if self.normalization_review_service is None:
            self._normalization_persistence_state = "UNSAVED"
            self._refresh_normalization_persistence_status()
            return

        try:
            future = self.runtime.async_runtime.submit(
                self.normalization_review_service.persist_candidate_decision(
                    event["candidate"],
                    event["decision"],
                    event["context_snapshot"],
                    event["normalization_revision"],
                )
            )
        except Exception:
            # Runtime 或数据库启动失败时，人工决定仍然保留在当前内存任务中。
            self._normalization_persistence_state = "UNSAVED"
            self._refresh_normalization_persistence_status()
            return

        self._normalization_save_futures[event_id] = future
        self._normalization_persistence_state = "SAVING"
        self._refresh_normalization_persistence_status()
        future.add_done_callback(
            lambda completed_future, saved_event_id=event_id: self._on_normalization_decision_save_done(
                completed_future,
                saved_event_id,
            )
        )

    def _on_normalization_decision_save_done(
        self,
        future: Future,
        event_id: str,
    ) -> None:
        """在 AsyncRuntime 回调中只判定结果，再用 Signal 回到 Qt 主线程。"""

        try:
            future.result()
        except Exception:
            self.normalization_decision_save_failed.emit(event_id)
            return
        self.normalization_decision_saved.emit(event_id)

    @Slot(str)
    def _on_normalization_decision_saved(self, event_id: str) -> None:
        """移除已持久化事件；其它尚在保存的事件继续维持 SAVING。"""

        event = self._normalization_decision_events.get(event_id)
        self._normalization_save_futures.pop(event_id, None)
        self._normalization_decision_events.pop(event_id, None)
        self._normalization_persistence_state = (
            "SAVING" if self._normalization_save_futures else "CLEAN"
        )
        self._refresh_normalization_persistence_status()
        if event is not None:
            # 仅在 INSERT 真正成功后刷新该候选的只读历史，失败保存绝不伪造历史。
            self._load_normalization_history_references(
                event["candidate_generation"],
                [event["candidate"]],
            )

    @Slot(str)
    def _on_normalization_decision_save_failed(self, event_id: str) -> None:
        """写入失败只标记事件待重试，绝不撤销已经展示的人工决定。"""

        self._normalization_save_futures.pop(event_id, None)
        if event_id in self._normalization_decision_events:
            self._normalization_persistence_state = "SAVE_FAILED"
        elif self._normalization_save_futures:
            self._normalization_persistence_state = "SAVING"
        else:
            self._normalization_persistence_state = "CLEAN"
        self._refresh_normalization_persistence_status()
        self.window.set_status("审核记录保存失败，可重试")

    @Slot()
    def _retry_normalization_decision_persistence(self) -> None:
        """仅重试尚未保存的人工事件，不重放或改变当前候选审核状态。"""

        for event_id in tuple(self._normalization_decision_events):
            self._submit_normalization_decision_event(event_id)

    def _refresh_normalization_persistence_status(self) -> None:
        """由 Controller 统一维护保存状态，View 不观察 Future 或数据库。"""

        if self._normalization_save_futures:
            self._normalization_persistence_state = "SAVING"
        elif self._normalization_decision_events:
            if self._normalization_persistence_state != "SAVE_FAILED":
                self._normalization_persistence_state = "UNSAVED"
        else:
            self._normalization_persistence_state = "CLEAN"
        self.window.set_normalization_persistence_status(
            self._normalization_persistence_state
        )

    def _normalization_context_snapshot(self) -> dict[str, Any]:
        """从真实任务状态构建白名单上下文，绝不接触 Cookie、Header 或 Token。"""

        relation_context = self._relation_query_context or {}
        context: dict[str, Any] = {
            # 当前 relation / reversing 请求固定使用 COM；该值来自真实请求路径。
            "market": "COM",
            "relationQueryMonth": relation_context.get("month"),
            "analysisMonths": list(self._preparation_months),
            "sourceKeywordOrAsin": relation_context.get("asin"),
            "selectedAsins": list(self._preparation_asins),
        }
        if "station" in relation_context:
            context["station"] = relation_context["station"]
        return context

    def _wait_for_normalization_persistence_on_shutdown(self) -> None:
        """仅在退出阶段有限等待在途保存，随后由 ApplicationRuntime 关闭连接池。"""

        deadline = monotonic() + 5.0
        for future in tuple(self._normalization_save_futures.values()):
            remaining_timeout = deadline - monotonic()
            if remaining_timeout <= 0:
                break
            try:
                future.result(timeout=remaining_timeout)
            except Exception:
                # 退出时不能因单条审计写入失败阻塞后续数据库和 Runtime 清理。
                continue

    def _load_normalization_history_references(
        self,
        generation: int,
        candidates: list[Mapping[str, Any]],
    ) -> None:
        """在既有 AsyncRuntime 批量查询历史，不在 Qt 主线程等待结果。"""

        if self.normalization_review_service is None:
            self._normalization_history_state = "LOAD_FAILED"
            self.window.set_normalization_history_load_status("LOAD_FAILED")
            return
        if not candidates:
            self._normalization_history_state = "LOADED"
            self.window.set_normalization_history_load_status("LOADED")
            return

        self._normalization_history_state = "LOADING"
        self.window.set_normalization_history_load_status("LOADING")
        try:
            future = self.runtime.async_runtime.submit(
                self.normalization_review_service.load_history_references(
                    deepcopy(candidates)
                )
            )
        except Exception:
            self._normalization_history_state = "LOAD_FAILED"
            self.window.set_normalization_history_load_status("LOAD_FAILED")
            return

        self._normalization_history_futures.add(future)
        future.add_done_callback(
            lambda completed_future, current_generation=generation: self._on_normalization_history_references_done(
                completed_future,
                current_generation,
            )
        )

    def _on_normalization_history_references_done(
        self,
        future: Future,
        generation: int,
    ) -> None:
        """后台回调只转发结果或失败，Qt 状态更新统一由 Slot 完成。"""

        self._normalization_history_futures.discard(future)
        try:
            references = future.result()
        except Exception:
            self.normalization_history_references_failed.emit(generation)
            return
        self.normalization_history_references_loaded.emit(generation, references)

    @Slot(int, object)
    def _on_normalization_history_references_loaded(
        self,
        generation: int,
        references: object,
    ) -> None:
        """只接纳当前任务 generation 的历史参考，旧任务结果直接丢弃。"""

        if generation != self._normalization_candidate_generation:
            return
        if not isinstance(references, dict):
            self._on_normalization_history_references_failed(generation)
            return
        self._normalization_history_references.update(references)
        self._normalization_history_state = "LOADED"
        self.window.set_normalization_history_references(
            self._normalization_history_references
        )
        self.window.set_normalization_history_load_status("LOADED")

    @Slot(int)
    def _on_normalization_history_references_failed(self, generation: int) -> None:
        """历史参考失败不影响当前候选审核、正式规则或任务成功状态。"""

        if generation != self._normalization_candidate_generation:
            return
        self._normalization_history_state = "LOAD_FAILED"
        self.window.set_normalization_history_load_status("LOAD_FAILED")

    @staticmethod
    def _normalization_decision_count(
        decision: str,
        candidates: list[Mapping[str, Any]],
    ) -> int:
        """按给定候选快照统计审核状态，避免读取运行中的可变内存。"""

        return sum(
            candidate.get("decision", "PENDING") == decision
            for candidate in candidates
        )

    def _refresh_normalization_result_status(self) -> None:
        """由 Controller 统一判定正式结果状态，View 只展示传入文本。"""

        pending_count = self._normalization_decision_count(
            "PENDING",
            self._normalization_candidates,
        )
        skipped_count = self._normalization_decision_count(
            "SKIPPED",
            self._normalization_candidates,
        )
        if self._normalization_result_state == "RUNNING":
            status = "正在按已批准规则重算..."
            mode = (
                "人工归一结果（正在更新）"
                if self._analysis_word_results
                else "未归一预览"
            )
        elif self._normalization_result_state == "CURRENT":
            status = (
                "正式结果已生成 · "
                f"已应用 {self._analysis_word_result_meta.get('approved_rule_count', 0)} 条人工批准规则 · "
                f"仍有 {pending_count} 条待审核、{skipped_count} 条已跳过"
            )
            mode = "人工归一结果"
        elif self._normalization_result_state == "STALE":
            status = "审核内容已变化，需要重新应用；上一份正式结果仍可查看"
            mode = "人工归一结果（规则已变化，需重算）"
        elif self._normalization_result_state == "CONFLICT":
            status = "规则存在冲突，无法应用"
            mode = (
                "人工归一结果（规则存在冲突）"
                if self._analysis_word_results
                else "未归一预览"
            )
        elif self._normalization_result_state == "FAILED":
            status = (
                "重算失败，保留上一份正式结果"
                if self._analysis_word_results
                else "重算失败，尚未生成正式结果"
            )
            mode = (
                "人工归一结果（上次重算失败，需重新计算）"
                if self._analysis_word_results
                else "未归一预览"
            )
        else:
            status = "尚未生成正式结果"
            mode = "未归一预览"

        self.window.set_normalization_result_status(status)
        self.window.set_analysis_result_mode(mode)

    def _find_normalization_candidate(
        self,
        candidate_id: str,
    ) -> dict[str, Any] | None:
        """只在 Controller 的当前任务内存对象中定位候选，避免 View 直接写状态。"""

        for candidate in self._normalization_candidates:
            if candidate.get("id") == candidate_id:
                return candidate
        return None

    @Slot(str)
    def _on_reversing_result_analysis_failed(self, message: str):
        """处理失败时保留 reversing 原始结果，并恢复可继续操作的界面。"""

        self._analysis_processing_active = False
        self._reversing_preparation_status = "failed"
        self.window.set_relation_preparation_running(False)
        self.window.set_status(message)

    @Slot()
    def _on_analysis_thread_finished(self):
        """清除已退出分析线程的 Python 引用，避免后续任务误复用。"""

        self.analysis_worker = None
        self.analysis_thread = None

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

    def _current_preparation_month(self) -> str | None:
        """返回当前月份，防止旧 Future 回调写入错误的月份桶。"""

        if 0 <= self._preparation_month_index < len(
            self._preparation_months
        ):
            return self._preparation_months[
                self._preparation_month_index
            ]

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
