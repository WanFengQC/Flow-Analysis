"""将正式 Word Result 与本轮标签结果合成为唯一最终数据源。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from models.final_analysis import INTERNAL_FINAL_ANALYSIS_FIELDS
from models.tagging_label import TaggingPipelineResult, TaggingPipelineStatus


class FinalAnalysisService:
    """只合并已完成的内存结果，绝不调用 API、AI、Amazon 或数据库。"""

    _LABEL_SOURCE_BY_STATUS = {
        TaggingPipelineStatus.CACHE_HIT: "历史缓存",
        TaggingPipelineStatus.AI_CONSENSUS: "AI共识",
        TaggingPipelineStatus.HUMAN_REVIEW: "人工审核",
        TaggingPipelineStatus.DISAGREEMENT: "待人工审核",
        TaggingPipelineStatus.INCOMPLETE: "AI失败",
    }

    def build_final_analysis_rows(
        self,
        word_results_by_month: Mapping[str, Sequence[Mapping[str, Any]]],
        tagging_results: Mapping[tuple[str, str], TaggingPipelineResult],
    ) -> list[dict[str, Any]]:
        """按正式 Word Result 原有月份与行顺序构造最终行。"""

        rows: list[dict[str, Any]] = []
        for month, word_results in word_results_by_month.items():
            if not isinstance(month, str) or not isinstance(word_results, Sequence):
                continue
            for word_result in word_results:
                if not isinstance(word_result, Mapping):
                    continue
                word = str(word_result.get("word") or "").strip()
                if not word:
                    continue
                # 保留所有正式业务字段；仅移除明确禁止进入最终展示/导出的
                # ProductContext 内部元数据。
                row = {
                    field: deepcopy(value)
                    for field, value in word_result.items()
                    if field not in INTERNAL_FINAL_ANALYSIS_FIELDS
                }
                row["month"] = month
                row["word"] = word
                row.update(
                    self._tagging_fields(
                        tagging_results.get((month, word))
                    )
                )
                rows.append(row)
        return rows

    def update_tagging_fields(
        self,
        rows: list[dict[str, Any]],
        tagging_result: TaggingPipelineResult,
    ) -> dict[str, Any] | None:
        """在人工审核成功后仅更新对应最终行的标签字段。"""

        identity = (tagging_result.month, tagging_result.word)
        for row in rows:
            if (row.get("month"), row.get("word")) != identity:
                continue
            row.update(self._tagging_fields(tagging_result))
            return row
        return None

    def _tagging_fields(
        self,
        tagging_result: TaggingPipelineResult | None,
    ) -> dict[str, str | None]:
        """将 Pipeline 状态转换为最终表允许公开的三个标签字段。"""

        if tagging_result is None:
            return {
                "label": None,
                "labelSource": "未完成",
                "labelReason": None,
            }

        status = tagging_result.status
        source = self._LABEL_SOURCE_BY_STATUS.get(status, "未完成")
        if status in {
            TaggingPipelineStatus.DISAGREEMENT,
            TaggingPipelineStatus.INCOMPLETE,
        }:
            return {
                "label": None,
                "labelSource": source,
                "labelReason": None,
            }

        label = tagging_result.label
        return {
            "label": label.value if label is not None else None,
            "labelSource": source,
            "labelReason": tagging_result.reason,
        }
