"""将当前月度 RAW 行写入 SUMMARY_raw 模板 Sheet。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.excel_export.template_support import (
    apply_cell_style,
    copy_cell_style,
    month_title,
    sort_months,
    to_excel_value,
)


class SummaryRawSheetBuilder:
    """复刻参考 SUMMARY_raw 的 70 列横向月份区块，不重算 RAW 数据。"""

    SHEET_NAME = "SUMMARY_raw"
    HEADER_ROW = 2
    DATA_START_ROW = 3
    BLOCK_WIDTH = 69
    SEPARATOR_WIDTH = 1
    # 参考 SUMMARY_raw 的原始 70 列顺序。模板被刻意脱敏后不保留表头文本，
    # 因此字段结构在 Builder 中显式定义，值仍完全来自本轮运行时 RAW。
    RAW_HEADERS = (
        "搜索词", "预估周曝光量", "ABA周排名", "月搜索量", "展示量", "点击量",
        "naturalRatio", "adRatio", "source_asin", "guestId",
        "station", "keywords", "keywordCn", "keywordJp", "searches", "products",
        "purchases", "purchaseRate", "bid", "bidMax", "bidMin", "minPhrasePpc",
        "maxPhrasePpc", "phrasePpc", "minBroadPpc", "maxBroadPpc", "broadPpc",
        "minExactPpc", "maxExactPpc", "exactPpc", "badges", "position", "positions",
        "gkDatas", "top10Asin", "rankPosition", "adPosition", "updatedTime",
        "searchesRank", "searchesRankTimeFrom", "searchesRankTimeTo", "latest1daysAds",
        "latest7daysAds", "latest30daysAds", "supplyDemandRatio", "searchesTrend",
        "trafficPercentage", "trafficKeywordTypes", "conversionKeywordTypes",
        "calculatedWeeklySearches", "araClickTop3", "titleDensityExact", "cprExact",
        "avgPrice", "avgReviews", "avgRating", "ac", "payload_naturalRatio",
        "recommendRatio", "payload_adRatio", "monopolyClickRate", "top3ClickingRate",
        "top3ConversionRate", "clicks", "impressions", "latestMonth", "searchesGrowth",
        "yearlyGrowthRate", "guestVisited",
    )
    _DISPLAY_FIELD_MAP = {
        "搜索词": "keywords",
        "预估周曝光量": "calculatedWeeklySearches",
        "ABA周排名": "searchesRank",
        "月搜索量": "searches",
        "展示量": "impressions",
        "点击量": "clicks",
    }
    # 原模板第 10 列为 source_parent_asin 的灰色列。该字段不再导出；后续
    # 列必须沿用其原本的样式和列宽，不能继承已删除列的灰色样式。
    _TEMPLATE_STYLE_COLUMNS = tuple(range(1, 10)) + tuple(range(11, 71))

    def write(self, worksheet, analysis_raw: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        """按月份写入现有 RAW 快照，月份之间不合并。"""

        # 先读取模板表头，再清理历史业务值；清理后不能从 Sheet 回读表头。
        header_styles = [
            copy_cell_style(worksheet.cell(self.HEADER_ROW, column))
            for column in self._TEMPLATE_STYLE_COLUMNS
        ]
        data_styles = [
            copy_cell_style(worksheet.cell(self.DATA_START_ROW, column))
            for column in self._TEMPLATE_STYLE_COLUMNS
        ]
        column_widths = [
            worksheet.column_dimensions[
                worksheet.cell(self.HEADER_ROW, column).column_letter
            ].width
            for column in self._TEMPLATE_STYLE_COLUMNS
        ]
        title_style = copy_cell_style(worksheet.cell(1, 1))
        headers = self.RAW_HEADERS
        self.prepare_template(worksheet)
        month_order = sort_months([month for month, rows in analysis_raw.items() if rows])
        if not month_order:
            # 空数据 Sheet 仍保持模板的首行合并结构；不能因清理历史数据而
            # 让工作簿结构在不同导出之间变化。
            self.restore_empty_template_structure(worksheet)
            return

        for month_index, month in enumerate(month_order):
            start_column = 1 + month_index * (self.BLOCK_WIDTH + self.SEPARATOR_WIDTH)
            self._write_month_block(
                worksheet, start_column, month, analysis_raw[month], headers,
                title_style, header_styles, data_styles, column_widths,
            )

    def prepare_template(self, worksheet) -> None:
        """清理 RAW 历史业务区，只保留表头和一行格式样本。

        参考模板预创建了数十万空白样式单元格。保留它们会让 openpyxl 在
        Windows 上反复加载、复制、保存无效对象。真实数据行仍由 write()
        使用同一行样式写入，因此业务布局和实际数据格式不变。
        """

        for merged_range in tuple(worksheet.merged_cells.ranges):
            worksheet.unmerge_cells(str(merged_range))

        for coordinate, cell in tuple(worksheet._cells.items()):
            row, _ = coordinate
            if row > self.DATA_START_ROW:
                del worksheet._cells[coordinate]
                continue
            cell.value = None
            cell.comment = None
            cell.hyperlink = None

    def restore_empty_template_structure(self, worksheet) -> None:
        """恢复无数据 RAW Sheet 所需的首行合并结构。"""

        worksheet.merge_cells(
            start_row=1,
            start_column=1,
            end_row=1,
            end_column=self.BLOCK_WIDTH,
        )

    def _write_month_block(self, worksheet, start_column: int, month: str, rows: Sequence[Mapping[str, Any]], headers: Sequence[Any], title_style: Mapping[str, Any], header_styles: Sequence[Mapping[str, Any]], data_styles: Sequence[Mapping[str, Any]], column_widths: Sequence[float | None]) -> None:
        """写入一个独立月份，字段值只取已经生成的 RAW 行。"""

        for offset, width in enumerate(column_widths):
            worksheet.column_dimensions[
                worksheet.cell(
                    self.HEADER_ROW,
                    start_column + offset,
                ).column_letter
            ].width = width
        end_column = start_column + self.BLOCK_WIDTH - 1
        worksheet.merge_cells(start_row=1, start_column=start_column, end_row=1, end_column=end_column)
        title_cell = worksheet.cell(1, start_column, month_title(month))
        apply_cell_style(title_cell, title_style)

        for offset, header in enumerate(headers):
            cell = worksheet.cell(self.HEADER_ROW, start_column + offset, header)
            apply_cell_style(cell, header_styles[offset])

        for row_index, raw_row in enumerate(rows, start=self.DATA_START_ROW):
            for offset, header in enumerate(headers):
                field_name = self._field_name(header)
                cell = worksheet.cell(
                    row_index,
                    start_column + offset,
                    to_excel_value(raw_row.get(field_name)),
                )
                apply_cell_style(cell, data_styles[offset])

    @classmethod
    def _field_name(cls, header: Any) -> str:
        """把参考表中文展示列映射回运行时 RAW 的真实字段。"""

        header_text = str(header or "").strip()
        return cls._DISPLAY_FIELD_MAP.get(header_text, header_text)
