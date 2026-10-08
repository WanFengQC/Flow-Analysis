"""在 QThread 中按人工批准规则重算正式词结果。"""

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from models.normalization_rule import ApprovedNormalizationRules
from services.analysis_service import AnalysisService
from services.word_filter_service import WordFilterService


class NormalizationApplyWorker(QObject):
    """只处理固定快照，不读取 Controller、View 或候选的可变状态。"""

    processed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        analysis_service: AnalysisService,
        monthly_results: Mapping[str, list[Mapping[str, Any]]],
        monthly_raw: Mapping[str, list[Mapping[str, Any]]],
        months: list[str],
        approved_rules: ApprovedNormalizationRules,
        allowed_output_words_by_month: Mapping[str, set[str]] | None = None,
        analysis_generation: int | None = None,
    ) -> None:
        """保存点击应用瞬间固定的 RESULT、月份与人工规则快照。"""

        super().__init__()
        self._analysis_service = analysis_service
        self._monthly_results = monthly_results
        self._monthly_raw = monthly_raw
        self._months = list(months)
        self._approved_rules = approved_rules
        self._allowed_output_words_by_month = allowed_output_words_by_month or {}
        # 只用于 Controller 在跨线程回调中识别当前分析 generation。
        self.analysis_generation = analysis_generation

    @Slot()
    def run(self) -> None:
        """逐月独立重算；任何异常都不能把半成品交给 Controller。"""

        try:
            word_results, diagnostics = (
                self._analysis_service.analyze_monthly_word_results(
                    self._monthly_results,
                    self._months,
                    approved_rules=self._approved_rules,
                )
            )
            asin_word_results, asin_diagnostics = (
                self._analysis_service.analyze_monthly_asin_word_results(
                    self._monthly_raw,
                    self._months,
                    approved_rules=self._approved_rules,
                )
            )
            # 筛选发生在 Word Analysis 之后。正式归一只能改写筛选词的
            # canonical，绝不让同一原始搜索词中的其他词重新进入流程。
            if self._allowed_output_words_by_month:
                word_results = WordFilterService.retain_monthly_words(
                    word_results,
                    self._allowed_output_words_by_month,
                )
                asin_word_results = {
                    month: {
                        asin: WordFilterService.retain_monthly_words(
                            {month: rows},
                            self._allowed_output_words_by_month,
                        )[month]
                        for asin, rows in asin_rows.items()
                    }
                    for month, asin_rows in asin_word_results.items()
                }
            # 防御性确认每个请求月份都成功得到独立桶，避免缺失月份被当作
            # “成功的空结果”而覆盖上一份完整正式结果。
            if list(word_results) != self._months:
                raise ValueError("正式词结果缺少月份")
            self.processed.emit(
                {
                    "word_results": word_results,
                    "diagnostics": diagnostics,
                    "asin_word_results": asin_word_results,
                    "asin_diagnostics": asin_diagnostics,
                }
            )
        except Exception:
            # Worker 不泄露可能包含原始业务记录的异常细节；Controller 负责
            # 保留上一份成功结果并给出可理解的状态。
            self.error.emit("正式词结果重算失败")
        finally:
            self.finished.emit()
