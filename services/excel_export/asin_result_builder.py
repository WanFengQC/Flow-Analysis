"""为每个当前分析 ASIN 生成正式 Word Analysis Sheet。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.excel_export.template_support import (
    apply_cell_style,
    clear_sheet_business_values,
    copy_cell_style,
    copy_column_widths,
    display_ratio,
    month_title,
    sort_months,
    to_excel_value,
)


class AsinResultSheetBuilder:
    """写入分析阶段已生成的单 ASIN Word Result，不读取关键词 RESULT。"""

    HEADER_ROW = 2
    DATA_START_ROW = 3
    BLOCK_WIDTH = 10
    SEPARATOR_WIDTH = 1
    RESULT_HEADERS = (
        "单词", "频次", "打标", "mark", "Weight", "Total", "占比", "自然占比",
        "广告占比", "导入短语数据",
    )
    _TEMPLATE_STYLE_COLUMNS = tuple(range(1, BLOCK_WIDTH + 1))

    def write(self, worksheet, asin: str, asin_word_results: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]], final_rows: Sequence[Mapping[str, Any]]) -> None:
        """把当前 ASIN 的已完成 Word Result 按月写入动态 Sheet。"""

        filtered = {
            month: list(month_results.get(asin, []))
            for month, month_results in asin_word_results.items()
            if isinstance(month_results, Mapping)
        }
        filtered = {month: rows for month, rows in filtered.items() if rows}
        month_order = sort_months(list(filtered))
        title_style = copy_cell_style(worksheet.cell(1, 1))
        header_styles = [
            copy_cell_style(worksheet.cell(self.HEADER_ROW, column))
            for column in range(1, self.BLOCK_WIDTH + 1)
        ]
        data_styles = [
            copy_cell_style(worksheet.cell(self.DATA_START_ROW, column))
            for column in self._TEMPLATE_STYLE_COLUMNS
        ]
        headers = self.RESULT_HEADERS
        clear_sheet_business_values(worksheet)
        if not filtered:
            return
        label_index = self._label_index(final_rows)
        for index, month in enumerate(month_order):
            start_column = 1 + index * (self.BLOCK_WIDTH + self.SEPARATOR_WIDTH)
            copy_column_widths(worksheet, 1, start_column, self.BLOCK_WIDTH)
            end_column = start_column + self.BLOCK_WIDTH - 1
            if start_column != 1:
                worksheet.merge_cells(start_row=1, start_column=start_column, end_row=1, end_column=end_column)
            cell = worksheet.cell(1, start_column, month_title(month))
            apply_cell_style(cell, title_style)
            for offset, header in enumerate(headers):
                cell = worksheet.cell(self.HEADER_ROW, start_column + offset, header)
                apply_cell_style(cell, header_styles[offset])
            for row_index, result_row in enumerate(filtered[month], start=self.DATA_START_ROW):
                values = self._values_for_word(month, result_row, label_index)
                for offset, value in enumerate(values):
                    cell = worksheet.cell(
                        row_index,
                        start_column + offset,
                        to_excel_value(value),
                    )
                    apply_cell_style(cell, data_styles[offset])

    @staticmethod
    def _values_for_word(month: str, word_row: Mapping[str, Any], label_index: Mapping[tuple[str, str], Mapping[str, Any]]) -> tuple[Any, ...]:
        """映射单 ASIN Word Result 与已完成标签，不重算词级指标。"""

        word = str(word_row.get("word") or "").strip()
        final_row = label_index.get((month, word), {})
        return (
            word or None,
            word_row.get("frequency"),
            final_row.get("label"),
            final_row.get("labelReason"),
            word_row.get("weight"),
            word_row.get("total"),
            display_ratio(word_row.get("ratio"), word_row.get("total")),
            word_row.get("naturalRatio"),
            word_row.get("adRatio"),
            AsinResultSheetBuilder._phrases_value(word_row.get("topPhrases")),
        )

    @staticmethod
    def _phrases_value(value: Any) -> str | None:
        """沿用 SUMMARY_result 的短语展示形式，避免列表被 JSON 化写入单元格。"""

        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None
        phrases = [str(item).strip() for item in value if str(item).strip()]
        return " | ".join(phrases) or None

    @staticmethod
    def _label_index(final_rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
        """复用已有 Final Row 的标签字段；导出不接触 AI 或人工审核逻辑。"""

        return {
            (str(row.get("month") or ""), str(row.get("word") or "")): row
            for row in final_rows
            if str(row.get("month") or "") and str(row.get("word") or "")
        }
