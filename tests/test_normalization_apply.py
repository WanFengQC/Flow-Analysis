"""人工批准规则应用、revision 与正式词结果原子替换测试。"""

import copy
import os
import time
import unittest

# 测试不依赖真实桌面服务，必须在导入 Qt 前固定离屏平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from services.analysis_service import AnalysisService
from services.normalization_rule_service import NormalizationRuleService
from views.main_window import MainWindow
from workers.normalization_apply_worker import NormalizationApplyWorker


class _AsyncRuntimeStub:
    """提供 Controller 初始化所需形状；本测试不提交异步任务。"""

    def submit(self, _coroutine):
        """防止测试误将正式重算提交到 AsyncRuntime。"""

        raise AssertionError("正式词重算必须使用 QThread Worker")


class _ApplicationRuntimeStub:
    """提供 MainController 的最小运行时依赖。"""

    def __init__(self) -> None:
        self.async_runtime = _AsyncRuntimeStub()


def _candidate(
    candidate_id: str,
    variants: list[str],
    approved_canonical: str,
    *,
    decision: str = "APPROVED",
    reason_types: list[str] | None = None,
) -> dict:
    """构造最小候选，明确 suggestedCanonical 不等于人工最终值也允许。"""

    return {
        "id": candidate_id,
        "variants": variants,
        "suggestedCanonical": "machine-suggestion",
        "approvedCanonical": approved_canonical,
        "decision": decision,
        "reasonTypes": reason_types or ["KNOWN_WORD_ALIAS_VARIANT"],
        "months": ["202608", "202607", "202606"],
        "totalFrequency": 1,
        "matchingKeywordCount": 1,
        "impactWeeklyExposure": 1.0,
        "confidence": 0.9,
        "evidence": [],
    }


def _monthly_results() -> dict[str, list[dict]]:
    """构造三个月独立 RESULT，便于验证禁止跨月聚合。"""

    return {
        "202608": [
            {
                "keywords": "black cat animals animals cats dogs bears",
                "calculatedWeeklySearches": 100.0,
                "clicks": 10.0,
                "impressions": 100.0,
                "naturalRatio": 0.2,
                "adRatio": 0.8,
            }
        ],
        "202607": [
            {
                "keywords": "animals",
                "calculatedWeeklySearches": 50.0,
                "clicks": 20.0,
                "impressions": 100.0,
                "naturalRatio": 0.5,
                "adRatio": 0.5,
            }
        ],
        "202606": [
            {
                "keywords": "black cat animal",
                "calculatedWeeklySearches": 25.0,
                "clicks": 30.0,
                "impressions": 100.0,
                "naturalRatio": 0.3,
                "adRatio": 0.7,
            }
        ],
    }


def _monthly_raw() -> dict[str, list[dict]]:
    """构造与 RESULT 对应的 RAW，用于 ASIN 级正式词结果快照。"""

    return {
        month: [
            {
                **row,
                "source_asin": "B0TEST",
                "month": month,
            }
            for row in rows
        ]
        for month, rows in _monthly_results().items()
    }


class NormalizationApplyTest(unittest.TestCase):
    """验证批准快照是唯一正式归一输入，并保护历史正式结果。"""

    @classmethod
    def setUpClass(cls) -> None:
        """整个模块共用一个 QApplication，避免测试重复初始化 Qt。"""

        cls._application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """为每个测试准备独立 View / Controller，防止审核状态串扰。"""

        self.window = MainWindow()
        self.controller = MainController(
            self.window,
            _ApplicationRuntimeStub(),
        )
        self.months = ["202608", "202607", "202606"]
        self.controller._preparation_months = list(self.months)
        self.controller._analysis_results = _monthly_results()
        self.controller._analysis_raw = _monthly_raw()
        self.controller._analysis_word_preview_results = {
            month: [{"word": "preview", "month": month}]
            for month in self.months
        }
        self.controller._analysis_word_results = copy.deepcopy(
            self.controller._analysis_word_preview_results
        )
        self.controller._filtered_word_results = copy.deepcopy(
            self.controller._analysis_word_preview_results
        )

    def tearDown(self) -> None:
        """关闭 View 并处理延迟删除，避免 QThread/控件残留。"""

        self.window.close()
        self.window.deleteLater()
        self._application.processEvents()

    def test_only_approved_rules_change_worker_results(self) -> None:
        """APPROVED 之外的候选不能改变正式 token，且短语优先于单词。"""

        candidates = [
            _candidate("word", ["animal", "animals"], "animal"),
            _candidate(
                "phrase",
                ["black cat", "black cats"],
                "black-cat",
                reason_types=["PHRASE_TOKEN_VARIANT"],
            ),
            _candidate("pending", ["cats"], "cat", decision="PENDING"),
            _candidate("rejected", ["dogs"], "dog", decision="REJECTED"),
            _candidate("skipped", ["bears"], "bear", decision="SKIPPED"),
        ]
        build_result = NormalizationRuleService().build_approved_rules(
            candidates
        )
        self.assertIsNotNone(build_result.approved_rules)

        worker = NormalizationApplyWorker(
            AnalysisService(),
            _monthly_results(),
            _monthly_raw(),
            self.months,
            build_result.approved_rules,
        )
        payloads = []
        worker.processed.connect(payloads.append)
        worker.run()

        self.assertEqual(list(payloads[0]["word_results"]), self.months)
        august_words = {
            row["word"]: row for row in payloads[0]["word_results"]["202608"]
        }
        self.assertIn("black-cat", august_words)
        self.assertIn("animal", august_words)
        self.assertNotIn("black", august_words)
        self.assertNotIn("cat", august_words)
        self.assertEqual(august_words["animal"]["frequency"], 2)
        # 同一 keyword 的重复 animals 只增加 frequency，Weight / Total 仍只算一次。
        self.assertEqual(august_words["animal"]["matchingKeywordCount"], 1)
        self.assertEqual(august_words["animal"]["weight"], 430.0)
        self.assertEqual(august_words["animal"]["total"], 10.0)
        self.assertIn("cats", august_words)
        self.assertIn("dogs", august_words)
        self.assertIn("bears", august_words)

    def test_revision_marks_existing_result_stale_without_deleting_it(self) -> None:
        """审核实际变化必须递增 revision，并保留可追溯的上一份正式结果。"""

        candidate = _candidate("animal", ["animal", "animals"], "animal", decision="PENDING")
        self.controller._normalization_candidates = [candidate]
        self.window.set_normalization_candidates([candidate])
        old_results = {month: [{"word": "animal"}] for month in self.months}
        self.controller._analysis_word_results = copy.deepcopy(old_results)
        self.controller._filtered_word_results = copy.deepcopy(old_results)
        self.controller._analysis_word_result_meta = {
            "normalization_revision": 10,
        }
        self.controller._normalization_revision = 10
        self.controller._normalization_result_state = "CURRENT"

        self.controller._approve_normalization_candidate("animal", "animal")

        self.assertEqual(self.controller._normalization_revision, 11)
        self.assertEqual(self.controller._normalization_result_state, "STALE")
        self.assertEqual(self.controller._analysis_word_results, old_results)
        self.assertIn("需要重新应用", self.window.ui.normalizationResultStatusLabel.text())

    def test_conflict_blocks_worker_before_thread_is_created(self) -> None:
        """同 variant 指向不同 canonical 时，不得创建 Worker 或覆盖旧正式结果。"""

        candidates = [
            _candidate("first", ["animals"], "animal"),
            _candidate("second", ["animals"], "animals-general"),
        ]
        self.controller._normalization_candidates = candidates
        self.window.set_normalization_candidates(candidates)
        old_results = {month: [{"word": "old"}] for month in self.months}
        self.controller._analysis_word_results = copy.deepcopy(old_results)
        self.controller._filtered_word_results = copy.deepcopy(old_results)
        # 避免测试弹出模态窗口；仍验证 Controller 向 View 提供了定位数据。
        received_conflicts = []
        self.window.show_normalization_rule_conflicts = received_conflicts.append

        self.controller._apply_normalization_rules()

        self.assertFalse(self.controller._normalization_apply_active)
        self.assertIsNone(self.controller.normalization_apply_thread)
        self.assertEqual(self.controller._analysis_word_results, old_results)
        self.assertEqual(self.controller._normalization_result_state, "CONFLICT")
        self.assertEqual(received_conflicts[0][0]["variant"], "animals")

    def test_chain_reference_also_blocks_worker_before_thread_is_created(self) -> None:
        """A→B、B→C 不能作为中间规则集进入后台重算。"""

        candidates = [
            _candidate("first", ["a"], "b"),
            _candidate("second", ["b"], "c"),
        ]
        self.controller._normalization_candidates = candidates
        self.window.set_normalization_candidates(candidates)
        received_conflicts = []
        self.window.show_normalization_rule_conflicts = received_conflicts.append

        self.controller._apply_normalization_rules()

        self.assertFalse(self.controller._normalization_apply_active)
        self.assertIsNone(self.controller.normalization_apply_thread)
        self.assertEqual(
            received_conflicts[0][0]["conflict_type"],
            "CHAIN_REFERENCE",
        )

    def test_failure_does_not_replace_previous_complete_results(self) -> None:
        """任一月份失败后的回调只能保留旧正式结果，绝不能留下半成品。"""

        old_results = {month: [{"word": "old"}] for month in self.months}
        self.controller._analysis_word_results = copy.deepcopy(old_results)
        self.controller._filtered_word_results = copy.deepcopy(old_results)
        candidate = _candidate("animal", ["animal", "animals"], "animal")
        self.controller._normalization_candidates = [candidate]
        self.window.set_normalization_candidates([candidate])
        self.controller._normalization_apply_active = True
        self.controller._normalization_result_state = "RUNNING"
        self.window.set_normalization_review_editable(False)

        self.controller._on_normalization_apply_failed("模拟 202607 失败")

        self.assertEqual(self.controller._analysis_word_results, old_results)
        self.assertEqual(self.controller._normalization_result_state, "FAILED")
        self.assertTrue(self.window.ui.finishNormalizationReviewButton.isEnabled())

    def test_success_replaces_only_complete_monthly_snapshot_and_keeps_preview(self) -> None:
        """成功回调一次性替换全部月份，preview 永远不被正式重算改写。"""

        preview_before = copy.deepcopy(
            self.controller._analysis_word_preview_results
        )
        self.controller._normalization_apply_active = True
        self.controller._normalization_apply_context = {
            "normalization_revision": 3,
            "approved_rule_count": 2,
            "approved_candidate_ids": ["word", "phrase"],
            "pending_candidate_count": 1,
            "skipped_candidate_count": 1,
            "months": list(self.months),
        }
        new_results = {month: [{"word": month}] for month in self.months}
        new_asin_results = {
            month: {"B0TEST": [{"word": month}]}
            for month in self.months
        }

        self.controller._on_normalization_apply_succeeded(
            {
                "word_results": new_results,
                "asin_word_results": new_asin_results,
                "diagnostics": {month: {} for month in self.months},
            }
        )

        self.assertEqual(self.controller._filtered_word_results, new_results)
        self.assertEqual(
            self.controller._filtered_asin_word_results,
            new_asin_results,
        )
        self.assertEqual(
            self.controller._analysis_word_preview_results,
            preview_before,
        )
        self.assertEqual(
            self.controller._analysis_word_result_meta["normalization_revision"],
            3,
        )
        self.assertEqual(self.controller._normalization_result_state, "CURRENT")

    def test_controller_runs_real_qthread_and_replaces_all_months(self) -> None:
        """应用入口必须通过专用 QThread 完成三个月全量正式重算。"""

        candidates = [
            _candidate("word", ["animal", "animals"], "animal"),
            _candidate(
                "phrase",
                ["black cat", "black cats"],
                "black-cat",
                reason_types=["PHRASE_TOKEN_VARIANT"],
            ),
        ]
        preview_before = copy.deepcopy(
            self.controller._analysis_word_preview_results
        )
        self.controller._normalization_candidates = candidates
        self.window.set_normalization_candidates(candidates)

        self.controller._apply_normalization_rules()

        self.assertTrue(self.controller._normalization_apply_active)
        self.assertFalse(self.window.ui.approveNormalizationButton.isEnabled())
        deadline = time.monotonic() + 2.0
        while (
            self.controller._normalization_apply_active
            and time.monotonic() < deadline
        ):
            self._application.processEvents()
            time.sleep(0.01)
        self._application.processEvents()

        self.assertFalse(self.controller._normalization_apply_active)
        self.assertEqual(self.controller._normalization_result_state, "CURRENT")
        self.assertEqual(list(self.controller._filtered_word_results), self.months)
        self.assertEqual(
            self.controller._analysis_word_preview_results,
            preview_before,
        )
        self.assertEqual(
            self.controller._analysis_word_result_meta["approved_rule_count"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
