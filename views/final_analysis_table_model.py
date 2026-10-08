"""Final Analysis Dataset 的只读 QTableView Model。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Number
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from models.final_analysis import (
    FINAL_ANALYSIS_COLUMNS,
    INTERNAL_FINAL_ANALYSIS_FIELDS,
    FinalAnalysisColumn,
)


class FinalAnalysisTableModel(QAbstractTableModel):
    """仅显示、筛选与排序 Final Analysis Dataset，不承担业务计算。"""

    def __init__(
        self,
        rows: Sequence[Mapping[str, Any]],
        parent=None,
    ) -> None:
        """复制展示快照，确保 View 排序不会修改 Controller 的正式顺序。"""

        super().__init__(parent)
        self._source_rows = [deepcopy(dict(row)) for row in rows]
        self._columns = self._build_columns(self._source_rows)
        self._visible_indexes = list(range(len(self._source_rows)))
        self._filter_text = ""

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """返回当前筛选后的展示行数。"""

        return 0 if parent.isValid() else len(self._visible_indexes)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """返回当前正式字段对应的展示列数。"""

        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """只做已有字段格式化，禁止在这里发起或重算任何业务数据。"""

        if not index.isValid() or role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.TextAlignmentRole,
        }:
            return None
        row = self._source_rows[self._visible_indexes[index.row()]]
        column = self._columns[index.column()]
        value = row.get(column.field)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return (
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                if self._is_number(value)
                else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        formatted = self._format_value(value, column)
        return formatted

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        """返回中文友好列名；未知正式字段保留其原始 field 名。"""

        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self._columns)
        ):
            return self._columns[section].title
        return super().headerData(section, orientation, role)

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """只重排 View 的可见索引，不触碰 Final Analysis Dataset 业务顺序。"""

        if not 0 <= column < len(self._columns):
            return
        field = self._columns[column].field
        self.layoutAboutToBeChanged.emit()
        self._visible_indexes.sort(
            key=lambda row_index: self._sort_key(
                self._source_rows[row_index].get(field)
            ),
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        self.layoutChanged.emit()

    def set_filter_text(self, text: str) -> None:
        """对已有最终行做纯展示筛选，不改变或重新计算任何业务字段。"""

        self._filter_text = text.strip().casefold()
        self.beginResetModel()
        self._visible_indexes = [
            index
            for index, row in enumerate(self._source_rows)
            if self._row_matches_filter(row)
        ]
        self.endResetModel()

    def update_final_row(self, updated_row: Mapping[str, Any]) -> None:
        """用人工审核后的标签字段刷新匹配行，不重建整份最终结果。"""

        identity = (updated_row.get("month"), updated_row.get("word"))
        for index, row in enumerate(self._source_rows):
            if (row.get("month"), row.get("word")) != identity:
                continue
            self._source_rows[index] = deepcopy(dict(updated_row))
            if index not in self._visible_indexes:
                return
            view_row = self._visible_indexes.index(index)
            top_left = self.index(view_row, 0)
            bottom_right = self.index(view_row, len(self._columns) - 1)
            self.dataChanged.emit(top_left, bottom_right)
            return

    @property
    def columns(self) -> tuple[FinalAnalysisColumn, ...]:
        """提供列定义供 View 隐藏可选列使用。"""

        return tuple(self._columns)

    @staticmethod
    def _build_columns(rows: Sequence[Mapping[str, Any]]) -> list[FinalAnalysisColumn]:
        """在固定主列后追加未被禁止的既有正式业务字段。"""

        columns = list(FINAL_ANALYSIS_COLUMNS)
        known_fields = {column.field for column in columns}
        for row in rows:
            for field in row:
                if field in known_fields or field in INTERNAL_FINAL_ANALYSIS_FIELDS:
                    continue
                columns.append(FinalAnalysisColumn(field, field))
                known_fields.add(field)
        return columns

    def _row_matches_filter(self, row: Mapping[str, Any]) -> bool:
        """用当前列的展示文本匹配用户输入。"""

        if not self._filter_text:
            return True
        return any(
            self._filter_text in self._format_value(row.get(column.field), column).casefold()
            for column in self._columns
        )

    @staticmethod
    def _format_value(value: Any, column: FinalAnalysisColumn) -> str:
        """统一格式化空值、比例和短语，不把未知值伪造成零。"""

        if value is None:
            return ""
        if column.value_kind == "phrases":
            if isinstance(value, str):
                return value
            if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
                return " | ".join(str(item) for item in value if item is not None)
        if column.value_kind == "percentage" and FinalAnalysisTableModel._is_number(value):
            return f"{float(value) * 100:.2f}%"
        if isinstance(value, float):
            return f"{value:,.2f}"
        return str(value)

    @staticmethod
    def _is_number(value: Any) -> bool:
        """bool 不是业务数值，避免在表格中被格式化为 0/1。"""

        return isinstance(value, Number) and not isinstance(value, bool)

    @staticmethod
    def _sort_key(value: Any) -> tuple[int, Any]:
        """缺失值排到末尾，其他值保持可预测的展示排序。"""

        if value is None:
            return (1, "")
        if FinalAnalysisTableModel._is_number(value):
            return (0, float(value))
        return (0, str(value).casefold())
