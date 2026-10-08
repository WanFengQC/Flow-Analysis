"""正式 AI Tagging 入口、generation 防护与主表回填测试。"""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from controllers.main_controller import AnalysisPipelineState, MainController
from models.tagging import TagLabel, TaggingDecision, TaggingInput
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingPipelineResult,
    TaggingPipelineRun,
    TaggingPipelineStatus,
    TaggingReviewItem,
    TaggingReviewStatus,
)
from models.tagging_provider import (
    ProviderTaggingFailure,
    ProviderTaggingResult,
    TaggingFailureType,
)
from views.main_window import MainWindow


def _word_results() -> dict[str, list[dict]]:
    """构造满足正式词结果表展示的最小跨月快照。"""

    return {
        "202608": [
            {
                "word": "weighted",
                "frequency": 2,
                "weight": 12.5,
                "total": 4.0,
                "ratio": 0.5,
                "naturalRatio": 0.25,
                "adRatio": 0.75,
                "matchingKeywordCount": 1,
                "sourceAsinStats": {"B0DSHYXD4G": {"exposure": 12.5}},
            }
        ],
        "202607": [
            {
                "word": "weighted",
                "frequency": 1,
                "weight": 8.0,
                "total": 2.0,
                "ratio": 0.4,
                "naturalRatio": 0.2,
                "adRatio": 0.8,
                "matchingKeywordCount": 1,
                "sourceAsinStats": {"B0DSHYXD4G": {"exposure": 8.0}},
            }
        ],
    }


def _result(
    month: str,
    status: TaggingPipelineStatus,
    label: TagLabel | None,
) -> TaggingPipelineResult:
    """构造一个已完成 Pipeline 的月度结果。"""

    return TaggingPipelineResult(
        month=month,
        word="weighted",
        category_key=TaggingCategoryKey.PILLOW,
        taxonomy_version=1,
        status=status,
        label=label,
        reason="测试理由" if label is not None else None,
        decision_source=(
            TaggingDecisionSource.AI_CONSENSUS
            if label is not None
            else None
        ),
    )


def _incomplete_result(month: str = "202608") -> TaggingPipelineResult:
    """构造保留两家成功、Google 超时的当前运行时恢复快照。"""

    tagging_input = TaggingInput(
        item_id="pillow:1:weighted",
        month=month,
        word="weighted",
        category="Pillow",
        top_phrases=("weighted stuffed animal",),
        product_context_source="PRODUCT_KNOWLEDGE_BASE",
        representative_asin="B0DSHYXD4G",
        product_context={"project": "M"},
    )
    provider_results = {
        provider_id: ProviderTaggingResult(
            provider_id=provider_id,
            model_id=f"{provider_id}-model",
            decisions=(
                TaggingDecision(
                    item_id=tagging_input.item_id,
                    label=TagLabel.ATTRIBUTE,
                    reason=f"{provider_id} 原始理由",
                ),
            ),
            latency_ms=1,
            attempt_count=1,
        )
        for provider_id in ("openai", "anthropic")
    }
    provider_results["google"] = ProviderTaggingFailure(
        provider_id="google",
        model_id="google-model",
        failure_type=TaggingFailureType.TIMEOUT,
        attempt_count=4,
        latency_ms=1,
    )
    return TaggingPipelineResult(
        month=month,
        word="weighted",
        category_key=TaggingCategoryKey.PILLOW,
        taxonomy_version=1,
        status=TaggingPipelineStatus.INCOMPLETE,
        label=None,
        reason=None,
        decision_source=None,
        provider_results=provider_results,
        tagging_input=tagging_input,
    )


class _RetryPipelineService:
    """只返回预设局部恢复结果，验证 Controller 不重跑完整 Pipeline。"""

    def __init__(self, retry_run: TaggingPipelineRun) -> None:
        self.retry_run = retry_run
        self.calls: list[tuple[TaggingPipelineResult, ...]] = []

    async def retry_incomplete_results(self, results, progress_callback):
        snapshots = tuple(results)
        self.calls.append(snapshots)
        progress_callback("正在重试 AI 失败项...")
        return self.retry_run

    def cancel_current_task(self) -> None:
        return None

    async def wait_for_external_cleanup(self, _timeout: float) -> bool:
        return True


class _AiService:
    """仅记录配置预检；测试不接触 UniAPI。"""

    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.validate_calls = 0

    def validate_tagging_configuration(self) -> None:
        self.validate_calls += 1
        if not self.configured:
            from services.ai_service import AiServiceConfigurationError

            raise AiServiceConfigurationError("缺少测试配置")


class _AsyncRuntime:
    """立即执行协程并保持 Future 回调行为，不创建真实后台线程。"""

    def submit(self, coroutine):
        future = Future()
        try:
            future.set_result(asyncio.run(coroutine))
        except BaseException as exc:
            future.set_exception(exc)
        return future


class _Runtime:
    """测试专用运行时，绝不连接真实 PostgreSQL。"""

    def __init__(self, configured: bool = True) -> None:
        self.async_runtime = _AsyncRuntime()
        self.database = object()
        self.ai_service = _AiService(configured)


class _PipelineService:
    """记录冻结输入并返回可控 Pipeline 结果。"""

    last_snapshot = None
    progress_messages: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.cancelled = False

    async def tag_monthly_word_results(self, snapshot, category_key, progress_callback):
        type(self).last_snapshot = deepcopy(snapshot)
        progress_callback("正在查询历史标签缓存...")
        type(self).progress_messages.append(category_key)
        return TaggingPipelineRun(
            results=(
                _result(
                    "202608",
                    TaggingPipelineStatus.CACHE_HIT,
                    TagLabel.ATTRIBUTE,
                ),
                _result(
                    "202607",
                    TaggingPipelineStatus.DISAGREEMENT,
                    None,
                ),
            ),
            review_items=(),
        )

    def cancel_current_task(self) -> None:
        self.cancelled = True

    async def wait_for_external_cleanup(self, _timeout: float) -> bool:
        return True


class TaggingPipelineEntryTest(unittest.TestCase):
    """验证 AI 仅由完整 Analysis Job 自动推进，且回调受 generation 保护。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()
        self.runtime = _Runtime()
        self.controller = MainController(self.window, self.runtime)
        self.controller._analysis_word_results = _word_results()
        self.controller._normalization_result_state = "CURRENT"
        self.controller._analysis_generation = 7
        self.controller._analysis_job_active = True
        self.window.set_analysis_job_running(True)
        # 默认测试不得读取用户真实 QSettings 或弹出目录选择框；需要验证
        # 自动导出的用例会在自身范围内提供临时目录。
        self.window.ensure_export_directory = lambda: None
        _PipelineService.last_snapshot = None
        _PipelineService.progress_messages = []

    def tearDown(self) -> None:
        self.controller._clear_runtime_tagging_results()
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def _start_pipeline(self) -> None:
        """以假产品资料和假 Pipeline 验证 Controller 编排。"""

        with patch("controllers.main_controller.ProductKnowledgeService"), patch(
            "controllers.main_controller.AmazonProductContextProvider"
        ), patch("controllers.main_controller.TaggingService", _PipelineService):
            self.controller._start_tagging_pipeline()
        self.application.processEvents()

    def test_no_formal_word_results_cannot_start(self):
        """空正式结果不能越过启动条件。"""

        self.controller._analysis_word_results = {}
        self.controller._start_tagging_pipeline()
        self.assertFalse(self.controller._tagging_task_active)
        self.assertEqual(
            self.controller._analysis_pipeline_state,
            AnalysisPipelineState.FAILED,
        )

    def test_stale_normalization_cannot_start(self):
        """规则已变化时旧正式词结果不能发起打标。"""

        self.controller._normalization_result_state = "STALE"
        self.controller._start_tagging_pipeline()
        self.assertFalse(self.controller._tagging_task_active)
        self.assertEqual(
            self.controller._analysis_pipeline_state,
            AnalysisPipelineState.FAILED,
        )

    def test_missing_uniapi_configuration_stops_before_product_context(self):
        """配置缺失时不得构造产品资料服务，更不能启动 Amazon。"""

        self.runtime.ai_service = _AiService(configured=False)
        with patch("controllers.main_controller.ProductKnowledgeService") as product_service:
            self.controller._start_tagging_pipeline()
        self.assertEqual(product_service.call_count, 0)
        self.assertEqual(self.runtime.ai_service.validate_calls, 1)
        self.assertEqual(
            self.controller._analysis_pipeline_state,
            AnalysisPipelineState.FAILED,
        )

    def test_start_freezes_word_snapshot_and_category_key(self):
        """启动后原始正式词结果被修改，不会改变已提交的任务快照。"""

        self._start_pipeline()
        self.controller._analysis_word_results["202608"][0]["word"] = "changed"
        self.assertEqual(_PipelineService.last_snapshot["202608"][0]["word"], "weighted")
        self.assertEqual(_PipelineService.progress_messages, ["pillow"])

    def test_completed_run_publishes_final_rows_with_tagging_status_mapping(self):
        """Data Tab 只显示 Final Dataset，并正确映射缓存和待审核标签。"""

        self._start_pipeline()
        model = self.window.ui.resultTableView.model()
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.data(model.index(0, 10)), TagLabel.ATTRIBUTE.value)
        self.assertEqual(model.data(model.index(0, 11)), "历史缓存")
        self.assertEqual(model.data(model.index(1, 10)), "")
        self.assertEqual(model.data(model.index(1, 11)), "待人工审核")

    def test_old_generation_completion_is_discarded(self):
        """旧 generation 的回调不得覆盖新分析的运行时标签。"""

        self.controller._tagging_task_generation = 5
        self.controller._analysis_generation = 9
        self.controller._on_tagging_pipeline_succeeded(
            4,
            9,
            1,
            TaggingPipelineRun(results=(), review_items=()),
        )
        self.assertEqual(self.controller._tagging_results, {})

    def test_new_analysis_clears_runtime_tagging_results_only(self):
        """新分析清理运行时标签和 Final Dataset，不触碰任何数据库缓存入口。"""

        self._start_pipeline()
        self.assertTrue(self.controller._tagging_results)
        self.assertTrue(self.controller._final_analysis_rows)
        self.controller._clear_runtime_tagging_results()
        self.assertEqual(self.controller._tagging_results, {})
        self.assertEqual(self.controller._tagging_statistics["total"], 0)
        self.assertEqual(self.controller._final_analysis_rows, [])

    def test_export_failure_keeps_final_analysis_rows_unchanged(self):
        """文件占用或权限错误只能影响导出状态，不能清空正式分析数据。"""

        self._start_pipeline()
        final_snapshot = deepcopy(self.controller._final_analysis_rows)

        self.controller._on_export_failed("文件被占用")

        self.assertEqual(self.controller._final_analysis_rows, final_snapshot)
        self.assertFalse(self.window.ui.exportButton.isEnabled())

    def test_completion_automatically_exports_final_snapshot_in_background_thread(self):
        """任务完成自动保存 Final Dataset，并在成功后开放“打开 Excel”。"""

        with TemporaryDirectory() as directory:
            with patch.object(
                self.window,
                "ensure_export_directory",
                return_value=directory,
            ):
                self._start_pipeline()
                deadline = monotonic() + 3.0
                while self.controller._export_active and monotonic() < deadline:
                    self.application.processEvents()
                    sleep(0.01)

            output_paths = list(Path(directory).glob("flow_analysis_*.xlsx"))
            self.assertFalse(self.controller._export_active)
            self.assertEqual(len(output_paths), 1)
            self.assertEqual(
                self.controller._last_exported_path,
                output_paths[0],
            )
            self.assertTrue(self.window.ui.exportButton.isEnabled())
            self.assertEqual(self.window.ui.exportButton.text(), "打开 Excel")

    def test_open_excel_only_opens_the_current_auto_export(self):
        """打开按钮只委托 View 打开当前成功的自动导出文件。"""

        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.xlsx"
            output.touch()
            self.controller._last_exported_path = output
            self.window.set_export_enabled(True)
            with patch.object(
                self.window,
                "open_exported_file",
                return_value=True,
            ) as open_exported_file:
                self.controller._open_latest_export()

            open_exported_file.assert_called_once_with(str(output))

    def test_incomplete_final_row_keeps_empty_label_and_ai_failure_source(self):
        """INCOMPLETE 仍保留最终行，但标签为空且来源明确为 AI失败。"""

        generation = self.controller.begin_tagging_review_generation()
        run = TaggingPipelineRun(
            results=(
                _result("202608", TaggingPipelineStatus.INCOMPLETE, None),
            ),
            review_items=(),
        )
        self.controller.accept_tagging_pipeline_run(generation, run)
        self.controller._complete_analysis_job()
        model = self.window.ui.resultTableView.model()
        self.assertEqual(model.data(model.index(0, 10)), "")
        self.assertEqual(model.data(model.index(0, 11)), "AI失败")
        self.assertFalse(
            self.window.ui.retryIncompleteTaggingButton.isHidden()
        )
        self.assertEqual(
            self.window.ui.retryIncompleteTaggingButton.text(),
            "重试失败项 (1)",
        )

    def test_incomplete_retry_only_updates_failed_item_and_final_data_tab(self):
        """恢复只提交 INCOMPLETE 快照，Consensus 后即时更新 Final Result。"""

        initial = _incomplete_result()
        self.controller._tagging_results = {"202608": [initial]}
        self.controller._tagging_result_index = {("202608", "weighted"): initial}
        self.controller._complete_analysis_job()

        recovered_provider_results = dict(initial.provider_results)
        recovered_provider_results["google"] = ProviderTaggingResult(
            provider_id="google",
            model_id="google-model",
            decisions=(
                TaggingDecision(
                    item_id=initial.tagging_input.item_id,
                    label=TagLabel.ATTRIBUTE,
                    reason="google 重试理由",
                ),
            ),
            latency_ms=1,
            attempt_count=1,
        )
        recovered = replace(
            initial,
            status=TaggingPipelineStatus.AI_CONSENSUS,
            label=TagLabel.ATTRIBUTE,
            reason="openai 原始理由",
            decision_source=TaggingDecisionSource.AI_CONSENSUS,
            provider_results=recovered_provider_results,
        )
        retry_service = _RetryPipelineService(
            TaggingPipelineRun(results=(recovered,), review_items=())
        )
        self.controller._tagging_pipeline_service = retry_service

        self.controller._retry_incomplete_tagging()
        self.application.processEvents()

        self.assertEqual(len(retry_service.calls), 1)
        self.assertEqual(retry_service.calls[0], (initial,))
        self.assertEqual(
            self.controller._tagging_result_index[("202608", "weighted")].status,
            TaggingPipelineStatus.AI_CONSENSUS,
        )
        model = self.window.ui.resultTableView.model()
        self.assertEqual(model.data(model.index(0, 10)), TagLabel.ATTRIBUTE.value)
        self.assertEqual(model.data(model.index(0, 11)), "AI共识")
        self.assertFalse(self.window.ui.retryIncompleteTaggingButton.isVisible())

    def test_incomplete_retry_disagreement_reuses_existing_review_dialog(self):
        """局部补齐后分歧只加入既有 Dialog 工作集，不另造审核流程。"""

        initial = _incomplete_result()
        self.controller._tagging_results = {"202608": [initial]}
        self.controller._tagging_result_index = {("202608", "weighted"): initial}
        self.controller._complete_analysis_job()
        provider_results = dict(initial.provider_results)
        provider_results["google"] = ProviderTaggingResult(
            provider_id="google",
            model_id="google-model",
            decisions=(
                TaggingDecision(
                    item_id=initial.tagging_input.item_id,
                    label=TagLabel.SPECIFICATION,
                    reason="Google 不同理由",
                ),
            ),
            latency_ms=1,
            attempt_count=1,
        )
        disagreement = replace(
            initial,
            status=TaggingPipelineStatus.DISAGREEMENT,
            provider_results=provider_results,
        )
        review_item = TaggingReviewItem(
            item_id=f"{initial.tagging_input.item_id}:202608",
            month="202608",
            word="weighted",
            category_key=TaggingCategoryKey.PILLOW,
            taxonomy_version=1,
            top_phrases=initial.tagging_input.top_phrases,
            representative_asin=initial.tagging_input.representative_asin,
            product_context_source=initial.tagging_input.product_context_source,
            product_context=initial.tagging_input.product_context,
            provider_results=provider_results,
            status=TaggingReviewStatus.PENDING,
        )
        self.controller._tagging_pipeline_service = _RetryPipelineService(
            TaggingPipelineRun(
                results=(disagreement,),
                review_items=(review_item,),
            )
        )

        self.controller._retry_incomplete_tagging()
        self.application.processEvents()

        self.assertEqual(len(self.controller._tagging_review_items), 1)
        self.assertEqual(
            self.controller._tagging_result_index[("202608", "weighted")].status,
            TaggingPipelineStatus.DISAGREEMENT,
        )
        self.assertEqual(
            self.controller._analysis_pipeline_state,
            AnalysisPipelineState.WAITING_TAGGING_REVIEW,
        )

    def test_stale_incomplete_retry_callback_cannot_pollute_new_generation(self):
        """旧 generation 的恢复回调必须被直接丢弃。"""

        initial = _incomplete_result()
        self.controller._tagging_results = {"202608": [initial]}
        self.controller._tagging_result_index = {("202608", "weighted"): initial}
        self.controller._complete_analysis_job()
        self.controller._tagging_retry_active = True
        self.controller._tagging_retry_generation = 4
        replacement = replace(
            initial,
            status=TaggingPipelineStatus.AI_CONSENSUS,
            label=TagLabel.ATTRIBUTE,
            reason="不应写入",
            decision_source=TaggingDecisionSource.AI_CONSENSUS,
        )

        self.controller._on_tagging_retry_succeeded(
            3,
            self.controller._analysis_generation,
            TaggingPipelineRun(results=(replacement,), review_items=()),
        )

        self.assertEqual(
            self.controller._tagging_result_index[("202608", "weighted")].status,
            TaggingPipelineStatus.INCOMPLETE,
        )

    def test_statistics_keep_months_physically_separate(self):
        """跨月同词仍生成两个 TaggingResult，并按状态各自统计。"""

        self._start_pipeline()
        self.assertEqual(self.controller._tagging_statistics["total"], 2)
        self.assertEqual(self.controller._tagging_statistics["cache_hit"], 1)
        self.assertEqual(self.controller._tagging_statistics["disagreement"], 1)

    def test_cache_hit_without_review_completes_the_single_analysis_job(self):
        """没有分歧时，缓存/共识结果必须自动完成整个 Analysis Job。"""

        class _CompletedPipelineService(_PipelineService):
            async def tag_monthly_word_results(
                self, snapshot, category_key, progress_callback
            ):
                progress_callback("正在查询历史标签缓存...")
                return TaggingPipelineRun(
                    results=(
                        _result(
                            "202608",
                            TaggingPipelineStatus.CACHE_HIT,
                            TagLabel.ATTRIBUTE,
                        ),
                    ),
                    review_items=(),
                )

        with patch("controllers.main_controller.ProductKnowledgeService"), patch(
            "controllers.main_controller.AmazonProductContextProvider"
        ), patch(
            "controllers.main_controller.TaggingService",
            _CompletedPipelineService,
        ):
            self.controller._start_tagging_pipeline()
        self.application.processEvents()

        self.assertFalse(self.controller._analysis_job_active)
        self.assertEqual(
            self.controller._analysis_pipeline_state,
            AnalysisPipelineState.COMPLETED,
        )
        self.assertFalse(self.window.ui.exportButton.isEnabled())

    def test_main_window_has_no_independent_ai_tagging_entry(self):
        """Designer 主界面只保留开始分析，不能泄露第二个 AI 启动入口。"""

        self.assertFalse(hasattr(self.window.ui, "startAiTaggingButton"))
        self.assertFalse(hasattr(self.window.ui, "aiTaggingStatusLabel"))
        self.assertTrue(hasattr(self.window.ui, "analysisPipelineStatusLabel"))


if __name__ == "__main__":
    unittest.main()
