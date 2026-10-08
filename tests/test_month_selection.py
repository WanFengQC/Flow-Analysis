"""月份多选与查询上下文快照的 View / Controller 回归测试。"""

import os
from concurrent.futures import Future
from datetime import date
import unittest
from unittest.mock import patch

# 测试环境不依赖真实桌面显示服务，必须在导入 Qt 前固定离屏平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from views.main_window import MainWindow, MultiSelectMonthComboBox
from workers.analysis_worker import AnalysisWorker


class _CrawlerServiceStub:
    """只提供协程形状，避免上下文快照测试发起真实网络请求。"""

    async def get_relation_reversing_for_asin_month(self, **_kwargs):
        """测试不运行该协程；实际请求必须由正式 CrawlerService 负责。"""

        raise AssertionError("上下文快照测试不应执行 reversing 请求")


class _AsyncRuntimeStub:
    """接收并关闭协程，验证 Controller 提交前固定了月份快照。"""

    def submit(self, coroutine):
        """避免未 await 协程警告，同时返回未完成 Future 阻止后续推进。"""

        coroutine.close()
        return Future()


class _ApplicationRuntimeStub:
    """提供 MainController 本测试所需的最小运行时接口。"""

    def __init__(self):
        self.async_runtime = _AsyncRuntimeStub()


class _AnalysisServiceSpy:
    """记录 Worker 接收到的月份，并返回最小月度处理结果。"""

    def __init__(self):
        self.received_months: list[str] | None = None
        self.result_keys: list[str] | None = None

    def analyze_monthly_reversing_results(self, _results, months):
        """保留输入月份顺序，模拟正式 AnalysisService 的分月返回结构。"""

        self.received_months = list(months)
        self.result_keys = list(months)
        return {
            "raw": {month: [] for month in months},
            "results": {month: [] for month in months},
            "diagnostics": {month: {} for month in months},
        }

    def analyze_monthly_word_results(
        self,
        _monthly_results,
        months,
        approved_rules=None,
    ):
        """审核前预览必须不传入人工规则，避免 Stub 遮蔽调用顺序。"""

        assert approved_rules is None
        return (
            {month: [] for month in months},
            {month: {} for month in months},
        )

    def analyze_monthly_asin_word_results(
        self,
        _monthly_raw,
        months,
        approved_rules=None,
    ):
        """模拟空 ASIN 词结果，确保月份快照仍完整传递。"""

        assert approved_rules is None
        return (
            {month: {} for month in months},
            {month: {} for month in months},
        )


class _CandidateServiceSpy:
    """记录 CandidateDiscovery 实际收到的月度 RESULT 键。"""

    def __init__(self):
        self.received_month_keys: list[str] | None = None

    def discover_candidates(self, monthly_results):
        """测试只验证传参，不重新计算正式候选。"""

        self.received_month_keys = list(monthly_results)
        return []


class MonthSelectionTest(unittest.TestCase):
    """验证最近 30 天与自然月互斥，并保护查询时的多月快照。"""

    @classmethod
    def setUpClass(cls):
        """整个测试模块共用一个 QApplication，避免重复创建 Qt 应用。"""

        cls._application = QApplication.instance() or QApplication([])

    def setUp(self):
        """每个测试创建独立主窗口，确保初始最近 30 天状态可验证。"""

        self.window = MainWindow()
        self.window.ui.asinLineEdit.setText("B0DSHYXD4G")

    def tearDown(self):
        """关闭窗口并处理延迟删除，避免 Qt 控件状态跨测试泄漏。"""

        self.window.close()
        self.window.deleteLater()
        self._application.processEvents()

    def test_only_recent_30_days_uses_relative_analysis_month(self):
        """仅选最近 30 天时必须仅传递空字符串相对月份。"""

        parameters = self.window.relation_query_parameters()

        self.assertEqual(self.window.selected_analysis_months(), [""])
        self.assertEqual(parameters["month"], "")
        self.assertEqual(parameters["analysis_months"], [""])

    def test_natural_month_options_end_at_previous_month(self):
        """当月不应进入自然月下拉；最近30天仍由独立选项承载实时范围。"""

        class _SeptemberDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 28)

        with patch("views.main_window.date", _SeptemberDate):
            months = MainWindow._recent_months()

        self.assertEqual(months[0], "2026-08")
        self.assertNotIn("2026-09", months)
        self.assertEqual(months[-1], "2024-01")

    def test_natural_month_options_handle_january_rollover(self):
        """一月时首个可选自然月必须回退为上一年的十二月。"""

        class _JanuaryDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 1, 15)

        with patch("views.main_window.date", _JanuaryDate):
            months = MainWindow._recent_months()

        self.assertEqual(months[0], "2025-12")
        self.assertNotIn("2026-01", months)

    def test_single_natural_month_uses_that_month_for_both_values(self):
        """仅选 2026-08 时 relation 月与分析快照都必须是 202608。"""

        self._click_time_range("2026-08")
        parameters = self.window.relation_query_parameters()

        self.assertFalse(
            self.window.month_selector.is_recent_30_days_selected()
        )
        self.assertEqual(self.window.selected_analysis_months(), ["202608"])
        self.assertEqual(parameters["month"], "202608")
        self.assertEqual(parameters["analysis_months"], ["202608"])

    def test_multiple_natural_months_keep_newest_to_oldest_snapshot(self):
        """多选自然月时 relation 取最新月，分析保留全部顺序。"""

        self._select_three_natural_months()
        parameters = self.window.relation_query_parameters()

        self.assertEqual(
            self.window.selected_analysis_months(),
            ["202608", "202607", "202606"],
        )
        self.assertEqual(parameters["month"], "202608")
        self.assertEqual(
            parameters["analysis_months"],
            ["202608", "202607", "202606"],
        )

    def test_selecting_natural_month_clears_default_recent_30_days(self):
        """默认最近 30 天后点击自然月，内部勾选状态必须同步清除。"""

        self.assertTrue(
            self.window.month_selector.is_recent_30_days_selected()
        )
        self._click_time_range("2026-08")

        self.assertFalse(
            self.window.month_selector.is_recent_30_days_selected()
        )
        self.assertEqual(self.window.selected_analysis_months(), ["202608"])

    def test_selecting_recent_30_days_clears_all_natural_months(self):
        """已有多月时点击最近 30 天，所有自然月必须同步取消。"""

        self._select_three_natural_months()
        self._click_time_range(
            MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION
        )

        self.assertEqual(self.window.month_selector.selected_months(), [])
        self.assertEqual(self.window.selected_analysis_months(), [""])

    def test_analysis_uses_relation_query_month_snapshot_after_ui_changes(self):
        """查询成功后修改 UI 月份，不得影响开始分析使用的原始多月快照。"""

        self._select_three_natural_months()
        query_parameters = self.window.relation_query_parameters()
        controller = MainController(
            self.window,
            _ApplicationRuntimeStub(),
        )
        controller._api_ready = True
        controller.crawler_service = _CrawlerServiceStub()
        controller._relation_query_context = {
            "analysis_months": list(query_parameters["analysis_months"]),
        }

        # 模拟 relation 查询完成后用户将界面改成最近 30 天。
        self._click_time_range(
            MultiSelectMonthComboBox.RECENT_30_DAYS_OPTION
        )
        self.window.selected_relation_items = lambda: [{"asin": "B0TEST"}]

        controller._start_analysis_preparation()

        self.assertEqual(self.window.selected_analysis_months(), [""])
        self.assertEqual(
            controller._preparation_months,
            ["202608", "202607", "202606"],
        )

    def test_month_snapshot_is_preserved_from_view_to_diagnostics_writer(self):
        """A 至 H 链路中三个月快照不得被最近 30 天 sentinel 覆盖。"""

        self._select_three_natural_months()
        # A：View 的正式分析月份。
        selected_analysis_months = self.window.selected_analysis_months()
        # B：View 查询参数中的 relation 月与完整分析月份。
        parameters = self.window.relation_query_parameters()
        controller = MainController(
            self.window,
            _ApplicationRuntimeStub(),
        )
        # C：relation 查询完成后 Controller 保存的上下文。
        controller._on_relation_query_succeeded(
            [],
            "B0DSHYXD4G",
            parameters["month"],
            parameters["display_time_range"],
            parameters["analysis_months"],
        )
        controller._api_ready = True
        controller.crawler_service = _CrawlerServiceStub()
        self.window.selected_relation_items = lambda: [{"asin": "B0TEST"}]
        controller._start_analysis_preparation()

        # D：开始分析时只读取查询上下文，得到固定的预处理月份。
        preparation_months = list(controller._preparation_months)
        analysis_service = _AnalysisServiceSpy()
        worker = AnalysisWorker(
            analysis_service,
            {month: {} for month in preparation_months},
            preparation_months,
        )
        processed_payloads = []
        worker.processed.connect(processed_payloads.append)
        worker.run()

        self.assertEqual(selected_analysis_months, ["202608", "202607", "202606"])
        self.assertEqual(parameters["month"], "202608")
        self.assertEqual(parameters["analysis_months"], selected_analysis_months)
        self.assertEqual(
            controller._relation_query_context["analysis_months"],
            selected_analysis_months,
        )
        self.assertEqual(preparation_months, selected_analysis_months)
        # E：Worker、F：AnalysisService 均收到原快照；CandidateDiscovery
        # 必须在用户确认 Word Filter 后才允许读取该月份快照。
        self.assertEqual(analysis_service.received_months, selected_analysis_months)
        self.assertEqual(analysis_service.result_keys, selected_analysis_months)

        controller._analysis_processing_active = True
        controller._on_reversing_result_analysis_succeeded(
            processed_payloads[0]
        )

        # H：候选仅留在 Controller / View 的当前运行时内存，不写诊断快照。
        self.assertEqual(
            controller._analysis_word_preview_results,
            {month: [] for month in selected_analysis_months},
        )
        # 无候选时基础分词预览会直接成为本 generation 的正式词结果，随后
        # Pipeline 自动进入 AI Tagging；这里仅验证月份快照仍被物理隔离。
        self.assertEqual(
            controller._analysis_word_results,
            {month: [] for month in selected_analysis_months},
        )

    def _select_three_natural_months(self):
        """按用户实际操作依次点击六月、七月和八月。"""

        for month in ("2026-06", "2026-07", "2026-08"):
            self._click_time_range(month)

    def _click_time_range(self, time_range: str):
        """调用与下拉列表点击相同的处理函数，验证真实内部 checked 状态。"""

        model = self.window.month_selector.model()
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            if model.data(index) == time_range:
                self.window.month_selector._toggle_month(index)
                return

        self.fail(f"未找到测试时间范围：{time_range}")


if __name__ == "__main__":
    unittest.main()
