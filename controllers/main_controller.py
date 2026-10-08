from concurrent.futures import Future
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from enum import StrEnum
import logging
from pathlib import Path
from time import monotonic
import threading
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, QThread, Signal, Slot

from infrastructure.application_runtime import ApplicationRuntime
from infrastructure.diagnostics import write_ai_tagging_failure_diagnostic
from infrastructure.update_service import UpdateError, UpdateManifest
from services.analysis_service import AnalysisService
from services.export_service import ExportService
from services.final_analysis_service import FinalAnalysisService
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
from models.normalization_rule import NormalizationRuleType
from models.tagging import TagLabel
from models.tagging_label import (
    TaggingDecisionSource,
    TaggingPipelineResult,
    TaggingPipelineRun,
    TaggingPipelineStatus,
    TaggingReviewItem,
    TaggingReviewStatus,
)
from repositories.normalization_review_repository import (
    NormalizationReviewRepository,
)
from services.ai_service import AiServiceConfigurationError
from services.amazon_product_context_provider import (
    AmazonProductContextProvider,
)
from services.product_knowledge_service import ProductKnowledgeService
from services.tagging_service import TaggingService
from services.word_filter_service import WordFilterService
from workers.analysis_worker import AnalysisWorker
from workers.cookie_worker import CookieWorker
from workers.normalization_apply_worker import NormalizationApplyWorker
from workers.export_worker import ExportWorker


logger = logging.getLogger(__name__)


class AnalysisPipelineState(StrEnum):
    """完整 Analysis Job 的唯一顶层阶段，禁止拆成独立 AI 任务。"""

    IDLE = "IDLE"
    # 关联 ASIN 查询仍可在开始完整任务前由用户完成；保留该阶段是为了
    # 让完整状态机能够准确表达未来由 Pipeline 统一触发查询时的状态。
    FETCHING_RELATION = "FETCHING_RELATION"
    FETCHING_REVERSING = "FETCHING_REVERSING"
    BUILDING_RESULT = "BUILDING_RESULT"
    ANALYZING_WORDS = "ANALYZING_WORDS"
    WAITING_WORD_FILTER = "WAITING_WORD_FILTER"
    DISCOVERING_NORMALIZATION = "DISCOVERING_NORMALIZATION"
    WAITING_NORMALIZATION_REVIEW = "WAITING_NORMALIZATION_REVIEW"
    APPLYING_NORMALIZATION = "APPLYING_NORMALIZATION"
    PREPARING_TAGGING = "PREPARING_TAGGING"
    LOOKING_UP_TAG_CACHE = "LOOKING_UP_TAG_CACHE"
    FETCHING_PRODUCT_CONTEXT = "FETCHING_PRODUCT_CONTEXT"
    FETCHING_AMAZON_CONTEXT = "FETCHING_AMAZON_CONTEXT"
    AI_TAGGING = "AI_TAGGING"
    RETRYING_INCOMPLETE_TAGGING = "RETRYING_INCOMPLETE_TAGGING"
    WAITING_TAGGING_REVIEW = "WAITING_TAGGING_REVIEW"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MainController(QObject):
    """协调主窗口、异步基础设施、Cookie 与关联 ASIN 查询流程。"""

    # 完整 Analysis Job 只使用一条单调递增的 0–100 进度线。五个业务阶段
    # 各占 20%，人工审核暂停时保留当前位置，绝不因切换月份或子阶段回退。
    _PROGRESS_REVERSING_END = 20
    _PROGRESS_ANALYSIS_END = 40
    _PROGRESS_NORMALIZATION_END = 60
    _PROGRESS_TAGGING_END = 80
    _PROGRESS_COMPLETED = 100

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
    tagging_human_review_saved = Signal(int, str, object)
    tagging_human_review_save_failed = Signal(int, str)
    tagging_pipeline_progressed = Signal(int, int, str)
    tagging_pipeline_succeeded = Signal(int, int, int, object)
    tagging_pipeline_failed = Signal(int, int, int, str)
    tagging_retry_progressed = Signal(int, int, str)
    tagging_retry_succeeded = Signal(int, int, object)
    tagging_retry_failed = Signal(int, int, str)
    export_succeeded = Signal(str)
    export_failed = Signal(str)
    update_available = Signal(object)
    update_check_failed = Signal(str)
    update_downloaded = Signal(object, object)
    update_download_failed = Signal(str)

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
        self.final_analysis_service = FinalAnalysisService()
        self.export_service = ExportService()
        self.normalization_candidate_service = (
            NormalizationCandidateService()
        )
        self.word_filter_service = WordFilterService()
        self.normalization_rule_service = NormalizationRuleService()
        database = getattr(runtime, "database", None)
        self.normalization_review_service = (
            NormalizationReviewService(
                NormalizationReviewRepository(database)
            )
            if database is not None
            else None
        )
        # 人工标签保存复用既有正式写入入口；这里不触发产品资料读取或 AI
        # 调用，避免审核阶段重新构造任何外部上下文。
        ai_service = getattr(runtime, "ai_service", None)
        self.tagging_service = (
            TaggingService(database, ai_service, None)
            if database is not None and ai_service is not None
            else None
        )

        # Cookie 获取仍是现有同步 Worker 的职责。
        self.cookie_thread = None
        self.cookie_worker = None
        self.analysis_thread = None
        self.analysis_worker = None
        self.normalization_apply_thread = None
        self.normalization_apply_worker = None
        self.export_thread = None
        self.export_worker = None
        self._export_active = False
        # 仅保存当前 generation 已成功写出的文件，供“打开 Excel”操作使用；
        # 新分析启动后旧路径立即失效，防止用户误打开上一轮结果。
        self._last_exported_path: Path | None = None

        # 更新只在安装包启动时触发。Future 回调运行于 AsyncRuntime，因此一律
        # 通过以下 Qt Signal 回到 Controller，再由 Controller 协调重启。
        self._update_check_future: Future | None = None
        self._update_download_future: Future | None = None
        self._pending_downloaded_update: tuple[
            Path,
            UpdateManifest,
        ] | None = None
        # 每个应用进程只允许检查、下载和启动更新器各一次。即使某个异步
        # 回调被重复投递，也不能再次派生独立更新器去竞争同一安装包。
        self._update_check_started = False
        self._update_download_started = False
        self._update_apply_started = False

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
        # 原始 Word Analysis 永远保留；后续阶段仅使用筛选后的独立快照。
        self._filtered_word_results: dict[str, list[dict[str, Any]]] = {}
        # 每月、每个 ASIN 独立的正式 Word Result。<ASIN>_result 只能读取
        # 该快照，绝不能在 Excel 导出阶段临时从关键词 RESULT 重算。
        self._analysis_asin_word_results: dict[
            str,
            dict[str, list[dict[str, Any]]],
        ] = {}
        self._filtered_asin_word_results: dict[
            str,
            dict[str, list[dict[str, Any]]],
        ] = {}
        self._word_filter_diagnostics: dict[str, Any] = {}
        self._analysis_asin_word_preview_results: dict[
            str,
            dict[str, list[dict[str, Any]]],
        ] = {}
        self._analysis_word_result_meta: dict[str, Any] = {}
        # 这是 Data Tab 与 Excel 唯一共用的最终数据源。它只由正式 Word
        # Result 与本轮 TaggingResult 合成，绝不在展示或导出阶段重算业务。
        self._final_analysis_rows: list[dict[str, Any]] = []
        # 一个 generation 只允许存在一个完整 Analysis Job；归一与标签人工
        # 审核都只是该 Job 的暂停阶段，不能被误当作已完成。
        self._analysis_job_active = False
        self._analysis_pipeline_state = AnalysisPipelineState.IDLE
        # 该值只表示当前完整任务的总进度；View 不自行计算或重置阶段进度。
        self._analysis_progress_value = 0
        # 标签结果按月份物理隔离；分歧只有当前 generation 的运行时工作集。
        self._tagging_results: dict[str, list[TaggingPipelineResult]] = {}
        self._tagging_result_index: dict[
            tuple[str, str],
            TaggingPipelineResult,
        ] = {}
        self._tagging_review_generation = 0
        self._tagging_review_items: dict[str, TaggingReviewItem] = {}
        self._tagging_review_total_count = 0
        self._tagging_review_save_futures: dict[str, Future] = {}
        # AI 打标必须绑定到当前正式词结果 generation，防止旧异步回调污染
        # 后续重新分析或重新应用归一规则得到的新结果。
        self._analysis_generation = 0
        self._tagging_task_generation = 0
        self._tagging_task_active = False
        self._tagging_future: Future | None = None
        self._tagging_pipeline_service: TaggingService | None = None
        # 仅保存固定阶段名称，供未分类异常的安全诊断使用；不能保存 Prompt、
        # Provider 原始响应或任何认证信息。
        self._tagging_pipeline_phase = "未启动"
        # INCOMPLETE 恢复与完整 Analysis Job 分离，但仍严格绑定本轮分析
        # generation；它只持有当前运行时的输入和局部 Provider 结果。
        self._tagging_retry_generation = 0
        self._tagging_retry_active = False
        self._tagging_retry_future: Future | None = None
        self._tagging_statistics: dict[str, int] = self._empty_tagging_statistics()
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
        self.tagging_human_review_saved.connect(
            self._on_tagging_human_review_saved
        )
        self.tagging_human_review_save_failed.connect(
            self._on_tagging_human_review_save_failed
        )
        self.tagging_pipeline_progressed.connect(
            self._on_tagging_pipeline_progressed
        )
        self.tagging_pipeline_succeeded.connect(
            self._on_tagging_pipeline_succeeded
        )
        self.tagging_pipeline_failed.connect(
            self._on_tagging_pipeline_failed
        )
        self.tagging_retry_progressed.connect(
            self._on_tagging_retry_progressed
        )
        self.tagging_retry_succeeded.connect(self._on_tagging_retry_succeeded)
        self.tagging_retry_failed.connect(self._on_tagging_retry_failed)
        self.export_succeeded.connect(self._on_export_succeeded)
        self.export_failed.connect(self._on_export_failed)
        self.update_available.connect(self._on_update_available)
        self.update_check_failed.connect(self._on_update_check_failed)
        self.update_downloaded.connect(self._on_update_downloaded)
        self.update_download_failed.connect(self._on_update_download_failed)

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
        self.window.normalization_review_finish_requested.connect(
            self._finish_normalization_review
        )
        if hasattr(self.window, "normalization_review_close_requested"):
            self.window.normalization_review_close_requested.connect(
                self._request_close_normalization_review
            )
        self.window.normalization_persistence_retry_requested.connect(
            self._retry_normalization_decision_persistence
        )
        if hasattr(self.window, "open_export_requested"):
            self.window.open_export_requested.connect(self._open_latest_export)
        if hasattr(self.window, "incomplete_tagging_retry_requested"):
            self.window.incomplete_tagging_retry_requested.connect(
                self._retry_incomplete_tagging
            )
        if hasattr(self.window, "analysis_cancel_requested"):
            self.window.analysis_cancel_requested.connect(
                self._cancel_analysis_job
            )
        # 旧的纯 Controller 单测使用最小 View stub；正式 MainWindow 一定
        # 提供以下信号，兼容分层测试时不应强迫 stub 伪造 UI 功能。
        if hasattr(self.window, "tagging_human_review_requested"):
            self.window.tagging_human_review_requested.connect(
                self._save_tagging_human_review
            )
            self.window.tagging_review_close_requested.connect(
                self._request_close_tagging_review
            )
            self.window.tagging_review_discard_requested.connect(
                self._clear_tagging_review_state
            )
        self._set_analysis_pipeline_state(AnalysisPipelineState.IDLE)

    def start(self):
        """并行启动 PostgreSQL 基础设施与 Cookie 获取流程。"""

        self._start_database()
        self._start_auto_update_check()
        self._start_cookie_task()

    def _set_analysis_pipeline_state(
        self,
        state: AnalysisPipelineState,
        failure_reason: str | None = None,
    ) -> None:
        """统一更新完整任务状态；顶部只展示这一份业务阶段。"""

        self._analysis_pipeline_state = state
        display_names = {
            AnalysisPipelineState.IDLE: "未开始",
            AnalysisPipelineState.FETCHING_RELATION: "正在获取关联数据",
            AnalysisPipelineState.FETCHING_REVERSING: "正在获取分析数据",
            AnalysisPipelineState.BUILDING_RESULT: "正在构建月度结果",
            AnalysisPipelineState.ANALYZING_WORDS: "正在分析关键词",
            AnalysisPipelineState.WAITING_WORD_FILTER: "等待词筛选",
            AnalysisPipelineState.DISCOVERING_NORMALIZATION: "正在发现归一候选",
            AnalysisPipelineState.WAITING_NORMALIZATION_REVIEW: "等待归一审核",
            AnalysisPipelineState.APPLYING_NORMALIZATION: "正在应用归一规则",
            AnalysisPipelineState.PREPARING_TAGGING: "正在准备 AI 打标",
            AnalysisPipelineState.LOOKING_UP_TAG_CACHE: "正在查询历史标签缓存",
            AnalysisPipelineState.FETCHING_PRODUCT_CONTEXT: "正在准备商品背景",
            AnalysisPipelineState.FETCHING_AMAZON_CONTEXT: "正在获取 Amazon 商品背景",
            AnalysisPipelineState.AI_TAGGING: "正在进行 AI 打标",
            AnalysisPipelineState.RETRYING_INCOMPLETE_TAGGING: "正在重试 AI 失败项",
            AnalysisPipelineState.WAITING_TAGGING_REVIEW: "等待 AI 分歧审核",
            AnalysisPipelineState.FINALIZING: "正在整理最终结果",
            AnalysisPipelineState.COMPLETED: "分析完成",
            AnalysisPipelineState.FAILED: "分析失败",
            AnalysisPipelineState.CANCELLED: "分析已取消",
        }
        display_name = display_names[state]
        if state == AnalysisPipelineState.FAILED and failure_reason:
            display_name = f"分析失败：{failure_reason}"
        self.window.set_analysis_pipeline_status(display_name)

    def _log_analysis_pipeline_debug(self, message: str) -> None:
        """记录无敏感数据的线程诊断，便于定位 Pipeline 阶段停滞。"""

        logger.debug(
            "[AnalysisPipeline] %s thread=%s ident=%s generation=%s",
            message,
            threading.current_thread().name,
            threading.get_ident(),
            self._analysis_generation,
        )

    def _worker_analysis_generation(
        self,
        explicit_generation: int | None,
    ) -> int | None:
        """从直接连接的 Worker 读取 generation，避免 lambda 跨线程转发。"""

        if explicit_generation is not None:
            return explicit_generation
        sender = self.sender()
        generation = getattr(sender, "analysis_generation", None)
        return generation if isinstance(generation, int) else None

    def _reset_analysis_progress(self) -> None:
        """仅在启动新的完整 Analysis Job 时将总进度归零。"""

        self._analysis_progress_value = 0
        self.window.set_analysis_progress(0)

    def _advance_analysis_progress(
        self,
        target_value: int,
    ) -> None:
        """单调推进当前任务进度，禁止任一子流程把总进度拉回。"""

        if not self._analysis_job_active:
            return

        bounded_value = max(0, min(int(target_value), self._PROGRESS_COMPLETED))
        self._analysis_progress_value = max(
            self._analysis_progress_value,
            bounded_value,
        )
        self.window.set_analysis_progress(self._analysis_progress_value)

    def _update_reversing_progress(self, completed_task_count: int) -> None:
        """按“月份 × ASIN”总任务数累计 reversing 阶段的 0–20% 进度。"""

        total_task_count = (
            len(self._preparation_months) * len(self._preparation_asins)
        )
        if total_task_count <= 0:
            return

        bounded_completed_count = max(
            0,
            min(completed_task_count, total_task_count),
        )
        progress_value = int(
            bounded_completed_count
            * self._PROGRESS_REVERSING_END
            / total_task_count
        )
        self._advance_analysis_progress(progress_value)

    def _complete_analysis_job(self) -> None:
        """在无待处理人工审核时结束唯一 Analysis Job。"""

        if not self._analysis_job_active:
            return
        self._set_analysis_pipeline_state(AnalysisPipelineState.FINALIZING)
        self._advance_analysis_progress(self._PROGRESS_TAGGING_END)
        self._build_final_analysis_rows()
        self._advance_analysis_progress(self._PROGRESS_COMPLETED)
        self._analysis_job_active = False
        self.window.set_analysis_job_running(False)
        self.window.set_export_enabled(False)
        self._refresh_tagging_result_presentation()
        self._set_analysis_pipeline_state(AnalysisPipelineState.COMPLETED)
        incomplete_count = self._tagging_statistics["incomplete"]
        if incomplete_count:
            self.window.set_status(f"分析完成（{incomplete_count} 条未完成）")
        else:
            self.window.set_status("分析完成")
        self._auto_export_final_analysis()

    def _build_final_analysis_rows(self) -> None:
        """在 FINALIZING 阶段建立唯一 Final Analysis Dataset 并刷新 Data Tab。"""

        self._final_analysis_rows = self.final_analysis_service.build_final_analysis_rows(
            self._filtered_word_results,
            self._tagging_result_index,
        )
        self.window.set_final_analysis_rows(self._final_analysis_rows)
        self.window.set_analysis_result_mode("正式结果")

    def _clear_final_analysis_rows(self) -> None:
        """使旧 generation 的最终结果失效，防止 Data Tab 或导出混入旧数据。"""

        self._final_analysis_rows = []
        self._last_exported_path = None
        if hasattr(self.window, "clear_final_analysis_rows"):
            self.window.clear_final_analysis_rows()
        self.window.set_export_enabled(False)
        if hasattr(self.window, "set_incomplete_tagging_retry_available"):
            self.window.set_incomplete_tagging_retry_available(0)

    @Slot()
    def _auto_export_final_analysis(self) -> None:
        """完成分析后自动保存 Excel；首次仅询问一次默认导出目录。"""

        if self._export_active:
            # 开始下一轮分析前会阻止用户继续，正常情况下不会进入该分支；保留
            # 防御判断，避免两个 ExportWorker 争用同一份 Controller 状态。
            self.window.set_status("正在保存上一轮 Excel，暂不能启动新的导出")
            return
        if not self._final_analysis_rows:
            self.window.set_status("分析完成，但没有可自动保存的最终结果")
            self.window.set_export_enabled(False)
            return

        export_directory = self.window.ensure_export_directory()
        if not export_directory:
            self.window.set_status("分析完成；未设置默认导出文件夹，本次未保存 Excel")
            self.window.set_export_enabled(False)
            return

        category = self.window.analysis_category_display_name()
        category_slug = "_".join(category.lower().split())
        # 微秒级时间戳保证短时间内连续完成的任务也不会覆盖彼此的正式结果。
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = Path(export_directory) / (
            f"flow_analysis_{category_slug}_{timestamp}.xlsx"
        )
        if not output_path.parent.is_dir():
            self.window.set_status("默认导出文件夹不可用，本次未保存 Excel")
            self.window.set_export_enabled(False)
            return

        self._export_active = True
        self.window.set_export_enabled(False)
        self.window.set_status("分析完成，正在自动保存 Excel...")
        # 导出期间禁止启动新分析，因此运行时结果不会再变。仅复制容器结构，
        # 避免深拷贝全部 RAW/词表记录造成数 GB 的额外内存与主线程停顿。
        # ExportWorker 只读这些快照，不会触发任何 SellerSprite、Amazon、AI 或数据库访问。
        export_rows = tuple(self._final_analysis_rows)
        export_raw = {
            month: tuple(month_rows)
            for month, month_rows in self._analysis_raw.items()
        }
        export_reversing = {
            month: dict(month_results)
            for month, month_results in self._reversing_results.items()
        }
        export_word_results = {
            month: tuple(month_rows)
            for month, month_rows in self._filtered_word_results.items()
        }
        export_asin_word_results = {
            month: {
                asin: tuple(asin_rows)
                for asin, asin_rows in month_results.items()
            }
            for month, month_results in self._filtered_asin_word_results.items()
        }
        self.export_thread = QThread()
        self.export_worker = ExportWorker(
            self.export_service,
            export_rows,
            output_path,
            export_raw,
            export_reversing,
            export_word_results,
            export_asin_word_results,
        )
        self.export_worker.moveToThread(self.export_thread)
        self.export_thread.started.connect(self.export_worker.run)
        self.export_worker.succeeded.connect(self.export_succeeded)
        self.export_worker.failed.connect(self.export_failed)
        self.export_worker.progress.connect(self._on_export_progress)
        self.export_worker.finished.connect(self.export_thread.quit)
        self.export_worker.finished.connect(self.export_worker.deleteLater)
        self.export_thread.finished.connect(self.export_thread.deleteLater)
        self.export_thread.finished.connect(self._on_export_thread_finished)
        self.export_thread.start()

    @Slot(str)
    def _on_export_progress(self, message: str) -> None:
        """在保存大文件时持续说明当前阶段，不把导出误认为界面卡死。"""

        if self._export_active:
            self.window.set_status(f"分析完成，{message}")

    @Slot()
    def _open_latest_export(self) -> None:
        """仅打开本轮自动保存成功的 Excel，不再提供手动另存为入口。"""

        output_path = self._last_exported_path
        if output_path is None or not output_path.is_file():
            self._last_exported_path = None
            self.window.set_export_enabled(False)
            self.window.set_status("暂无可打开的 Excel 文件")
            return

        if self.window.open_exported_file(str(output_path)):
            self.window.set_status(f"已打开 Excel：{output_path.name}")
            return

        self.window.set_status("无法使用系统默认程序打开 Excel 文件")

    @Slot(str)
    def _on_export_succeeded(self, output_path: str) -> None:
        """导出完成后仅恢复按钮与状态，不修改当前 Final Dataset。"""

        self._export_active = False
        self._last_exported_path = Path(output_path)
        self.window.set_export_enabled(True)
        self.window.set_status(f"Excel 已自动保存：{output_path}")
        self._apply_downloaded_update_if_safe()

    @Slot(str)
    def _on_export_failed(self, message: str) -> None:
        """文件错误只报告导出失败，绝不破坏已完成的分析结果。"""

        self._export_active = False
        self._last_exported_path = None
        self.window.set_export_enabled(False)
        self.window.set_status(f"Excel 自动保存失败：{message}")

    @Slot()
    def _on_export_thread_finished(self) -> None:
        """释放一次性文件导出线程引用，避免长期占用 QThread 对象。"""

        self.export_worker = None
        self.export_thread = None

    def _fail_analysis_job(self, message: str) -> None:
        """失败必须结束当前 job 并恢复唯一启动入口，不伪造完成结果。"""

        self._analysis_job_active = False
        self.window.set_analysis_job_running(False)
        self._set_analysis_pipeline_state(
            AnalysisPipelineState.FAILED,
            message,
        )
        self.window.set_status(message)

    @Slot()
    def _cancel_analysis_job(self) -> None:
        """取消当前完整流水线，包括后台 I/O 与尚未完成的人工审核。"""

        if not self._analysis_job_active:
            return
        if self._tagging_review_items and not self.window.confirm_discard_tagging_review(
            len(self._tagging_review_items)
        ):
            return
        if self._normalization_candidates and not self.window.confirm_finish_normalization_review(
            pending_count=self._normalization_decision_count(
                "PENDING", self._normalization_candidates
            ),
            skipped_count=self._normalization_decision_count(
                "SKIPPED", self._normalization_candidates
            ),
            saving_count=len(self._normalization_save_futures),
            unsaved_count=len(self._normalization_decision_events),
        ):
            return

        # generation 前移使晚到的 QThread/Future 回调无法污染下一次任务。
        self._analysis_generation += 1
        self._reversing_preparation_active = False
        self._analysis_processing_active = False
        self._normalization_apply_active = False
        if self.reversing_preparation_future is not None:
            self.reversing_preparation_future.cancel()
        self._clear_runtime_tagging_results()
        self._clear_normalization_review_state()
        self._analysis_job_active = False
        self.window.set_relation_preparation_running(False)
        self.window.set_analysis_job_running(False)
        self.window.set_export_enabled(False)
        self._set_analysis_pipeline_state(AnalysisPipelineState.CANCELLED)

    def shutdown(self) -> None:
        """在程序退出阶段取消业务任务并关闭 SellerSprite API Client。"""

        # 用户主动退出时不再继续后台下载；ApplicationRuntime 随后关闭更新
        # 客户端，避免下载任务在已关闭 EventLoop 上继续运行。
        if self._update_check_future is not None:
            self._update_check_future.cancel()
        if self._update_download_future is not None:
            self._update_download_future.cancel()

        # 退出阶段先请求停止正在运行的打标任务；此处允许有限同步等待，确保
        # Windows Proactor bridge 在 AiService、数据库池和 AsyncRuntime 前结束。
        self._cancel_tagging_pipeline_for_shutdown()
        # 审核记录属于用户已经确认的操作。退出阶段允许有限同步等待，尽量让
        # 已提交到现有 AsyncRuntime 的 INSERT 在数据库连接池关闭前完成。
        self._wait_for_normalization_persistence_on_shutdown()
        self._clear_normalization_review_state()
        # 主窗口 closeEvent 已在用户确认后发出销毁信号；这里作为应用级
        # 退出兜底，确保 ProductContext 与 Provider 结果不残留在内存。
        self._clear_tagging_review_state()

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

    def _cancel_tagging_pipeline_for_shutdown(self) -> None:
        """退出阶段取消 Tagging，并等待可选 Playwright bridge 的清理。"""

        service = self._tagging_pipeline_service
        if service is not None:
            service.cancel_current_task()
        if self._tagging_future is not None:
            self._tagging_future.cancel()
        if self._tagging_retry_future is not None:
            self._tagging_retry_future.cancel()
        self._tagging_task_active = False
        self._tagging_retry_active = False
        self._tagging_retry_generation += 1

        if service is None:
            return
        try:
            cleanup_future = self.runtime.async_runtime.submit(
                service.wait_for_external_cleanup(5.0)
            )
            # 程序退出阶段允许有限等待，正常 UI 流程从不调用 Future.result()。
            cleanup_future.result(timeout=6.0)
        except Exception:
            # 后续 Runtime.shutdown 仍会关闭 AiService、连接池和 EventLoop。
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

    def _start_auto_update_check(self) -> None:
        """安装包启动后后台检查签名更新清单；源码开发不触发自动替换。"""

        import sys

        if self._update_check_started or not getattr(sys, "frozen", False):
            return
        update_service = getattr(self.runtime, "update_service", None)
        if update_service is None or not update_service.is_enabled:
            return

        self._update_check_started = True
        try:
            future = self.runtime.async_runtime.submit(
                update_service.check_for_update()
            )
        except Exception:
            logger.warning("自动更新检查未能启动", exc_info=True)
            return

        self._update_check_future = future
        future.add_done_callback(self._on_auto_update_check_done)

    def _on_auto_update_check_done(self, future: Future) -> None:
        """将已完成的检查结果转回 Qt 主线程；失败不影响正常启动。"""

        if future.cancelled():
            return
        try:
            manifest = future.result()
        except Exception as exc:
            logger.warning("自动更新检查失败：%s", exc)
            self.update_check_failed.emit("更新检查失败，已继续启动")
            return
        if manifest is not None:
            self.update_available.emit(manifest)

    def _allowed_normalized_words_by_month(self, approved_rules) -> dict[str, set[str]]:
        """将已筛选词映射到本轮人工批准后的输出词，防止重算词回流。"""

        allowed_by_month: dict[str, set[str]] = {}
        word_mapping = {
            variant: rule.canonical
            for rule in approved_rules.rules
            if rule.rule_type == NormalizationRuleType.WORD
            for variant in rule.variants
        }
        phrase_rules = [
            rule
            for rule in approved_rules.rules
            if rule.rule_type == NormalizationRuleType.PHRASE
        ]
        for month, rows in self._filtered_word_results.items():
            selected_words = {
                str(row.get("word") or "").strip()
                for row in rows
                if str(row.get("word") or "").strip()
            }
            output_words = {
                word_mapping.get(word, word) for word in selected_words
            }
            # 只有一个已批准短语的全部组成词均通过当前筛选时，才允许该
            # phrase canonical 进入筛选后的正式结果。
            for rule in phrase_rules:
                for variant in rule.variants:
                    tokens = variant.split()
                    if tokens and all(token in selected_words for token in tokens):
                        output_words.add(rule.canonical)
            allowed_by_month[month] = output_words
        return allowed_by_month

    @Slot(object)
    def _on_update_available(self, manifest: UpdateManifest) -> None:
        """发现新版后立即后台下载，下载完成才会请求安全重启。"""

        if self._update_download_started:
            return
        update_service = getattr(self.runtime, "update_service", None)
        if update_service is None:
            return
        self._update_download_started = True
        self.window.set_status(
            f"发现新版本 {manifest.version}，正在后台下载更新..."
        )
        try:
            future = self.runtime.async_runtime.submit(
                update_service.download_update(manifest)
            )
        except Exception:
            logger.warning("自动更新下载未能启动", exc_info=True)
            self.update_download_failed.emit("更新下载未能启动")
            return

        self._update_download_future = future
        future.add_done_callback(
            lambda completed_future: self._on_auto_update_download_done(
                completed_future,
                manifest,
            )
        )

    def _on_auto_update_download_done(
        self,
        future: Future,
        manifest: UpdateManifest,
    ) -> None:
        """校验下载结果后才通知主线程重启，绝不信任未校验的文件。"""

        if future.cancelled():
            return
        try:
            installer_path = future.result()
        except Exception as exc:
            logger.warning("自动更新下载失败：%s", exc)
            self.update_download_failed.emit("更新下载失败，已继续启动")
            return
        self.update_downloaded.emit(installer_path, manifest)

    @Slot(str)
    def _on_update_check_failed(self, message: str) -> None:
        """更新不可用不能阻塞业务初始化，只保留简短可见状态。"""

        self.window.set_status(message)

    @Slot(object, object)
    def _on_update_downloaded(
        self,
        installer_path: Path,
        manifest: UpdateManifest,
    ) -> None:
        """记录完整更新包；若当前没有业务任务则立即交给独立更新器。"""

        self._pending_downloaded_update = (Path(installer_path), manifest)
        self._apply_downloaded_update_if_safe()

    @Slot(str)
    def _on_update_download_failed(self, message: str) -> None:
        """下载失败仅报告更新状态，不能把应用启动判定为失败。"""

        self.window.set_status(message)

    def _apply_downloaded_update_if_safe(self) -> None:
        """只在没有分析、审核或 Excel 写入时退出，保护用户当前工作。"""

        if self._update_apply_started:
            return
        pending_update = self._pending_downloaded_update
        if pending_update is None:
            return
        if (
            self._analysis_job_active
            or self._reversing_preparation_active
            or self._export_active
            or self._normalization_apply_active
            or self._tagging_task_active
            or self._tagging_retry_active
        ):
            self.window.set_status("新版已下载，当前任务完成后将自动更新")
            return

        installer_path, manifest = pending_update
        update_service = getattr(self.runtime, "update_service", None)
        if update_service is None:
            return
        self._update_apply_started = True
        try:
            update_service.launch_updater(
                installer_path,
                manifest.package_sha256,
            )
        except UpdateError as exc:
            self._update_apply_started = False
            logger.warning("自动更新器启动失败：%s", exc)
            self.window.set_status("新版已下载，但自动重启失败")
            return

        self._pending_downloaded_update = None
        self.window.set_status("新版已下载，正在启动安装程序...")
        if hasattr(self.window, "show_update_installation_progress"):
            self.window.show_update_installation_progress(manifest.version)
        if hasattr(self.window, "request_application_exit"):
            self.window.request_application_exit()

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
        # 首次启动同样会得到一次 Cookie ready 信号。只有 reversing 预处理
        # 实际运行时，Cookie 变化才需要终止当前数据准备；否则不能把空闲
        # 初始状态误标记为“分析失败”。
        if self._reversing_preparation_active:
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

        self._log_analysis_pipeline_debug("START clicked")

        if self._analysis_job_active:
            self.window.set_status("当前分析任务尚未结束")
            return
        if self._export_active:
            # 自动导出使用独立 QThread，不会卡 UI；但下一轮若立即清空正式
            # Dataset，会让上轮“完成即保存”失去确定的结果归属。
            self.window.set_status("正在自动保存上一轮 Excel，请稍候")
            return
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

        # 新任务不能静默抹掉上一轮未确认的 AI 分歧；用户明确确认后才继续。
        if self._tagging_review_items and not self.window.confirm_discard_tagging_review(
            len(self._tagging_review_items),
            for_new_generation=True,
        ):
            return

        # 每次新的“开始分析”任务建立独立内存结果；手动重试则不会经过
        # 此入口，因此已经成功的 ASIN 结果不会被清空或重新处理。
        # 预先按 UI 已固定的“最新到最旧”顺序建立月份桶，彻底阻断
        # 不同月份之间的隐式聚合。
        self._analysis_generation += 1
        self._analysis_job_active = True
        self.window.set_analysis_job_running(True)
        self._reset_analysis_progress()
        self.window.set_export_enabled(False)
        self._set_analysis_pipeline_state(
            AnalysisPipelineState.FETCHING_REVERSING
        )
        self._clear_runtime_tagging_results()
        self._reversing_results = {
            month: {} for month in analysis_months
        }
        self._analysis_raw = {}
        self._analysis_results = {}
        self._analysis_word_preview_results = {}
        self._analysis_word_results = {}
        self._filtered_word_results = {}
        self._analysis_asin_word_preview_results = {}
        self._analysis_asin_word_results = {}
        self._filtered_asin_word_results = {}
        self._word_filter_diagnostics = {}
        self._analysis_word_result_meta = {}
        # _clear_runtime_tagging_results 已在上方清空旧 Final Dataset；本轮
        # Data Tab 只会在最终阶段接收新的统一结果，绝不展示中间草稿。
        self._clear_normalization_review_state()
        self._analysis_diagnostics = {}
        # 旧任务已明确触发的审计写入仍可能在 AsyncRuntime 中执行，不能因为
        # 用户启动新分析就丢弃其跟踪或取消 INSERT。
        self._preparation_asins = asins
        self._preparation_months = list(analysis_months)
        self._preparation_month_index = 0
        self._preparation_index = 0
        self._reversing_preparation_active = True
        self._reversing_preparation_status = "running"

        self.window.set_relation_preparation_running(True)
        self._log_analysis_pipeline_debug("before reversing")
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
        completed_task_count = (
            self._preparation_month_index * len(self._preparation_asins)
            + self._preparation_index
        )
        self._update_reversing_progress(completed_task_count)

        try:
            self._log_analysis_pipeline_debug("before reversing submit")
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
        self._log_analysis_pipeline_debug("reversing submitted")
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

        self._log_analysis_pipeline_debug("reversing callback")

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
        self._log_analysis_pipeline_debug("reversing succeeded in Qt slot")
        self._reversing_results[month][asin] = data
        completed_task_count = (
            self._preparation_month_index * len(self._preparation_asins)
            + self._preparation_index
            + 1
        )
        self._update_reversing_progress(completed_task_count)
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

        self._reversing_preparation_active = False
        self._reversing_preparation_status = "processing"
        self.reversing_preparation_future = None
        self._start_reversing_result_analysis()

    def _start_reversing_result_analysis(self):
        """用现有 QThread 执行纯 Python 聚合，避免在 Qt 主线程循环大量 RAW。"""

        if self._analysis_processing_active:
            return

        if not self._preparation_months:
            self._stop_reversing_preparation("数据处理失败")
            return

        self._analysis_processing_active = True
        self._set_analysis_pipeline_state(AnalysisPipelineState.BUILDING_RESULT)
        self.analysis_thread = QThread()
        analysis_generation = self._analysis_generation
        self.analysis_worker = AnalysisWorker(
            self.analysis_service,
            self._reversing_results,
            self._preparation_months,
            analysis_generation=analysis_generation,
        )
        self.analysis_worker.moveToThread(self.analysis_thread)

        self.analysis_thread.started.connect(self.analysis_worker.run)
        # 直接连接 QObject Slot：跨线程时 Qt 会使用 QueuedConnection，把
        # Controller / View 更新排回 Qt 主线程。绝不能经 Python lambda
        # 直接跨线程调用 Controller。
        self.analysis_worker.stage_changed.connect(
            self._on_analysis_worker_stage_changed
        )
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
        self._log_analysis_pipeline_debug("analysis worker submitted")
        self.analysis_thread.start()

    @Slot(str)
    def _on_analysis_worker_stage_changed(
        self,
        stage: str,
        analysis_generation: int | None = None,
    ) -> None:
        """只接纳当前 generation 的纯数据处理阶段提示。"""

        analysis_generation = self._worker_analysis_generation(
            analysis_generation
        )
        self._log_analysis_pipeline_debug(f"worker stage={stage}")
        if (
            not self._analysis_processing_active
            or (
                analysis_generation is not None
                and analysis_generation != self._analysis_generation
            )
        ):
            return
        state_by_stage = {
            "BUILDING_RESULT": AnalysisPipelineState.BUILDING_RESULT,
            "ANALYZING_WORDS": AnalysisPipelineState.ANALYZING_WORDS,
            "DISCOVERING_NORMALIZATION": AnalysisPipelineState.DISCOVERING_NORMALIZATION,
        }
        progress_by_stage = {
            "BUILDING_RESULT": self._PROGRESS_REVERSING_END,
            "ANALYZING_WORDS": 27,
            "DISCOVERING_NORMALIZATION": 34,
        }
        state = state_by_stage.get(stage)
        if state is not None:
            self._set_analysis_pipeline_state(state)
            self._advance_analysis_progress(progress_by_stage[stage])

    @Slot(object)
    def _on_reversing_result_analysis_succeeded(
        self,
        processed_data: dict[str, Any],
        analysis_generation: int | None = None,
    ):
        """保存后台生成的 RAW、正式词结果、候选与诊断信息。"""

        analysis_generation = self._worker_analysis_generation(
            analysis_generation
        )
        self._log_analysis_pipeline_debug("worker processed callback")
        if (
            not self._analysis_processing_active
            or (
                analysis_generation is not None
                and analysis_generation != self._analysis_generation
            )
        ):
            return

        self._analysis_raw = processed_data["raw"]
        self._analysis_results = processed_data["results"]
        self._analysis_word_preview_results = processed_data[
            "word_preview_results"
        ]
        self._analysis_asin_word_preview_results = processed_data[
            "asin_word_preview_results"
        ]
        # Word Analysis 的原始结果必须完整保留。后续筛选、归一、AI 与导出
        # 都从独立 _filtered_word_results 快照继续，绝不删改此原始结果。
        self._clear_runtime_tagging_results()
        self._analysis_word_results = deepcopy(
            self._analysis_word_preview_results
        )
        self._analysis_asin_word_results = deepcopy(
            self._analysis_asin_word_preview_results
        )
        self._filtered_word_results = {}
        self._filtered_asin_word_results = {}
        self._word_filter_diagnostics = {}
        self._analysis_word_result_meta = {}
        self._clear_normalization_review_state()
        self._analysis_diagnostics = processed_data["diagnostics"]
        self._advance_analysis_progress(self._PROGRESS_ANALYSIS_END)
        self._normalization_result_state = "NOT_GENERATED"
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self._analysis_processing_active = False
        self._reversing_preparation_status = "completed"
        self.window.set_relation_preparation_running(False)
        self.window.show_word_filter(
            self._analysis_word_results,
            total_count=sum(
                len(rows) for rows in self._analysis_word_results.values()
            ),
        )
        # 词筛选没有额外确认按钮：用户在开始分析前配置的范围会在此处
        # 自动冻结并立即作用于归一、AI 与导出链路。
        self._apply_word_filter()

    @Slot()
    def _apply_word_filter(self) -> None:
        """冻结当前 Word Filter，并只让通过的词进入后续流水线。"""

        if not self._analysis_job_active or not self._analysis_word_results:
            self.window.set_status("当前没有可筛选的词频分析结果")
            return
        if self._normalization_apply_active or self._tagging_task_active:
            self.window.set_status("当前分析正在继续，暂不能修改词筛选")
            return

        try:
            conditions = self.window.word_filter_conditions()
            filtered_results, diagnostics = (
                self.word_filter_service.filter_monthly_word_results(
                    self._analysis_word_results,
                    conditions,
                )
            )
        except ValueError as error:
            self.window.set_status(str(error))
            return

        self._clear_runtime_tagging_results()
        self._clear_normalization_review_state()
        if hasattr(self.window, "close_normalization_review_after_discard"):
            self.window.close_normalization_review_after_discard()
        self._filtered_word_results = filtered_results
        self._filtered_asin_word_results = (
            self.word_filter_service.filter_monthly_asin_word_results(
                self._analysis_asin_word_results,
                filtered_results,
            )
        )
        self._word_filter_diagnostics = diagnostics
        self.window.set_word_filter_counts(
            diagnostics["total"],
            diagnostics["filtered"],
        )

        # Candidate Discovery 仍需关键词 RESULT 的完整 evidence，但会严格按
        # 当前月已通过的词白名单裁剪，避免被筛掉的词回流到审核工作集。
        allowed_words_by_month = {
            month: {
                str(row.get("word") or "").strip().lower()
                for row in rows
                if str(row.get("word") or "").strip()
            }
            for month, rows in filtered_results.items()
        }
        self._set_analysis_pipeline_state(
            AnalysisPipelineState.DISCOVERING_NORMALIZATION
        )
        self._normalization_candidates = (
            self.normalization_candidate_service.discover_candidates(
                self._analysis_results,
                allowed_words_by_month=allowed_words_by_month,
            )
        )
        self.window.set_normalization_candidates(
            self._normalization_candidates
        )
        self._normalization_history_references = {}
        self.window.set_normalization_history_references({})
        if self._normalization_candidates:
            self._load_normalization_history_references(
                self._normalization_candidate_generation,
                self._normalization_candidates,
            )
            self.window.show_normalization_review()
            self._set_analysis_pipeline_state(
                AnalysisPipelineState.WAITING_NORMALIZATION_REVIEW
            )
            self.window.set_status(
                f"词筛选完成：{diagnostics['filtered']} / {diagnostics['total']}，请审核归一候选"
            )
            return

        # 没有候选时，筛选后的基础词结果就是唯一可继续的正式输入。
        self._analysis_word_result_meta = {
            "applied_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "approved_rule_count": 0,
            "months": list(self._preparation_months),
            "word_filter": deepcopy(diagnostics),
        }
        self._normalization_result_state = "CURRENT"
        self._refresh_normalization_result_status()
        self._start_tagging_pipeline()

    def _clear_normalization_review_state(self) -> None:
        """销毁当前任务的候选工作集，不影响已经生成的正式词结果。"""

        # generation 前移后，旧异步历史读取即使晚到也会被 Slot 丢弃。
        self._normalization_candidate_generation += 1
        for future in tuple(self._normalization_history_futures):
            future.cancel()
        self._normalization_history_futures.clear()
        self._normalization_candidates = []
        self._normalization_history_references = {}
        self._normalization_history_state = "IDLE"
        self._normalization_revision = 0
        self._normalization_apply_context = {}
        if not self._normalization_apply_active:
            self._normalization_result_state = (
                "CURRENT"
                if self._filtered_word_results
                else "NOT_GENERATED"
            )

        # View 会在一个入口中清掉 candidate 索引、筛选选中项、冲突索引与
        # 已展开 evidence，避免旧 generation 残留在 Dialog 内存。
        self.window.set_normalization_candidates([])
        self.window.set_normalization_history_references({})
        self.window.set_normalization_history_load_status("IDLE")
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self._refresh_normalization_persistence_status()

    def begin_tagging_review_generation(self) -> int | None:
        """开始新的标签 generation，并阻止其静默覆盖旧的人工分歧。"""

        if self._tagging_review_items:
            if not self.window.confirm_discard_tagging_review(
                len(self._tagging_review_items),
                for_new_generation=True,
            ):
                return None
            self._clear_tagging_review_state()
        self._tagging_review_generation += 1
        self._tagging_results = {}
        self._tagging_result_index = {}
        return self._tagging_review_generation

    def _start_tagging_pipeline(self) -> None:
        """作为 Analysis Job 自动阶段，从当前正式词结果启动 Tagging。"""

        if self._tagging_task_active:
            return
        if not self._analysis_job_active:
            return
        if (
            self._normalization_result_state != "CURRENT"
            or not self._filtered_word_results
        ):
            self._fail_analysis_job("正式词结果不可用，无法继续 AI 打标")
            return

        self._advance_analysis_progress(self._PROGRESS_NORMALIZATION_END)

        try:
            category_key = self.window.tagging_category_key()
        except (AttributeError, ValueError):
            self._fail_analysis_job("当前打标品类无效")
            return

        ai_service = getattr(self.runtime, "ai_service", None)
        database = getattr(self.runtime, "database", None)
        if ai_service is None or database is None:
            self._fail_analysis_job("AI 打标基础设施尚未就绪")
            return

        # 必须先校验 UniAPI，再构造产品资料服务或启动 Amazon 浏览器。
        try:
            ai_service.validate_tagging_configuration()
        except AiServiceConfigurationError:
            self._fail_analysis_job("AI 配置未完成")
            return
        except Exception:
            self._fail_analysis_job("AI 配置校验失败")
            return

        try:
            product_knowledge_service = ProductKnowledgeService()
        except Exception:
            self._fail_analysis_job("产品资料不可用，无法继续 AI 打标")
            return

        review_generation = self.begin_tagging_review_generation()
        if review_generation is None:
            return

        # 深拷贝确保任务输入与此后任何 UI/审核状态变化物理隔离。
        word_result_snapshot = deepcopy(self._filtered_word_results)
        analysis_generation = self._analysis_generation
        self._tagging_task_generation += 1
        tagging_generation = self._tagging_task_generation
        self._tagging_pipeline_service = TaggingService(
            database,
            ai_service,
            product_knowledge_service,
            AmazonProductContextProvider(),
        )
        # 人工审核必须复用本轮正式 Service，保证后续写入使用同一数据库边界。
        self.tagging_service = self._tagging_pipeline_service
        self._tagging_task_active = True
        self._tagging_pipeline_phase = "正在准备 AI 打标"
        self._set_analysis_pipeline_state(
            AnalysisPipelineState.PREPARING_TAGGING
        )

        try:
            future = self.runtime.async_runtime.submit(
                self._run_ai_tagging_pipeline(
                    word_result_snapshot,
                    category_key,
                    tagging_generation,
                    analysis_generation,
                )
            )
        except Exception:
            self._tagging_task_active = False
            self._fail_analysis_job("AI 打标任务启动失败")
            return

        self._tagging_future = future
        future.add_done_callback(
            lambda completed_future: self._on_ai_tagging_future_done(
                completed_future,
                tagging_generation,
                analysis_generation,
                review_generation,
            )
        )

    async def _run_ai_tagging_pipeline(
        self,
        word_result_snapshot: dict[str, list[dict[str, Any]]],
        category_key: str,
        tagging_generation: int,
        analysis_generation: int,
    ) -> TaggingPipelineRun:
        """仅在 AsyncRuntime 中调用 Service；进度经 Signal 返回 Controller。"""

        if self._tagging_pipeline_service is None:
            raise RuntimeError("AI 打标 Service 未初始化")
        return await self._tagging_pipeline_service.tag_monthly_word_results(
            word_result_snapshot,
            category_key,
            progress_callback=lambda message: self.tagging_pipeline_progressed.emit(
                tagging_generation,
                analysis_generation,
                message,
            ),
        )

    def _on_ai_tagging_future_done(
        self,
        future: Future,
        tagging_generation: int,
        analysis_generation: int,
        review_generation: int,
    ) -> None:
        """后台 Future 只读取结果，Qt 状态更新一律交给 Signal Slot。"""

        try:
            pipeline_run = future.result()
        except Exception as exc:
            # Future 已经完成；这里读取异常不会阻塞 Qt。只持久化固定、非敏感
            # 元数据，避免 UI 的泛化提示掩盖“发生在哪个阶段、哪种异常”。
            write_ai_tagging_failure_diagnostic(
                exc,
                stage=self._tagging_pipeline_phase,
                analysis_generation=analysis_generation,
                tagging_generation=tagging_generation,
            )
            message = (
                "AI 打标已取消"
                if future.cancelled()
                else "AI 打标未完成，请稍后重试"
            )
            # 原始 Provider/认证异常可能含不应显示的内部细节，不能直接透传。
            self.tagging_pipeline_failed.emit(
                tagging_generation,
                analysis_generation,
                review_generation,
                message,
            )
            return

        self.tagging_pipeline_succeeded.emit(
            tagging_generation,
            analysis_generation,
            review_generation,
            pipeline_run,
        )

    @Slot(int, int, str)
    def _on_tagging_pipeline_progressed(
        self,
        tagging_generation: int,
        analysis_generation: int,
        message: str,
    ) -> None:
        """仅接受当前分析与当前打标 generation 的阶段提示。"""

        if (
            not self._tagging_task_active
            or tagging_generation != self._tagging_task_generation
            or analysis_generation != self._analysis_generation
        ):
            return
        self._tagging_pipeline_phase = message
        state_by_message = {
            "正在查询历史标签缓存...": AnalysisPipelineState.LOOKING_UP_TAG_CACHE,
            "正在准备产品背景...": AnalysisPipelineState.FETCHING_PRODUCT_CONTEXT,
            "正在获取 Amazon 产品背景...": AnalysisPipelineState.FETCHING_AMAZON_CONTEXT,
            "正在进行 AI 三模型判断...": AnalysisPipelineState.AI_TAGGING,
            "正在处理共识结果...": AnalysisPipelineState.FINALIZING,
        }
        progress_by_message = {
            "正在查询历史标签缓存...": 63,
            "正在准备产品背景...": 66,
            "正在获取 Amazon 产品背景...": 69,
            "正在进行 AI 三模型判断...": 72,
            "正在处理共识结果...": 78,
        }
        self._set_analysis_pipeline_state(
            state_by_message.get(message, AnalysisPipelineState.PREPARING_TAGGING)
        )
        self._advance_analysis_progress(
            progress_by_message.get(
                message,
                self._PROGRESS_NORMALIZATION_END,
            )
        )

    @Slot(int, int, int, object)
    def _on_tagging_pipeline_succeeded(
        self,
        tagging_generation: int,
        analysis_generation: int,
        review_generation: int,
        pipeline_run: object,
    ) -> None:
        """接纳当代完整 Pipeline 结果，并回填正式词结果表。"""

        if (
            tagging_generation != self._tagging_task_generation
            or analysis_generation != self._analysis_generation
            or not isinstance(pipeline_run, TaggingPipelineRun)
        ):
            return

        self._tagging_task_active = False
        self._tagging_future = None
        self.accept_tagging_pipeline_run(review_generation, pipeline_run)
        self._advance_analysis_progress(self._PROGRESS_TAGGING_END)
        self._refresh_tagging_result_presentation()
        if self._tagging_review_items:
            self._set_analysis_pipeline_state(
                AnalysisPipelineState.WAITING_TAGGING_REVIEW
            )
            self.window.set_status(
                f"{self._tagging_statistics_message()}；等待人工审核"
            )
        else:
            self._complete_analysis_job()

    @Slot(int, int, int, str)
    def _on_tagging_pipeline_failed(
        self,
        tagging_generation: int,
        analysis_generation: int,
        review_generation: int,
        message: str,
    ) -> None:
        """失败或取消时不接纳半成品结果，也不打开人工审核窗口。"""

        if (
            tagging_generation != self._tagging_task_generation
            or analysis_generation != self._analysis_generation
        ):
            return
        self._tagging_task_active = False
        self._tagging_future = None
        # 当前 generation 若尚未产生结果，清理它的临时审核空间即可。
        if review_generation == self._tagging_review_generation:
            self._clear_tagging_review_state()
        self._fail_analysis_job(message)

    def accept_tagging_pipeline_run(
        self,
        generation: int,
        pipeline_run: TaggingPipelineRun,
    ) -> None:
        """接纳当前 generation 的打标结果，并自动展示仅分歧审核项。"""

        if generation != self._tagging_review_generation:
            return
        self._tagging_results = {}
        self._tagging_result_index = {}
        for result in pipeline_run.results:
            self._tagging_results.setdefault(result.month, []).append(result)
            self._tagging_result_index[(result.month, result.word)] = result

        # 防御性按 Pipeline status 再过滤一次：即使未来调用方错误构造 run，
        # CACHE_HIT、AI_CONSENSUS、INCOMPLETE 也绝不进入人工审核窗口。
        disagreement_identities = {
            (result.month, result.word)
            for result in pipeline_run.results
            if result.status == TaggingPipelineStatus.DISAGREEMENT
        }
        self._tagging_review_items = {
            item.item_id: item
            for item in pipeline_run.review_items
            if (item.month, item.word) in disagreement_identities
        }
        self._tagging_review_total_count = len(self._tagging_review_items)
        self._refresh_tagging_review_view()
        if self._tagging_review_items:
            self.window.show_tagging_review()

        self._refresh_tagging_statistics()

    @Slot()
    def _retry_incomplete_tagging(self) -> None:
        """增量恢复当前 Final Result 中可重试的 INCOMPLETE Provider。"""

        if self._tagging_retry_active:
            return
        if not self._final_analysis_rows:
            self.window.set_status("当前没有可恢复的最终分析结果")
            return
        if self._analysis_job_active or self._tagging_review_items:
            self.window.set_status("当前任务尚未结束，暂时不能重试失败项")
            return
        service = self._tagging_pipeline_service
        incomplete_results = self._incomplete_tagging_results()
        if not incomplete_results:
            self.window.set_status("当前没有 AI 失败项")
            self._refresh_tagging_result_presentation()
            return
        retryable_results = [
            result
            for result in incomplete_results
            if TaggingService.retryable_provider_ids(result)
        ]
        if service is None or not retryable_results:
            categories = sorted(
                {
                    category
                    for result in incomplete_results
                    for category in TaggingService.failed_provider_categories(
                        result
                    ).values()
                }
            )
            category_text = "、".join(categories) or "缺少运行时输入快照"
            self.window.set_status(
                f"AI 失败项不可自动重试，请先检查 AI 设置：{category_text}"
            )
            return

        self._tagging_retry_generation += 1
        retry_generation = self._tagging_retry_generation
        analysis_generation = self._analysis_generation
        self._tagging_retry_active = True
        self._set_analysis_pipeline_state(
            AnalysisPipelineState.RETRYING_INCOMPLETE_TAGGING
        )
        self.window.set_status(
            f"正在重试 {len(retryable_results)} 条 AI 失败项"
        )
        self._refresh_tagging_result_presentation()
        try:
            future = self.runtime.async_runtime.submit(
                self._run_incomplete_tagging_retry(
                    tuple(retryable_results),
                    retry_generation,
                    analysis_generation,
                )
            )
        except Exception:
            self._tagging_retry_active = False
            self._refresh_tagging_result_presentation()
            self._set_analysis_pipeline_state(AnalysisPipelineState.COMPLETED)
            self.window.set_status("AI 失败项重试任务启动失败")
            return

        self._tagging_retry_future = future
        future.add_done_callback(
            lambda completed_future: self._on_incomplete_tagging_retry_done(
                completed_future,
                retry_generation,
                analysis_generation,
            )
        )

    async def _run_incomplete_tagging_retry(
        self,
        results: tuple[TaggingPipelineResult, ...],
        retry_generation: int,
        analysis_generation: int,
    ) -> TaggingPipelineRun:
        """只把运行时快照交给 Service，不读取 UI、Amazon 或正式 Word Result。"""

        service = self._tagging_pipeline_service
        if service is None:
            raise RuntimeError("AI 重试 Service 未初始化")
        return await service.retry_incomplete_results(
            results,
            progress_callback=lambda message: self.tagging_retry_progressed.emit(
                retry_generation,
                analysis_generation,
                message,
            ),
        )

    def _on_incomplete_tagging_retry_done(
        self,
        future: Future,
        retry_generation: int,
        analysis_generation: int,
    ) -> None:
        """后台重试完成后经 Signal 返回 Qt 主线程，不直接操作 View。"""

        try:
            retry_run = future.result()
        except Exception:
            message = "AI 失败项重试已取消" if future.cancelled() else "AI 失败项重试未完成"
            self.tagging_retry_failed.emit(
                retry_generation,
                analysis_generation,
                message,
            )
            return
        self.tagging_retry_succeeded.emit(
            retry_generation,
            analysis_generation,
            retry_run,
        )

    @Slot(int, int, str)
    def _on_tagging_retry_progressed(
        self,
        retry_generation: int,
        analysis_generation: int,
        message: str,
    ) -> None:
        """拒绝旧 generation 的进度回调，避免覆盖新分析状态。"""

        if (
            not self._tagging_retry_active
            or retry_generation != self._tagging_retry_generation
            or analysis_generation != self._analysis_generation
        ):
            return
        self.window.set_status(message)

    @Slot(int, int, object)
    def _on_tagging_retry_succeeded(
        self,
        retry_generation: int,
        analysis_generation: int,
        retry_run: object,
    ) -> None:
        """把局部恢复结果原地回填 Final Result，并按需打开既有审核窗口。"""

        if (
            not self._tagging_retry_active
            or retry_generation != self._tagging_retry_generation
            or analysis_generation != self._analysis_generation
            or not isinstance(retry_run, TaggingPipelineRun)
        ):
            return
        self._tagging_retry_active = False
        self._tagging_retry_future = None
        for result in retry_run.results:
            self._replace_tagging_result(result)
            self._update_final_analysis_row(result)

        for item in retry_run.review_items:
            identity = (item.month, item.word)
            current = self._tagging_result_index.get(identity)
            if current is None or current.status != TaggingPipelineStatus.DISAGREEMENT:
                continue
            self._tagging_review_items[item.item_id] = item
        self._tagging_review_total_count = len(self._tagging_review_items)
        self._refresh_tagging_review_view()
        self._refresh_tagging_result_presentation()

        if self._tagging_review_items:
            self.window.show_tagging_review()
            self._set_analysis_pipeline_state(
                AnalysisPipelineState.WAITING_TAGGING_REVIEW
            )
            self.window.set_status(
                f"{self._tagging_statistics_message()}；等待人工审核"
            )
            return

        self._set_analysis_pipeline_state(AnalysisPipelineState.COMPLETED)
        incomplete_count = self._tagging_statistics["incomplete"]
        if incomplete_count:
            self.window.set_status(f"分析完成（{incomplete_count} 条未完成）")
        else:
            self.window.set_status("分析完成")

    @Slot(int, int, str)
    def _on_tagging_retry_failed(
        self,
        retry_generation: int,
        analysis_generation: int,
        message: str,
    ) -> None:
        """重试异常不覆盖已成功的局部结果，恢复原 Final Result 展示。"""

        if (
            retry_generation != self._tagging_retry_generation
            or analysis_generation != self._analysis_generation
        ):
            return
        self._tagging_retry_active = False
        self._tagging_retry_future = None
        self._refresh_tagging_result_presentation()
        self._set_analysis_pipeline_state(AnalysisPipelineState.COMPLETED)
        self.window.set_status(message)

    def _replace_tagging_result(
        self,
        updated_result: TaggingPipelineResult,
    ) -> None:
        """原地替换一个月度结果，不触碰其他月份、成功项或展示顺序。"""

        identity = (updated_result.month, updated_result.word)
        if identity not in self._tagging_result_index:
            return
        self._tagging_result_index[identity] = updated_result
        month_results = self._tagging_results.get(updated_result.month, [])
        self._tagging_results[updated_result.month] = [
            updated_result
            if (result.month, result.word) == identity
            else result
            for result in month_results
        ]

    def _incomplete_tagging_results(self) -> list[TaggingPipelineResult]:
        """按既有月度结果顺序定位当前 generation 的 INCOMPLETE。"""

        return [
            result
            for month_results in self._tagging_results.values()
            for result in month_results
            if result.status == TaggingPipelineStatus.INCOMPLETE
        ]

    @staticmethod
    def _empty_tagging_statistics() -> dict[str, int]:
        """创建固定字段的运行时统计，避免把未完成状态误算为正式标签。"""

        return {
            "total": 0,
            "cache_hit": 0,
            "ai_consensus": 0,
            "human_review": 0,
            "disagreement": 0,
            "incomplete": 0,
        }

    def _refresh_tagging_statistics(self) -> None:
        """由当前月度结果重建展示统计，人工审核回填后可立即更新。"""

        statistics = self._empty_tagging_statistics()
        status_to_field = {
            TaggingPipelineStatus.CACHE_HIT: "cache_hit",
            TaggingPipelineStatus.AI_CONSENSUS: "ai_consensus",
            TaggingPipelineStatus.HUMAN_REVIEW: "human_review",
            TaggingPipelineStatus.DISAGREEMENT: "disagreement",
            TaggingPipelineStatus.INCOMPLETE: "incomplete",
        }
        for results in self._tagging_results.values():
            for result in results:
                statistics["total"] += 1
                statistics[status_to_field[result.status]] += 1
        self._tagging_statistics = statistics

    def _tagging_statistics_message(self) -> str:
        """生成不含 Provider 细节的当前任务摘要。"""

        stats = self._tagging_statistics
        return (
            f"AI 打标完成：共 {stats['total']} 个词，"
            f"历史缓存 {stats['cache_hit']}，"
            f"AI 共识 {stats['ai_consensus']}，"
            f"人工审核 {stats['human_review']}，"
            f"待人工审核 {stats['disagreement']}，"
            f"AI 失败 {stats['incomplete']}"
        )

    def _refresh_tagging_result_presentation(self) -> None:
        """刷新标签统计；Final Result 已存在时仅增量更新 Data Tab。"""

        self._refresh_tagging_statistics()
        if hasattr(self.window, "set_incomplete_tagging_retry_available"):
            # 恢复按钮只在本轮 Final Result 已生成时出现；初次 Pipeline 中的
            # INCOMPLETE 仍由完整任务按既有路径收尾。
            self.window.set_incomplete_tagging_retry_available(
                self._tagging_statistics["incomplete"]
                if self._final_analysis_rows
                else 0,
                enabled=not self._tagging_retry_active,
            )

    def _update_final_analysis_row(
        self,
        tagging_result: TaggingPipelineResult,
    ) -> None:
        """将人工审核成功后的标签即时回填既有 Final Dataset 对应行。"""

        if not self._final_analysis_rows:
            return
        updated_row = self.final_analysis_service.update_tagging_fields(
            self._final_analysis_rows,
            tagging_result,
        )
        if updated_row is not None:
            self.window.update_final_analysis_row(updated_row)

    def _clear_runtime_tagging_results(self) -> None:
        """使当前标签及审核项立即 stale，不触碰历史缓存或审计表。"""

        self._tagging_task_generation += 1
        self._tagging_retry_generation += 1
        if self._tagging_pipeline_service is not None:
            self._tagging_pipeline_service.cancel_current_task()
        if self._tagging_future is not None:
            self._tagging_future.cancel()
        if self._tagging_retry_future is not None:
            self._tagging_retry_future.cancel()
        self._tagging_future = None
        self._tagging_retry_future = None
        self._tagging_task_active = False
        self._tagging_retry_active = False
        self._tagging_results = {}
        self._tagging_result_index = {}
        self._tagging_statistics = self._empty_tagging_statistics()
        self._clear_tagging_review_state()
        self._clear_final_analysis_rows()

    def _refresh_tagging_review_view(self) -> None:
        """把当前 generation 的工作集快照一次性交给 View。"""

        if not hasattr(self.window, "set_tagging_review_items"):
            return
        self.window.set_tagging_review_items(
            list(self._tagging_review_items.values()),
            self._tagging_review_total_count,
        )

    @Slot(str, str, str)
    def _save_tagging_human_review(
        self,
        item_id: str,
        label_value: str,
        reason: str,
    ) -> None:
        """异步保存一条人工最终决定，禁止在 Qt 主线程等待数据库。"""

        item = self._tagging_review_items.get(item_id)
        if item is None or item.status != TaggingReviewStatus.PENDING:
            return
        try:
            label = TagLabel(label_value)
        except ValueError:
            self.window.set_status("人工标签无效，请重新选择")
            return
        clean_reason = reason.strip()
        if not clean_reason:
            self.window.set_status("请填写人工标签原因")
            return
        if self.tagging_service is None:
            self.window.set_status("人工标签保存失败，请重试。")
            return

        # 先锁住当前项，防止按钮连点生成重复 append-only 审计记录。
        self._tagging_review_items[item_id] = replace(
            item,
            status=TaggingReviewStatus.SAVING,
        )
        self._refresh_tagging_review_view()
        self.window.set_tagging_review_save_status(
            item_id,
            "正在保存人工标签...",
        )
        generation = self._tagging_review_generation
        try:
            future = self.runtime.async_runtime.submit(
                self.tagging_service.record_human_review(
                    category_key=item.category_key.value,
                    word=item.word,
                    label=label,
                    reason=clean_reason,
                    representative_asin=item.representative_asin,
                    product_context_source=item.product_context_source,
                    taxonomy_version=item.taxonomy_version,
                )
            )
        except Exception:
            self._restore_tagging_review_item_after_save_failure(
                generation,
                item_id,
            )
            return

        self._tagging_review_save_futures[item_id] = future
        future.add_done_callback(
            lambda completed_future, current_generation=generation, current_item_id=item_id: self._on_tagging_human_review_save_done(
                completed_future,
                current_generation,
                current_item_id,
            )
        )

    def _on_tagging_human_review_save_done(
        self,
        future: Future,
        generation: int,
        item_id: str,
    ) -> None:
        """后台回调只读取 Future，所有 UI 和内存变更通过 Qt Signal 返回。"""

        try:
            saved_record = future.result()
        except Exception:
            self.tagging_human_review_save_failed.emit(generation, item_id)
            return
        self.tagging_human_review_saved.emit(
            generation,
            item_id,
            saved_record,
        )

    @Slot(int, str, object)
    def _on_tagging_human_review_saved(
        self,
        generation: int,
        item_id: str,
        saved_record: object,
    ) -> None:
        """仅在 DB 事务成功后回填 HUMAN_REVIEW 并移除审核工作项。"""

        self._tagging_review_save_futures.pop(item_id, None)
        if generation != self._tagging_review_generation:
            return
        item = self._tagging_review_items.get(item_id)
        if item is None or item.status != TaggingReviewStatus.SAVING:
            return
        # record_human_review 的返回缓存行是成功事务的唯一依据；不采用
        # Dialog 表单中可能已经切换过的临时输入。
        label = getattr(saved_record, "label", None)
        reason = getattr(saved_record, "reason", None)
        if not isinstance(label, TagLabel) or not isinstance(reason, str):
            self._restore_tagging_review_item_after_save_failure(
                generation,
                item_id,
            )
            return

        identity = (item.month, item.word)
        existing_result = self._tagging_result_index.get(identity)
        if existing_result is not None:
            human_result = replace(
                existing_result,
                status=TaggingPipelineStatus.HUMAN_REVIEW,
                label=label,
                reason=reason,
                decision_source=TaggingDecisionSource.HUMAN_REVIEW,
            )
            self._tagging_result_index[identity] = human_result
            month_results = self._tagging_results.get(item.month, [])
            self._tagging_results[item.month] = [
                human_result
                if (result.month, result.word) == identity
                else result
                for result in month_results
            ]
            self._update_final_analysis_row(human_result)

        self._tagging_review_items.pop(item_id, None)
        self._refresh_tagging_review_view()
        self._refresh_tagging_result_presentation()
        self.window.set_status("人工标签已保存")
        if not self._tagging_review_items:
            if self._analysis_job_active:
                self._complete_analysis_job()
            else:
                # INCOMPLETE 重试转为分歧时，Final Result 已经存在；审核
                # 保存后只更新该行与统计，不能错误重建或清空整个结果集。
                self._refresh_tagging_result_presentation()
                self._set_analysis_pipeline_state(
                    AnalysisPipelineState.COMPLETED
                )

    @Slot(int, str)
    def _on_tagging_human_review_save_failed(
        self,
        generation: int,
        item_id: str,
    ) -> None:
        """数据库失败必须将当前项恢复 PENDING，绝不伪造完成状态。"""

        self._tagging_review_save_futures.pop(item_id, None)
        self._restore_tagging_review_item_after_save_failure(generation, item_id)

    def _restore_tagging_review_item_after_save_failure(
        self,
        generation: int,
        item_id: str,
    ) -> None:
        """恢复一次失败的保存，保留原始 ProductContext 与三方结果。"""

        if generation != self._tagging_review_generation:
            return
        item = self._tagging_review_items.get(item_id)
        if item is None:
            return
        self._tagging_review_items[item_id] = replace(
            item,
            status=TaggingReviewStatus.PENDING,
        )
        self._refresh_tagging_review_view()
        if hasattr(self.window, "show_tagging_review_save_failed"):
            self.window.show_tagging_review_save_failed(item_id)
        self.window.set_status("人工标签保存失败，请重试。")

    @Slot()
    def _request_close_tagging_review(self) -> None:
        """处理 Dialog 的 X：有未审核数据必须先经用户明确确认。"""

        pending_count = len(self._tagging_review_items)
        if pending_count and not self.window.confirm_discard_tagging_review(
            pending_count
        ):
            return
        self._clear_tagging_review_state()
        if self._analysis_pipeline_state == AnalysisPipelineState.WAITING_TAGGING_REVIEW:
            if self._analysis_job_active:
                self._complete_analysis_job()
            else:
                self._set_analysis_pipeline_state(
                    AnalysisPipelineState.COMPLETED
                )
                self.window.set_status(self._tagging_statistics_message())

    @Slot()
    def _clear_tagging_review_state(self) -> None:
        """统一销毁本轮分歧、背景和 Provider 引用，不碰已经保存的数据库记录。"""

        self._tagging_review_generation += 1
        for future in tuple(self._tagging_review_save_futures.values()):
            future.cancel()
        self._tagging_review_save_futures.clear()
        self._tagging_review_items.clear()
        self._tagging_review_total_count = 0
        if hasattr(self.window, "close_tagging_review_after_discard"):
            self.window.close_tagging_review_after_discard()

    @Slot()
    def _finish_normalization_review(self) -> None:
        """完成审核时自动应用当前批准规则，避免完整任务停在审核阶段。"""

        if not self._normalization_candidates:
            return
        if self._normalization_apply_active:
            self.window.set_status("正式重算进行中，暂时不能结束本次审核")
            return

        pending_count = self._normalization_decision_count(
            "PENDING", self._normalization_candidates
        )
        skipped_count = self._normalization_decision_count(
            "SKIPPED", self._normalization_candidates
        )
        if not self.window.confirm_finish_normalization_review(
            pending_count=pending_count,
            skipped_count=skipped_count,
            saving_count=len(self._normalization_save_futures),
            unsaved_count=len(self._normalization_decision_events),
        ):
            return

        # 正在运行的完整 Analysis Job 中，“完成审核”不能只是清除窗口数据，
        # 否则会留下一个永远无法进入 AI Tagging 的暂停任务。候选快照由
        # _apply_normalization_rules 在当前 UI 线程立即复制，随后安全重算。
        if self._analysis_job_active:
            self._apply_normalization_rules()
            return

        # 已提交的审计事件持有自己的 deepcopy payload，清除候选后仍可安全
        # 完成；它们绝不回头读取 Controller 的候选工作集。
        self._clear_normalization_review_state()
        self.window.set_status("本次归一审核已结束，待审核数据已从内存清除")

    @Slot()
    def _request_close_normalization_review(self) -> None:
        """处理审核窗口关闭 X：销毁未处理候选后继续完整流水线。"""

        if not self._normalization_candidates:
            if hasattr(self.window, "close_normalization_review_after_discard"):
                self.window.close_normalization_review_after_discard()
            return
        if self._normalization_apply_active:
            self.window.set_status("正式重算进行中，暂时不能关闭本次审核")
            return

        pending_count = self._normalization_decision_count(
            "PENDING",
            self._normalization_candidates,
        )
        if not self.window.confirm_discard_normalization_review(pending_count):
            return

        # 关闭后 PENDING 及其 evidence 必须立即销毁，但已批准规则仍要参与
        # 本轮正式词结果重算。因此先冻结非 PENDING 决定，再清空完整工作集。
        confirmed_snapshot = deepcopy(
            [
                candidate
                for candidate in self._normalization_candidates
                if candidate.get("decision") != "PENDING"
            ]
        )
        self._clear_normalization_review_state()
        if hasattr(self.window, "close_normalization_review_after_discard"):
            self.window.close_normalization_review_after_discard()

        if self._analysis_job_active:
            self._apply_normalization_rules(confirmed_snapshot)
            return

        self.window.set_status("本次归一审核已结束，未处理候选已从内存清除")

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
    def _apply_normalization_rules(
        self,
        candidate_snapshot_override: list[dict[str, Any]] | None = None,
    ) -> None:
        """编译当前或关闭前冻结的审核快照，并后台原子重算正式词结果。"""

        if self._normalization_apply_active:
            return
        if not self._analysis_results or not self._preparation_months:
            self.window.set_status("当前没有可重算的月度分析结果")
            return

        # 点击瞬间复制候选；关闭审核时则使用关闭前冻结的已确认决定。
        # 两种来源都不会被后续 UI 操作影响。
        candidate_snapshot = deepcopy(
            candidate_snapshot_override
            if candidate_snapshot_override is not None
            else self._normalization_candidates
        )
        build_result = self.normalization_rule_service.build_approved_rules(
            candidate_snapshot
        )
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
        monthly_raw_snapshot = deepcopy(
            {
                month: self._analysis_raw.get(month, [])
                for month in months
            }
        )
        allowed_output_words_by_month = self._allowed_normalized_words_by_month(
            build_result.approved_rules,
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
            "allowed_output_words_by_month": allowed_output_words_by_month,
        }
        # 一旦用户显式应用新的归一快照，旧标签即使仍对应旧正式词结果也
        # 不能再被当作当前结果展示；同时使旧异步回调失效。
        self._clear_runtime_tagging_results()
        self._normalization_apply_active = True
        self._normalization_result_state = "RUNNING"
        self._advance_analysis_progress(self._PROGRESS_ANALYSIS_END)
        self._set_analysis_pipeline_state(
            AnalysisPipelineState.APPLYING_NORMALIZATION
        )
        self.window.set_normalization_review_editable(False)
        self._refresh_normalization_result_status()

        self.normalization_apply_thread = QThread()
        analysis_generation = self._analysis_generation
        self.normalization_apply_worker = NormalizationApplyWorker(
            self.analysis_service,
            monthly_result_snapshot,
            monthly_raw_snapshot,
            months,
            build_result.approved_rules,
            allowed_output_words_by_month,
            analysis_generation=analysis_generation,
        )
        self.normalization_apply_worker.moveToThread(
            self.normalization_apply_thread
        )
        self.normalization_apply_thread.started.connect(
            self.normalization_apply_worker.run
        )
        # 与初始 AnalysisWorker 相同：只让 Qt 在主线程执行 Controller Slot。
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
        analysis_generation: int | None = None,
    ) -> None:
        """仅在全部月份成功后，一次性替换正式词结果和对应元数据。"""

        analysis_generation = self._worker_analysis_generation(
            analysis_generation
        )
        if (
            not self._normalization_apply_active
            or (
                analysis_generation is not None
                and analysis_generation != self._analysis_generation
            )
        ):
            return

        word_results = processed_data.get("word_results")
        asin_word_results = processed_data.get("asin_word_results")
        diagnostics = processed_data.get("diagnostics")
        months = self._normalization_apply_context.get("months", [])
        if (
            not isinstance(word_results, Mapping)
            or list(word_results) != months
            or not isinstance(asin_word_results, Mapping)
            or list(asin_word_results) != months
            or not isinstance(diagnostics, Mapping)
        ):
            self._on_normalization_apply_failed(
                "正式词结果重算失败",
                analysis_generation,
            )
            return

        # 原始 Word Analysis 保持不变；正式归一结果只能替换筛选后的工作
        # 快照，后续 AI、Final Result 与 Excel 都由它读取。
        self._filtered_word_results = dict(word_results)
        self._filtered_asin_word_results = dict(asin_word_results)
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
        self._advance_analysis_progress(self._PROGRESS_NORMALIZATION_END)
        # 正式规则快照已经写入 meta，审核工作集不再属于当前 Pipeline。清理
        # 后归一审核入口会禁用，避免用户在 AI 阶段修改已冻结的规则快照。
        self._clear_normalization_review_state()
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self._start_tagging_pipeline()

    @Slot(str)
    def _on_normalization_apply_failed(
        self,
        _message: str,
        analysis_generation: int | None = None,
    ) -> None:
        """失败时不覆盖旧正式结果，并解除审核编辑锁定。"""

        analysis_generation = self._worker_analysis_generation(
            analysis_generation
        )
        if (
            not self._normalization_apply_active
            or (
                analysis_generation is not None
                and analysis_generation != self._analysis_generation
            )
        ):
            return

        self._normalization_apply_active = False
        self._normalization_result_state = "FAILED"
        self.window.set_normalization_review_editable(True)
        self._refresh_normalization_result_status()
        self._fail_analysis_job("正式词结果重算失败，已保留上一份正式结果")

    @Slot()
    def _on_normalization_apply_thread_finished(self) -> None:
        """清理正式重算专用线程引用，避免与初始分析 Worker 混用。"""

        self.normalization_apply_worker = None
        self.normalization_apply_thread = None

    def _record_normalization_review_change(self) -> None:
        """递增审核 revision；旧正式结果保留但不能再被标记为最新。"""

        self._normalization_revision += 1
        self._clear_runtime_tagging_results()
        self._normalization_result_state = (
            "STALE"
            if self._filtered_word_results
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
        if (
            event is not None
            and event["candidate_generation"]
            == self._normalization_candidate_generation
        ):
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
                if self._filtered_word_results
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
                if self._filtered_word_results
                else "未归一预览"
            )
        elif self._normalization_result_state == "FAILED":
            status = (
                "重算失败，保留上一份正式结果"
                if self._filtered_word_results
                else "重算失败，尚未生成正式结果"
            )
            mode = (
                "人工归一结果（上次重算失败，需重新计算）"
                if self._filtered_word_results
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
    def _on_reversing_result_analysis_failed(
        self,
        message: str,
        analysis_generation: int | None = None,
    ):
        """处理失败时保留 reversing 原始结果，并恢复可继续操作的界面。"""

        if (
            analysis_generation is not None
            and analysis_generation != self._analysis_generation
        ):
            return
        self._analysis_processing_active = False
        self._reversing_preparation_status = "failed"
        self.window.set_relation_preparation_running(False)
        self._fail_analysis_job(message)

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
        self._fail_analysis_job(status_message)

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
