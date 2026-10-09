"""归一审核 modeless Dialog 的布局、单实例与自动打开行为测试。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSizePolicy

from controllers.main_controller import AnalysisPipelineState, MainController
from views.main_window import MainWindow


class _AsyncRuntimeStub:
    """本组测试不发起历史查询，防止意外连接外部基础设施。"""

    def submit(self, coroutine):
        coroutine.close()
        raise RuntimeError("测试不应提交异步 I/O")


class _RuntimeStub:
    """仅提供 Controller 初始化所需的最小运行时形状。"""

    def __init__(self):
        self.async_runtime = _AsyncRuntimeStub()


def _candidate(candidate_id="dialog-candidate"):
    """构造审核窗口展示所需的最小 PENDING 候选。"""

    return {
        "id": candidate_id,
        "suggestedCanonical": "animal",
        "approvedCanonical": None,
        "variants": ["animal", "animals"],
        "reasonTypes": ["KNOWN_WORD_ALIAS_VARIANT"],
        "decision": "PENDING",
        "confidence": 0.9,
        "months": ["202608"],
        "totalFrequency": 2,
        "matchingKeywordCount": 1,
        "impactWeeklyExposure": 10.0,
        "evidence": [],
        "variantDetails": [],
    }


class NormalizationReviewDialogTest(unittest.TestCase):
    """验证审核界面不再占用主窗口 Tab，且不会创建重复窗口。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window._normalization_review_dialog.close()
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_main_window_has_no_review_tab_and_status_row_is_compact(self):
        """审核页已移出 Tab，底部状态行没有垂直 Expanding 或 spacer。"""

        self.assertEqual(
            self.window.ui.resultTabWidget.indexOf(
                self.window.ui.normalizationReviewTab
            ),
            -1,
        )
        self.assertFalse(self.window._normalization_review_dialog.isModal())
        self.assertFalse(self.window.ui.normalizationReviewTab.isHidden())
        self.assertEqual(
            self.window.ui.normalizationResultStatusLabel.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Fixed,
        )
        self.assertEqual(
            self.window.ui.normalizationPersistenceStatusLabel.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Fixed,
        )
        self.assertEqual(self.window.ui.normalizationReviewLayout.count(), 3)
        self.assertIs(
            self.window.ui.normalizationReviewLayout.itemAt(2).layout(),
            self.window.ui.normalizationStatusLayout,
        )

    def test_empty_candidates_do_not_open_dialog_and_candidates_auto_open_single_instance(self):
        """零候选不弹窗；候选只能由流水线自动打开同一 Dialog。"""

        dialog = self.window._normalization_review_dialog
        self.window.set_normalization_candidates([])
        self.window.show_normalization_review()
        self.assertFalse(dialog.isVisible())

        self.window.set_normalization_candidates([_candidate()])
        self.assertFalse(hasattr(self.window.ui, "openNormalizationReviewButton"))
        self.window.show_normalization_review()
        self.application.processEvents()
        self.assertTrue(dialog.isVisible())
        self.window.set_normalization_candidates([_candidate("new-candidate")])
        self.assertIs(self.window._normalization_review_dialog, dialog)
        self.assertIn(
            "new-candidate", self.window._normalization_candidates_by_id
        )

    def test_candidate_discovery_completion_auto_opens_only_when_candidates_exist(self):
        """Controller 在候选完整保存到内存和 View 后，才自动显示审核窗口。"""

        controller = MainController(self.window, _RuntimeStub())
        controller._analysis_processing_active = True
        controller._preparation_months = ["202608"]
        processed_data = {
            "raw": {},
            "results": {},
            "word_preview_results": {},
            "word_results": {},
            "normalization_candidates": [_candidate()],
            "diagnostics": {},
        }
        controller._on_reversing_result_analysis_succeeded(processed_data)
        self.application.processEvents()
        self.assertTrue(self.window._normalization_review_dialog.isVisible())

        self.window._normalization_review_dialog.hide()
        controller._analysis_processing_active = True
        processed_data["normalization_candidates"] = []
        controller._on_reversing_result_analysis_succeeded(processed_data)
        self.application.processEvents()
        self.assertFalse(self.window._normalization_review_dialog.isVisible())

    def test_close_event_routes_to_controller_and_destroys_pending_runtime_state(self):
        """点击 Dialog 的 X 必须走关闭确认，而不是只隐藏窗口等待重开。"""

        controller = MainController(self.window, _RuntimeStub())
        controller._analysis_job_active = True
        controller._normalization_candidates = [_candidate()]
        self.window.set_normalization_candidates(
            controller._normalization_candidates
        )
        self.window.show_normalization_review()

        with patch.object(
            self.window,
            "confirm_discard_normalization_review",
            return_value=True,
        ), patch.object(controller, "_apply_normalization_rules") as apply_rules:
            self.window._normalization_review_dialog.close()
            self.application.processEvents()

        apply_rules.assert_called_once_with([])
        self.assertEqual(controller._normalization_candidates, [])
        self.assertFalse(self.window._normalization_review_dialog.isVisible())
        self.assertNotEqual(
            controller._analysis_pipeline_state,
            AnalysisPipelineState.FAILED,
        )

    def test_initial_state_is_idle_and_fatal_failure_includes_reason(self):
        """启动不继承失败状态；只有 fatal 失败才显示可读原因。"""

        controller = MainController(self.window, _RuntimeStub())

        self.assertEqual(
            controller._analysis_pipeline_state,
            AnalysisPipelineState.IDLE,
        )
        self.assertEqual(
            self.window.ui.analysisPipelineStatusLabel.text(),
            "当前状态：未开始",
        )

        controller._analysis_job_active = True
        controller._fail_analysis_job("核心数据不可用")

        self.assertEqual(
            controller._analysis_pipeline_state,
            AnalysisPipelineState.FAILED,
        )
        self.assertEqual(
            self.window.ui.analysisPipelineStatusLabel.text(),
            "当前状态：分析失败：核心数据不可用",
        )

    def test_new_generation_can_start_after_a_previous_failure(self):
        """FAILED 只是上一轮终态，不能阻止下一轮创建新的 analysis generation。"""

        controller = MainController(self.window, _RuntimeStub())
        controller._api_ready = True
        controller.crawler_service = object()
        controller._relation_query_context = {
            "analysis_months": ["202608"],
        }
        controller._set_analysis_pipeline_state(AnalysisPipelineState.FAILED, "旧失败")
        previous_generation = controller._analysis_generation

        with patch.object(
            self.window,
            "selected_relation_items",
            return_value=[{"asin": "B0TEST0001"}],
        ), patch.object(controller, "_submit_current_reversing_preparation"):
            controller._start_analysis_preparation()

        self.assertEqual(
            controller._analysis_generation,
            previous_generation + 1,
        )
        self.assertTrue(controller._analysis_job_active)
        self.assertEqual(
            controller._analysis_pipeline_state,
            AnalysisPipelineState.FETCHING_REVERSING,
        )

    def test_shortcuts_are_owned_by_dialog_not_main_window(self):
        """A/R/S 的 QShortcut 父窗口必须是审核 Dialog，避免污染主窗口输入。"""

        dialog = self.window._normalization_review_dialog
        self.assertIs(self.window._normalization_approve_shortcut.parent(), dialog)
        self.assertIs(self.window._normalization_reject_shortcut.parent(), dialog)
        self.assertIs(self.window._normalization_skip_shortcut.parent(), dialog)

    def test_finish_review_clears_only_runtime_review_state(self):
        """明确结束审核才销毁候选，正式词结果和旧异步回调均受保护。"""

        controller = MainController(self.window, _RuntimeStub())
        controller._normalization_candidates = [_candidate()]
        controller._normalization_history_references = {
            "dialog-candidate": {"status": "HAS_HISTORY"}
        }
        controller._analysis_word_results = {"202608": [{"word": "animal"}]}
        controller._normalization_result_state = "CURRENT"
        controller._normalization_candidate_generation = 4
        self.window.set_normalization_candidates(
            controller._normalization_candidates
        )
        self.window.set_normalization_history_references(
            controller._normalization_history_references
        )

        with patch.object(
            self.window,
            "confirm_discard_normalization_review",
            return_value=True,
        ):
            controller._request_close_normalization_review()

        self.assertEqual(controller._normalization_candidates, [])
        self.assertEqual(controller._normalization_history_references, {})
        self.assertEqual(self.window._normalization_candidates, [])
        self.assertEqual(
            controller._analysis_word_results,
            {"202608": [{"word": "animal"}]},
        )
        self.assertEqual(controller._normalization_candidate_generation, 5)

        # generation 4 的异步历史读取晚到时，不能再污染已清空的工作集。
        controller._on_normalization_history_references_loaded(
            4,
            {"dialog-candidate": {"status": "HAS_HISTORY"}},
        )
        self.assertEqual(controller._normalization_history_references, {})

    def test_close_review_continues_active_analysis_job_with_confirmed_rules_only(self):
        """关闭后立即销毁 PENDING，并使用已确认规则继续完整流水线。"""

        controller = MainController(self.window, _RuntimeStub())
        controller._analysis_job_active = True
        approved = _candidate("approved-candidate")
        approved["decision"] = "APPROVED"
        approved["approvedCanonical"] = "animal"
        controller._normalization_candidates = [_candidate(), approved]
        controller._normalization_history_references = {
            "dialog-candidate": {"status": "HAS_HISTORY"}
        }
        self.window.set_normalization_candidates(
            controller._normalization_candidates
        )
        with patch.object(
            self.window,
            "confirm_discard_normalization_review",
            return_value=True,
        ), patch.object(controller, "_apply_normalization_rules") as apply_rules:
            controller._request_close_normalization_review()

        apply_rules.assert_called_once()
        frozen_candidates = apply_rules.call_args.args[0]
        self.assertEqual([item["id"] for item in frozen_candidates], ["approved-candidate"])
        self.assertEqual(controller._normalization_candidates, [])
        self.assertEqual(controller._normalization_history_references, {})
        self.assertFalse(controller._normalization_apply_active)
        self.assertTrue(controller._analysis_job_active)
        self.assertFalse(self.window._normalization_review_dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
