"""将全部 ASIN 的正式 Word Analysis 写入 SUMMARY_result_list。"""

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


class ResultListSheetBuilder:
    """以正式 Word Result 填充纵向明细，不使用关键词 RESULT。"""

    SHEET_NAME = "SUMMARY_result_list"
    HEADER_ROW = 2
    DATA_START_ROW = 3
    BLOCK_WIDTH = 12
    SEPARATOR_WIDTH = 1
    RESULT_LIST_HEADERS = (
        "时间", "父ASIN", "单词", "频次", "打标", "mark", "Weight", "Total",
        "占比", "自然占比", "广告占比", "导入短语数据",
    )
    _TEMPLATE_STYLE_COLUMNS = tuple(range(1, BLOCK_WIDTH + 1))

    def write(self, worksheet, word_results: Mapping[str, Sequence[Mapping[str, Any]]], final_rows: Sequence[Mapping[str, Any]]) -> None:
        """写入每个月独立的正式 Word Result；没有结果的月份不产生伪造行。"""

        # 先保留参考样式，随后移除模板中的全部旧业务值。
        title_style = copy_cell_style(worksheet.cell(1, 1))
        header_styles = [
            copy_cell_style(worksheet.cell(self.HEADER_ROW, column))
            for column in range(1, self.BLOCK_WIDTH + 1)
        ]
        data_styles = [
            copy_cell_style(worksheet.cell(self.DATA_START_ROW, column))
            for column in self._TEMPLATE_STYLE_COLUMNS
        ]
        headers = self.RESULT_LIST_HEADERS
        clear_sheet_business_values(worksheet)
        month_order = sort_months([month for month, rows in word_results.items() if rows])
        if not month_order:
            return
        label_index = self._label_index(final_rows)
        for index, month in enumerate(month_order):
            start_column = 1 + index * (self.BLOCK_WIDTH + self.SEPARATOR_WIDTH)
            self._write_month_block(worksheet, start_column, month, word_results[month], label_index, headers, title_style, header_styles, data_styles)

    def _write_month_block(self, worksheet, start_column: int, month: str, rows: Sequence[Mapping[str, Any]], label_index: Mapping[tuple[str, str], Mapping[str, Any]], headers: Sequence[Any], title_style: Mapping[str, Any], header_styles: Sequence[Mapping[str, Any]], data_styles: Sequence[Mapping[str, Any]]) -> None:
        """将已完成的 Word Result 写入原模板 Word 明细布局。"""

        copy_column_widths(worksheet, 1, start_column, self.BLOCK_WIDTH)
        end_column = start_column + self.BLOCK_WIDTH - 1
        if start_column != 1:
            worksheet.merge_cells(start_row=1, start_column=start_column, end_row=1, end_column=end_column)
        title_cell = worksheet.cell(1, start_column, month_title(month))
        apply_cell_style(title_cell, title_style)
        for offset, header in enumerate(headers):
            cell = worksheet.cell(self.HEADER_ROW, start_column + offset, header)
            apply_cell_style(cell, header_styles[offset])
        for row_index, result_row in enumerate(rows, start=self.DATA_START_ROW):
            for offset, value in enumerate(self._values_for_word(month, result_row, label_index)):
                cell = worksheet.cell(
                    row_index,
                    start_column + offset,
                    to_excel_value(value),
                )
                apply_cell_style(cell, data_styles[offset])

    @staticmethod
    def _values_for_word(month: str, word_row: Mapping[str, Any], label_index: Mapping[tuple[str, str], Mapping[str, Any]]) -> tuple[Any, ...]:
        """直接映射正式词结果及已完成标签，不在导出阶段计算任何词指标。"""

        source_stats = word_row.get("sourceAsinStats")
        source_asins = " | ".join(sorted(source_stats)) if isinstance(source_stats, Mapping) else None
        word = str(word_row.get("word") or "").strip()
        final_row = label_index.get((month, word), {})
        return (
            month_title(month),
            source_asins,
            word or None,
            word_row.get("frequency"),
            final_row.get("label"),
            final_row.get("labelReason"),
            word_row.get("weight"),
            word_row.get("total"),
            display_ratio(word_row.get("ratio"), word_row.get("total")),
            word_row.get("naturalRatio"),
            word_row.get("adRatio"),
            ResultListSheetBuilder._phrases_value(word_row.get("topPhrases")),
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
        """用既有 Final Row 的标签补充 Word 明细，绝不调用 Tagging 服务。"""

        return {
            (str(row.get("month") or ""), str(row.get("word") or "")): row
            for row in final_rows
            if str(row.get("month") or "") and str(row.get("word") or "")
        }
