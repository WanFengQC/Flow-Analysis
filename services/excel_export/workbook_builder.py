"""协调模板工作簿中五类业务 Sheet 的写入，不包含业务计算。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.excel_export.asin_raw_builder import AsinRawSheetBuilder
from services.excel_export.asin_result_builder import AsinResultSheetBuilder
from services.excel_export.result_list_builder import ResultListSheetBuilder
from services.excel_export.summary_raw_builder import SummaryRawSheetBuilder
from services.excel_export.template_support import clear_sheet_business_values


class WorkbookBuilder:
    """按模板角色写入运行时快照，并为多 ASIN 复制示例 Sheet。"""

    SUMMARY_RAW_SHEET = "SUMMARY_raw"
    SUMMARY_RESULT_LIST_SHEET = "SUMMARY_result_list"
    SAMPLE_RESULT_SUFFIX = "_result"
    SAMPLE_RAW_SUFFIX = "_raw"

    def __init__(self) -> None:
        """创建无状态 Sheet Builder；所有数据由 export_analysis 显式传入。"""

        self._summary_raw_builder = SummaryRawSheetBuilder()
        self._result_list_builder = ResultListSheetBuilder()
        self._asin_result_builder = AsinResultSheetBuilder()
        self._asin_raw_builder = AsinRawSheetBuilder()

    def populate_runtime_sheets(self, workbook, *, analysis_raw: Mapping[str, Sequence[Mapping[str, Any]]], reversing_results: Mapping[str, Mapping[str, Mapping[str, Any]]], analysis_word_results: Mapping[str, Sequence[Mapping[str, Any]]], analysis_asin_word_results: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]], final_rows: Sequence[Mapping[str, Any]]) -> None:
        """写入四个非 SUMMARY_result Sheet，且先清除模板业务数据。"""

        self._require_sheet(workbook, self.SUMMARY_RAW_SHEET)
        self._require_sheet(workbook, self.SUMMARY_RESULT_LIST_SHEET)
        self._summary_raw_builder.write(workbook[self.SUMMARY_RAW_SHEET], analysis_raw)
        self._result_list_builder.write(
            workbook[self.SUMMARY_RESULT_LIST_SHEET],
            analysis_word_results,
            final_rows,
        )
        self._write_asin_sheets(
            workbook,
            analysis_raw,
            reversing_results,
            analysis_asin_word_results,
            final_rows,
        )

    def _write_asin_sheets(self, workbook, analysis_raw: Mapping[str, Sequence[Mapping[str, Any]]], reversing_results: Mapping[str, Mapping[str, Mapping[str, Any]]], analysis_asin_word_results: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]], final_rows: Sequence[Mapping[str, Any]]) -> None:
        """将示例 ASIN Sheet 替换为本轮 ASIN；多 ASIN 时复制相同模板样式。"""

        sample_result, sample_raw = self._find_sample_asin_sheets(workbook)
        asins = self._collect_asins(analysis_raw, reversing_results)
        if not asins:
            clear_sheet_business_values(sample_result)
            clear_sheet_business_values(sample_raw)
            return

        # <ASIN>_result 只能展示分析阶段产出的 ASIN 级 Word Result。
        # 缺失时必须显式失败，绝不能退回到关键词 RESULT 或在导出层重算。
        missing_asins = [
            asin
            for asin in asins
            if not any(
                isinstance(month_results, Mapping) and asin in month_results
                for month_results in analysis_asin_word_results.values()
            )
        ]
        if missing_asins:
            raise ValueError(
                "当前运行时缺少 ASIN Word Analysis 数据："
                + "、".join(missing_asins)
            )

        # 必须先从“空白模板”复制出全部 ASIN Sheet，再逐个写入业务数据。
        # 若先写第一个 ASIN 再复制，openpyxl 会反复复制整张已填满的数据表，
        # 多 ASIN 导出会造成大量无效内存和保存时间。
        asin_sheets = [(asins[0], sample_result, sample_raw)]
        for asin in asins[1:]:
            asin_sheets.append((
                asin,
                workbook.copy_worksheet(sample_result),
                workbook.copy_worksheet(sample_raw),
            ))

        for asin, result_sheet, raw_sheet in asin_sheets:
            result_sheet.title = f"{asin}{self.SAMPLE_RESULT_SUFFIX}"
            raw_sheet.title = f"{asin}{self.SAMPLE_RAW_SUFFIX}"
            self._asin_result_builder.write(
                result_sheet,
                asin,
                analysis_asin_word_results,
                final_rows,
            )
            self._asin_raw_builder.write(raw_sheet, asin, analysis_raw)

    def _find_sample_asin_sheets(self, workbook):
        """定位模板唯一的示例 ASIN Sheet 对，避免把固定示例名称写入导出结果。"""

        result_sheets = [sheet for sheet in workbook.worksheets if sheet.title.endswith(self.SAMPLE_RESULT_SUFFIX) and sheet.title != "SUMMARY_result"]
        raw_sheets = [sheet for sheet in workbook.worksheets if sheet.title.endswith(self.SAMPLE_RAW_SUFFIX) and sheet.title != self.SUMMARY_RAW_SHEET]
        if len(result_sheets) != 1 or len(raw_sheets) != 1:
            raise ValueError("Excel 模板必须包含一组示例 ASIN_result / ASIN_raw 工作表")
        return result_sheets[0], raw_sheets[0]

    @staticmethod
    def _collect_asins(analysis_raw: Mapping[str, Sequence[Mapping[str, Any]]], reversing_results: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[str]:
        """按当前任务首次出现顺序收集 ASIN，绝不引用模板中的历史 ASIN。"""

        asins: list[str] = []
        seen: set[str] = set()
        for month_results in reversing_results.values():
            if not isinstance(month_results, Mapping):
                continue
            for asin in month_results:
                normalized = str(asin).strip().upper()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    asins.append(normalized)
        for month_rows in analysis_raw.values():
            for row in month_rows:
                normalized = str(row.get("source_asin") or "").strip().upper()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    asins.append(normalized)
        return asins

    @staticmethod
    def _require_sheet(workbook, sheet_name: str) -> None:
        """模板缺少固定结构时明确失败，避免静默导出不完整工作簿。"""

        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Excel 模板缺少 {sheet_name} 工作表")
