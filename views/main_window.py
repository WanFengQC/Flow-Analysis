"""Flow Analysis 主窗口的展示与交互状态管理。"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
)

from ui.ui_main_window import Ui_MainWindow
from views.relation_product_card import (
    RelationProductCard,
    extract_variation_attributes,
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
        """用传入时间范围重建选项，并保留指定的选中范围。"""

        selected_set = set(selected_ranges or [])
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
        """切换被点击项目的勾选状态，并同步收起状态的汇总文本。"""

        item = self._month_model.itemFromIndex(index)
        if item is None:
            return

        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        self._update_summary()
        self.selection_changed.emit(self._selected_time_ranges())

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


class MainWindow(QMainWindow):
    """Flow Analysis 主窗口，仅负责界面展示、输入读取和状态更新。"""

    relation_query_requested = Signal()
    analysis_preparation_requested = Signal()
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

        self._setup_month_selector()
        self._setup_workspace()
        self._setup_status_bar()
        self._setup_relation_query()

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

    def relation_query_parameters(self) -> dict[str, str | None]:
        """读取关联查询输入，并集中计算实际请求使用的最新时间范围。"""

        asin = self.ui.asinLineEdit.text().strip().upper()
        selected_ranges = self.month_selector.selected_time_ranges()

        if not selected_ranges:
            return {
                "asin": asin,
                "selected_time_range": None,
                "month": None,
                "display_time_range": None,
            }

        if MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION in selected_ranges:
            return {
                "asin": asin,
                "selected_time_range": (
                    MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION
                ),
                "month": "",
                "display_time_range": (
                    MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION
                ),
            }

        latest_month = max(
            selected_ranges,
            key=self._month_sort_key,
        )
        return {
            "asin": asin,
            "selected_time_range": latest_month,
            "month": latest_month.replace("-", ""),
            "display_time_range": latest_month,
        }

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
