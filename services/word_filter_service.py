"""Word Analysis 完成后的只读筛选服务。"""

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import Any


class WordFilterService:
    """按月筛选 Word Result，绝不修改调用方保留的原始结果。"""

    METRICS = (
        "weeklyExposure",
        "abaWeeklyRank",
        "monthlySearches",
        "frequency",
    )

    def filter_monthly_word_results(
        self,
        monthly_word_results: Mapping[str, list[Mapping[str, Any]]],
        conditions: Mapping[str, Any],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        """以 AND 关系筛选各月词结果，并返回独立快照和数量诊断。"""

        normalized_conditions = self.normalize_conditions(conditions)
        enabled = bool(normalized_conditions["enabled"])
        output: dict[str, list[dict[str, Any]]] = {}
        by_month: dict[str, dict[str, int]] = {}

        for month, rows in monthly_word_results.items():
            safe_rows = rows if isinstance(rows, list) else []
            selected_rows = [
                deepcopy(dict(row))
                for row in safe_rows
                if isinstance(row, Mapping)
                and (
                    not enabled
                    or self._matches_conditions(row, normalized_conditions)
                )
            ]
            output[month] = selected_rows
            by_month[month] = {
                "total": len(safe_rows),
                "filtered": len(selected_rows),
            }

        return output, {
            "enabled": enabled,
            "conditions": normalized_conditions,
            "total": sum(item["total"] for item in by_month.values()),
            "filtered": sum(item["filtered"] for item in by_month.values()),
            "byMonth": by_month,
        }

    def filter_monthly_asin_word_results(
        self,
        monthly_asin_word_results: Mapping[
            str,
            Mapping[str, list[Mapping[str, Any]]],
        ],
        filtered_word_results: Mapping[str, list[Mapping[str, Any]]],
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """按全局筛选后的词集合裁剪 ASIN Word Result，不在导出阶段重算。"""

        output: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for month, asin_rows in monthly_asin_word_results.items():
            allowed_words = {
                str(row.get("word") or "").strip()
                for row in filtered_word_results.get(month, [])
                if isinstance(row, Mapping)
                and str(row.get("word") or "").strip()
            }
            month_output: dict[str, list[dict[str, Any]]] = {}
            for asin, rows in (asin_rows.items() if isinstance(asin_rows, Mapping) else []):
                month_output[str(asin)] = [
                    deepcopy(dict(row))
                    for row in rows
                    if isinstance(row, Mapping)
                    and str(row.get("word") or "").strip() in allowed_words
                ]
            output[month] = month_output
        return output

    @staticmethod
    def retain_monthly_words(
        monthly_word_results: Mapping[str, list[Mapping[str, Any]]],
        allowed_words_by_month: Mapping[str, set[str]],
    ) -> dict[str, list[dict[str, Any]]]:
        """仅保留明确允许的规范词，用于归一重算后阻止词回流。"""

        return {
            month: [
                deepcopy(dict(row))
                for row in rows
                if isinstance(row, Mapping)
                and str(row.get("word") or "").strip()
                in allowed_words_by_month.get(month, set())
            ]
            for month, rows in monthly_word_results.items()
        }

    @classmethod
    def normalize_conditions(cls, conditions: Mapping[str, Any]) -> dict[str, Any]:
        """校验可选上下限；空值代表该边界不参与筛选。"""

        normalized: dict[str, Any] = {"enabled": bool(conditions.get("enabled"))}
        for metric in cls.METRICS:
            minimum = cls._as_optional_number(conditions.get(f"{metric}Min"))
            maximum = cls._as_optional_number(conditions.get(f"{metric}Max"))
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{metric} 的最小值不能大于最大值")
            normalized[f"{metric}Min"] = minimum
            normalized[f"{metric}Max"] = maximum
        return normalized

    @classmethod
    def _matches_conditions(
        cls,
        row: Mapping[str, Any],
        conditions: Mapping[str, Any],
    ) -> bool:
        """所有已填写条件以 AND 关系判断；缺失指标不能伪装成零。"""

        for metric in cls.METRICS:
            minimum = conditions.get(f"{metric}Min")
            maximum = conditions.get(f"{metric}Max")
            if minimum is None and maximum is None:
                continue
            value = cls._metric_value(row, metric)
            if value is None:
                return False
            if minimum is not None and value < minimum:
                return False
            if maximum is not None and value > maximum:
                return False
        return True

    @classmethod
    def _metric_value(
        cls,
        row: Mapping[str, Any],
        metric: str,
    ) -> float | None:
        """从现有 Word Result 来源统计提取原查询筛选指标。"""

        if metric == "frequency":
            return cls._as_optional_number(row.get(metric))

        source_asin_stats = row.get("sourceAsinStats")
        if not isinstance(source_asin_stats, Mapping):
            return None
        field_name = {
            "weeklyExposure": "exposure",
            "abaWeeklyRank": "abaWeeklyRank",
            "monthlySearches": "searches",
        }.get(metric)
        if field_name is None:
            return None
        values = [
            cls._as_optional_number(stats.get(field_name))
            for stats in source_asin_stats.values()
            if isinstance(stats, Mapping)
        ]
        valid_values = [value for value in values if value is not None]
        if not valid_values:
            return None
        # ABA 周排名越小越靠前；AnalysisService 对来源 ASIN 已使用同一口径
        # 保留最小 rank。多 ASIN 聚合的 word 继续选择其中最靠前的真实排名。
        if metric == "abaWeeklyRank":
            return min(valid_values)
        return sum(valid_values)

    @staticmethod
    def _as_optional_number(value: Any) -> float | None:
        """只接纳有限数值；空文本是 UI 未填写边界。"""

        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError("筛选条件必须是数值")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("筛选条件必须是数值") from error
        if not isfinite(numeric):
            raise ValueError("筛选条件必须是有限数值")
        return numeric
