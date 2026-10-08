"""AI 标签分歧的仅运行时人工审核窗口。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QListWidgetItem, QMessageBox, QWidget

from models.tagging import TagLabel, TaggingDecision
from models.tagging_label import (
    TaggingReviewItem,
    TaggingReviewStatus,
    category_display_name,
)
from models.tagging_provider import ProviderTaggingResult
from ui.ui_tagging_review_dialog import Ui_TaggingReviewDialog


class TaggingReviewDialog(QDialog):
    """仅展示当前 generation 的分歧，不保存任何业务状态到磁盘。"""

    save_requested = Signal(str, str, str)
    close_requested = Signal()

    _PROVIDER_DISPLAY_NAMES = {
        "openai": "GPT-6 Sol",
        "anthropic": "Claude Sonnet 5",
        "google": "Gemini 3.8 Flash",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        """加载 Designer 界面，并初始化纯 View 级搜索与表单状态。"""

        super().__init__(parent)
        self.ui = Ui_TaggingReviewDialog()
        self.ui.setupUi(self)
        self.setModal(False)
        self.setMinimumSize(980, 640)
        self.resize(1240, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        # Designer 根布局未指定垂直拉伸时，Qt 会把窗口多余高度平均分配给
        # Header 和内容 Splitter，导致标题区域异常占满窗口。只让 Splitter
        # 吸收余量，Header 与搜索框保持自身的最小内容高度。
        self.ui.taggingReviewRootLayout.setStretch(0, 0)
        self.ui.taggingReviewRootLayout.setStretch(1, 0)
        self.ui.taggingReviewRootLayout.setStretch(2, 1)

        self._items_by_id: dict[str, TaggingReviewItem] = {}
        self._ordered_item_ids: list[str] = []
        self._total_count = 0
        self._selected_item_id: str | None = None

        self._populate_tag_labels()
        self.ui.taggingReviewSplitter.setStretchFactor(0, 0)
        self.ui.taggingReviewSplitter.setStretchFactor(1, 1)
        self.ui.taggingReviewSplitter.setSizes([320, 860])
        self.ui.taggingReviewSearchLineEdit.textChanged.connect(
            self._refresh_list
        )
        self.ui.taggingReviewListWidget.currentItemChanged.connect(
            self._on_current_item_changed
        )
        self.ui.adoptOpenaiButton.clicked.connect(
            lambda: self._adopt_provider("openai")
        )
        self.ui.adoptAnthropicButton.clicked.connect(
            lambda: self._adopt_provider("anthropic")
        )
        self.ui.adoptGoogleButton.clicked.connect(
            lambda: self._adopt_provider("google")
        )
        self.ui.confirmTaggingReviewButton.clicked.connect(
            self._request_save
        )
        self._set_current_item(None)

    def set_review_items(
        self,
        items: Sequence[TaggingReviewItem],
        total_count: int,
    ) -> None:
        """接收 Controller 的当前只读快照，不在 View 内改变审核状态。"""

        current_id = self._selected_item_id
        self._items_by_id = {item.item_id: item for item in items}
        self._ordered_item_ids = [item.item_id for item in items]
        self._total_count = total_count
        self._selected_item_id = (
            current_id if current_id in self._items_by_id else None
        )
        self._refresh_list()

    def set_save_status(self, item_id: str, message: str) -> None:
        """显示 Controller 传入的保存状态，避免 Dialog 接触 Future。"""

        if item_id == self._selected_item_id:
            self.ui.taggingReviewSaveStatusLabel.setText(message)

    def show_save_failed(self, item_id: str) -> None:
        """将失败明确暴露给当前人工，待审核项仍由 Controller 保留。"""

        self.set_save_status(item_id, "人工标签保存失败，请重试。")

    def reset_runtime_view(self) -> None:
        """清空列表、搜索和详情引用，配合 Controller 统一销毁工作集。"""

        self._items_by_id.clear()
        self._ordered_item_ids.clear()
        self._total_count = 0
        self._selected_item_id = None
        self.ui.taggingReviewSearchLineEdit.clear()
        self.ui.taggingReviewListWidget.clear()
        self._set_current_item(None)
        self.hide()

    def closeEvent(self, event) -> None:
        """X 不直接隐藏；是否销毁待审核项只能由 Controller 决定。"""

        event.ignore()
        self.close_requested.emit()

    def _populate_tag_labels(self) -> None:
        """固定九个正式标签，禁止人工输入不存在的分类。"""

        combo_box = self.ui.taggingHumanLabelComboBox
        combo_box.clear()
        combo_box.addItem("未选择", None)
        for label in TagLabel:
            combo_box.addItem(label.value, label.value)

    def _refresh_list(self) -> None:
        """按 word 或 Top Phrase 搜索，不重排 Controller 的业务结果。"""

        search_text = self.ui.taggingReviewSearchLineEdit.text().strip().lower()
        selected_id = self._selected_item_id
        self.ui.taggingReviewListWidget.blockSignals(True)
        self.ui.taggingReviewListWidget.clear()
        visible_ids: list[str] = []
        for item_id in self._ordered_item_ids:
            item = self._items_by_id.get(item_id)
            if item is None:
                continue
            searchable_text = "\n".join((item.word, *item.top_phrases)).lower()
            if search_text and search_text not in searchable_text:
                continue
            list_item = QListWidgetItem(item.word)
            list_item.setData(Qt.ItemDataRole.UserRole, item_id)
            if item.status == TaggingReviewStatus.SAVING:
                list_item.setText(f"{item.word}（保存中）")
                list_item.setFlags(
                    list_item.flags() & ~Qt.ItemFlag.ItemIsEnabled
                )
            self.ui.taggingReviewListWidget.addItem(list_item)
            visible_ids.append(item_id)
        self.ui.taggingReviewListWidget.blockSignals(False)

        self.ui.taggingReviewEmptyLabel.setVisible(not visible_ids)
        pending_count = len(self._items_by_id)
        completed_count = max(self._total_count - pending_count, 0)
        self.ui.taggingReviewProgressLabel.setText(
            f"已完成 {completed_count} / {self._total_count}，待审核 {pending_count}"
        )

        if selected_id not in visible_ids:
            selected_id = visible_ids[0] if visible_ids else None
        self._select_item_id(selected_id)

    def _select_item_id(self, item_id: str | None) -> None:
        """同步左侧选择与右侧详情，空列表只显示已完成状态。"""

        self._selected_item_id = item_id
        list_widget = self.ui.taggingReviewListWidget
        if item_id is not None:
            for row in range(list_widget.count()):
                list_item = list_widget.item(row)
                if list_item.data(Qt.ItemDataRole.UserRole) == item_id:
                    list_widget.setCurrentRow(row)
                    break
        self._set_current_item(self._items_by_id.get(item_id))

    def _on_current_item_changed(self, current, _previous) -> None:
        """响应用户选择，不向 Controller 写任何中间人工表单数据。"""

        item_id = (
            current.data(Qt.ItemDataRole.UserRole)
            if current is not None
            else None
        )
        self._selected_item_id = item_id
        self._set_current_item(self._items_by_id.get(item_id))

    def _set_current_item(self, item: TaggingReviewItem | None) -> None:
        """将单条只读运行时快照映射到详情控件。"""

        has_item = item is not None
        if not has_item:
            self.ui.taggingWordValueLabel.setText("—")
            self.ui.taggingMonthValueLabel.setText("—")
            self.ui.taggingCategoryValueLabel.setText("—")
            self.ui.taggingTopPhrasesTextEdit.clear()
            self.ui.taggingRepresentativeAsinValueLabel.setText("—")
            self.ui.taggingContextSourceValueLabel.setText("—")
            self.ui.taggingProductContextTextEdit.clear()
            self._display_provider_result("openai", None)
            self._display_provider_result("anthropic", None)
            self._display_provider_result("google", None)
            self.ui.taggingOpinionSummaryLabel.setText("本轮 AI 分歧已全部人工确认。")
            self._reset_human_form()
            self._set_detail_enabled(False)
            return

        self.ui.taggingWordValueLabel.setText(item.word)
        self.ui.taggingMonthValueLabel.setText(item.month)
        self.ui.taggingCategoryValueLabel.setText(
            category_display_name(item.category_key)
        )
        self.ui.taggingTopPhrasesTextEdit.setPlainText(
            "\n".join(item.top_phrases)
        )
        self.ui.taggingRepresentativeAsinValueLabel.setText(
            item.representative_asin
        )
        self.ui.taggingContextSourceValueLabel.setText(
            item.product_context_source
        )
        self.ui.taggingProductContextTextEdit.setPlainText(
            self._format_product_context(item.product_context)
        )
        for provider_id in self._PROVIDER_DISPLAY_NAMES:
            self._display_provider_result(
                provider_id,
                item.provider_results.get(provider_id),
            )
        self.ui.taggingOpinionSummaryLabel.setText(
            self._opinion_summary(item.provider_results)
        )
        self._reset_human_form()
        self._set_detail_enabled(item.status == TaggingReviewStatus.PENDING)
        if item.status == TaggingReviewStatus.SAVING:
            self.ui.taggingReviewSaveStatusLabel.setText("正在保存人工标签...")

    def _display_provider_result(self, provider_id: str, result: object) -> None:
        """仅展示校验后的标签和原因，不暴露 Provider 原始响应或隐藏推理。"""

        label_widget = getattr(
            self.ui,
            f"{provider_id}ProviderLabelValue",
        )
        reason_widget = getattr(
            self.ui,
            f"{provider_id}ProviderReasonTextEdit",
        )
        adopt_button = getattr(self.ui, f"adopt{provider_id.title()}Button")
        decision = self._provider_decision(result)
        if decision is None:
            label_widget.setText("不可用")
            reason_widget.setPlainText("该模型未返回可用于审核的合法结果。")
            adopt_button.setEnabled(False)
            return
        label_widget.setText(decision.label.value)
        reason_widget.setPlainText(decision.reason)
        adopt_button.setEnabled(True)

    @staticmethod
    def _provider_decision(result: object) -> TaggingDecision | None:
        """从严格校验过的 Provider 结果中提取当前 item 的唯一决定。"""

        if not isinstance(result, ProviderTaggingResult):
            return None
        if len(result.decisions) != 1:
            return None
        return result.decisions[0]

    def _adopt_provider(self, provider_id: str) -> None:
        """只回填人工表单，最终保存仍需用户再次点击确认。"""

        item = self._items_by_id.get(self._selected_item_id or "")
        if item is None or item.status != TaggingReviewStatus.PENDING:
            return
        decision = self._provider_decision(item.provider_results.get(provider_id))
        if decision is None:
            return
        label_index = self.ui.taggingHumanLabelComboBox.findData(
            decision.label.value
        )
        if label_index >= 0:
            self.ui.taggingHumanLabelComboBox.setCurrentIndex(label_index)
        self.ui.taggingHumanReasonTextEdit.setPlainText(decision.reason)
        self.ui.taggingReviewSaveStatusLabel.setText(
            f"已采用 {self._PROVIDER_DISPLAY_NAMES[provider_id]} 的填写建议，"
            "请确认后保存。"
        )

    def _request_save(self) -> None:
        """验证人工明确选择后，向 Controller 发送一次最终保存请求。"""

        item = self._items_by_id.get(self._selected_item_id or "")
        if item is None or item.status != TaggingReviewStatus.PENDING:
            return
        label_value = self.ui.taggingHumanLabelComboBox.currentData()
        if not isinstance(label_value, str):
            QMessageBox.warning(self, "请先选择人工标签", "请明确选择一个人工最终标签。")
            return
        reason = self.ui.taggingHumanReasonTextEdit.toPlainText().strip()
        if not reason:
            QMessageBox.warning(self, "请填写人工原因", "请填写本次人工标签的原因。")
            return
        self.save_requested.emit(item.item_id, label_value, reason)

    def _reset_human_form(self) -> None:
        """切换审核项时永不预选任何模型结论。"""

        self.ui.taggingHumanLabelComboBox.setCurrentIndex(0)
        self.ui.taggingHumanReasonTextEdit.clear()
        self.ui.taggingReviewSaveStatusLabel.clear()

    def _set_detail_enabled(self, enabled: bool) -> None:
        """保存期间只锁定当前人工操作，不影响窗口和列表的响应。"""

        self.ui.taggingHumanLabelComboBox.setEnabled(enabled)
        self.ui.taggingHumanReasonTextEdit.setEnabled(enabled)
        self.ui.confirmTaggingReviewButton.setEnabled(enabled)
        current_item = self._items_by_id.get(self._selected_item_id or "")
        pending_item = (
            current_item is not None
            and current_item.status == TaggingReviewStatus.PENDING
        )
        for provider_id in self._PROVIDER_DISPLAY_NAMES:
            button = getattr(self.ui, f"adopt{provider_id.title()}Button")
            decision = (
                self._provider_decision(
                    current_item.provider_results.get(provider_id)
                )
                if pending_item and current_item is not None
                else None
            )
            button.setEnabled(enabled and decision is not None)

    @staticmethod
    def _format_product_context(context: Mapping[str, Any] | None) -> str:
        """以易读 JSON 展示运行时背景摘要，不保留任何 HTTP 敏感字段。"""

        if context is None:
            return "无可展示的产品背景。"
        return json.dumps(dict(context), ensure_ascii=False, indent=2)

    @staticmethod
    def _opinion_summary(provider_results: Mapping[str, object]) -> str:
        """仅统计可用 label，提示 2+1 或完全分歧但不替代人工判断。"""

        labels = [
            decision.label.value
            for result in provider_results.values()
            if (decision := TaggingReviewDialog._provider_decision(result))
            is not None
        ]
        counts = Counter(labels)
        if len(counts) == 3:
            return "模型意见：三模型标签完全不同，请人工判断。"
        if counts:
            return "模型意见：" + "，".join(
                f"{count} × {label}" for label, count in counts.items()
            )
        return "模型意见不可用。"
