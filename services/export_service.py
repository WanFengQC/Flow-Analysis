"""Final Analysis Dataset 的 SUMMARY_result Excel 导出服务。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import copy
import logging
from numbers import Number
import os
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.utils import get_column_letter

from config.settings import EXPORT_TEMPLATE_PATH
from services.excel_export.template_support import display_ratio
from services.excel_export.export_diagnostics import (
    ExportDiagnostics,
    MemoryPeakSampler,
)
from services.excel_export.workbook_builder import WorkbookBuilder


class ExportServiceError(RuntimeError):
    """表示模板、文件系统或工作簿写入失败，供 Controller 展示简短错误。"""


logger = logging.getLogger(__name__)


class ExportService:
    """仅将 Final Analysis Dataset 写为与参考 SUMMARY_result 一致的工作簿。"""

    SHEET_NAME = "SUMMARY_result"
    TAG_LABELS = (
        "1核心词", "2外形", "3属性", "4痛点", "5规格", "6受众", "7场景", "8品牌", "无效词",
    )
    SUMMARY_HEADERS = ("打标", "weight", "Total", "占比", "自然占比", "广告占比", "打标weight占比")
    DETAIL_HEADERS = ("单词", "频次", "打标", "mark", "Weight", "Total", "占比", "自然占比", "广告占比", "导入短语数据")
    MONTH_BLOCK_WIDTH = 10
    MONTH_SEPARATOR_WIDTH = 1
    DEFAULT_COLUMN_WIDTH = 10.0
    DEFAULT_ROW_HEIGHT = 15.0

    def __init__(self, *, collect_memory_diagnostics: bool = False) -> None:
        """创建导出服务；峰值内存仅在开发/测试显式启用时采集。"""

        self._collect_memory_diagnostics = collect_memory_diagnostics
        self.last_diagnostics: ExportDiagnostics | None = None

    def export_analysis(
        self,
        *,
        rows: Sequence[Mapping[str, Any]],
        output_path: str | Path,
        analysis_raw: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        reversing_results: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
        analysis_word_results: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        analysis_asin_word_results: Mapping[
            str,
            Mapping[str, Sequence[Mapping[str, Any]]],
        ] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> Path:
        """以参考模板导出最终结果及当前任务已生成的四类运行时数据。"""

        diagnostics = ExportDiagnostics()
        self.last_diagnostics = diagnostics
        if not rows:
            raise ExportServiceError("没有可导出的最终分析结果")
        month_rows = self._group_rows_by_month(rows)
        if not month_rows:
            raise ExportServiceError("最终分析结果缺少有效月份")
        output = Path(output_path)
        memory_sampler: MemoryPeakSampler | None = None
        if self._collect_memory_diagnostics:
            memory_sampler = MemoryPeakSampler()
            memory_sampler.start()
        workbook = None
        temporary_output = output.with_name(f".{output.name}.part")
        export_succeeded = False
        try:
            self._report_progress(progress_callback, "正在加载 Excel 模板...")
            with diagnostics.phase("load_workbook"):
                workbook = load_workbook(self._template_path())
            if self.SHEET_NAME not in workbook.sheetnames:
                raise ExportServiceError("Excel 模板缺少 SUMMARY_result 工作表")

            self._report_progress(progress_callback, "正在生成汇总词表...")
            with diagnostics.phase("SUMMARY_result_build"):
                worksheet = workbook[self.SHEET_NAME]
                styles, thin_side, medium_side, show_gridlines = self._capture_template(worksheet)
                self._clear_template_values(worksheet, show_gridlines)
                month_order = self._sort_months(month_rows)
                row_plan = self._build_row_plan(month_rows, month_order)
                for month_index, month in enumerate(month_order):
                    start_column = 1 + month_index * (self.MONTH_BLOCK_WIDTH + self.MONTH_SEPARATOR_WIDTH)
                    self._write_month_block(
                        worksheet=worksheet,
                        start_column=start_column,
                        month=month,
                        rows=month_rows[month],
                        row_plan=row_plan,
                        styles=styles,
                    )
                self._apply_all_block_borders(
                    worksheet=worksheet,
                    month_count=len(month_order),
                    row_plan=row_plan,
                    thin_side=thin_side,
                    medium_side=medium_side,
                )
                self._set_sheet_dimensions(
                    worksheet=worksheet,
                    month_count=len(month_order),
                    last_row=row_plan["last_row"],
                )
            diagnostics.data_rows["SUMMARY_result"] = len(rows)
            # 四个非 SUMMARY_result Sheet 仅消费 Controller 传入的现有快照；
            # ExportService 不调用 AI、Amazon、SellerSprite，也不触发业务重算。
            self._report_progress(progress_callback, "正在生成 RAW 与 ASIN 分表...")
            WorkbookBuilder().populate_runtime_sheets(
                workbook,
                analysis_raw=analysis_raw or {},
                reversing_results=reversing_results or {},
                analysis_word_results=analysis_word_results or {},
                analysis_asin_word_results=analysis_asin_word_results or {},
                final_rows=rows,
                diagnostics=diagnostics,
            )
            self._report_progress(progress_callback, "正在压缩并保存 Excel...")
            output.parent.mkdir(parents=True, exist_ok=True)
            with diagnostics.phase("workbook_save"):
                workbook.save(temporary_output)
            # 只有 ZIP 已完整关闭且保存成功后，才原子替换正式文件，避免失败时
            # 留下看似可打开的半成品 xlsx。
            os.replace(temporary_output, output)
            diagnostics.sheet_count = len(workbook.sheetnames)
            diagnostics.output_bytes = output.stat().st_size
            diagnostics.finish()
            logger.info("Excel export diagnostics: %s", diagnostics.to_log_fields())
            export_succeeded = True
            return output
        except ExportServiceError:
            raise
        except ValueError as exc:
            raise ExportServiceError(str(exc)) from exc
        except OSError as exc:
            raise ExportServiceError(
                f"Excel 导出失败：{exc}"
            ) from exc
        finally:
            if workbook is not None:
                workbook.close()
            if not export_succeeded:
                try:
                    temporary_output.unlink(missing_ok=True)
                except OSError:
                    logger.warning("未能清理失败导出的临时文件", exc_info=True)
            if memory_sampler is not None:
                diagnostics.peak_memory_bytes = memory_sampler.stop()

    @staticmethod
    def _report_progress(callback: Callable[[str], None] | None, message: str) -> None:
        """进度提示不可影响正式导出。"""

        if callback is None:
            return
        try:
            callback(message)
        except Exception:
            pass

    @classmethod
    def _template_path(cls) -> Path:
        """返回配置层统一维护的完整工作簿模板路径。"""

        return EXPORT_TEMPLATE_PATH

    @classmethod
    def _group_rows_by_month(cls, rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
        """保持输入顺序，物理隔离自然月与最近30天的最终词结果。"""

        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            raw_month = row.get("month")
            # SellerSprite 用精确空字符串表示最近30天；它是合法的独立时间
            # 范围，不能与缺失 month（None）或空白字符串混为一谈。
            if raw_month == "":
                grouped.setdefault("", []).append(row)
                continue
            if not isinstance(raw_month, str):
                continue
            month = raw_month.strip()
            if month:
                grouped.setdefault(month, []).append(row)
        return grouped

    @classmethod
    def _sort_months(cls, month_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
        """按自然月份从新到旧排列；异常月份仍保留稳定输入顺序。"""

        input_order = {month: index for index, month in enumerate(month_rows)}

        def sort_key(month: str) -> tuple[int, int]:
            digits = "".join(character for character in month if character.isdigit())
            if len(digits) >= 6:
                return (1, int(digits[:6]))
            return (0, -input_order[month])

        return sorted(month_rows, key=sort_key, reverse=True)

    @classmethod
    def _build_row_plan(cls, month_rows: Mapping[str, Sequence[Mapping[str, Any]]], month_order: Sequence[str]) -> dict[str, Any]:
        """按同类标签跨月最大行数规划横向并排的详情区域。"""

        cursor = 16
        detail_rows: dict[str, int] = {}
        maximum_rows: dict[str, int] = {}
        for label in cls.TAG_LABELS:
            maximum = max(
                1,
                *(sum(1 for row in month_rows[month] if cls._group_label(row) == label) for month in month_order),
            )
            detail_rows[label] = cursor
            maximum_rows[label] = maximum
            # 类别标题 + 字段标题 + 数据行 + 三个空白行。
            cursor += 2 + maximum + 3
        return {
            "summary_end_row": 11,
            "detail_rows": detail_rows,
            "maximum_rows": maximum_rows,
            "last_row": cursor - 1,
        }

    @classmethod
    def _capture_template(cls, worksheet) -> tuple[dict[str, dict[str, Any]], Side, Side, bool | None]:
        """从清洗后的参考模板抽取样式，避免在代码中重新猜测样式。"""

        coordinates = {
            "month_left": "A1", "month_metric": "B1", "month_ratio": "D1",
            "summary_header_left": "A2", "summary_header": "B2",
            "summary_data_left": "A3", "summary_data_metric": "B3", "summary_data_ratio": "D3", "summary_data_share": "G3",
            "category_left": "A16", "category_fill": "B16", "category_metric": "E16", "category_ratio": "G16", "category_right": "J16",
            "detail_header_left": "A17", "detail_header": "B17", "detail_header_right": "J17",
            "detail_left": "A18", "detail_integer": "B18", "detail_mark": "D18", "detail_number": "E18", "detail_ratio": "G18", "detail_percent": "H18", "detail_phrase": "J18",
        }
        styles = {name: cls._copy_cell_style(worksheet[coordinate]) for name, coordinate in coordinates.items()}
        return (
            styles,
            copy(worksheet["B1"].border.left),
            copy(worksheet["A1"].border.left),
            worksheet.sheet_view.showGridLines,
        )

    @staticmethod
    def _copy_cell_style(cell) -> dict[str, Any]:
        """捕获完整样式数组，避免大工作簿逐格复制样式对象。"""

        return {"_style": copy(cell._style)}

    @staticmethod
    def _apply_cell_style(cell, style: Mapping[str, Any]) -> None:
        """将捕获的样式数组写到新单元格。"""

        cell._style = style["_style"]

    @classmethod
    def _clear_template_values(cls, worksheet, show_gridlines: bool | None) -> None:
        """保留工作表对象但移除模板的旧行与值，防止历史内容泄漏。"""

        if worksheet.max_row:
            worksheet.delete_rows(1, worksheet.max_row)
        worksheet.auto_filter.ref = None
        worksheet.freeze_panes = None
        worksheet.sheet_view.showGridLines = show_gridlines
        worksheet.sheet_format.defaultRowHeight = cls.DEFAULT_ROW_HEIGHT

    @classmethod
    def _write_month_block(cls, *, worksheet, start_column: int, month: str, rows: Sequence[Mapping[str, Any]], row_plan: Mapping[str, Any], styles: Mapping[str, Mapping[str, Any]]) -> None:
        """写入一个月份的顶部汇总和九类详情，且不读取其他月份数据。"""

        rows_by_label = {label: [row for row in rows if cls._group_label(row) == label] for label in cls.TAG_LABELS}
        month_metrics = cls._calculate_metrics(rows)
        cls._write_styled_values(
            worksheet, 1, start_column,
            (cls._month_title(month), month_metrics["weight_wan"], month_metrics["total_wan"], month_metrics["ratio"], month_metrics["natural_ratio"], month_metrics["ad_ratio"], None),
            ("month_left", "month_metric", "month_metric", "month_ratio", "month_ratio", "month_ratio", "month_ratio"), styles,
        )
        cls._write_styled_values(
            worksheet, 2, start_column, cls.SUMMARY_HEADERS,
            ("summary_header_left",) + ("summary_header",) * 6, styles,
        )
        for row_number, label in enumerate(cls.TAG_LABELS, start=3):
            metrics = cls._calculate_metrics(rows_by_label[label])
            cls._write_styled_values(
                worksheet, row_number, start_column,
                (label, metrics["weight_wan"], metrics["total_wan"], metrics["ratio"], metrics["natural_ratio"], metrics["ad_ratio"], cls._safe_divide(metrics["weight"], month_metrics["weight"])),
                ("summary_data_left", "summary_data_metric", "summary_data_metric", "summary_data_ratio", "summary_data_ratio", "summary_data_ratio", "summary_data_share"), styles,
            )
        for label in cls.TAG_LABELS:
            category_row = row_plan["detail_rows"][label]
            metrics = cls._calculate_metrics(rows_by_label[label])
            cls._write_styled_values(
                worksheet, category_row, start_column,
                (label, None, None, None, metrics["weight_wan"], metrics["total_wan"], metrics["ratio"], metrics["natural_ratio"], metrics["ad_ratio"], None),
                ("category_left", "category_fill", "category_fill", "category_fill", "category_metric", "category_metric", "category_ratio", "category_ratio", "category_ratio", "category_right"), styles,
            )
            cls._write_styled_values(
                worksheet, category_row + 1, start_column, cls.DETAIL_HEADERS,
                ("detail_header_left",) + ("detail_header",) * 8 + ("detail_header_right",), styles,
            )
            for index in range(row_plan["maximum_rows"][label]):
                result_row = rows_by_label[label][index] if index < len(rows_by_label[label]) else None
                cls._write_detail_row(
                    worksheet=worksheet, row=category_row + 2 + index, start_column=start_column,
                    result_row=result_row, styles=styles,
                )

    @classmethod
    def _write_styled_values(cls, worksheet, row: int, start_column: int, values: Sequence[Any], style_names: Sequence[str], styles: Mapping[str, Mapping[str, Any]]) -> None:
        """按模板样式写入连续单元格。"""

        for offset, (value, style_name) in enumerate(zip(values, style_names)):
            cell = worksheet.cell(row, start_column + offset, value)
            cls._apply_cell_style(cell, styles[style_name])

    @classmethod
    def _write_detail_row(cls, *, worksheet, row: int, start_column: int, result_row: Mapping[str, Any] | None, styles: Mapping[str, Mapping[str, Any]]) -> None:
        """写入一个最终词结果；无数据行仅保留参考模板的空白样式。"""

        label = cls._display_label(result_row) if result_row else None
        values = (
            result_row.get("word") if result_row else None,
            result_row.get("frequency") if result_row else None,
            label,
            result_row.get("labelReason") if label and result_row else None,
            result_row.get("weight") if result_row else None,
            result_row.get("total") if result_row else None,
            display_ratio(
                result_row.get("ratio"),
                result_row.get("total"),
            ) if result_row else None,
            result_row.get("naturalRatio") if result_row else None,
            result_row.get("adRatio") if result_row else None,
            cls._phrases_value(result_row.get("topPhrases")) if result_row else None,
        )
        cls._write_styled_values(
            worksheet, row, start_column, values,
            ("detail_left", "detail_integer", "detail_integer", "detail_mark", "detail_number", "detail_number", "detail_ratio", "detail_percent", "detail_percent", "detail_phrase"), styles,
        )
        if cls._is_number(values[6]):
            ratio_cell = worksheet.cell(row, start_column + 6)
            # 同一模板 StyleArray 会被多行复用。填写不同占比颜色前必须拆分
            # 当前单元格的样式，否则后一个词会把前一个词的占比底色一并覆盖。
            ratio_cell._style = copy(ratio_cell._style)
            ratio_cell.fill = PatternFill(
                fill_type="solid", fgColor=cls._percent_fill_hex(float(values[6]))
            )

    @classmethod
    def _apply_all_block_borders(cls, *, worksheet, month_count: int, row_plan: Mapping[str, Any], thin_side: Side, medium_side: Side) -> None:
        """以参考工作簿的细内线和中等外框勾勒每个横向月份区块。"""

        for month_index in range(month_count):
            start_column = 1 + month_index * (cls.MONTH_BLOCK_WIDTH + cls.MONTH_SEPARATOR_WIDTH)
            cls._apply_block_border(
                worksheet, start_row=1, end_row=row_plan["summary_end_row"],
                start_column=start_column, end_column=start_column + 6,
                thin_side=thin_side, medium_side=medium_side,
            )
            for label in cls.TAG_LABELS:
                start_row = row_plan["detail_rows"][label]
                cls._apply_block_border(
                    worksheet, start_row=start_row,
                    end_row=start_row + 1 + row_plan["maximum_rows"][label],
                    start_column=start_column, end_column=start_column + cls.MONTH_BLOCK_WIDTH - 1,
                    thin_side=thin_side, medium_side=medium_side,
                )

    @staticmethod
    def _apply_block_border(worksheet, *, start_row: int, end_row: int, start_column: int, end_column: int, thin_side: Side, medium_side: Side) -> None:
        """复用单元格已有的模板边线，仅将区块外框加粗。"""

        for row in range(start_row, end_row + 1):
            for column in range(start_column, end_column + 1):
                cell = worksheet.cell(row, column)
                cell.border = Border(
                    left=medium_side if column == start_column else thin_side,
                    right=medium_side if column == end_column else thin_side,
                    top=medium_side if row == start_row else thin_side,
                    bottom=medium_side if row == end_row else thin_side,
                )

    @classmethod
    def _set_sheet_dimensions(cls, *, worksheet, month_count: int, last_row: int) -> None:
        """统一参考模板的列宽与默认行高，并保留月份之间的一列空白。"""

        last_column = month_count * cls.MONTH_BLOCK_WIDTH + (month_count - 1) * cls.MONTH_SEPARATOR_WIDTH
        for column in range(1, last_column + 1):
            worksheet.column_dimensions[get_column_letter(column)].width = cls.DEFAULT_COLUMN_WIDTH
        for row in range(1, last_row + 1):
            worksheet.row_dimensions[row].height = cls.DEFAULT_ROW_HEIGHT
        worksheet.auto_filter.ref = None
        worksheet.freeze_panes = None

    @classmethod
    def _calculate_metrics(cls, rows: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
        """按当前 Final Analysis 已有指标汇总，不改变任何业务计算公式。"""

        weight = cls._sum_metric(rows, "weight")
        total = cls._sum_metric(rows, "total")
        return {
            "weight": weight, "total": total, "weight_wan": cls._to_wan(weight), "total_wan": cls._to_wan(total),
            "ratio": cls._safe_divide(weight, total),
            "natural_ratio": cls._weighted_ratio(rows, "naturalRatio"),
            "ad_ratio": cls._weighted_ratio(rows, "adRatio"),
        }

    @classmethod
    def _sum_metric(cls, rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
        """仅对真实数值求和；缺失指标保持空白而不是伪造零。"""

        values = [float(row[field]) for row in rows if cls._is_number(row.get(field))]
        return sum(values) if values else None

    @classmethod
    def _weighted_ratio(cls, rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
        """沿用现有 word analysis 的 Weight 加权比例语义。"""

        contributions = [
            (float(row["weight"]), float(row[field])) for row in rows
            if cls._is_number(row.get("weight")) and cls._is_number(row.get(field))
        ]
        denominator = sum(weight for weight, _ in contributions)
        if not contributions or denominator <= 0:
            return None
        return sum(weight * ratio for weight, ratio in contributions) / denominator

    @staticmethod
    def _to_wan(value: float | None) -> float | None:
        """参考表顶部汇总统一以万为展示单位。"""

        return value / 10_000 if value is not None else None

    @staticmethod
    def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
        """避免把缺失或零分母错误显示为 0%。"""

        if numerator is None or denominator is None:
            return None
        if denominator == 0:
            return 0.0
        return numerator / denominator

    @classmethod
    def _display_label(cls, row: Mapping[str, Any] | None) -> str | None:
        """未达成共识或未完成的词仍导出，但打标与 mark 必须留白。"""

        if not row:
            return None
        label = row.get("label")
        return label if label in cls.TAG_LABELS else None

    @classmethod
    def _group_label(cls, row: Mapping[str, Any]) -> str:
        """将未共识词安放在无效词区块，同时保持详情打标与 mark 留白。"""

        return cls._display_label(row) or "无效词"

    @staticmethod
    def _phrases_value(value: Any) -> str | None:
        """将 Top Phrases 以参考表的竖线格式写入，不暴露内部字段。"""

        if value is None:
            return None
        if isinstance(value, str):
            return value or None
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            phrases = [str(item).strip() for item in value if item is not None]
            return " | ".join(phrase for phrase in phrases if phrase) or None
        return str(value)

    @staticmethod
    def _month_title(month: str) -> str:
        """将 YYYYMM 或最近30天请求范围展示为 Excel 标题。"""

        if month == "":
            return "最近30天"
        digits = "".join(character for character in month if character.isdigit())
        return f"{int(digits[4:6])}月" if len(digits) >= 6 else month

    @staticmethod
    def _is_number(value: Any) -> bool:
        """保证 Excel 数值格式与聚合只作用于真实数值而不影响 bool。"""

        return isinstance(value, Number) and not isinstance(value, bool)

    @staticmethod
    def _percent_fill_hex(value: float) -> str:
        """复刻参考表根据占比直接填充的分段线性渐变色。"""

        percent = max(0, min(190, int(value * 100 + 0.5)))
        anchors = (
            (0, (146, 208, 80)), (2, (226, 239, 218)), (3, (255, 242, 204)),
            (8, (255, 230, 153)), (18, (255, 217, 102)), (35, (244, 177, 131)),
            (60, (241, 151, 90)), (90, (237, 125, 49)), (130, (255, 107, 87)),
            (190, (255, 59, 48)),
        )
        for index in range(len(anchors) - 1):
            lower_percent, lower_color = anchors[index]
            upper_percent, upper_color = anchors[index + 1]
            if lower_percent <= percent <= upper_percent:
                progress = (percent - lower_percent) / (upper_percent - lower_percent)
                color = tuple(round(lower + (upper - lower) * progress) for lower, upper in zip(lower_color, upper_color))
                return "".join(f"{component:02X}" for component in color)
        return "FF3B30"
