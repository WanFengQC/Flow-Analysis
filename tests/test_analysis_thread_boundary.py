"""完整 Analysis Pipeline 的线程边界回归测试。"""

from concurrent.futures import Future
import os
import threading
import unittest

# 测试环境不依赖真实桌面显示服务，必须在导入 Qt 前固定离屏平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication

from controllers.main_controller import AnalysisPipelineState, MainController
from views.main_window import MainWindow
from workers.analysis_worker import AnalysisWorker


class _ResultForbiddenFuture(Future):
    """用于证明开始入口只提交 Future，绝不在 UI 线程同步读取结果。"""

    def __init__(self) -> None:
        """初始化尚未完成且禁止调用 result 的 Future。"""

        super().__init__()
        self.result_called = False

    def result(self, timeout=None):
        """若开始入口误等待结果，测试必须立即失败。"""

        self.result_called = True
        raise AssertionError("Qt 开始分析入口禁止调用 Future.result()")


class _AsyncRuntimeStub:
    """保留提交协程的证据，但不执行任何 SellerSprite 网络请求。"""

    def __init__(self) -> None:
        """为每次提交创建一个未完成 Future。"""

        self.submitted_coroutines = []
        self.future = _ResultForbiddenFuture()

    def submit(self, coroutine):
        """立即关闭测试协程，模拟提交后不阻塞 Qt 主线程。"""

        self.submitted_coroutines.append(coroutine)
        coroutine.close()
        return self.future


class _PendingAsyncRuntime:
    """为严格串行测试保存每一次独立提交的未完成 Future。"""

    def __init__(self) -> None:
        """初始化提交记录与待外部完成的 Future 列表。"""

        self.submitted_coroutines = []
        self.futures: list[Future] = []

    def submit(self, coroutine):
        """只模拟 AsyncRuntime 的非阻塞提交，不执行网络协程。"""

        self.submitted_coroutines.append(coroutine)
        coroutine.close()
        future = Future()
        self.futures.append(future)
        return future


class _RuntimeStub:
    """提供 Controller 启动分析所需的最小异步运行时。"""

    def __init__(self, async_runtime=None) -> None:
        """固定测试使用的异步运行时替身。"""

        self.async_runtime = async_runtime or _AsyncRuntimeStub()


class _CrawlerServiceStub:
    """只声明正式 reversing 协程形状，测试中不访问网络。"""

    async def get_relation_reversing_for_asin_month(self, **_kwargs):
        """运行时替身不应真的执行该协程。"""

        raise AssertionError("线程边界测试不应执行 SellerSprite 请求")


class _AnalysisServiceStub:
    """为真实 QThread Worker 提供极小且纯 CPU 的处理结果。"""

    def analyze_monthly_reversing_results(self, _results, months):
        """返回每个月物理隔离的空 RAW/RESULT。"""

        return {
            "raw": {month: [] for month in months},
            "results": {month: [] for month in months},
            "diagnostics": {month: {} for month in months},
        }

    def analyze_monthly_word_results(
        self,
        _results,
        months,
        approved_rules=None,
    ):
        """模拟审核前的无规则词分析。"""

        assert approved_rules is None
        return ({month: [] for month in months}, {month: {} for month in months})

    def analyze_monthly_asin_word_results(
        self,
        _raw,
        months,
        approved_rules=None,
    ):
        """模拟 ASIN 独立词结果，保持 Worker 输出契约完整。"""

        assert approved_rules is None
        return ({month: {} for month in months}, {month: {} for month in months})


class _CandidateServiceStub:
    """候选发现替身，避免测试涉及任何归一业务规则。"""

    def discover_candidates(self, _results):
        """返回空候选，仅验证 QThread 到 Controller 的信号投递。"""

        return []


class AnalysisThreadBoundaryTest(unittest.TestCase):
    """避免 Future 等待与 Worker 跨线程直接操作 UI 的回归。"""

    @classmethod
    def setUpClass(cls) -> None:
        """整个模块共用一个 QApplication。"""

        cls._application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """每个测试使用独立 Window / Controller，避免 Qt 状态串扰。"""

        self.window = MainWindow()
        self.runtime = _RuntimeStub()
        self.controller = MainController(self.window, self.runtime)

    def tearDown(self) -> None:
        """关闭窗口并处理延迟删除。"""

        self.window.close()
        self.window.deleteLater()
        self._application.processEvents()

    def test_start_handler_only_submits_reversing_future(self) -> None:
        """开始按钮入口必须立即返回，不能在 Qt 主线程等待 reversing。"""

        self.controller._api_ready = True
        self.controller.crawler_service = _CrawlerServiceStub()
        self.controller._relation_query_context = {
            "analysis_months": ["202608"],
        }
        self.window.selected_relation_items = lambda: [{"asin": "B0TEST"}]

        self.controller._start_analysis_preparation()

        self.assertEqual(len(self.runtime.async_runtime.submitted_coroutines), 1)
        self.assertFalse(self.runtime.async_runtime.future.result_called)
        self.assertTrue(self.controller._analysis_job_active)
        self.assertTrue(self.controller._reversing_preparation_active)
        self.assertTrue(self.window.ui.cancelAnalysisButton.isEnabled())

        # 取消必须在 Future 尚未完成时立即生效，且不能等待后台 I/O。
        self.controller._cancel_analysis_job()
        self.assertFalse(self.controller._analysis_job_active)
        self.assertTrue(self.runtime.async_runtime.future.cancelled())

    def test_worker_stage_slots_are_delivered_to_qt_main_thread(self) -> None:
        """QThread Worker 的阶段信号必须经 Controller Slot 排回主线程。"""

        main_thread_id = threading.get_ident()
        recorded_thread_ids: list[int] = []
        recorded_states: list[AnalysisPipelineState] = []

        def record_state(state, _failure_reason=None):
            """记录真正执行 Controller 状态更新时的 Python 线程。"""

            recorded_thread_ids.append(threading.get_ident())
            recorded_states.append(state)

        self.controller._set_analysis_pipeline_state = record_state
        self.controller._analysis_generation = 7
        self.controller._analysis_processing_active = True

        thread = QThread()
        worker = AnalysisWorker(
            _AnalysisServiceStub(),
            {"202608": {}},
            ["202608"],
            analysis_generation=7,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # 这里刻意使用正式 Controller Slot；若改回 Python lambda，记录将
        # 来自 Worker 线程，从而让本测试失败。
        worker.stage_changed.connect(
            self.controller._on_analysis_worker_stage_changed
        )
        worker.finished.connect(thread.quit)

        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        thread.finished.connect(loop.quit)
        thread.start()
        timeout.start(3000)
        loop.exec()
        timeout.stop()

        self.assertFalse(thread.isRunning())
        self.assertEqual(
            recorded_states,
            [
                AnalysisPipelineState.BUILDING_RESULT,
                AnalysisPipelineState.ANALYZING_WORDS,
            ],
        )
        self.assertEqual(recorded_thread_ids, [main_thread_id] * 2)

    def test_reversing_asins_are_submitted_only_after_previous_callback(self) -> None:
        """严格串行属于 AsyncRuntime Future 回调推进，不能在开始 Slot 中循环等待。"""

        async_runtime = _PendingAsyncRuntime()
        self.runtime.async_runtime = async_runtime
        self.controller._api_ready = True
        self.controller.crawler_service = _CrawlerServiceStub()
        self.controller._relation_query_context = {
            "analysis_months": ["202608"],
        }
        self.window.selected_relation_items = lambda: [
            {"asin": "B0FIRST"},
            {"asin": "B0SECOND"},
        ]

        self.controller._start_analysis_preparation()

        # 开始 Slot 只提交第一个 ASIN；第二个绝不能抢先提交或同步等待。
        self.assertEqual(len(async_runtime.futures), 1)
        self.assertFalse(async_runtime.futures[0].done())

        async_runtime.futures[0].set_result({"total": 1, "items": []})

        # 完成 callback 经 Qt Slot 处理后，才提交第二个 ASIN。
        self.assertEqual(len(async_runtime.futures), 2)
        self.assertEqual(
            self.controller._reversing_results["202608"]["B0FIRST"],
            {"total": 1, "items": []},
        )
        self.assertEqual(self.controller._current_preparation_asin(), "B0SECOND")

        self.controller._cancel_analysis_job()
        self.assertFalse(self.controller._analysis_job_active)

    def test_stale_worker_stage_is_discarded_by_generation(self) -> None:
        """已取消或旧任务的 QThread 阶段信号不得覆盖当前任务状态。"""

        recorded_states: list[AnalysisPipelineState] = []

        def record_state(state, _failure_reason=None):
            """只记录被 Controller 真正接纳的状态。"""

            recorded_states.append(state)

        self.controller._set_analysis_pipeline_state = record_state
        self.controller._analysis_generation = 7
        self.controller._analysis_processing_active = True

        thread = QThread()
        worker = AnalysisWorker(
            _AnalysisServiceStub(),
            {"202608": {}},
            ["202608"],
            analysis_generation=6,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stage_changed.connect(
            self.controller._on_analysis_worker_stage_changed
        )
        worker.finished.connect(thread.quit)

        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        thread.finished.connect(loop.quit)
        thread.start()
        timeout.start(3000)
        loop.exec()
        timeout.stop()

        self.assertFalse(thread.isRunning())
        self.assertEqual(recorded_states, [])


if __name__ == "__main__":
    unittest.main()
