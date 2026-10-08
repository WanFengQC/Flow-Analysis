"""为每个当前分析 ASIN 写入未归一化的 reversing RAW Sheet。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.excel_export.summary_raw_builder import SummaryRawSheetBuilder


class AsinRawSheetBuilder:
    """从现有 analysis_raw 过滤单个 ASIN；不读取或修改 Word/Tagging 数据。"""

    def write(self, worksheet, asin: str, analysis_raw: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        """将指定 ASIN 的原始 reversing 行按月份横向写入。"""

        per_asin_raw = {
            month: [row for row in rows if str(row.get("source_asin") or "").upper() == asin]
            for month, rows in analysis_raw.items()
        }
        SummaryRawSheetBuilder().write(worksheet, {month: rows for month, rows in per_asin_raw.items() if rows})
