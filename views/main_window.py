"""Flow Analysis 主窗口的展示与交互状态管理。"""

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QSettings, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QKeySequence,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
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
from views.tagging_review_dialog import TaggingReviewDialog
from views.final_analysis_table_model import FinalAnalysisTableModel


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
    """承载单实例审核界面，并把关闭意图交给 Controller 决定。"""

    close_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        """初始化可缩放、非模态的独立审核窗口。"""

        super().__init__(parent)
        self.setObjectName("normalizationReviewDialog")
        self.setWindowTitle("关键词归一审核")
        self.setModal(False)
        self.setMinimumSize(900, 600)
        self.resize(1200, 760)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

    def closeEvent(self, event) -> None:
        """关闭 X 代表结束本轮审核，确认和清理由 Controller 统一处理。"""

        event.ignore()
        self.close_requested.emit()


class MainWindow(QMainWindow):
    """Flow Analysis 主窗口，仅负责界面展示、输入读取和状态更新。"""

    relation_query_requested = Signal()
    analysis_preparation_requested = Signal()
    normalization_approve_requested = Signal(str, str)
    normalization_reject_requested = Signal(str)
    normalization_skip_requested = Signal(str)
    normalization_review_finish_requested = Signal()
    normalization_review_close_requested = Signal()
    normalization_persistence_retry_requested = Signal()
    normalization_candidate_selected = Signal(str)
    # Excel 由完成分析后的 Controller 自动保存；该信号只请求打开本轮文件。
    open_export_requested = Signal()
    # 恢复动作只面向当前 Final Result 的 INCOMPLETE，不是第二个分析入口。
    incomplete_tagging_retry_requested = Signal()
    # “取消”属于整个 Analysis Job，View 不直接取消任何具体 Service。
    analysis_cancel_requested = Signal()
    # AI 标签审核的最终保存仍由 Controller 决定；View 只转发明确动作。
    tagging_human_review_requested = Signal(str, str, str)
    tagging_review_close_requested = Signal()
    tagging_review_discard_requested = Signal()
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
        self._analysis_job_running = False
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
        # 最终表格只持有 Controller 交付的展示快照；搜索和排序不会回写
        # Controller 的 _final_analysis_rows，也不会重算任何业务字段。
        self._final_analysis_table_model: FinalAnalysisTableModel | None = None
        self._final_analysis_sort_column: int | None = None
        self._final_analysis_sort_order = Qt.SortOrder.AscendingOrder
        # 仅用于关闭确认文案；实际待审核对象只由 Controller 保存。
        self._tagging_review_pending_count = 0
        # QSettings 是桌面端界面偏好存储，只保存用户选择的文件夹，不保存
        # 分析结果、数据库凭据或任何业务数据。
        self._user_settings = QSettings("Flow Analysis", "Flow Analysis")
        # 自动更新开始后短暂显示的应用内进度弹窗。安装进度由独立的
        # Inno Setup 安装器继续展示；这里仅负责避免主窗口突然退出。
        self._update_installation_dialog: QDialog | None = None

        self._setup_month_selector()
        self._setup_workspace()
        self._setup_status_bar()
        self._setup_relation_query()
        self._setup_word_filter()
        self._setup_normalization_review()
        self._setup_tagging_review()

    def _setup_month_selector(self):
        """创建多选时间范围控件，提供最近30天及截至上月的自然月。"""

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
        """从上个月倒序生成至 2024-01，避免展示尚未完整的当月数据。"""

        today = date.today()
        year = today.year
        month = today.month - 1
        if month == 0:
            year -= 1
            month = 12
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
        # 初始顺序必须沿用正式 Word Result。用户点击列头时才触发 View 内部
        # 排序，避免 QTableView 自动排序改写首次展示顺序。
        self.ui.resultTableView.setSortingEnabled(False)
        self.ui.resultTableView.horizontalHeader().sectionClicked.connect(
            self._sort_final_analysis_table
        )
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
        self.ui.cancelAnalysisButton.clicked.connect(
            self.analysis_cancel_requested.emit
        )
        self.ui.exportButton.clicked.connect(self.open_export_requested.emit)
        self.ui.retryIncompleteTaggingButton.clicked.connect(
            self.incomplete_tagging_retry_requested.emit
        )
        self.ui.tableSearchLineEdit.textChanged.connect(
            self._filter_final_analysis_table
        )
        self.ui.resetFilterButton.clicked.connect(
            self._reset_final_analysis_table_filter
        )
        self.show_relation_empty()
        self.set_relation_query_available(False)

    def _setup_word_filter(self) -> None:
        """初始化始终预设、并在 Word Analysis 后自动生效的词筛选。"""

        self.ui.wordFilterConditionsWidget.setEnabled(True)
        self.ui.wordFilterCountLabel.setText("筛选条件将在词频分析完成后自动应用")
        # 词筛选始终预设在左侧配置栏，用户可在开始分析前配置四组范围。
        self.ui.wordFilterPanel.setVisible(True)

    def show_word_filter(
        self,
        _monthly_word_results: Mapping[str, list[Mapping[str, Any]]],
        *,
        total_count: int,
    ) -> None:
        """显示当前 Word Analysis 的筛选入口，但不在 View 内进行业务筛选。"""

        self.set_word_filter_counts(total_count, total_count)
        self.ui.wordFilterPanel.setVisible(True)

    def word_filter_conditions(self) -> dict[str, Any]:
        """读取可选上下限；数字校验由纯 WordFilterService 统一完成。"""

        controls = {
            "weeklyExposure": (
                "weeklyExposureMinLineEdit",
                "weeklyExposureMaxLineEdit",
            ),
            "abaWeeklyRank": (
                "abaWeeklyRankMinLineEdit",
                "abaWeeklyRankMaxLineEdit",
            ),
            "monthlySearches": (
                "monthlySearchesMinLineEdit",
                "monthlySearchesMaxLineEdit",
            ),
            "frequency": ("frequencyMinLineEdit", "frequencyMaxLineEdit"),
        }
        conditions: dict[str, Any] = {
            # 没有额外开关或按钮；空范围即表示该字段不参与本轮自动筛选。
            "enabled": True,
        }
        for metric, (minimum_name, maximum_name) in controls.items():
            conditions[f"{metric}Min"] = getattr(
                self.ui, minimum_name
            ).text().strip()
            conditions[f"{metric}Max"] = getattr(
                self.ui, maximum_name
            ).text().strip()
        return conditions

    def set_word_filter_counts(self, total_count: int, filtered_count: int) -> None:
        """只展示 Controller 提供的计数，不由 UI 自行推导结果。"""

        self.ui.wordFilterCountLabel.setText(
            f"原始词：{total_count}　筛选后：{filtered_count}"
        )

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
        self.ui.finishNormalizationReviewButton.clicked.connect(
            self.normalization_review_finish_requested.emit
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
        # QTabWidget.removeTab() 会隐藏被移出的页面；重新放入 Dialog 后必须
        # 显式恢复可见性，否则 Dialog 只剩空白窗口外壳。
        self.ui.normalizationReviewTab.show()

        # Splitter 是唯一可伸缩区域；紧凑状态行移动到 Dialog 底部。
        review_layout = self.ui.normalizationReviewLayout
        status_item = review_layout.takeAt(1)
        if status_item is not None:
            review_layout.addItem(status_item)
        review_layout.setStretch(0, 0)
        review_layout.setStretch(1, 1)
        review_layout.setStretch(2, 0)

        self._normalization_review_dialog.close_requested.connect(
            self.normalization_review_close_requested.emit
        )

    def show_normalization_review(self) -> None:
        """显示同一个审核 Dialog；没有候选时不打开空审核窗口。"""

        if not self._normalization_candidates:
            return
        self._normalization_review_dialog.show()
        self._normalization_review_dialog.raise_()
        self._normalization_review_dialog.activateWindow()

    def confirm_discard_normalization_review(self, pending_count: int) -> bool:
        """确认关闭时销毁尚未处理的当前运行时归一候选。"""

        if pending_count <= 0:
            return True
        message_box = QMessageBox(
            QMessageBox.Icon.Warning,
            "结束本次归一审核",
            f"仍有 {pending_count} 条归一候选未处理。\n\n"
            "关闭并继续后，未处理候选及其证据、历史参考、筛选和选择状态"
            "将立即从内存中销毁，无法重新打开本轮审核；"
            "已经保存的人工审核决定不会受影响。",
            parent=self._normalization_review_dialog,
        )
        cancel_button = message_box.addButton(
            "取消",
            QMessageBox.ButtonRole.RejectRole,
        )
        continue_button = message_box.addButton(
            "关闭并继续",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        message_box.setDefaultButton(cancel_button)
        message_box.exec()
        return message_box.clickedButton() is continue_button

    def close_normalization_review_after_discard(self) -> None:
        """隐藏已清空的审核窗口，禁止重新打开当前 generation。"""

        self._normalization_review_dialog.hide()

    def _setup_tagging_review(self) -> None:
        """创建独立的标签分歧审核 Dialog，不复用归一审核关闭语义。"""

        self._tagging_review_dialog = TaggingReviewDialog(self)
        self._tagging_review_dialog.save_requested.connect(
            self.tagging_human_review_requested.emit
        )
        self._tagging_review_dialog.close_requested.connect(
            self.tagging_review_close_requested.emit
        )

    def set_tagging_review_items(
        self,
        items: list,
        total_count: int,
    ) -> None:
        """将 Controller 的本轮运行时快照交给 Dialog 显示。"""

        self._tagging_review_pending_count = len(items)
        self._tagging_review_dialog.set_review_items(items, total_count)

    def show_tagging_review(self) -> None:
        """仅由 Controller 在本轮存在 DISAGREEMENT 时自动打开。"""

        if self._tagging_review_pending_count <= 0:
            return
        self._tagging_review_dialog.show()
        self._tagging_review_dialog.raise_()
        self._tagging_review_dialog.activateWindow()

    def set_tagging_review_save_status(
        self,
        item_id: str,
        message: str,
    ) -> None:
        """由 Controller 转发异步保存状态，Dialog 不观察数据库 Future。"""

        self._tagging_review_dialog.set_save_status(item_id, message)

    def show_tagging_review_save_failed(self, item_id: str) -> None:
        """显示保存失败提示，并保留该条审核项供用户再次确认。"""

        self._tagging_review_dialog.show_save_failed(item_id)

    def confirm_discard_tagging_review(
        self,
        pending_count: int,
        *,
        for_new_generation: bool = False,
        for_exit: bool = False,
    ) -> bool:
        """明确说明关闭、新任务或退出都会销毁未保存的运行时分歧。"""

        if pending_count <= 0:
            return True
        if for_new_generation:
            title = "开始新的 AI 标签任务"
            action_text = "放弃并开始新任务"
            leading_text = "开始新分析将销毁这些待审核数据"
        elif for_exit:
            title = "退出 Flow Analysis"
            action_text = "退出并销毁"
            leading_text = "退出后这些待审核数据将被销毁"
        else:
            title = "关闭 AI 标签审核"
            action_text = "关闭并销毁"
            leading_text = "关闭后这些待审核数据将立即从内存中销毁"

        message = (
            f"仍有 {pending_count} 个 AI 分歧词尚未完成人工审核。\n\n"
            f"{leading_text}，且不会写入数据库，无法恢复；"
            "已成功保存的人工标签不会受影响。"
        )
        message_box = QMessageBox(
            QMessageBox.Icon.Warning,
            title,
            message,
            parent=self._tagging_review_dialog,
        )
        cancel_button = message_box.addButton(
            "取消",
            QMessageBox.ButtonRole.RejectRole,
        )
        discard_button = message_box.addButton(
            action_text,
            QMessageBox.ButtonRole.DestructiveRole,
        )
        message_box.setDefaultButton(cancel_button)
        message_box.exec()
        return message_box.clickedButton() is discard_button

    def close_tagging_review_after_discard(self) -> None:
        """清除 Dialog 的显示缓存；正式工作集由 Controller 同步清理。"""

        self._tagging_review_pending_count = 0
        self._tagging_review_dialog.reset_runtime_view()

    def confirm_finish_normalization_review(
        self,
        *,
        pending_count: int,
        skipped_count: int,
        saving_count: int,
        unsaved_count: int,
    ) -> bool:
        """确认结束审核，并明确说明待审核内存数据不可恢复。"""

        details = [f"仍有 {pending_count} 条待审核候选"]
        if skipped_count:
            details.append(f"{skipped_count} 条已跳过候选")
        if saving_count:
            details.append(f"{saving_count} 条审核记录仍在保存")
        elif unsaved_count:
            details.append(f"{unsaved_count} 条审核记录尚未保存")
        message = "；".join(details)
        result = QMessageBox.question(
            self._normalization_review_dialog,
            "结束本次归一审核",
            f"{message}。\n\n结束后待审核候选及其证据将从内存清除，无法恢复；已保存的人工审计记录不会删除。",
            QMessageBox.StandardButton.Cancel
            | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        return result == QMessageBox.StandardButton.Yes

    def _populate_normalization_filter_controls(self) -> None:
        """填充审核页固定筛选项，内部值始终保持正式 reason/decision 字符串。"""

        self.ui.normalizationReasonComboBox.addItem("全部类型", None)
        self.ui.normalizationReasonComboBox.addItem(
            "短语变体",
            "PHRASE_TOKEN_VARIANT",
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
        self._normalization_current_candidate_id = None
        self._normalization_next_candidate_id = None
        self.ui.finishNormalizationReviewButton.setEnabled(
            bool(self._normalization_candidates)
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
                )
            )

    def _build_normalization_variant_widget(
        self,
        candidate_id: str,
        index: int,
        variant: str,
        detail: Mapping[str, Any],
        fallback_evidence: object,
    ) -> QWidget:
        """创建单个 variant 的只读统计、月度分布和可展开关键词证据。"""

        group = QGroupBox(f"Variant {index}：{variant}")
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

    def set_normalization_review_editable(self, editable: bool) -> None:
        """切换审核写操作；筛选、搜索、查看与滚动始终不受影响。"""

        self._normalization_review_editable = editable
        # 结束审核本身不改变 candidate 的 decision，因此重算锁定期间仍由
        # Controller 决定是否允许；当前候选为空时始终禁用。
        self.ui.finishNormalizationReviewButton.setEnabled(
            bool(self._normalization_candidates)
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

    def tagging_category_key(self) -> str:
        """将固定 UI 品类文本映射为稳定缓存 key，拒绝文本直接下传。"""

        category_mapping = {
            "Pillow": "pillow",
            "Stuffed Animals": "stuffed_animals",
        }
        category_key = category_mapping.get(
            self.ui.analysisTypeComboBox.currentText()
        )
        if category_key is None:
            raise ValueError("当前打标品类无有效 category_key")
        return category_key

    def analysis_category_display_name(self) -> str:
        """返回当前 UI 已确认的品类显示名，仅用于导出文件默认名。"""

        return self.ui.analysisTypeComboBox.currentText().strip() or "analysis"

    def ensure_export_directory(self) -> str | None:
        """返回持久化的导出目录；首次自动导出时请求用户选择一次。"""

        configured_directory = self._user_settings.value(
            "export/default_directory",
            "",
            type=str,
        )
        if configured_directory:
            configured_path = Path(configured_directory)
            if configured_path.is_dir():
                return str(configured_path)

        initial_directory = (
            str(Path.home())
            if not configured_directory
            else str(Path(configured_directory).parent)
        )
        selected_directory = QFileDialog.getExistingDirectory(
            self,
            "选择默认 Excel 导出文件夹",
            initial_directory,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected_directory:
            return None

        selected_path = Path(selected_directory)
        if not selected_path.is_dir():
            return None

        # 用户确认的目录仅在第一次选择或原目录失效后才更新；后续完成分析
        # 直接复用该配置，不再弹出另存为窗口。
        self._user_settings.setValue(
            "export/default_directory",
            str(selected_path),
        )
        self._user_settings.sync()
        return str(selected_path)

    def open_exported_file(self, output_path: str) -> bool:
        """请求系统默认程序打开已成功自动保存的 Excel 文件。"""

        path = Path(output_path)
        if not path.is_file():
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def request_application_exit(self) -> None:
        """供 Controller 在独立更新器已启动后请求 Qt 安全退出。"""

        application = QApplication.instance()
        if application is not None:
            # 自动更新弹窗至少保留一个短暂事件循环，让用户能明确看到
            # "正在安装更新"，再交由独立安装器展示持续的安装进度。
            delay_ms = 450 if self._update_installation_dialog is not None else 0
            QTimer.singleShot(delay_ms, application.quit)

    def show_update_installation_progress(self, version: str) -> None:
        """显示更新交接提示，避免主程序退出被误认为异常关闭。"""

        if self._update_installation_dialog is None:
            dialog = QDialog(self)
            dialog.setObjectName("updateInstallationProgressDialog")
            dialog.setWindowTitle("正在安装更新")
            dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
            # 更新器已经启动后不允许在这里取消；真正的安装进度会在主程序
            # 退出后由 Inno Setup 标准窗口持续显示。
            dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
            dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
            dialog.setMinimumWidth(440)

            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(24, 22, 24, 22)
            layout.setSpacing(12)

            title_label = QLabel("正在准备安装更新", dialog)
            title_label.setObjectName("updateInstallationTitleLabel")
            title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
            layout.addWidget(title_label)

            message_label = QLabel(dialog)
            message_label.setObjectName("updateInstallationMessageLabel")
            message_label.setWordWrap(True)
            layout.addWidget(message_label)

            progress_bar = QProgressBar(dialog)
            progress_bar.setObjectName("updateInstallationProgressBar")
            # 交接阶段无法估算安装时间，使用 Qt 标准不确定进度动画。
            progress_bar.setRange(0, 0)
            progress_bar.setTextVisible(False)
            layout.addWidget(progress_bar)

            self._update_installation_dialog = dialog

        message_label = self._update_installation_dialog.findChild(
            QLabel,
            "updateInstallationMessageLabel",
        )
        if message_label is not None:
            message_label.setText(
                f"更新包 {version} 已下载完成。应用将自动关闭，随后显示"
                "安装进度。请勿手动重新打开 Flow Analysis。"
            )
        self._update_installation_dialog.show()
        self._update_installation_dialog.raise_()
        self._update_installation_dialog.activateWindow()
        # 确保短暂交接窗口在 QTimer 请求退出前完成一次绘制。
        QApplication.processEvents()

    def set_analysis_pipeline_status(self, status: str) -> None:
        """展示整个 Analysis Job 的当前阶段，不拆分独立 AI 顶层状态。"""

        self.ui.analysisPipelineStatusLabel.setText(f"当前状态：{status}")

    def set_analysis_job_running(self, running: bool) -> None:
        """锁定或恢复唯一“开始分析”入口，并让取消覆盖完整流水线。"""

        self._analysis_job_running = running
        self.ui.startAnalysisButton.setEnabled(not running)
        self.ui.cancelAnalysisButton.setEnabled(running)
        self.analysis_progress_bar.setVisible(running)
        # 完成、失败或取消时只隐藏进度条，不能在阶段结束时偷偷归零；下一轮
        # Analysis Job 会由 Controller 的唯一重置入口显式从 0 开始。

    def set_final_analysis_rows(
        self,
        rows: list[Mapping[str, Any]],
    ) -> None:
        """接收唯一 Final Analysis Dataset 并建立只读 QTableView Model。"""

        model = FinalAnalysisTableModel(rows, self)
        self._final_analysis_table_model = model
        self._final_analysis_sort_column = None
        self._final_analysis_sort_order = Qt.SortOrder.AscendingOrder
        self.set_result_model(model)
        for index, column in enumerate(model.columns):
            self.ui.resultTableView.setColumnHidden(
                index,
                not column.default_visible,
            )
        self._filter_final_analysis_table(
            self.ui.tableSearchLineEdit.text()
        )

    def clear_final_analysis_rows(self) -> None:
        """清空旧 generation 的最终表格快照与用户筛选文本。"""

        self.ui.tableSearchLineEdit.clear()
        self.set_final_analysis_rows([])
        self.set_analysis_result_mode("未生成最终结果")

    def update_final_analysis_row(self, row: Mapping[str, Any]) -> None:
        """在人工标签保存后，仅刷新匹配的最终表格行。"""

        if self._final_analysis_table_model is not None:
            self._final_analysis_table_model.update_final_row(row)

    def _filter_final_analysis_table(self, text: str) -> None:
        """把搜索条件交给 Model 做纯展示过滤。"""

        if self._final_analysis_table_model is not None:
            self._final_analysis_table_model.set_filter_text(text)

    def _reset_final_analysis_table_filter(self) -> None:
        """清空 Data Tab 搜索条件，恢复全部 Final Analysis 行。"""

        self.ui.tableSearchLineEdit.clear()

    def _sort_final_analysis_table(self, column: int) -> None:
        """用户点击列头后才在 Model 内重排列，不影响 Controller 原始顺序。"""

        model = self._final_analysis_table_model
        if model is None:
            return
        if self._final_analysis_sort_column == column:
            self._final_analysis_sort_order = (
                Qt.SortOrder.DescendingOrder
                if self._final_analysis_sort_order
                == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self._final_analysis_sort_column = column
            self._final_analysis_sort_order = Qt.SortOrder.AscendingOrder
        model.sort(column, self._final_analysis_sort_order)

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
            # 仅用于读取旧审计记录；新候选和筛选项不再提供该类型。
            "PHRASE_CONCEPT_SEED": "旧版短语概念候选",
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

        if not self._relation_preparation_running and not self._analysis_job_running:
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
        # 保留最后一个任务值；总进度的重置由 Controller 统一负责，避免旧
        # 兼容入口也产生“完成后瞬间跳回 0%”的视觉问题。

    def set_analysis_progress(
        self,
        value: int,
        message: str | None = None,
    ):
        """显示并更新运行中的分析进度。"""

        self.analysis_progress_bar.setVisible(True)
        self.analysis_progress_bar.setValue(max(0, min(value, 100)))
        # 详细业务阶段只在顶部 Analysis Pipeline 状态展示；底部状态栏保留
        # Cookie/API/数据库等基础设施状态，避免两处重复展示流程文字。

    def set_relation_preparation_running(self, running: bool):
        """锁定预处理期间会改变关联结果上下文的界面交互。"""

        self._relation_preparation_running = running
        # “开始分析 / 取消”由完整 Analysis Job 管理；这里仅锁定会改变
        # 当前 relation 上下文的控件，不能把等待人工审核误当作任务完成。
        self.ui.startAnalysisButton.setEnabled(
            not self._analysis_job_running and not running
        )
        self.ui.cancelAnalysisButton.setEnabled(self._analysis_job_running)
        self.analysis_progress_bar.setVisible(
            self._analysis_job_running or running
        )

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

    def ask_reversing_data_retry(
        self,
        asin: str,
        *,
        month: str,
        reason: str,
    ) -> str:
        """在 Qt 主线程询问重试、跳过当前 ASIN 或终止分析。"""

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("获取数据失败")
        dialog.setText("获取数据失败")
        dialog.setInformativeText(
            f"ASIN：{asin}\n\n"
            f"当前无数据月份：{month}\n\n"
            "已自动尝试 4 次；每次均按“预热 → 等待就绪 → 请求数据”执行。\n\n"
            f"最后失败原因：{reason}\n\n"
            "可能是 SellerSprite 数据尚未就绪，\n"
            "或者当前 ASIN / 时间范围暂无数据。\n\n"
            "跳过只忽略当前月份，保留该 ASIN 在其他月份的真实数据，\n"
            "并继续分析其余月份和 ASIN。"
        )
        retry_button = dialog.addButton(
            "重试",
            QMessageBox.ButtonRole.AcceptRole,
        )
        skip_button = dialog.addButton(
            "跳过该 ASIN",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        dialog.addButton("取消分析", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is retry_button:
            return "retry"
        if dialog.clickedButton() is skip_button:
            return "skip"
        return "cancel"

    def set_task_summary(self, task_name: str, record_count: int):
        """更新工作区顶部的当前任务说明和数据记录数量。"""

        self.ui.currentTaskLabel.setText(task_name)
        self.ui.recordCountLabel.setText(f"{record_count} 条数据")

    def set_export_enabled(self, enabled: bool):
        """仅在 Controller 确认本轮 Excel 已成功写出后开放打开按钮。"""

        self.ui.exportButton.setEnabled(enabled)

    def set_incomplete_tagging_retry_available(
        self,
        count: int,
        *,
        enabled: bool = True,
    ) -> None:
        """显示当前 generation 可恢复的 AI 失败项，不在 View 判断业务状态。"""

        safe_count = max(0, count) if isinstance(count, int) else 0
        self.ui.retryIncompleteTaggingButton.setText(
            f"重试失败项 ({safe_count})"
        )
        self.ui.retryIncompleteTaggingButton.setVisible(safe_count > 0)
        self.ui.retryIncompleteTaggingButton.setEnabled(
            safe_count > 0 and enabled
        )

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
            # 对单张卡片设置 AlignLeft 会让 Grid 保留单元格宽度、却按每张
            # 卡片自身 sizeHint 显示，标题较长时便会出现同排卡片宽度不一致。
            # 整个 Grid 已统一左上对齐，卡片无需再设置单独的对齐标记。
            layout.addWidget(card, row, column)

        for column in range(4):
            layout.setColumnMinimumWidth(column, 0)
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

    def closeEvent(self, event) -> None:
        """应用退出前确认是否放弃尚未保存的 AI 标签分歧。"""

        if self._tagging_review_pending_count > 0:
            if not self.confirm_discard_tagging_review(
                self._tagging_review_pending_count,
                for_exit=True,
            ):
                event.ignore()
                return
            self.tagging_review_discard_requested.emit()
        event.accept()

    @staticmethod
    def _month_sort_key(month_value: str) -> tuple[int, int]:
        """将 YYYY-MM 转为可比较的完整年月元组。"""

        year_text, month_text = month_value.split("-", maxsplit=1)
        return int(year_text), int(month_text)

    @staticmethod
    def _normalized_asin(value: Any) -> str:
        """将接口中的 ASIN 规范为选择集合使用的稳定字符串 ID。"""

        return str(value).strip().upper() if value is not None else ""
