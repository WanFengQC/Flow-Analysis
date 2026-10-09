"""完整 Analysis Job 总进度的回归测试。"""

import os
import unittest

# 测试环境不依赖真实桌面显示服务，必须在导入 Qt 前固定离屏平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from services.crawler_service import ReversingPreparationRetriesExhaustedError
from views.main_window import MainWindow


class _AsyncRuntimeStub:
    """本测试不提交协程，只提供 Controller 初始化所需的最小形状。"""


class _RuntimeStub:
    """确保进度测试不会访问真实数据库、API 或 AI Provider。"""

    def __init__(self) -> None:
        self.async_runtime = _AsyncRuntimeStub()


class AnalysisProgressTest(unittest.TestCase):
    """验证多月份子任务不会让同一条总进度回退。"""

    @classmethod
    def setUpClass(cls) -> None:
        """整个模块共用一个 QApplication。"""

        cls._application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """每个测试独立创建正式 View 与 Controller。"""

        self.window = MainWindow()
        self.controller = MainController(self.window, _RuntimeStub())
        self.controller._analysis_job_active = True
        self.window.set_analysis_job_running(True)

    def tearDown(self) -> None:
        """释放 Qt 控件，避免后续测试读取到旧窗口状态。"""

        self.window.close()
        self.window.deleteLater()
        self._application.processEvents()

    def test_progress_is_monotonic_across_months_and_pipeline_stages(self) -> None:
        """两 ASIN、三月份的串行 reversing 与后续阶段共用 0–100 进度。"""

        recorded_values: list[int] = []
        original_set_progress = self.window.set_analysis_progress

        def record_progress(value: int, message: str | None = None) -> None:
            """保留每次 View 更新值，同时继续执行正式 UI 更新。"""

            recorded_values.append(value)
            original_set_progress(value, message)

        self.window.set_analysis_progress = record_progress
        self.controller._preparation_asins = ["B0FIRST", "B0SECOND"]
        self.controller._preparation_months = ["202608", "202607", "202606"]
        self.controller._reset_analysis_progress()

        # 六个“月份 × ASIN”任务完成后刚好到 reversing 阶段上限 20%。
        for completed_task_count in range(1, 7):
            self.controller._update_reversing_progress(completed_task_count)

        self.controller._analysis_processing_active = True
        self.controller._on_analysis_worker_stage_changed(
            "BUILDING_RESULT",
            self.controller._analysis_generation,
        )
        self.controller._on_analysis_worker_stage_changed(
            "ANALYZING_WORDS",
            self.controller._analysis_generation,
        )
        self.controller._on_analysis_worker_stage_changed(
            "DISCOVERING_NORMALIZATION",
            self.controller._analysis_generation,
        )
        self.controller._advance_analysis_progress(
            self.controller._PROGRESS_ANALYSIS_END
        )
        self.controller._advance_analysis_progress(
            self.controller._PROGRESS_NORMALIZATION_END
        )

        self.controller._analysis_generation = 9
        self.controller._tagging_task_generation = 3
        self.controller._tagging_task_active = True
        for message in (
            "正在查询历史标签缓存...",
            "正在准备产品背景...",
            "正在获取 Amazon 产品背景...",
            "正在进行 AI 三模型判断...",
            "正在处理共识结果...",
        ):
            self.controller._on_tagging_pipeline_progressed(3, 9, message)

        self.assertEqual(recorded_values[0], 0)
        self.assertEqual(recorded_values[6], 20)
        self.assertEqual(recorded_values[-1], 78)
        self.assertEqual(recorded_values, sorted(recorded_values))

    def test_skip_failed_month_keeps_other_months_and_continues(self) -> None:
        """跳过仅影响当前月份，其他月份数据必须保留且继续下一个任务。"""

        self.controller._reversing_preparation_active = True
        self.controller._preparation_asins = ["B0KEEP", "B0SKIP"]
        self.controller._preparation_months = ["202608", "202607"]
        self.controller._preparation_month_index = 1
        self.controller._preparation_index = 1
        self.controller._preparation_total_task_count = 4
        self.controller._preparation_completed_task_count = 3
        self.controller._reversing_results = {
            "202608": {"B0KEEP": {"total": 1}, "B0SKIP": {"total": 1}},
            "202607": {"B0KEEP": {"total": 1}},
        }
        self.window.ask_reversing_data_retry = lambda *_args, **_kwargs: "skip"
        submitted: list[bool] = []
        self.controller._submit_current_reversing_preparation = (
            lambda: submitted.append(True)
        )

        self.controller._on_reversing_preparation_failed(
            "202607",
            "B0SKIP",
            ReversingPreparationRetriesExhaustedError(
                "B0SKIP",
                4,
                "reversing 返回空数据",
            ),
        )

        self.assertEqual(
            self.controller._preparation_asins,
            ["B0KEEP", "B0SKIP"],
        )
        self.assertEqual(self.controller._preparation_month_index, 2)
        self.assertEqual(self.controller._preparation_index, 0)
        self.assertEqual(self.controller._preparation_completed_task_count, 4)
        self.assertIn("B0SKIP", self.controller._reversing_results["202608"])
        self.assertEqual(
            self.controller._skipped_reversing_months["B0SKIP"][0]["month"],
            "202607",
        )
        self.assertIn(
            "B0SKIP: 202607 无数据",
            self.controller._analysis_completion_status(),
        )
        self.assertEqual(submitted, [True])
