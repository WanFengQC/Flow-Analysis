"""标签管理持久化窗口；不持有任何当前分析的 AI 审核工作集。"""

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from models.tagging import TagLabel
from models.tagging_label import (
    TAGGING_TAXONOMY_VERSION,
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    category_display_name,
)
from ui.ui_tagging_label_management_dialog import Ui_TaggingLabelManagementDialog


class TaggingLabelTableModel(QAbstractTableModel):
    """管理页标签表的只读展示模型。"""

    _HEADERS = ("品类", "标准词", "标签", "人工原因", "来源", "版本", "更新时间")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[TaggingLabelCacheRecord] = []

    def set_records(self, records: Sequence[TaggingLabelCacheRecord]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()

    def record_at(self, row: int) -> TaggingLabelCacheRecord | None:
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
            category_display_name(record.category_key),
            record.word,
            record.label.value,
            record.reason or "—",
            record.decision_source.value,
            str(record.taxonomy_version),
            self._datetime_text(record.updated_at or record.created_at),
        )
        return values[index.column()] if index.column() < len(values) else None

    @staticmethod
    def _datetime_text(value: object) -> str:
        return value.strftime("%Y-%m-%d %H:%M") if hasattr(value, "strftime") else "—"


class TaggingLabelEditorDialog(QDialog):
    """标签新增/编辑独立表单；编辑时稳定缓存 identity 默认为只读。"""

    def __init__(
        self,
        parent: QDialog,
        record: TaggingLabelCacheRecord | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑标签" if record else "新增标签")
        self.setModal(True)
        self.resize(500, 310)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.category_combo = QComboBox(self)
        for category in TaggingCategoryKey:
            self.category_combo.addItem(category_display_name(category), category.value)
        self.word_edit = QLineEdit(self)
        self.label_combo = QComboBox(self)
        for label in TagLabel:
            self.label_combo.addItem(label.value, label.value)
        self.reason_edit = QPlainTextEdit(self)
        self.reason_edit.setPlaceholderText("填写人工确认原因")
        self.reason_edit.setMinimumHeight(110)
        form.addRow("品类", self.category_combo)
        form.addRow("标准词", self.word_edit)
        form.addRow("正式标签", self.label_combo)
        form.addRow("人工原因", self.reason_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if record is not None:
            self.category_combo.setCurrentIndex(
                self.category_combo.findData(record.category_key.value)
            )
            self.word_edit.setText(record.word)
            self.label_combo.setCurrentIndex(self.label_combo.findData(record.label.value))
            self.reason_edit.setPlainText(record.reason or "")
            # 防止无意改变 category + word + taxonomy 的缓存 identity。
            self.category_combo.setEnabled(False)
            self.word_edit.setReadOnly(True)

    def values(self) -> tuple[str, str, str, str]:
        return (
            str(self.category_combo.currentData()),
            self.word_edit.text().strip(),
            str(self.label_combo.currentData()),
            self.reason_edit.toPlainText().strip(),
        )


class TaggingLabelManagementDialog(QDialog):
    """正式标签缓存管理，独立于 TaggingReviewDialog 的短生命周期数据。"""

    refresh_requested = Signal(object, object, object, str, int)
    create_requested = Signal(str, str, str, str)
    update_requested = Signal(object, int, str, str)
    delete_requested = Signal(object, int)
    history_requested = Signal(object)
    PAGE_SIZE = 100

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ui = Ui_TaggingLabelManagementDialog()
        self.ui.setupUi(self)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(1160, 650)
        self._page = 0
        self._total = 0
        self._model = TaggingLabelTableModel(self)
        self.ui.labelsTableView.setModel(self._model)
        self.ui.labelsTableView.horizontalHeader().setStretchLastSection(True)
        self.ui.labelsTableView.setColumnWidth(3, 340)
        self._populate_filters()
        self.ui.refreshButton.clicked.connect(self._refresh_first_page)
        self.ui.searchLineEdit.returnPressed.connect(self._refresh_first_page)
        self.ui.categoryComboBox.currentIndexChanged.connect(self._refresh_first_page)
        self.ui.labelComboBox.currentIndexChanged.connect(self._refresh_first_page)
        self.ui.sourceComboBox.currentIndexChanged.connect(self._refresh_first_page)
        self.ui.previousPageButton.clicked.connect(self._previous_page)
        self.ui.nextPageButton.clicked.connect(self._next_page)
        self.ui.addButton.clicked.connect(self._create_label)
        self.ui.editButton.clicked.connect(self._edit_label)
        self.ui.deleteButton.clicked.connect(self._delete_label)
        self.ui.historyButton.clicked.connect(self._request_history)
        self.ui.labelsTableView.selectionModel().currentChanged.connect(
            lambda *_: self._update_controls()
        )
        self._update_controls()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_first_page()

    def set_records(self, records: Sequence[TaggingLabelCacheRecord], total: int) -> None:
        self._model.set_records(records)
        self._total = max(0, total)
        self._page = min(self._page, max(0, (self._total - 1) // self.PAGE_SIZE))
        self.ui.countLabel.setText(f"当前有效标签：{self._total}")
        self.ui.pageLabel.setText(f"第 {self._page + 1} 页")
        self._update_controls()

    def refresh_current(self) -> None:
        """由宿主 View 在写入成功后请求按当前条件重新查询。"""

        self._refresh_first_page()

    def show_history(self, entries: Sequence[Mapping[str, Any]]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("标签历史")
        dialog.resize(760, 500)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        lines = [
            f"{entry.get('createdAt') or '—'}  {entry.get('eventType')} / {entry.get('action')}\n{entry.get('snapshot')}"
            for entry in entries
        ]
        text.setPlainText("\n\n".join(lines) or "暂无历史")
        layout.addWidget(text)
        dialog.exec()

    def _populate_filters(self) -> None:
        self.ui.categoryComboBox.addItem("全部品类", None)
        for category in TaggingCategoryKey:
            self.ui.categoryComboBox.addItem(category_display_name(category), category.value)
        self.ui.labelComboBox.addItem("全部标签", None)
        for label in TagLabel:
            self.ui.labelComboBox.addItem(label.value, label.value)
        self.ui.sourceComboBox.addItem("全部来源", None)
        for source in TaggingDecisionSource:
            self.ui.sourceComboBox.addItem(source.value, source.value)

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
            self.ui.categoryComboBox.currentData(),
            self.ui.labelComboBox.currentData(),
            self.ui.sourceComboBox.currentData(),
            self.ui.searchLineEdit.text().strip(),
            self._page,
        )

    def _selected_record(self) -> TaggingLabelCacheRecord | None:
        index = self.ui.labelsTableView.currentIndex()
        return self._model.record_at(index.row()) if index.isValid() else None

    def _create_label(self) -> None:
        editor = TaggingLabelEditorDialog(self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self.create_requested.emit(*editor.values())

    def _edit_label(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "标签管理", "请先选择要编辑的标签")
            return
        editor = TaggingLabelEditorDialog(self, record)
        if editor.exec() == QDialog.DialogCode.Accepted:
            _, _, label, reason = editor.values()
            self.update_requested.emit(record.id, record.revision, label, reason)

    def _delete_label(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "标签管理", "请先选择要删除的标签")
            return
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"删除当前标签“{record.word} / {record.label.value}”？\n将保留 cache ID 和全部历史，后续查询会成为 CACHE_MISS。",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(record.id, record.revision)

    def _request_history(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "标签管理", "请先选择要查看历史的标签")
            return
        self.history_requested.emit(record.id)

    def _update_controls(self) -> None:
        has_selection = self._selected_record() is not None
        self.ui.editButton.setEnabled(has_selection)
        self.ui.deleteButton.setEnabled(has_selection)
        self.ui.historyButton.setEnabled(has_selection)
        self.ui.previousPageButton.setEnabled(self._page > 0)
        self.ui.nextPageButton.setEnabled((self._page + 1) * self.PAGE_SIZE < self._total)
