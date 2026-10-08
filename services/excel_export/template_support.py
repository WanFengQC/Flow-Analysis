"""Excel 模板清理、样式复制与月份区块的共享基础能力。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import copy
from datetime import date, datetime, time
import json
from numbers import Number
from typing import Any

from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter


def copy_cell_style(cell) -> dict[str, Any]:
    """捕获完整样式数组，避免按单元格重复创建六类样式对象。"""

    # StyleArray 在写入属性时会被 openpyxl 替换，不会反向修改参考单元格。
    # 直接复用它能显著降低大工作簿的内存峰值与 xlsx 序列化时间。
    return {"_style": copy(cell._style)}


def apply_cell_style(cell, style: dict[str, Any]) -> None:
    """将模板样式数组写到目标单元格。"""

    cell._style = style["_style"]


def clear_sheet_business_values(worksheet) -> None:
    """移除全部旧业务值，但完整保留 Sheet、样式、合并与页面属性。"""

    for cell in worksheet._cells.values():
        # 合并区域内非左上角单元格不可写，且不保存独立业务值。
        if isinstance(cell, MergedCell):
            continue
        cell.value = None
        cell.comment = None
        cell.hyperlink = None


def copy_column_widths(worksheet, source_start: int, target_start: int, width: int) -> None:
    """为动态扩展的月份区块沿用第一块列宽。"""

    for offset in range(width):
        source_key = get_column_letter(source_start + offset)
        target_key = get_column_letter(target_start + offset)
        worksheet.column_dimensions[target_key].width = worksheet.column_dimensions[source_key].width


def month_title(month: str) -> str:
    """将 YYYYMM 或最近30天请求范围显示为模板标题。"""

    # SellerSprite 的精确空字符串是最近30天请求范围，不是缺失月份。
    if month == "":
        return "最近30天"
    digits = "".join(character for character in str(month) if character.isdigit())
    return f"{int(digits[4:6])}月" if len(digits) >= 6 else str(month)


def sort_months(months: list[str]) -> list[str]:
    """按最新到最旧排序有效月份，异常月份保持稳定输入顺序。"""

    positions = {month: index for index, month in enumerate(months)}

    def sort_key(month: str) -> tuple[int, int]:
        digits = "".join(character for character in str(month) if character.isdigit())
        if len(digits) >= 6:
            return (1, int(digits[:6]))
        return (0, -positions[month])

    return sorted(months, key=sort_key, reverse=True)


def is_number(value: Any) -> bool:
    """只把真实数值写为 Excel 数值，避免把布尔值误当成数字。"""

    return isinstance(value, Number) and not isinstance(value, bool)


def display_ratio(value: Any, total: Any) -> Any:
    """将 Total 为零而缺失的比率展示为 0，保留真正缺失数据为空。"""

    if value is None and is_number(total) and total == 0:
        return 0.0
    return value


def to_excel_value(value: Any) -> Any:
    """将运行时复杂字段转换为 Excel 可写值，保留标量的真实数据类型。"""

    # openpyxl 只接受 Excel 标量。SellerSprite 的 RAW 中存在 list / dict，
    # 例如 trafficKeywordTypes，必须转为 UTF-8 JSON，而不能让导出线程失败。
    if value is None or isinstance(value, (str, bool, Number, date, datetime, time)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    ):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)
