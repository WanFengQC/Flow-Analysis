"""在 QThread 中执行纯 Python 搜索词聚合，避免阻塞 Qt UI 主线程。"""

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from services.analysis_service import AnalysisService
from services.normalization_candidate_service import (
    NormalizationCandidateService,
)


class AnalysisWorker(QObject):
    """仅执行 AnalysisService，不接触 View、网络或数据库。"""

    processed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        analysis_service: AnalysisService,
        normalization_candidate_service: NormalizationCandidateService,
        reversing_results_by_month: Mapping[
            str,
            Mapping[str, Mapping[str, Any]],
        ],
        months: list[str],
    ) -> None:
        """保存当前任务的只读输入引用，等待所属 QThread 启动后处理。"""

        super().__init__()
        self._analysis_service = analysis_service
        self._normalization_candidate_service = (
            normalization_candidate_service
        )
        self._reversing_results_by_month = reversing_results_by_month
        self._months = list(months)

    @Slot()
    def run(self) -> None:
        """先生成候选，再生成不含任何归一规则的单词预览。"""

        try:
            processed_data = (
                self._analysis_service.analyze_monthly_reversing_results(
                    self._reversing_results_by_month,
                    self._months,
                )
            )
            # 候选发现只读取月度 RESULT；Seed 只在此处作为发现证据，
            # 不会参与预览或正式词统计。
            processed_data["normalization_candidates"] = (
                self._normalization_candidate_service.discover_candidates(
                    processed_data["results"]
                )
            )
            # 审核前预览明确传入 None，因此绝不应用任何 Seed 或人工规则。
            (
                processed_data["word_preview_results"],
                word_preview_diagnostics,
            ) = self._analysis_service.analyze_monthly_word_results(
                processed_data["results"],
                self._months,
                approved_rules=None,
            )
            processed_data["word_results"] = {}
            for month, diagnostics in word_preview_diagnostics.items():
                processed_data["diagnostics"].setdefault(month, {})[
                    "word_preview"
                ] = diagnostics
            self.processed.emit(processed_data)
        except Exception:
            # 处理层不含认证数据；仍不把内部堆栈或原始 records 透传给 UI。
            self.error.emit("数据处理过程中发生未预期错误")
        finally:
            self.finished.emit()
