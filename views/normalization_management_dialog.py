"""归一化持久化规则管理窗口；View 只转发用户意图，不直接访问数据库。"""

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from models.normalization_active_rule import (
    NormalizationActiveRuleAuditRecord,
    NormalizationActiveRuleRecord,
)
from models.normalization_rule import NormalizationRuleType
from models.tagging_label import TaggingCategoryKey, category_display_name
from ui.ui_normalization_management_dialog import Ui_NormalizationManagementDialog


class NormalizationActiveRuleTableModel(QAbstractTableModel):
    """当前有效规则的纯展示模型，不承担搜索、排序或数据库访问。"""

    _HEADERS = ("品类", "类型", "变体词", "标准词", "来源", "版本", "更新时间")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[NormalizationActiveRuleRecord] = []

    def set_records(self, records: Sequence[NormalizationActiveRuleRecord]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()

    def record_at(self, row: int) -> NormalizationActiveRuleRecord | None:
        return self._records[row] if 0 <= row < len(self._records) else None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._HEADERS):
            return self._HEADERS[section]
        return section + 1 if orientation == Qt.Orientation.Vertical else None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        record = self.record_at(index.row())
        if record is None:
            return None
        values = (
            category_display_name(record.category_key) if record.category_key else "全局",
            record.rule_type.value,
            " / ".join(record.variants),
            record.canonical,
            record.source_candidate_id or "人工管理",
            str(record.revision),
            self._datetime_text(record.updated_at or record.created_at),
        )
        return values[index.column()] if index.column() < len(values) else None

    @staticmethod
    def _datetime_text(value: object) -> str:
        return value.strftime("%Y-%m-%d %H:%M") if hasattr(value, "strftime") else "—"


class NormalizationRuleEditorDialog(QDialog):
    """归一规则新增/编辑表单；所有字段在确认后通过 Signal 交给 Controller。"""

    def __init__(
        self,
        parent: QDialog,
        record: NormalizationActiveRuleRecord | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑归一规则" if record else "新增归一规则")
        self.setModal(True)
        self.resize(520, 310)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox(self)
        self.type_combo.addItem("WORD", NormalizationRuleType.WORD.value)
        self.type_combo.addItem("PHRASE", NormalizationRuleType.PHRASE.value)
        self.variants_edit = QPlainTextEdit(self)
        self.category_combo = QComboBox(self)
        self.category_combo.addItem("全局", None)
        for category in TaggingCategoryKey:
            self.category_combo.addItem(category_display_name(category), category.value)
        self.variants_edit.setPlaceholderText("每行一个 variant；也支持用英文逗号分隔")
        self.variants_edit.setMinimumHeight(120)
        self.canonical_edit = QLineEdit(self)
        form.addRow("规则类型", self.type_combo)
        form.addRow("所属品类", self.category_combo)
        form.addRow("变体词", self.variants_edit)
        form.addRow("标准词", self.canonical_edit)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        if record is not None:
            self.type_combo.setCurrentIndex(
                0 if record.rule_type == NormalizationRuleType.WORD else 1
            )
            self.variants_edit.setPlainText("\n".join(record.variants))
            self.canonical_edit.setText(record.canonical)
            self.category_combo.setCurrentIndex(
                self.category_combo.findData(
                    record.category_key.value if record.category_key else None
                )
            )

    def values(self) -> tuple[str, list[str], str, str | None]:
        variants = [
            item.strip()
            for line in self.variants_edit.toPlainText().splitlines()
            for item in line.split(",")
            if item.strip()
        ]
        return (
            str(self.type_combo.currentData()),
            variants,
            self.canonical_edit.text().strip(),
            self.category_combo.currentData(),
        )


class NormalizationManagementDialog(QDialog):
    """当前有效规则管理窗口，和当前分析的候选审核窗口完全隔离。"""

    refresh_requested = Signal(str, object, object, int)
    create_requested = Signal(str, object, str, object)
    update_requested = Signal(object, int, str, object, str, object)
    revoke_requested = Signal(object, int)
    history_requested = Signal(object)
    PAGE_SIZE = 100

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ui = Ui_NormalizationManagementDialog()
        self.ui.setupUi(self)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(1020, 650)
        self._page = 0
        self._total = 0
        self._model = NormalizationActiveRuleTableModel(self)
        self.ui.rulesTableView.setModel(self._model)
        self.ui.rulesTableView.horizontalHeader().setStretchLastSection(True)
        self.ui.rulesTableView.setColumnWidth(2, 300)
        self.category_filter = QComboBox(self)
        self.category_filter.setMinimumWidth(130)
        self.ui.normalizationManagementFilterLayout.insertWidget(2, self.category_filter)
        self.ui.rulesTableView.selectionModel().currentChanged.connect(
            lambda *_: self._update_controls()
        )
        self._populate_filters()
        self.ui.refreshButton.clicked.connect(self._refresh_first_page)
        self.ui.searchLineEdit.returnPressed.connect(self._refresh_first_page)
        self.ui.ruleTypeComboBox.currentIndexChanged.connect(self._refresh_first_page)
        self.category_filter.currentIndexChanged.connect(self._refresh_first_page)
        self.ui.previousPageButton.clicked.connect(self._previous_page)
        self.ui.nextPageButton.clicked.connect(self._next_page)
        self.ui.addButton.clicked.connect(self._create_rule)
        self.ui.editButton.clicked.connect(self._edit_rule)
        self.ui.revokeButton.clicked.connect(self._revoke_rule)
        self.ui.historyButton.clicked.connect(self._request_history)
        self._update_controls()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_first_page()

    def set_records(self, records: Sequence[NormalizationActiveRuleRecord], total: int) -> None:
        self._model.set_records(records)
        self._total = max(0, total)
        max_page = max(0, (self._total - 1) // self.PAGE_SIZE)
        self._page = min(self._page, max_page)
        self.ui.countLabel.setText(f"当前有效规则：{self._total}")
        self.ui.pageLabel.setText(f"第 {self._page + 1} 页")
        self._update_controls()

    def refresh_current(self) -> None:
        """由宿主 View 在写入成功后请求按当前条件重新查询。"""

        self._refresh_first_page()

    def show_history(self, entries: Sequence[NormalizationActiveRuleAuditRecord]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("归一规则历史")
        dialog.resize(720, 460)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        lines = []
        for entry in entries:
            created = entry.created_at.isoformat(sep=" ", timespec="seconds") if entry.created_at else "—"
            lines.append(f"{created}  {entry.action.value}\n{entry.rule_snapshot}")
        text.setPlainText("\n\n".join(lines) or "暂无管理历史")
        layout.addWidget(text)
        dialog.exec()

    def _populate_filters(self) -> None:
        self.ui.ruleTypeComboBox.addItem("全部类型", None)
        self.ui.ruleTypeComboBox.addItem("WORD", NormalizationRuleType.WORD.value)
        self.ui.ruleTypeComboBox.addItem("PHRASE", NormalizationRuleType.PHRASE.value)
        self.category_filter.addItem("全部品类", None)
        for category in TaggingCategoryKey:
            self.category_filter.addItem(category_display_name(category), category.value)

    def _refresh_first_page(self) -> None:
        self._page = 0
        self._emit_refresh()

    def _previous_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._emit_refresh()

    def _next_page(self) -> None:
        if (self._page + 1) * self.PAGE_SIZE < self._total:
            self._page += 1
            self._emit_refresh()

    def _emit_refresh(self) -> None:
        self.refresh_requested.emit(
            self.ui.searchLineEdit.text().strip(),
            self.ui.ruleTypeComboBox.currentData(),
            self.category_filter.currentData(),
            self._page,
        )

    def _selected_record(self) -> NormalizationActiveRuleRecord | None:
        index = self.ui.rulesTableView.currentIndex()
        return self._model.record_at(index.row()) if index.isValid() else None

    def _create_rule(self) -> None:
        editor = NormalizationRuleEditorDialog(self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        rule_type, variants, canonical, category_key = editor.values()
        self.create_requested.emit(rule_type, variants, canonical, category_key)

    def _edit_rule(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "归一化管理", "请先选择要编辑的规则")
            return
        editor = NormalizationRuleEditorDialog(self, record)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        rule_type, variants, canonical, category_key = editor.values()
        self.update_requested.emit(record.id, record.revision, rule_type, variants, canonical, category_key)

    def _revoke_rule(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "归一化管理", "请先选择要撤销的规则")
            return
        answer = QMessageBox.question(
            self,
            "确认撤销",
            f"撤销规则“{' / '.join(record.variants)} → {record.canonical}”？\n撤销后仅新分析不再读取该规则，历史不会删除。",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.revoke_requested.emit(record.id, record.revision)

    def _request_history(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "归一化管理", "请先选择要查看历史的规则")
            return
        self.history_requested.emit(record.id)

    def _update_controls(self) -> None:
        has_selection = self._selected_record() is not None
        self.ui.editButton.setEnabled(has_selection)
        self.ui.revokeButton.setEnabled(has_selection)
        self.ui.historyButton.setEnabled(has_selection)
        self.ui.previousPageButton.setEnabled(self._page > 0)
        self.ui.nextPageButton.setEnabled((self._page + 1) * self.PAGE_SIZE < self._total)
