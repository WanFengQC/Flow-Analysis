"""在 QThread 中执行纯 Python 搜索词聚合，避免阻塞 Qt UI 主线程。"""

from collections.abc import Mapping
import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from services.analysis_service import AnalysisService


logger = logging.getLogger(__name__)


class AnalysisWorker(QObject):
    """仅执行 AnalysisService，不接触 View、网络或数据库。"""

    # 阶段只描述本 Worker 的处理进度；Controller 负责将其映射为完整
    # Analysis Job 状态，Worker 始终不接触 View。
    stage_changed = Signal(str)
    processed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        analysis_service: AnalysisService,
        reversing_results_by_month: Mapping[
            str,
            Mapping[str, Mapping[str, Any]],
        ],
        months: list[str],
        analysis_generation: int | None = None,
    ) -> None:
        """保存当前任务的只读输入引用，等待所属 QThread 启动后处理。"""

        super().__init__()
        self._analysis_service = analysis_service
        self._reversing_results_by_month = reversing_results_by_month
        self._months = list(months)
        # 仅作 Controller 回调防污染 identity 使用；不参与任何分析计算。
        self.analysis_generation = analysis_generation

    @Slot()
    def run(self) -> None:
        """生成月度结果与无规则词预览，随后暂停等待 Word Filter。"""

        try:
            self.stage_changed.emit("BUILDING_RESULT")
            self._log_pipeline_debug("before RAW")
            processed_data = (
                self._analysis_service.analyze_monthly_reversing_results(
                    self._reversing_results_by_month,
                    self._months,
                )
            )
            self._log_pipeline_debug("after RAW")
            # 审核前预览明确传入 None，因此绝不应用任何 Seed 或人工规则。
            self.stage_changed.emit("ANALYZING_WORDS")
            self._log_pipeline_debug("before word analysis")
            (
                processed_data["word_preview_results"],
                word_preview_diagnostics,
            ) = self._analysis_service.analyze_monthly_word_results(
                processed_data["results"],
                self._months,
                approved_rules=None,
            )
            (
                processed_data["asin_word_preview_results"],
                asin_word_preview_diagnostics,
            ) = self._analysis_service.analyze_monthly_asin_word_results(
                processed_data["raw"],
                self._months,
                approved_rules=None,
            )
            self._log_pipeline_debug("after word analysis")
            processed_data["word_results"] = {}
            processed_data["asin_word_results"] = {}
            for month, diagnostics in word_preview_diagnostics.items():
                processed_data["diagnostics"].setdefault(month, {})[
                    "word_preview"
                ] = diagnostics
            for month, diagnostics in asin_word_preview_diagnostics.items():
                processed_data["diagnostics"].setdefault(month, {})[
                    "asin_word_preview"
                ] = diagnostics
            # Word Filter 必须发生在 Word Analysis 后、候选发现前。候选仅能
            # 由 Controller 在用户确认筛选条件后基于筛选快照生成。
            self.processed.emit(processed_data)
        except Exception:
            # 处理层不含认证数据；仍不把内部堆栈或原始 records 透传给 UI。
            self.error.emit("数据处理过程中发生未预期错误")
        finally:
            self.finished.emit()

    def _log_pipeline_debug(self, message: str) -> None:
        """记录后台 CPU 阶段，日志中不包含业务原始数据或认证信息。"""

        logger.debug(
            "[AnalysisPipeline] %s thread=%s ident=%s generation=%s",
            message,
            threading.current_thread().name,
            threading.get_ident(),
            self.analysis_generation,
        )
