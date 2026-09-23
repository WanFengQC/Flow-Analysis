"""Flow Analysis 主窗口的展示与交互状态管理。"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.ui_main_window import Ui_MainWindow
from views.relation_product_card import (
    RelationProductCard,
    extract_variation_attributes,
)
from views.normalization_candidate_list_model import (
    NormalizationCandidateListModel,
)


class MultiSelectMonthComboBox(QComboBox):
    """提供动态时间范围列表和多选结果汇总显示的下拉控件。"""

    selection_changed = Signal(list)
    RECENT_30_DAYS_OPTION = "最近30天"

    def __init__(self, parent=None):
        """初始化只读显示框和支持勾选的标准项模型。"""

        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText("请选择月份")
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumHeight(32)

        self._month_model = QStandardItemModel(self)
        self.setModel(self._month_model)
        self.view().pressed.connect(self._toggle_month)

        # 文本区域与列表均由本控件处理，保证整块点击可以展开下拉列表。
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        """处理文本区域展开和连续多选所需的鼠标事件。"""

        if (
            watched is self.lineEdit()
            and event.type() == QEvent.Type.MouseButtonPress
        ):
            self.showPopup()
            return True

        if (
            watched is self.view().viewport()
            and event.type() == QEvent.Type.MouseButtonRelease
        ):
            return True

        return super().eventFilter(watched, event)

    def set_time_ranges(
        self,
        time_ranges: list[str],
        selected_ranges: list[str] | None = None,
    ):
        """用传入时间范围重建选项，并确保相对日期与自然月互斥。"""

        selected_set = set(selected_ranges or [])
        selected_natural_months = {
            time_range
            for time_range in selected_set
            if time_range != self.RECENT_30_DAYS_OPTION
        }
        # 自然月与最近 30 天不能构成同一个分析任务。若调用方意外传入
        # 混合状态，自然月优先，避免默认最近 30 天吞掉用户选择的月份。
        if selected_natural_months:
            selected_set.discard(self.RECENT_30_DAYS_OPTION)
        self._month_model.clear()

        for time_range in time_ranges:
            item = QStandardItem(time_range)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if time_range in selected_set
                else Qt.CheckState.Unchecked
            )
            self._month_model.appendRow(item)

        self._update_summary()

    def selected_months(self) -> list[str]:
        """按显示顺序返回已勾选的自然月，不包含“最近30天”。"""

        return [
            self._month_model.item(row).text()
            for row in range(self._month_model.rowCount())
            if self._month_model.item(row).checkState()
            == Qt.CheckState.Checked
            and self._month_model.item(row).text()
            != self.RECENT_30_DAYS_OPTION
        ]

    def selected_time_ranges(self) -> list[str]:
        """按显示顺序返回当前所有勾选的时间范围。"""

        return self._selected_time_ranges()

    def is_recent_30_days_selected(self) -> bool:
        """返回用户是否勾选了相对当前日期计算的最近30天范围。"""

        return self.RECENT_30_DAYS_OPTION in self._selected_time_ranges()

    def _toggle_month(self, index):
        """切换时间范围，并在最近 30 天与自然月之间保持互斥。"""

        item = self._month_model.itemFromIndex(index)
        if item is None:
            return

        should_check = item.checkState() != Qt.CheckState.Checked
        item.setCheckState(
            Qt.CheckState.Checked
            if should_check
            else Qt.CheckState.Unchecked
        )

        if should_check:
            if item.text() == self.RECENT_30_DAYS_OPTION:
                # 相对日期模式只能独立运行，不能与任意自然月混合。
                self._set_natural_months_checked(False)
            else:
                # 用户开始选择自然月时，必须清除初始化时默认勾选的最近 30 天。
                self._set_recent_30_days_checked(False)

        self._update_summary()
        self.selection_changed.emit(self._selected_time_ranges())

    def _set_recent_30_days_checked(self, checked: bool):
        """同步相对日期选项的内部勾选状态，不依赖下拉框显示文本。"""

        for row in range(self._month_model.rowCount()):
            item = self._month_model.item(row)
            if item.text() == self.RECENT_30_DAYS_OPTION:
                item.setCheckState(
                    Qt.CheckState.Checked
                    if checked
                    else Qt.CheckState.Unchecked
                )
                return

    def _set_natural_months_checked(self, checked: bool):
        """统一切换所有自然月，供用户切入最近 30 天模式时使用。"""

        for row in range(self._month_model.rowCount()):
            item = self._month_model.item(row)
            if item.text() != self.RECENT_30_DAYS_OPTION:
                item.setCheckState(
                    Qt.CheckState.Checked
                    if checked
                    else Qt.CheckState.Unchecked
                )

    def _update_summary(self):
        """将已选时间范围显示在下拉框收起后的只读输入框中。"""

        self.lineEdit().setText(", ".join(self._selected_time_ranges()))

    def _selected_time_ranges(self) -> list[str]:
        """返回包含相对日期选项在内的全部已选范围。"""

        return [
            self._month_model.item(row).text()
            for row in range(self._month_model.rowCount())
            if self._month_model.item(row).checkState()
            == Qt.CheckState.Checked
        ]


class NormalizationReviewDialog(QDialog):
    """承载单实例审核界面；关闭时仅隐藏，不清理任何审核内存状态。"""

    def __init__(self, parent: QWidget) -> None:
        """初始化可缩放、非模态的独立审核窗口。"""

        super().__init__(parent)
        self.setObjectName("normalizationReviewDialog")
        self.setWindowTitle("关键词归一审核")
        self.setModal(False)
        self.setMinimumSize(900, 600)
        self.resize(1200, 760)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)


class MainWindow(QMainWindow):
    """Flow Analysis 主窗口，仅负责界面展示、输入读取和状态更新。"""

    relation_query_requested = Signal()
    analysis_preparation_requested = Signal()
    normalization_approve_requested = Signal(str, str)
    normalization_reject_requested = Signal(str)
    normalization_skip_requested = Signal(str)
    normalization_apply_requested = Signal()
    normalization_persistence_retry_requested = Signal()
    normalization_candidate_selected = Signal(str)
    _ALL_VARIATION_FILTER_VALUE = "全部"

    def __init__(self):
        """加载 Designer 生成的界面，并补充运行时自定义控件。"""

        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # 以下状态完全属于 View：本次结果、筛选、选择和动态卡片。
        self._relation_items: list[Mapping[str, Any]] = []
        self._relation_entries: list[
            tuple[
                Mapping[str, Any],
                dict[str, str],
                RelationProductCard,
            ]
        ] = []
        self._cards_by_image_url: dict[
            str,
            list[RelationProductCard],
        ] = {}
        self._variation_filter_combos: dict[str, QComboBox] = {}
        self._selected_asins: set[str] = set()
        self._no_match_relation_label: QLabel | None = None
        self._relation_query_available = False
        self._relation_query_running = False
        self._relation_preparation_running = False
        # 审核候选只保存 Controller 提供的当前任务内存引用；View 只读取、
        # 筛选和展示，绝不直接写入 decision 或 approvedCanonical。
        self._normalization_candidates: list[Mapping[str, Any]] = []
        self._normalization_candidates_by_id: dict[str, Mapping[str, Any]] = {}
        self._normalization_conflicts_by_id: dict[str, list[dict[str, Any]]] = {}
        # 历史参考单独保存，绝不向 candidate 注入或覆写任何审核字段。
        self._normalization_history_references: dict[str, Mapping[str, Any]] = {}
        self._normalization_history_load_status = "IDLE"
        self._normalization_current_candidate_id: str | None = None
        self._normalization_next_candidate_id: str | None = None
        self._expanded_normalization_evidence: set[tuple[str, str]] = set()
        # 重算期间仅锁定会改变审核快照的控件；筛选、列表查看和滚动仍可用。
        self._normalization_review_editable = True

        self._setup_month_selector()
        self._setup_workspace()
        self._setup_status_bar()
        self._setup_relation_query()
        self._setup_normalization_review()

    def _setup_month_selector(self):
        """创建多选时间范围控件，提供最近30天及 2024-01 起的月份。"""

        self.month_selector = MultiSelectMonthComboBox(
            self.ui.parameterPanel
        )
        self.month_selector.setObjectName("monthSelector")

        time_ranges = [
            MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION,
            *self._recent_months(),
        ]
        self.month_selector.set_time_ranges(
            time_ranges,
            selected_ranges=[
                MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION
            ],
        )
        self.ui.monthSelectorContainerLayout.addWidget(
            self.month_selector
        )

    @staticmethod
    def _recent_months() -> list[str]:
        """从当前月份倒序生成至 2024-01 的月份。"""

        today = date.today()
        year = today.year
        month = today.month
        months = []

        while (year, month) >= (2024, 1):
            months.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                year -= 1
                month = 12

        return months

    def _setup_workspace(self):
        """配置 Splitter、表格和关联结果区的集中视觉风格。"""

        self.ui.mainSplitter.setStretchFactor(0, 0)
        self.ui.mainSplitter.setStretchFactor(1, 1)
        self.ui.mainSplitter.setSizes([300, 900])
        self.ui.resultTableView.setSortingEnabled(True)
        self.ui.resultTableView.horizontalHeader().setStretchLastSection(
            True
        )

        # 只移除 ScrollArea 自身默认绘制的边框与背景，
        # 结果区域其余部分继续继承程序原有主题和父控件背景。
        self.ui.resultTab.setStyleSheet(
            """
            QScrollArea#relationResultScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea#relationResultScrollArea QWidget#qt_scrollarea_viewport {
                background: transparent;
                border: none;
            }
            QWidget#relationResultScrollContent {
                background: transparent;
            }
            """
        )

    def _setup_relation_query(self):
        """统一查询入口、筛选工具栏和关联结果默认空状态。"""

        self.ui.asinQueryButton.clicked.connect(
            self._emit_relation_query_requested
        )
        self.ui.asinLineEdit.returnPressed.connect(
            self._emit_relation_query_requested
        )
        self.ui.resetRelationFiltersButton.clicked.connect(
            self._reset_relation_filters
        )
        self.ui.selectVisibleRelationButton.clicked.connect(
            self._toggle_select_visible_relation_items
        )
        self.ui.startAnalysisButton.clicked.connect(
            self._emit_analysis_preparation_requested
        )
        self.show_relation_empty()
        self.set_relation_query_available(False)

    def _setup_normalization_review(self) -> None:
        """初始化归一候选审核页面的 View 级筛选、列表与交互。"""

        self._setup_normalization_review_dialog()

        self._normalization_list_model = NormalizationCandidateListModel(self)
        self.ui.normalizationCandidateListView.setModel(
            self._normalization_list_model
        )
        self.ui.normalizationCandidateListView.setUniformItemSizes(True)
        self.ui.normalizationCandidateListView.selectionModel().currentChanged.connect(
            self._on_normalization_list_current_changed
        )

        self._populate_normalization_filter_controls()
        self.ui.normalizationSearchLineEdit.textChanged.connect(
            self._refresh_normalization_review
        )
        self.ui.normalizationReasonComboBox.currentIndexChanged.connect(
            self._refresh_normalization_review
        )
        self.ui.normalizationDecisionComboBox.currentIndexChanged.connect(
            self._refresh_normalization_review
        )
        self.ui.normalizationHistoryComboBox.currentIndexChanged.connect(
            self._refresh_normalization_review
        )
        self.ui.normalizationSortComboBox.currentIndexChanged.connect(
            self._refresh_normalization_review
        )
        self.ui.approveNormalizationButton.clicked.connect(
            self._request_normalization_approval
        )
        self.ui.rejectNormalizationButton.clicked.connect(
            self._request_normalization_rejection
        )
        self.ui.skipNormalizationButton.clicked.connect(
            self._request_normalization_skip
        )
        self.ui.applyNormalizationRulesButton.clicked.connect(
            self._request_normalization_apply
        )
        self.ui.retryNormalizationPersistenceButton.clicked.connect(
            self.normalization_persistence_retry_requested.emit
        )
        self.ui.useHistoryCanonicalButton.clicked.connect(
            self._use_history_canonical
        )
        self.ui.viewNormalizationHistoryButton.clicked.connect(
            self._show_normalization_history_dialog
        )

        # 快捷键严格归属 modeless 审核 Dialog；主窗口输入 ASIN 时绝不触发。
        self._normalization_approve_shortcut = QShortcut(
            QKeySequence("A"), self._normalization_review_dialog
        )
        self._normalization_reject_shortcut = QShortcut(
            QKeySequence("R"), self._normalization_review_dialog
        )
        self._normalization_skip_shortcut = QShortcut(
            QKeySequence("S"), self._normalization_review_dialog
        )
        self._normalization_approve_shortcut.activated.connect(
            self._request_normalization_approval_from_shortcut
        )
        self._normalization_reject_shortcut.activated.connect(
            self._request_normalization_rejection_from_shortcut
        )
        self._normalization_skip_shortcut.activated.connect(
            self._request_normalization_skip_from_shortcut
        )

        self.ui.normalizationReviewSplitter.setStretchFactor(0, 35)
        self.ui.normalizationReviewSplitter.setStretchFactor(1, 65)
        self.ui.normalizationReviewSplitter.setSizes([360, 670])
        self._set_normalization_detail_candidate(None)
        self.set_normalization_result_status("尚未生成正式结果")
        self.set_normalization_persistence_status("CLEAN")
        self.set_analysis_result_mode("未归一预览")
        self.set_normalization_candidates([])

    def _setup_normalization_review_dialog(self) -> None:
        """将唯一的 Designer 审核页从主 Tab 移入 modeless Dialog。"""

        tab_index = self.ui.resultTabWidget.indexOf(
            self.ui.normalizationReviewTab
        )
        if tab_index >= 0:
            self.ui.resultTabWidget.removeTab(tab_index)

        self._normalization_review_dialog = NormalizationReviewDialog(self)
        dialog_layout = QVBoxLayout(self._normalization_review_dialog)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(self.ui.normalizationReviewTab)

        # Splitter 是唯一可伸缩区域；紧凑状态行移动到 Dialog 底部。
        review_layout = self.ui.normalizationReviewLayout
        status_item = review_layout.takeAt(1)
        if status_item is not None:
            review_layout.addItem(status_item)
        review_layout.setStretch(0, 0)
        review_layout.setStretch(1, 1)
        review_layout.setStretch(2, 0)

        self.ui.openNormalizationReviewButton.clicked.connect(
            self.show_normalization_review
        )

    def show_normalization_review(self) -> None:
        """显示同一个审核 Dialog；没有候选时不打开空审核窗口。"""

        if not self._normalization_candidates:
            return
        self._normalization_review_dialog.show()
        self._normalization_review_dialog.raise_()
        self._normalization_review_dialog.activateWindow()

    def _populate_normalization_filter_controls(self) -> None:
        """填充审核页固定筛选项，内部值始终保持正式 reason/decision 字符串。"""

        self.ui.normalizationReasonComboBox.addItem("全部类型", None)
        self.ui.normalizationReasonComboBox.addItem(
            "短语变体",
            "PHRASE_TOKEN_VARIANT",
        )
        self.ui.normalizationReasonComboBox.addItem(
            "短语概念 Seed",
            "PHRASE_CONCEPT_SEED",
        )
        self.ui.normalizationReasonComboBox.addItem(
            "格式变体",
            "COMPACT_SIGNATURE_MATCH",
        )
        self.ui.normalizationReasonComboBox.addItem(
            "已有词形规则",
            "KNOWN_WORD_ALIAS_VARIANT",
        )
        self.ui.normalizationReasonComboBox.addItem(
            "字符相似",
            "CHARACTER_SIMILARITY",
        )

        self.ui.normalizationDecisionComboBox.addItem("全部状态", None)
        self.ui.normalizationDecisionComboBox.addItem("待审核", "PENDING")
        self.ui.normalizationDecisionComboBox.addItem("已批准", "APPROVED")
        self.ui.normalizationDecisionComboBox.addItem("已拒绝", "REJECTED")
        self.ui.normalizationDecisionComboBox.addItem("已跳过", "SKIPPED")
        self.ui.normalizationDecisionComboBox.setCurrentIndex(1)

        self.ui.normalizationHistoryComboBox.addItem("全部历史", None)
        self.ui.normalizationHistoryComboBox.addItem("无历史", "NONE")
        self.ui.normalizationHistoryComboBox.addItem("有历史", "HAS_HISTORY")
        self.ui.normalizationHistoryComboBox.addItem("历史结论有变化", "CONFLICT")

        self.ui.normalizationSortComboBox.addItem("影响程度", "impact")
        self.ui.normalizationSortComboBox.addItem("频次", "frequency")
        self.ui.normalizationSortComboBox.addItem("匹配关键词数", "matching")
        self.ui.normalizationSortComboBox.addItem("置信度", "confidence")

    def set_normalization_history_references(
        self,
        references: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """接收 Controller 的只读历史参考，不修改候选或当前审核决定。"""

        self._normalization_history_references = {
            candidate_id: dict(reference)
            for candidate_id, reference in references.items()
            if isinstance(candidate_id, str) and isinstance(reference, Mapping)
        }
        self._normalization_list_model.set_history_references(
            self._normalization_history_references
        )
        self._refresh_normalization_review(
            preferred_candidate_id=self._normalization_current_candidate_id
        )

    def set_normalization_history_load_status(self, status: str) -> None:
        """展示非核心历史读取状态；失败不阻断候选审核。"""

        self._normalization_history_load_status = status
        candidate = self._normalization_candidates_by_id.get(
            self._normalization_current_candidate_id or ""
        )
        self._populate_normalization_history(
            str(candidate.get("id")) if candidate is not None else None
        )

    def set_normalization_candidates(
        self,
        candidates: list[Mapping[str, Any]],
    ) -> None:
        """接收 Controller 的候选快照，更新审核 Tab 而不修改候选对象。"""

        self._normalization_candidates = list(candidates)
        self._normalization_candidates_by_id = {
            str(candidate.get("id")): candidate
            for candidate in self._normalization_candidates
            if isinstance(candidate.get("id"), str)
        }
        self._normalization_conflicts_by_id = (
            self._build_normalization_conflict_index()
        )
        self._expanded_normalization_evidence.clear()
        self.ui.openNormalizationReviewButton.setText(
            f"归一审核 ({len(self._normalization_candidates)})"
        )
        self.ui.openNormalizationReviewButton.setEnabled(
            bool(self._normalization_candidates)
        )
        self._normalization_current_candidate_id = None
        self._normalization_next_candidate_id = None
        self.ui.applyNormalizationRulesButton.setEnabled(
            self._normalization_review_editable
            and bool(self._normalization_candidates)
        )
        self._refresh_normalization_review()
        if not self._normalization_candidates:
            self._normalization_review_dialog.hide()

    def update_normalization_candidate(
        self,
        candidate: Mapping[str, Any],
    ) -> None:
        """接收 Controller 更新后的单个候选，并保留当前筛选与排序条件。"""

        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str):
            return

        self._normalization_candidates_by_id[candidate_id] = candidate
        self._normalization_candidates = [
            self._normalization_candidates_by_id.get(
                str(existing.get("id")), existing
            )
            for existing in self._normalization_candidates
        ]
        self._normalization_conflicts_by_id = (
            self._build_normalization_conflict_index()
        )
        self._refresh_normalization_review(
            preferred_candidate_id=(
                self._normalization_next_candidate_id
                or self._normalization_current_candidate_id
            )
        )
        self._normalization_next_candidate_id = None

    def _refresh_normalization_review(
        self,
        _unused: object = None,
        *,
        preferred_candidate_id: str | None = None,
    ) -> None:
        """按当前 View 条件实时过滤、降序排序并刷新候选列表。"""

        visible_candidates = self._visible_normalization_candidates()
        selected_id = (
            preferred_candidate_id
            or self._normalization_current_candidate_id
        )
        self._normalization_list_model.set_candidates(visible_candidates)
        self._update_normalization_progress()

        has_candidates = bool(self._normalization_candidates)
        has_visible_candidates = bool(visible_candidates)
        self.ui.normalizationCandidateListView.setVisible(has_visible_candidates)
        self.ui.normalizationReviewEmptyLabel.setVisible(
            not has_visible_candidates
        )
        self.ui.normalizationReviewEmptyLabel.setText(
            "当前分析未发现需要人工审核的归一候选。"
            if not has_candidates
            else "当前筛选条件下没有匹配候选。"
        )

        if not has_visible_candidates:
            self._normalization_current_candidate_id = None
            self._set_normalization_detail_candidate(None)
            return

        visible_ids = [
            str(candidate["id"])
            for candidate in visible_candidates
            if isinstance(candidate.get("id"), str)
        ]
        if selected_id not in visible_ids:
            selected_id = visible_ids[0]
        self._select_normalization_candidate(selected_id)

    def _visible_normalization_candidates(self) -> list[Mapping[str, Any]]:
        """只在 View 内按文本、类型、状态筛选候选，并按选定字段降序。"""

        keyword = self.ui.normalizationSearchLineEdit.text().strip().lower()
        selected_reason = self.ui.normalizationReasonComboBox.currentData()
        selected_decision = self.ui.normalizationDecisionComboBox.currentData()
        selected_history = self.ui.normalizationHistoryComboBox.currentData()

        candidates = []
        for candidate in self._normalization_candidates:
            if not self._candidate_matches_normalization_search(candidate, keyword):
                continue
            if (
                selected_reason is not None
                and selected_reason not in candidate.get("reasonTypes", [])
            ):
                continue
            if (
                selected_decision is not None
                and candidate.get("decision", "PENDING") != selected_decision
            ):
                continue
            candidate_id = candidate.get("id")
            reference = (
                self._normalization_history_references.get(candidate_id)
                if isinstance(candidate_id, str)
                else None
            )
            history_count = (
                reference.get("totalDecisionCount", 0)
                if isinstance(reference, Mapping)
                else 0
            )
            if selected_history == "NONE" and history_count:
                continue
            if selected_history == "HAS_HISTORY" and not history_count:
                continue
            if selected_history == "CONFLICT" and not (
                isinstance(reference, Mapping)
                and (
                    reference.get("hasDecisionConflict")
                    or reference.get("hasCanonicalConflict")
                )
            ):
                continue
            candidates.append(candidate)

        sort_key = self.ui.normalizationSortComboBox.currentData()
        sort_field = {
            "impact": "impactWeeklyExposure",
            "frequency": "totalFrequency",
            "matching": "matchingKeywordCount",
            "confidence": "confidence",
        }.get(sort_key, "impactWeeklyExposure")
        return sorted(
            candidates,
            key=lambda candidate: (
                -self._normalization_number(candidate.get(sort_field)),
                str(candidate.get("id", "")),
            ),
        )

    @staticmethod
    def _candidate_matches_normalization_search(
        candidate: Mapping[str, Any],
        keyword: str,
    ) -> bool:
        """搜索 machine suggestion、人工 canonical 与所有真实 variant。"""

        if not keyword:
            return True
        values = [
            candidate.get("suggestedCanonical"),
            candidate.get("approvedCanonical"),
            *candidate.get("variants", []),
        ]
        return any(
            keyword in value.lower()
            for value in values
            if isinstance(value, str)
        )

    def _update_normalization_progress(self) -> None:
        """根据 Controller 当前内存候选实时汇总审核进度。"""

        total = len(self._normalization_candidates)
        decision_counts = {
            decision: sum(
                candidate.get("decision", "PENDING") == decision
                for candidate in self._normalization_candidates
            )
            for decision in ("PENDING", "APPROVED", "REJECTED", "SKIPPED")
        }
        reviewed = total - decision_counts["PENDING"]
        self.ui.normalizationProgressLabel.setText(
            f"已审核 {reviewed} / {total} · 待审核 "
            f"{decision_counts['PENDING']} · 批准 "
            f"{decision_counts['APPROVED']} · 拒绝 "
            f"{decision_counts['REJECTED']} · 跳过 "
            f"{decision_counts['SKIPPED']}"
        )

    def _on_normalization_list_current_changed(
        self,
        current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        """将 QListView 当前项转换为候选 ID，再刷新右侧详情。"""

        candidate_id = current.data(
            NormalizationCandidateListModel.CandidateIdRole
        )
        if isinstance(candidate_id, str):
            self._normalization_current_candidate_id = candidate_id
            self._set_normalization_detail_candidate(
                self._normalization_candidates_by_id.get(candidate_id)
            )
            self.normalization_candidate_selected.emit(candidate_id)
            return

        self._normalization_current_candidate_id = None
        self._set_normalization_detail_candidate(None)

    def _select_normalization_candidate(self, candidate_id: str) -> None:
        """在当前过滤列表中选中目标候选；不可见时保持现有筛选状态。"""

        for row in range(self._normalization_list_model.rowCount()):
            index = self._normalization_list_model.index(row, 0)
            if (
                index.data(NormalizationCandidateListModel.CandidateIdRole)
                == candidate_id
            ):
                self.ui.normalizationCandidateListView.setCurrentIndex(index)
                self.ui.normalizationCandidateListView.scrollTo(index)
                return

    def _set_normalization_detail_candidate(
        self,
        candidate: Mapping[str, Any] | None,
    ) -> None:
        """以当前候选的只读数据重建右侧详情，未选择时显示空状态。"""

        has_candidate = candidate is not None
        self.ui.normalizationDetailEmptyLabel.setVisible(not has_candidate)
        self.ui.normalizationDetailContent.setVisible(has_candidate)
        for button in (
            self.ui.approveNormalizationButton,
            self.ui.rejectNormalizationButton,
            self.ui.skipNormalizationButton,
        ):
            button.setEnabled(
                has_candidate and self._normalization_review_editable
            )
        self.ui.normalizationCanonicalLineEdit.setEnabled(
            has_candidate and self._normalization_review_editable
        )
        if not has_candidate:
            self._clear_layout(self.ui.normalizationVariantDetailsLayout)
            self._clear_layout(self.ui.normalizationConflictLayout)
            self._populate_normalization_history(None)
            return

        assert candidate is not None
        candidate_id = str(candidate.get("id", ""))
        suggested_canonical = str(candidate.get("suggestedCanonical") or "")
        self.ui.normalizationSuggestedCanonicalLabel.setText(suggested_canonical)
        self.ui.normalizationCanonicalLineEdit.setText(
            str(candidate.get("approvedCanonical") or suggested_canonical)
        )
        self.ui.normalizationReasonBadgeLabel.setText(
            " · ".join(
                self._normalization_reason_name(reason)
                for reason in candidate.get("reasonTypes", [])
                if isinstance(reason, str)
            )
        )
        self.ui.normalizationReasonBadgeLabel.setStyleSheet(
            "padding: 3px 7px; border: 1px solid #d6dce5; "
            "border-radius: 6px;"
        )
        self.ui.normalizationConfidenceValueLabel.setText(
            f"{self._normalization_number(candidate.get('confidence')):.2f}"
        )
        self.ui.normalizationImpactValueLabel.setText(
            self._format_normalization_number(
                candidate.get("impactWeeklyExposure"),
                decimal_places=2,
            )
        )
        self.ui.normalizationMonthsValueLabel.setText(
            " / ".join(
                month
                for month in candidate.get("months", [])
                if isinstance(month, str)
            )
            or "—"
        )
        self.ui.normalizationFrequencyValueLabel.setText(
            self._format_normalization_number(candidate.get("totalFrequency"))
        )
        self.ui.normalizationMatchingValueLabel.setText(
            self._format_normalization_number(
                candidate.get("matchingKeywordCount")
            )
        )
        self._populate_normalization_variant_details(candidate_id, candidate)
        self._populate_normalization_conflicts(candidate_id)
        self._populate_normalization_history(candidate_id)

    def _populate_normalization_history(self, candidate_id: str | None) -> None:
        """将历史信息作为参考展示，禁止它改变当前候选或任何决策。"""

        if candidate_id is None:
            self.ui.normalizationHistorySummaryLabel.setText("未选择候选。")
            self.ui.useHistoryCanonicalButton.setEnabled(False)
            self.ui.viewNormalizationHistoryButton.setEnabled(False)
            return
        if self._normalization_history_load_status == "LOADING":
            self.ui.normalizationHistorySummaryLabel.setText("历史审核记录加载中...")
            self.ui.useHistoryCanonicalButton.setEnabled(False)
            self.ui.viewNormalizationHistoryButton.setEnabled(False)
            return
        if self._normalization_history_load_status == "LOAD_FAILED":
            self.ui.normalizationHistorySummaryLabel.setText(
                "历史审核记录加载失败；当前候选仍可正常审核。"
            )
            self.ui.useHistoryCanonicalButton.setEnabled(False)
            self.ui.viewNormalizationHistoryButton.setEnabled(False)
            return

        reference = self._normalization_history_references.get(candidate_id)
        if not isinstance(reference, Mapping) or not reference.get("totalDecisionCount"):
            self.ui.normalizationHistorySummaryLabel.setText("无历史审核记录。")
            self.ui.useHistoryCanonicalButton.setEnabled(False)
            self.ui.viewNormalizationHistoryButton.setEnabled(False)
            return

        total = self._normalization_number(reference.get("totalDecisionCount"))
        decisions = reference.get("decisionCounts", {})
        summary_lines = [f"历史审核：{int(total)} 次"]
        latest_decision = self._normalization_history_decision_name(
            reference.get("latestDecision")
        )
        latest_at = reference.get("latestReviewedAt") or "—"
        summary_lines.append(f"最新：{latest_decision} · {latest_at}")
        latest_canonical = reference.get("latestApprovedCanonical")
        if isinstance(latest_canonical, str) and latest_canonical:
            summary_lines.append(f"最近批准 canonical：{latest_canonical}")
        if isinstance(decisions, Mapping):
            summary_lines.append(
                f" · 批准 {self._normalization_number(decisions.get('APPROVED')):.0f}"
                f" · 拒绝 {self._normalization_number(decisions.get('REJECTED')):.0f}"
                f" · 跳过 {self._normalization_number(decisions.get('SKIPPED')):.0f}"
            )
        canonical_items = reference.get("approvedCanonicals", [])
        if isinstance(canonical_items, list) and canonical_items:
            canonical_text = " · ".join(
                f"{item.get('canonical')} ×{item.get('count')}"
                for item in canonical_items
                if isinstance(item, Mapping)
                and isinstance(item.get("canonical"), str)
            )
            if canonical_text:
                summary_lines.append(f"历史标准词：{canonical_text}")
        if reference.get("hasDecisionConflict") or reference.get("hasCanonicalConflict"):
            summary_lines.append("⚠ 历史结论有变化")
        self.ui.normalizationHistorySummaryLabel.setText("\n".join(summary_lines))
        can_use_canonical = bool(reference.get("latestApprovedCanonical"))
        self.ui.useHistoryCanonicalButton.setEnabled(can_use_canonical)
        self.ui.viewNormalizationHistoryButton.setEnabled(True)

    @staticmethod
    def _normalization_history_decision_name(decision: object) -> str:
        """用中文展示仅供参考的历史决定，不对当前候选作任何暗示。"""

        return {
            "APPROVED": "已批准",
            "REJECTED": "已拒绝",
            "SKIPPED": "已跳过",
        }.get(str(decision), "—")

    def _use_history_canonical(self) -> None:
        """仅将历史批准词填入编辑框，用户仍须明确点击当前批准按钮。"""

        reference = self._normalization_history_references.get(
            self._normalization_current_candidate_id or ""
        )
        canonical = (
            reference.get("latestApprovedCanonical")
            if isinstance(reference, Mapping)
            else None
        )
        if isinstance(canonical, str) and canonical.strip():
            self.ui.normalizationCanonicalLineEdit.setText(canonical)
            self.ui.normalizationCanonicalLineEdit.setFocus()

    def _show_normalization_history_dialog(self) -> None:
        """显示最多 20 条脱敏历史记录，仅提供阅读，不提供任何审核操作。"""

        reference = self._normalization_history_references.get(
            self._normalization_current_candidate_id or ""
        )
        if not isinstance(reference, Mapping):
            return
        records = reference.get("recentDecisions", [])
        if not isinstance(records, list):
            return
        lines: list[str] = []
        for index, record in enumerate(records[:20], start=1):
            if not isinstance(record, Mapping):
                continue
            context = record.get("context")
            context_text = (
                ", ".join(
                    f"{key}={value}"
                    for key, value in context.items()
                )
                if isinstance(context, Mapping)
                else ""
            )
            canonical = record.get("approvedCanonical") or "-"
            reasons = ", ".join(record.get("reasonTypes", []))
            lines.extend(
                [
                    f"{index}. 时间：{record.get('reviewedAt') or '-'}",
                    f"   决定：{record.get('decision') or '-'} · 标准词：{canonical}",
                    f"   候选：{record.get('candidateId') or '-'} · 原因：{reasons or '-'}",
                    f"   上下文：{context_text or '-'}",
                ]
            )
        dialog = QDialog(self)
        dialog.setWindowTitle("历史审核参考")
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        text_edit = QPlainTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setPlainText("\n".join(lines) or "无历史审核记录。")
        layout.addWidget(text_edit)
        close_button = QPushButton("关闭", dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _populate_normalization_variant_details(
        self,
        candidate_id: str,
        candidate: Mapping[str, Any],
    ) -> None:
        """纵向展示任意数量 variant，兼容未来三项以上的候选组。"""

        layout = self.ui.normalizationVariantDetailsLayout
        self._clear_layout(layout)
        details_by_variant = {
            detail.get("variant"): detail
            for detail in candidate.get("variantDetails", [])
            if isinstance(detail, Mapping) and isinstance(detail.get("variant"), str)
        }
        variants = [
            variant
            for variant in candidate.get("variants", [])
            if isinstance(variant, str)
        ]
        for index, variant in enumerate(variants, start=1):
            detail = details_by_variant.get(variant, {"variant": variant})
            layout.addWidget(
                self._build_normalization_variant_widget(
                    candidate_id,
                    index,
                    variant,
                    detail,
                    candidate.get("evidence", []),
                    "PHRASE_CONCEPT_SEED"
                    in candidate.get("reasonTypes", []),
                )
            )

    def _build_normalization_variant_widget(
        self,
        candidate_id: str,
        index: int,
        variant: str,
        detail: Mapping[str, Any],
        fallback_evidence: object,
        is_phrase_concept: bool,
    ) -> QWidget:
        """创建单个 variant 的只读统计、月度分布和可展开关键词证据。"""

        group_title = (
            f"原始短语：{variant}"
            if is_phrase_concept
            else f"Variant {index}：{variant}"
        )
        group = QGroupBox(group_title)
        group_layout = QVBoxLayout(group)
        statistic_label = QLabel(
            "频次："
            f"{self._format_normalization_number(detail.get('frequency'))}    "
            "匹配搜索词："
            f"{self._format_normalization_number(detail.get('matchingKeywordCount'))}    "
            "影响周曝光："
            f"{self._format_normalization_number(detail.get('impactWeeklyExposure'), decimal_places=2)}"
        )
        statistic_label.setWordWrap(True)
        group_layout.addWidget(statistic_label)

        monthly_stats = detail.get("monthlyStats")
        if isinstance(monthly_stats, list) and monthly_stats:
            monthly_widget = QWidget(group)
            monthly_layout = QFormLayout(monthly_widget)
            monthly_layout.setContentsMargins(0, 0, 0, 0)
            for row in monthly_stats:
                if not isinstance(row, Mapping):
                    continue
                month = str(row.get("month") or "")
                values = (
                    f"频次 {self._format_normalization_number(row.get('frequency'))} · "
                    f"匹配词 {self._format_normalization_number(row.get('matchingKeywordCount'))} · "
                    "周曝光 "
                    f"{self._format_normalization_number(row.get('impactWeeklyExposure'), decimal_places=2)}"
                )
                monthly_layout.addRow(QLabel(month), QLabel(values))
            group_layout.addWidget(monthly_widget)
        else:
            months = detail.get("months", [])
            month_text = " / ".join(
                month for month in months if isinstance(month, str)
            )
            group_layout.addWidget(QLabel(f"出现月份：{month_text or '—'}"))

        evidence = detail.get("evidence")
        if not isinstance(evidence, list):
            evidence = fallback_evidence if isinstance(fallback_evidence, list) else []
        evidence = [item for item in evidence if isinstance(item, str)]
        expanded_key = (candidate_id, variant)
        evidence_limit = 20 if expanded_key in self._expanded_normalization_evidence else 5
        evidence_label = QLabel(
            "\n".join(f"• {item}" for item in evidence[:evidence_limit])
            or "暂无可展示的关键词证据"
        )
        evidence_label.setWordWrap(True)
        evidence_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        group_layout.addWidget(evidence_label)
        if len(evidence) > 5:
            toggle_button = QToolButton(group)
            toggle_button.setText(
                "收起证据" if expanded_key in self._expanded_normalization_evidence else "查看更多"
            )
            toggle_button.clicked.connect(
                lambda _checked=False, key=expanded_key: self._toggle_normalization_evidence(key)
            )
            group_layout.addWidget(toggle_button)
        return group

    def _toggle_normalization_evidence(
        self,
        expanded_key: tuple[str, str],
    ) -> None:
        """仅切换当前 variant 的 5/20 条展示上限，不改动原始 evidence。"""

        if expanded_key in self._expanded_normalization_evidence:
            self._expanded_normalization_evidence.remove(expanded_key)
        else:
            self._expanded_normalization_evidence.add(expanded_key)
        candidate = self._normalization_candidates_by_id.get(expanded_key[0])
        if candidate is not None:
            self._set_normalization_detail_candidate(candidate)

    def _populate_normalization_conflicts(self, candidate_id: str) -> None:
        """显示共享 variant 的其他候选，并允许用户直接跳转查看。"""

        layout = self.ui.normalizationConflictLayout
        self._clear_layout(layout)
        conflicts = self._normalization_conflicts_by_id.get(candidate_id, [])
        if not conflicts:
            layout.addWidget(QLabel("未发现跨候选组重复 variant。"))
            return

        layout.addWidget(
            QLabel(f"存在 {len(conflicts)} 个关联候选；点击可直接查看。")
        )
        for conflict in conflicts:
            button = QToolButton(self.ui.normalizationConflictContainer)
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextOnly
            )
            button.setText(
                "查看候选："
                f"{conflict['canonical']} · {conflict['otherVariants']} · "
                f"{conflict['reasonTypes']} · "
                f"{self._format_normalization_number(conflict['impact'], decimal_places=2)}"
            )
            button.clicked.connect(
                lambda _checked=False, target_id=conflict["candidateId"]: self._jump_to_normalization_candidate(target_id)
            )
            layout.addWidget(button)

    def _build_normalization_conflict_index(self) -> dict[str, list[dict[str, Any]]]:
        """从当前候选列表构建只读共享 variant 索引，不自动解决任何冲突。"""

        variant_to_candidates: dict[str, list[Mapping[str, Any]]] = {}
        for candidate in self._normalization_candidates:
            for variant in candidate.get("variants", []):
                if isinstance(variant, str):
                    variant_to_candidates.setdefault(variant, []).append(candidate)

        conflicts_by_id: dict[str, list[dict[str, Any]]] = {}
        for candidate in self._normalization_candidates:
            candidate_id = candidate.get("id")
            if not isinstance(candidate_id, str):
                continue
            shared_by_other_id: dict[str, set[str]] = {}
            for variant in candidate.get("variants", []):
                if not isinstance(variant, str):
                    continue
                for other in variant_to_candidates.get(variant, []):
                    other_id = other.get("id")
                    if isinstance(other_id, str) and other_id != candidate_id:
                        shared_by_other_id.setdefault(other_id, set()).add(variant)

            conflicts_by_id[candidate_id] = [
                {
                    "candidateId": other_id,
                    "canonical": str(other.get("suggestedCanonical") or "—"),
                    "otherVariants": " / ".join(sorted(shared_variants)),
                    "reasonTypes": " · ".join(
                        self._normalization_reason_name(reason)
                        for reason in other.get("reasonTypes", [])
                        if isinstance(reason, str)
                    ),
                    "impact": other.get("impactWeeklyExposure"),
                }
                for other_id, shared_variants in shared_by_other_id.items()
                if (other := self._normalization_candidates_by_id.get(other_id))
                is not None
            ]
            conflicts_by_id[candidate_id].sort(
                key=lambda conflict: -self._normalization_number(
                    conflict["impact"]
                )
            )
        return conflicts_by_id

    def _jump_to_normalization_candidate(self, candidate_id: str) -> None:
        """清除会阻止跳转的筛选条件，并定位到冲突候选。"""

        self.ui.normalizationSearchLineEdit.clear()
        self.ui.normalizationReasonComboBox.setCurrentIndex(0)
        self.ui.normalizationDecisionComboBox.setCurrentIndex(0)
        self._refresh_normalization_review(
            preferred_candidate_id=candidate_id
        )

    def _request_normalization_approval(self) -> None:
        """校验可编辑 canonical 后，向 Controller 发出批准请求。"""

        if not self._normalization_review_editable:
            return
        candidate_id = self._normalization_current_candidate_id
        canonical = self.ui.normalizationCanonicalLineEdit.text().strip()
        if not candidate_id:
            return
        if not canonical:
            QMessageBox.warning(self, "请输入标准词", "请输入标准词。")
            self.ui.normalizationCanonicalLineEdit.setFocus()
            return
        self._prepare_normalization_next_selection()
        self.normalization_approve_requested.emit(candidate_id, canonical)

    def _request_normalization_rejection(self) -> None:
        """向 Controller 发出拒绝当前候选组的请求。"""

        if not self._normalization_review_editable:
            return
        if not self._normalization_current_candidate_id:
            return
        self._prepare_normalization_next_selection()
        self.normalization_reject_requested.emit(
            self._normalization_current_candidate_id
        )

    def _request_normalization_skip(self) -> None:
        """向 Controller 发出暂时跳过当前候选组的请求。"""

        if not self._normalization_review_editable:
            return
        if not self._normalization_current_candidate_id:
            return
        self._prepare_normalization_next_selection()
        self.normalization_skip_requested.emit(
            self._normalization_current_candidate_id
        )

    def _request_normalization_apply(self) -> None:
        """将整批已审核状态交给 Controller，View 不参与规则编译或重算。"""

        if self._normalization_review_editable:
            self.normalization_apply_requested.emit()

    def set_normalization_review_editable(self, editable: bool) -> None:
        """切换审核写操作；筛选、搜索、查看与滚动始终不受影响。"""

        self._normalization_review_editable = editable
        self.ui.applyNormalizationRulesButton.setEnabled(
            editable and bool(self._normalization_candidates)
        )
        current_candidate = self._normalization_candidates_by_id.get(
            self._normalization_current_candidate_id or ""
        )
        self._set_normalization_detail_candidate(current_candidate)

    def set_normalization_result_status(self, status: str) -> None:
        """展示 Controller 判定的正式结果状态，不在 View 推导业务状态。"""

        presentation = {
            "尚未生成正式结果": "正式结果：尚未生成",
            "正在按已批准规则重算...": "正式结果：正在重新计算...",
        }
        self.ui.normalizationResultStatusLabel.setText(
            presentation.get(status, f"正式结果：{status}")
        )

    def set_analysis_result_mode(self, mode: str) -> None:
        """在数据页标明当前表格应被解释为预览、正式或旧版正式结果。"""

        self.ui.analysisResultModeLabel.setText(mode)

    def set_normalization_persistence_status(self, status: str) -> None:
        """展示 Controller 提供的审核持久化状态，并仅在失败/未保存时开放重试。"""

        presentation = {
            "CLEAN": "审核记录：已保存",
            "SAVING": "审核记录：正在保存...",
            "UNSAVED": "审核记录：有未保存内容",
            "SAVE_FAILED": "审核记录：保存失败，可重试",
        }
        self.ui.normalizationPersistenceStatusLabel.setText(
            presentation.get(status, "审核记录：状态未知")
        )
        self.ui.retryNormalizationPersistenceButton.setVisible(
            status in {"UNSAVED", "SAVE_FAILED"}
        )
        self.ui.retryNormalizationPersistenceButton.setEnabled(
            status in {"UNSAVED", "SAVE_FAILED"}
        )

    def show_normalization_rule_conflicts(
        self,
        conflicts: list[Mapping[str, Any]],
    ) -> None:
        """显示可定位的阻断项，避免只向用户提示笼统的“规则冲突”。"""

        details = []
        for conflict in conflicts:
            variant = str(conflict.get("variant") or "—")
            canonical_values = " / ".join(
                str(value)
                for value in conflict.get("canonical_values", [])
            )
            candidate_ids = "、".join(
                str(value)
                for value in conflict.get("source_candidate_ids", [])
            )
            conflict_type = str(conflict.get("conflict_type") or "规则冲突")
            details.append(
                f"{conflict_type}\n"
                f"variant：{variant}\n"
                f"canonical：{canonical_values or '—'}\n"
                f"候选：{candidate_ids or '—'}"
            )

        QMessageBox.warning(
            self,
            "无法应用审核结果",
            "发现不可执行的人工归一规则，请先解决以下候选：\n\n"
            + "\n\n".join(details),
        )

    def _request_normalization_approval_from_shortcut(self) -> None:
        """仅在非文本输入状态下响应 A，按钮点击不受该保护限制。"""

        if not self._normalization_input_has_focus():
            self._request_normalization_approval()

    def _request_normalization_rejection_from_shortcut(self) -> None:
        """仅在非文本输入状态下响应 R，按钮点击不受该保护限制。"""

        if not self._normalization_input_has_focus():
            self._request_normalization_rejection()

    def _request_normalization_skip_from_shortcut(self) -> None:
        """仅在非文本输入状态下响应 S，按钮点击不受该保护限制。"""

        if not self._normalization_input_has_focus():
            self._request_normalization_skip()

    def _prepare_normalization_next_selection(self) -> None:
        """记录当前过滤列表的下一项，决定变更后自动提高审核效率。"""

        current_row = self.ui.normalizationCandidateListView.currentIndex().row()
        row_count = self._normalization_list_model.rowCount()
        next_row = current_row + 1 if current_row + 1 < row_count else current_row - 1
        self._normalization_next_candidate_id = None
        if 0 <= next_row < row_count:
            self._normalization_next_candidate_id = self._normalization_list_model.index(
                next_row,
                0,
            ).data(NormalizationCandidateListModel.CandidateIdRole)

    def _normalization_input_has_focus(self) -> bool:
        """确保 A/R/S 在搜索或 canonical 输入时不会污染人工输入。"""

        focus_widget = (
            QApplication.focusWidget()
            or self._normalization_review_dialog.focusWidget()
            or self.focusWidget()
        )
        return isinstance(focus_widget, type(self.ui.asinLineEdit))

    @staticmethod
    def _normalization_reason_name(reason_type: str) -> str:
        """将内部规则来源映射为审核详情的友好名称。"""

        return {
            "PHRASE_TOKEN_VARIANT": "短语变体",
            "PHRASE_CONCEPT_SEED": "短语概念 Seed",
            "COMPACT_SIGNATURE_MATCH": "格式变体",
            "KNOWN_WORD_ALIAS_VARIANT": "已有词形规则",
            "CHARACTER_SIMILARITY": "字符相似",
        }.get(reason_type, reason_type)

    @staticmethod
    def _normalization_number(value: object) -> float:
        """将展示排序字段安全转为数值，缺失值自然排在降序末尾。"""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        return float(value)

    @staticmethod
    def _format_normalization_number(
        value: object,
        *,
        decimal_places: int = 0,
    ) -> str:
        """统一格式化候选统计，缺失值不伪造为零。"""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "—"
        return f"{float(value):,.{decimal_places}f}"

    @staticmethod
    def _clear_layout(layout) -> None:
        """删除详情区动态控件，避免筛选或选择后残留旧 candidate 内容。"""

        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                MainWindow._clear_layout(child_layout)

    def _setup_status_bar(self):
        """初始化底部临时消息、进度和永久运行状态控件。"""

        self.analysis_progress_bar = QProgressBar(self)
        self.analysis_progress_bar.setObjectName("analysisProgressBar")
        self.analysis_progress_bar.setRange(0, 100)
        self.analysis_progress_bar.setValue(0)
        self.analysis_progress_bar.setMinimumWidth(130)
        self.analysis_progress_bar.hide()

        self.cookie_status_label = QLabel("Cookie：检查中", self)
        self.cookie_status_label.setObjectName("cookieStatusLabel")
        self.api_status_label = QLabel("API：未初始化", self)
        self.api_status_label.setObjectName("apiStatusLabel")
        self.database_status_label = QLabel("数据库：未连接", self)
        self.database_status_label.setObjectName("databaseStatusLabel")

        self.statusBar().setStyleSheet(
            "QStatusBar::item { border: none; }"
        )
        self.statusBar().addPermanentWidget(self.analysis_progress_bar)
        self.statusBar().addPermanentWidget(self.cookie_status_label)
        self.statusBar().addPermanentWidget(self.api_status_label)
        self.statusBar().addPermanentWidget(self.database_status_label)
        self.set_status("正在初始化...")

    def _emit_relation_query_requested(self):
        """将按钮点击和回车行为收敛为 View 的公开查询信号。"""

        if (
            self._relation_query_available
            and not self._relation_query_running
            and not self._relation_preparation_running
        ):
            self.relation_query_requested.emit()

    def _emit_analysis_preparation_requested(self):
        """将“开始分析”收敛为 Controller 可编排的预处理入口。"""

        if not self._relation_preparation_running:
            self.analysis_preparation_requested.emit()

    def set_status(self, message: str, timeout_ms: int = 0):
        """在状态栏左侧显示 Controller 传入的临时状态消息。"""

        self.statusBar().showMessage(message, timeout_ms)

    def set_cookie_status(self, status: str):
        """更新状态栏中的 Cookie 连接状态。"""

        self.cookie_status_label.setText(f"Cookie：{status}")

    def set_api_status(self, status: str):
        """更新状态栏中的业务 API 状态。"""

        self.api_status_label.setText(f"API：{status}")

    def set_database_status(self, status: str):
        """更新状态栏中的数据库连接状态。"""

        self.database_status_label.setText(f"数据库：{status}")

    def set_analysis_running(self, running: bool):
        """根据分析任务是否运行切换操作按钮与进度条可见性。"""

        self.ui.startAnalysisButton.setEnabled(not running)
        self.ui.cancelAnalysisButton.setEnabled(running)
        self.analysis_progress_bar.setVisible(running)

        if not running:
            self.analysis_progress_bar.setValue(0)

    def set_analysis_progress(
        self,
        value: int,
        message: str | None = None,
    ):
        """显示并更新运行中的分析进度。"""

        self.analysis_progress_bar.setVisible(True)
        self.analysis_progress_bar.setValue(max(0, min(value, 100)))
        if message:
            self.set_status(message)

    def set_relation_preparation_running(self, running: bool):
        """锁定预处理期间会改变关联结果上下文的界面交互。"""

        self._relation_preparation_running = running
        self.ui.startAnalysisButton.setEnabled(not running)

        # 当前尚未实现取消业务，继续保持取消按钮的既有禁用状态。
        self.ui.cancelAnalysisButton.setEnabled(False)
        self.analysis_progress_bar.setVisible(running)
        if not running:
            self.analysis_progress_bar.setValue(0)

        self.ui.asinLineEdit.setEnabled(
            not running and not self._relation_query_running
        )
        self.month_selector.setEnabled(
            not running and not self._relation_query_running
        )
        self.ui.asinQueryButton.setEnabled(
            self._relation_query_available
            and not self._relation_query_running
            and not running
        )

        for combo_box in self._variation_filter_combos.values():
            combo_box.setEnabled(not running)
        self.ui.resetRelationFiltersButton.setEnabled(
            bool(self._variation_filter_combos) and not running
        )
        for _, _, card in self._relation_entries:
            card.setEnabled(not running)

        self._update_relation_selection_controls()

    def ask_reversing_data_retry(self, asin: str) -> bool:
        """在 Qt 主线程询问用户是否重新处理当前失败的单个 ASIN。"""

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("获取数据失败")
        dialog.setText("获取数据失败")
        dialog.setInformativeText(
            f"ASIN：{asin}\n\n"
            "已自动尝试 4 次，但仍未获取到有效数据。\n\n"
            "可能是 SellerSprite 页面尚未完成预热，\n"
            "或者当前 ASIN / 时间范围暂无数据。"
        )
        retry_button = dialog.addButton(
            "重试",
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is retry_button

    def set_task_summary(self, task_name: str, record_count: int):
        """更新工作区顶部的当前任务说明和数据记录数量。"""

        self.ui.currentTaskLabel.setText(task_name)
        self.ui.recordCountLabel.setText(f"{record_count} 条数据")

    def set_export_enabled(self, enabled: bool):
        """仅在 Controller 确认存在可导出结果后开放导出按钮。"""

        self.ui.exportButton.setEnabled(enabled)

    def set_result_model(self, model):
        """接收 Controller 提供的数据模型并交给 QTableView 展示。"""

        self.ui.resultTableView.setModel(model)

    def relation_query_parameters(self) -> dict[str, object]:
        """读取关联查询输入，并固定后续分析使用的全部月份顺序。"""

        asin = self.ui.asinLineEdit.text().strip().upper()
        analysis_months = self.selected_analysis_months()

        if not analysis_months:
            return {
                "asin": asin,
                "selected_time_range": None,
                "month": None,
                "display_time_range": None,
                "analysis_months": [],
            }

        if analysis_months == [""]:
            return {
                "asin": asin,
                "selected_time_range": (
                    MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION
                ),
                "month": "",
                "display_time_range": (
                    MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION
                ),
                # 最近 30 天是一个独立的相对时间范围，不能与自然月混合
                # 成跨月份任务；后续仍由 Controller 原样传给接口。
                "analysis_months": [""],
            }

        # relation/source 只查询最新自然月；分析则必须保留所有自然月的
        # 完整快照，后续 Controller 不得再从 UI 重新读取月份。
        relation_month = analysis_months[0]
        display_time_range = (
            f"{relation_month[:4]}-{relation_month[4:]}"
        )
        return {
            "asin": asin,
            "selected_time_range": display_time_range,
            "month": relation_month,
            "display_time_range": display_time_range,
            "analysis_months": analysis_months,
        }

    def selected_analysis_months(self) -> list[str]:
        """返回分析链路使用的完整月份快照，最近 30 天以空字符串表示。"""

        natural_months = self.month_selector.selected_months()
        if natural_months:
            # 防御旧状态遗留的混合勾选：自然月一旦存在，不能让最近 30 天
            # 的默认标记覆盖用户实际选择的多月任务。
            ordered_months = sorted(
                natural_months,
                key=self._month_sort_key,
                reverse=True,
            )
            return [
                selected_month.replace("-", "")
                for selected_month in ordered_months
            ]

        if self.month_selector.is_recent_30_days_selected():
            return [""]

        return []

    def set_relation_query_available(self, available: bool):
        """根据 Cookie 与 ApiService 状态控制查询按钮是否可用。"""

        self._relation_query_available = available
        if (
            not self._relation_query_running
            and not self._relation_preparation_running
        ):
            self.ui.asinQueryButton.setEnabled(available)

    def set_relation_query_running(self, running: bool):
        """查询期间只锁定关联查询涉及的输入控件。"""

        self._relation_query_running = running
        self.ui.asinLineEdit.setEnabled(
            not running and not self._relation_preparation_running
        )
        self.month_selector.setEnabled(
            not running and not self._relation_preparation_running
        )
        self.ui.asinQueryButton.setEnabled(
            self._relation_query_available
            and not running
            and not self._relation_preparation_running
        )
        self.ui.asinQueryButton.setText(
            "查询中..." if running else "查询"
        )

    def show_relation_empty(self):
        """显示默认空状态，并清空当前查询的筛选和选择状态。"""

        self._clear_relation_result_state()
        self.ui.relationEmptyTitleLabel.setText("暂无关联 ASIN")
        self.ui.relationEmptyHintLabel.setText("输入 ASIN 后点击查询")
        self._show_relation_page(self.ui.emptyPage)

    def show_relation_loading(self):
        """清除旧结果并展示当前关联 ASIN 查询的加载状态。"""

        self._clear_relation_result_state()
        self._show_relation_page(self.ui.loadingPage)

    def show_relation_results(
        self,
        items: list[Any],
        queried_asin: str,
    ):
        """保存本次完整结果，构建动态筛选并创建可重排的展示卡片。"""

        self._clear_relation_result_state()
        self._relation_items = [
            item for item in items if isinstance(item, Mapping)
        ]

        variation_attributes = [
            extract_variation_attributes(item.get("sku"))
            for item in self._relation_items
        ]
        self._build_variation_filters(variation_attributes)

        for item, attributes in zip(
            self._relation_items,
            variation_attributes,
            strict=True,
        ):
            card = RelationProductCard(
                item,
                queried_asin,
                attributes,
                self.ui.relationResultScrollContent,
            )
            card.selection_requested.connect(
                self._toggle_relation_asin_selection
            )
            self._relation_entries.append(
                (item, attributes, card)
            )

            image_url = card.image_url
            if image_url:
                self._cards_by_image_url.setdefault(
                    image_url,
                    [],
                ).append(card)

        if not self._relation_entries:
            self.ui.relationEmptyTitleLabel.setText("暂无关联 ASIN")
            self.ui.relationEmptyHintLabel.setText(
                "本次查询没有返回关联商品"
            )
            self._show_relation_page(self.ui.emptyPage)
            return

        self._show_relation_page(self.ui.resultPage)
        self._apply_relation_filters()
        QTimer.singleShot(0, self._rearrange_relation_cards)

    def show_relation_query_error(self, message: str):
        """显示简洁错误状态，并确保失败后不展示旧查询结果。"""

        self._clear_relation_result_state()
        self.ui.relationEmptyTitleLabel.setText("查询失败")
        self.ui.relationEmptyHintLabel.setText(message)
        self._show_relation_page(self.ui.emptyPage)

    def update_relation_product_image(
        self,
        image_url: str,
        image_bytes: bytes,
    ):
        """把后台成功下载的图片更新到使用该 URL 的所有卡片。"""

        for card in self._cards_by_image_url.get(image_url, []):
            card.set_image_bytes(image_bytes)

    def relation_image_urls(self) -> list[str]:
        """返回当前卡片需要的去重图片 URL，供 Controller 异步加载。"""

        return list(self._cards_by_image_url)

    def selected_relation_items(self) -> list[Mapping[str, Any]]:
        """返回当前被选择 ASIN 对应的完整 relation item，保持原始结果顺序。"""

        return [
            item
            for item in self._relation_items
            if self._normalized_asin(item.get("asin"))
            in self._selected_asins
        ]

    def analysis_parameters(self) -> dict[str, object]:
        """读取当前界面参数，供 Controller 在正式分析任务中使用。"""

        return {
            "asin": self.ui.asinLineEdit.text().strip(),
            "months": self.month_selector.selected_months(),
            "include_recent_30_days": (
                self.month_selector.is_recent_30_days_selected()
            ),
            "analysis_type": (
                self.ui.analysisTypeComboBox.currentText()
            ),
        }

    def resizeEvent(self, event):
        """窗口尺寸变化时只重排已有可见卡片，不触发业务请求。"""

        super().resizeEvent(event)
        if self._relation_entries:
            self._rearrange_relation_cards()

    def _build_variation_filters(
        self,
        variation_attributes: list[dict[str, str]],
    ):
        """根据本次结果中真实存在的动态属性生成筛选下拉框。"""

        self._clear_variation_filter_controls()
        values_by_attribute: dict[str, set[str]] = {}

        for attributes in variation_attributes:
            for name, value in attributes.items():
                values_by_attribute.setdefault(name, set()).add(value)

        self.ui.relationVariationFilterArea.setVisible(
            bool(values_by_attribute)
        )
        if not values_by_attribute:
            return

        layout = self.ui.relationVariationFiltersLayout
        for name in sorted(values_by_attribute):
            label = QLabel(f"{name}", self.ui.relationVariationFilterArea)
            combo_box = QComboBox(self.ui.relationVariationFilterArea)
            combo_box.setMinimumWidth(110)
            self._sync_relation_filter_control_heights(combo_box)
            combo_box.setEnabled(
                not self._relation_preparation_running
            )
            combo_box.addItem(self._ALL_VARIATION_FILTER_VALUE)
            combo_box.addItems(sorted(values_by_attribute[name]))
            combo_box.currentTextChanged.connect(
                self._apply_relation_filters
            )

            layout.addWidget(label)
            layout.addWidget(combo_box)
            self._variation_filter_combos[name] = combo_box

    def _sync_relation_filter_control_heights(
        self,
        combo_box: QComboBox,
    ) -> None:
        """以动态筛选下拉框的实际高度统一工具栏操作控件高度。"""

        control_height = combo_box.minimumHeight()
        if control_height <= 0:
            control_height = combo_box.sizeHint().height()

        # 下拉框与操作按钮始终使用同一个 Qt 计算出的控件高度，避免
        # 单独维护容易与当前主题不一致的固定尺寸。
        combo_box.setMinimumHeight(control_height)
        combo_box.setMaximumHeight(control_height)
        for button in (
            self.ui.selectVisibleRelationButton,
            self.ui.resetRelationFiltersButton,
        ):
            button.setMinimumHeight(control_height)
            button.setMaximumHeight(control_height)

    def _apply_relation_filters(self):
        """在内存中按所有当前变体条件执行 AND 筛选，并重排可见卡片。"""

        self._rearrange_relation_cards()
        self._update_relation_selection_controls()

    def _reset_relation_filters(self):
        """将所有动态变体筛选恢复为“全部”。"""

        for combo_box in self._variation_filter_combos.values():
            combo_box.setCurrentText(self._ALL_VARIATION_FILTER_VALUE)

        self._apply_relation_filters()

    def _toggle_relation_asin_selection(self, asin: str):
        """以 ASIN 为数据 ID 切换选择集合，而不是以 Card 对象保存选择。"""

        normalized_asin = self._normalized_asin(asin)
        if not normalized_asin:
            return

        if normalized_asin in self._selected_asins:
            self._selected_asins.remove(normalized_asin)
        else:
            self._selected_asins.add(normalized_asin)

        self._update_relation_selection_controls()

    def _toggle_select_visible_relation_items(self):
        """全选或取消全选当前筛选结果，不影响因筛选隐藏的已有选择。"""

        visible_asins = {
            self._normalized_asin(item.get("asin"))
            for item, _, _ in self._visible_relation_entries()
            if self._normalized_asin(item.get("asin"))
        }
        if not visible_asins:
            return

        if visible_asins.issubset(self._selected_asins):
            self._selected_asins.difference_update(visible_asins)
        else:
            self._selected_asins.update(visible_asins)

        self._update_relation_selection_controls()

    def _update_relation_selection_controls(self):
        """同步全部卡片的纯展示选中状态和工具栏计数。"""

        for item, _, card in self._relation_entries:
            card.set_selected(
                self._normalized_asin(item.get("asin"))
                in self._selected_asins
            )

        visible_asins = {
            self._normalized_asin(item.get("asin"))
            for item, _, _ in self._visible_relation_entries()
            if self._normalized_asin(item.get("asin"))
        }
        all_visible_selected = (
            bool(visible_asins)
            and visible_asins.issubset(self._selected_asins)
        )
        self.ui.selectVisibleRelationButton.setText(
            "取消全选" if all_visible_selected else "全选"
        )
        self.ui.selectVisibleRelationButton.setEnabled(
            bool(visible_asins)
            and not self._relation_preparation_running
        )
        self.ui.selectedRelationCountLabel.setText(
            f"已选择 {len(self._selected_asins)} / "
            f"{len(self._relation_items)}"
        )

    def _visible_relation_entries(
        self,
    ) -> list[
        tuple[
            Mapping[str, Any],
            dict[str, str],
            RelationProductCard,
        ]
    ]:
        """根据动态筛选下拉框返回当前应显示的既有卡片。"""

        active_filters = {
            name: combo_box.currentText()
            for name, combo_box in self._variation_filter_combos.items()
            if combo_box.currentText() != self._ALL_VARIATION_FILTER_VALUE
        }
        if not active_filters:
            return list(self._relation_entries)

        return [
            (item, attributes, card)
            for item, attributes, card in self._relation_entries
            if all(
                attributes.get(name) == value
                for name, value in active_filters.items()
            )
        ]

    def _rearrange_relation_cards(self):
        """按结果区宽度将可见卡片从左上角重排为 1 至 4 列。"""

        layout = self.ui.relationProductGridLayout
        visible_entries = self._visible_relation_entries()
        visible_cards = {
            card for _, _, card in visible_entries
        }

        while layout.count():
            layout.takeAt(0)
        self._clear_no_match_relation_label()

        # ScrollArea 内容区会随可视区域扩展；网格本身及其中的卡片均固定
        # 靠左、靠上，筛选后少量卡片不会被可用空间垂直或水平居中。
        top_left = (
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        layout.setAlignment(top_left)

        for _, _, card in self._relation_entries:
            card.setVisible(card in visible_cards)

        viewport_width = self.ui.relationResultScrollArea.viewport().width()
        column_count = max(1, min(4, viewport_width // 315))

        for index, (_, _, card) in enumerate(visible_entries):
            row = index // column_count
            column = index % column_count
            layout.addWidget(card, row, column, top_left)

        for column in range(4):
            layout.setColumnStretch(
                column,
                1 if column < column_count else 0,
            )

        # 不给任何行增加 stretch，结果区域余下的高度自然保留为空白。
        for row in range(layout.rowCount()):
            layout.setRowStretch(row, 0)

        if not visible_entries:
            self._no_match_relation_label = QLabel(
                "无匹配结果",
                self.ui.relationResultScrollContent,
            )
            self._no_match_relation_label.setObjectName(
                "relationNoMatchLabel"
            )
            layout.addWidget(
                self._no_match_relation_label,
                0,
                0,
                1,
                column_count,
                top_left,
            )

    def _clear_no_match_relation_label(self):
        """移除仅在筛选为空时临时显示的提示文本。"""

        if self._no_match_relation_label is not None:
            self._no_match_relation_label.deleteLater()
            self._no_match_relation_label = None

    def _show_relation_page(self, page):
        """切换到结果 Tab 中指定的三状态页面。"""

        self.ui.resultTabWidget.setCurrentWidget(self.ui.resultTab)
        self.ui.relationResultStackedWidget.setCurrentWidget(page)

    def _clear_relation_result_state(self):
        """清空旧卡片、筛选、图片映射与 selected_asins。"""

        layout = self.ui.relationProductGridLayout
        while layout.count():
            layout_item = layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()

        self._no_match_relation_label = None

        self._relation_items.clear()
        self._relation_entries.clear()
        self._cards_by_image_url.clear()
        self._selected_asins.clear()
        self._clear_variation_filter_controls()
        self.ui.relationVariationFilterArea.hide()
        self._update_relation_selection_controls()

    def _clear_variation_filter_controls(self):
        """移除上一次查询动态创建的属性标签和下拉框。"""

        layout = self.ui.relationVariationFiltersLayout
        while layout.count():
            layout_item = layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()

        self._variation_filter_combos.clear()

    @staticmethod
    def _month_sort_key(month_value: str) -> tuple[int, int]:
        """将 YYYY-MM 转为可比较的完整年月元组。"""

        year_text, month_text = month_value.split("-", maxsplit=1)
        return int(year_text), int(month_text)

    @staticmethod
    def _normalized_asin(value: Any) -> str:
        """将接口中的 ASIN 规范为选择集合使用的稳定字符串 ID。"""

        return str(value).strip().upper() if value is not None else ""
