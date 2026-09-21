"""Flow Analysis 主窗口的展示与交互状态管理。"""

from datetime import date

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QLabel, QMainWindow, QProgressBar

from ui.ui_main_window import Ui_MainWindow


class MultiSelectMonthComboBox(QComboBox):
    """提供动态时间范围列表和多选结果汇总显示的下拉控件。"""

    selection_changed = Signal(list)
    RECENT_30_DAYS_OPTION = "最近30天"

    def __init__(self, parent=None):
        """初始化只读显示框和支持勾选的标准项模型。"""
        super().__init__(parent)

        # 可编辑模式仅用于在收起状态显示汇总文本，用户不能直接编辑该文本。
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText("请选择月份")
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumHeight(32)

        # 每个标准项保存一个月份，并通过 CheckStateRole 表示选中状态。
        self._month_model = QStandardItemModel(self)
        self.setModel(self._month_model)
        self.view().pressed.connect(self._toggle_month)

        # 文本区域与列表均由本控件处理，分别实现整块点击展开和连续勾选。
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        """处理文本区域展开和列表连续多选所需的鼠标事件。"""
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            self.showPopup()
            return True
        if watched is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            return True
        return super().eventFilter(watched, event)

    def set_time_ranges(
        self, time_ranges: list[str], selected_ranges: list[str] | None = None
    ):
        """用传入时间范围重建选项，并按当前顺序保留选中项。"""
        selected_set = set(selected_ranges or [])
        self._month_model.clear()

        for time_range in time_ranges:
            item = QStandardItem(time_range)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if time_range in selected_set
                else Qt.CheckState.Unchecked
            )
            self._month_model.appendRow(item)

        self._update_summary()

    def selected_months(self) -> list[str]:
        """按列表显示顺序返回已勾选的自然月，不包含“最近30天”。"""
        return [
            self._month_model.item(row).text()
            for row in range(self._month_model.rowCount())
            if self._month_model.item(row).checkState() == Qt.CheckState.Checked
            and self._month_model.item(row).text() != self.RECENT_30_DAYS_OPTION
        ]

    def is_recent_30_days_selected(self) -> bool:
        """返回用户是否勾选了相对当前日期计算的最近30天范围。"""
        return self.RECENT_30_DAYS_OPTION in self._selected_time_ranges()

    def _toggle_month(self, index):
        """切换被点击月份的勾选状态，并同步收起状态的汇总文本。"""
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
        """按列表显示顺序返回包含相对日期选项在内的全部已选范围。"""
        return [
            self._month_model.item(row).text()
            for row in range(self._month_model.rowCount())
            if self._month_model.item(row).checkState() == Qt.CheckState.Checked
        ]


class MainWindow(QMainWindow):
    """Flow Analysis 主窗口，仅负责界面展示、输入读取和状态更新。"""

    def __init__(self):
        """加载 Designer 生成的界面，并补充运行时自定义控件。"""
        super().__init__()

        # Ui_MainWindow 由 pyside6-uic 自动生成，View 只在此处组合和配置控件。
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._setup_month_selector()
        self._setup_workspace()
        self._setup_status_bar()

    def _setup_month_selector(self):
        """创建多选时间范围控件，提供最近30天及 2024-01 起的月份。"""
        # 当前 Designer 文件将容器布局直接放入参数面板，因此以参数面板作为控件父对象。
        self.month_selector = MultiSelectMonthComboBox(self.ui.parameterPanel)
        self.month_selector.setObjectName("monthSelector")

        months = self._recent_months()
        # 相对日期放在首项并默认选中，后续自然月仍按从当前月到 2024-01 倒序排列。
        time_ranges = [
            MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION,
            *months,
        ]
        self.month_selector.set_time_ranges(
            time_ranges,
            selected_ranges=[MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION],
        )
        self.ui.monthSelectorContainerLayout.addWidget(self.month_selector)

    @staticmethod
    def _recent_months() -> list[str]:
        """从当前月份倒序生成至 2024-01 的月份，确保范围随日期自动延展。"""
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
        """配置后续数据模型可直接复用的表格与 Splitter 初始比例。"""
        self.ui.mainSplitter.setStretchFactor(0, 0)
        self.ui.mainSplitter.setStretchFactor(1, 1)
        self.ui.mainSplitter.setSizes([300, 900])

        # QTableView 保持 Model/View 结构，为后续大数据量排序和筛选留出接口。
        self.ui.resultTableView.setSortingEnabled(True)
        self.ui.resultTableView.horizontalHeader().setStretchLastSection(True)

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

        # 永久控件从左到右固定在状态栏右侧，临时消息由 set_status() 显示在左侧。
        # 状态栏默认会为永久控件绘制分隔线，这里移除它以保持底部空白、连续的视觉效果。
        self.statusBar().setStyleSheet("QStatusBar::item { border: none; }")
        self.statusBar().addPermanentWidget(self.analysis_progress_bar)
        self.statusBar().addPermanentWidget(self.cookie_status_label)
        self.statusBar().addPermanentWidget(self.api_status_label)
        self.statusBar().addPermanentWidget(self.database_status_label)
        self.set_status("正在初始化...")

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

    def set_analysis_progress(self, value: int, message: str | None = None):
        """显示并更新运行中的分析进度，进度值限制在 0 到 100 之间。"""
        self.analysis_progress_bar.setVisible(True)
        self.analysis_progress_bar.setValue(max(0, min(value, 100)))

        if message:
            self.set_status(message)

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

    def analysis_parameters(self) -> dict[str, object]:
        """读取当前界面参数，供 Controller 在启动正式任务时获取用户输入。"""
        return {
            "asin": self.ui.asinLineEdit.text().strip(),
            "months": self.month_selector.selected_months(),
            "include_recent_30_days": self.month_selector.is_recent_30_days_selected(),
            "analysis_type": self.ui.analysisTypeComboBox.currentText(),
        }
