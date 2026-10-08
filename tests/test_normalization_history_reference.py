"""历史归一审核参考的只读汇总、异步边界与 View 行为测试。"""

import asyncio
import copy
import os
from concurrent.futures import Future
from datetime import datetime, timezone
import unittest
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from models.normalization_review_decision import (
    NormalizationReviewDecision,
    NormalizationReviewDecisionRecord,
)
from models.normalization_rule import NormalizationRuleType
from services.normalization_review_service import NormalizationReviewService
from services.normalization_rule_service import NormalizationRuleService
from views.main_window import MainWindow


class _HistoryRepository:
    """统计批量读取次数，防止实现退化为每个 candidate 单独查库。"""

    def __init__(self, records):
        self.records = records
        self.batch_calls = 0

    async def list_decisions_by_fingerprints(self, fingerprints):
        """模拟单次 IN/ANY 查询，并按最新记录优先返回。"""

        self.batch_calls += 1
        fingerprint_set = set(fingerprints)
        result = {fingerprint: [] for fingerprint in fingerprint_set}
        for record in reversed(self.records):
            if record.candidate_fingerprint in fingerprint_set:
                result[record.candidate_fingerprint].append(record)
        return result


class _ImmediateAsyncRuntime:
    """同步完成提交，便于验证 Controller 仍经 Future/Signal 路径接收结果。"""

    def submit(self, coroutine):
        future = Future()
        try:
            future.set_result(asyncio.run(coroutine))
        except Exception as error:
            future.set_exception(error)
        return future


class _RuntimeWithDatabase:
    """提供 Controller 所需的最小运行时形状。"""

    def __init__(self):
        self.database = object()
        self.async_runtime = _ImmediateAsyncRuntime()


def _candidate(candidate_id="candidate-animal"):
    """构造不会被历史参考函数修改的 PENDING 候选。"""

    return {
        "id": candidate_id,
        "variants": ["animal", "animals"],
        "suggestedCanonical": "animal",
        "approvedCanonical": None,
        "reasonTypes": ["KNOWN_WORD_ALIAS_VARIANT"],
        "decision": "PENDING",
        "impactWeeklyExposure": 120.0,
        "totalFrequency": 12,
        "matchingKeywordCount": 4,
        "months": ["202608"],
    }


def _record(fingerprint, decision, canonical=None, *, minute=0):
    """构造不含任何认证信息的 append-only 历史记录。"""

    return NormalizationReviewDecisionRecord(
        id=uuid4(),
        candidate_fingerprint=fingerprint,
        candidate_id="old-runtime-candidate",
        decision=NormalizationReviewDecision(decision),
        rule_type=NormalizationRuleType.WORD,
        variants=("animal", "animals"),
        suggested_canonical="animal",
        approved_canonical=canonical,
        reason_types=("KNOWN_WORD_ALIAS_VARIANT",),
        context_snapshot={"market": "COM", "analysisMonths": ["202608"]},
        normalization_revision=minute,
        created_at=datetime(2026, 9, 23, 8, minute, tzinfo=timezone.utc),
    )


class NormalizationHistoryReferenceServiceTest(unittest.TestCase):
    """验证历史汇总是只读、批量且保留冲突证据。"""

    def test_batch_history_reference_keeps_candidate_pending_and_detects_conflict(self):
        """批准、拒绝、再批准必须显示冲突，但绝不改写当次 PENDING。"""

        candidate = _candidate()
        source_candidate = copy.deepcopy(candidate)
        probe = NormalizationReviewService(_HistoryRepository([]))
        fingerprint = probe.candidate_fingerprint_for_candidate(candidate)
        repository = _HistoryRepository(
            [
                _record(fingerprint, "APPROVED", "animal", minute=1),
                _record(fingerprint, "REJECTED", minute=2),
                _record(fingerprint, "APPROVED", "animal", minute=3),
            ]
        )
        service = NormalizationReviewService(repository)

        references = asyncio.run(service.load_history_references([candidate]))

        self.assertEqual(repository.batch_calls, 1)
        self.assertEqual(candidate, source_candidate)
        reference = references["candidate-animal"]
        self.assertEqual(reference["totalDecisionCount"], 3)
        self.assertEqual(reference["latestDecision"], "APPROVED")
        self.assertEqual(reference["latestApprovedCanonical"], "animal")
        self.assertTrue(reference["hasDecisionConflict"])
        self.assertFalse(reference["hasCanonicalConflict"])
        self.assertEqual(reference["decisionCounts"], {
            "APPROVED": 2, "REJECTED": 1, "SKIPPED": 0,
        })
        self.assertEqual(candidate["decision"], "PENDING")

    def test_multiple_candidates_still_use_one_batch_query(self):
        """100 个候选也必须通过一次批量 Repository 调用完成参考读取。"""

        candidates = [_candidate(f"candidate-{index}") for index in range(100)]
        repository = _HistoryRepository([])
        references = asyncio.run(
            NormalizationReviewService(repository).load_history_references(candidates)
        )

        self.assertEqual(repository.batch_calls, 1)
        self.assertEqual(len(references), 100)
        self.assertTrue(all(
            reference["totalDecisionCount"] == 0
            for reference in references.values()
        ))

    def test_canonical_conflict_and_history_approval_never_build_current_rule(self):
        """不同历史 canonical 要显式冲突，且历史批准不能替代本轮确认。"""

        candidate = _candidate()
        probe = NormalizationReviewService(_HistoryRepository([]))
        fingerprint = probe.candidate_fingerprint_for_candidate(candidate)
        service = NormalizationReviewService(_HistoryRepository([
            _record(fingerprint, "APPROVED", "animal", minute=1),
            _record(fingerprint, "APPROVED", "animals", minute=2),
        ]))

        reference = asyncio.run(
            service.load_history_references([candidate])
        )["candidate-animal"]
        build_result = NormalizationRuleService().build_approved_rules([candidate])

        self.assertTrue(reference["hasCanonicalConflict"])
        self.assertEqual(build_result.rules, ())
        self.assertEqual(candidate["decision"], "PENDING")


class NormalizationHistoryReferenceViewTest(unittest.TestCase):
    """验证历史参考只改变展示，且历史标准词必须由用户再次明确批准。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.candidate = _candidate()
        self.window.set_normalization_candidates([self.candidate])
        self.window.set_normalization_history_load_status("LOADED")
        self.window.set_normalization_history_references({
            "candidate-animal": {
                "totalDecisionCount": 3,
                "latestDecision": "APPROVED",
                "latestApprovedCanonical": "animal",
                "decisionCounts": {"APPROVED": 2, "REJECTED": 1, "SKIPPED": 0},
                "hasDecisionConflict": True,
                "hasCanonicalConflict": False,
                "recentDecisions": [],
            }
        })
        self.application.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_use_history_canonical_only_fills_editor_and_history_filter_works(self):
        """填充历史词不能产生批准动作，冲突筛选也只影响 View 列表。"""

        self.window.ui.normalizationCanonicalLineEdit.setText("different")
        self.window.ui.useHistoryCanonicalButton.click()
        self.assertEqual(
            self.window.ui.normalizationCanonicalLineEdit.text(), "animal"
        )
        self.assertEqual(self.candidate["decision"], "PENDING")
        self.assertIsNone(self.candidate["approvedCanonical"])
        self.window.ui.normalizationHistoryComboBox.setCurrentIndex(
            self.window.ui.normalizationHistoryComboBox.findData("CONFLICT")
        )
        self.application.processEvents()
        self.assertEqual(self.window._normalization_list_model.rowCount(), 1)

    def test_load_failure_keeps_current_candidate_reviewable(self):
        """历史读取失败只给出说明，审核按钮及候选列表仍保持可用。"""

        self.window.set_normalization_history_load_status("LOAD_FAILED")
        self.assertTrue(self.window.ui.approveNormalizationButton.isEnabled())
        self.assertEqual(self.window._normalization_list_model.rowCount(), 1)
        self.assertIn("加载失败", self.window.ui.normalizationHistorySummaryLabel.text())

    def test_stale_generation_reference_cannot_overwrite_new_task(self):
        """任务 B 启动后，任务 A 的异步历史结果必须被 Controller 丢弃。"""

        controller = MainController(self.window, _RuntimeWithDatabase())
        controller._normalization_candidate_generation = 2
        controller._normalization_history_references = {
            "candidate-animal": {"totalDecisionCount": 0}
        }

        controller._on_normalization_history_references_loaded(
            1,
            {"candidate-animal": {"totalDecisionCount": 99}},
        )

        self.assertEqual(
            controller._normalization_history_references[
                "candidate-animal"
            ]["totalDecisionCount"],
            0,
        )

    def test_repository_failure_marks_noncore_history_state_only(self):
        """真实异步入口遇到读取异常时，候选与当前人工审核功能都不退化。"""

        class _FailingHistoryService:
            async def load_history_references(self, _candidates):
                raise RuntimeError("history unavailable")

        controller = MainController(self.window, _RuntimeWithDatabase())
        controller.normalization_review_service = _FailingHistoryService()
        controller._normalization_candidate_generation = 1
        controller._load_normalization_history_references(1, [self.candidate])
        self.application.processEvents()

        self.assertEqual(controller._normalization_history_state, "LOAD_FAILED")
        self.assertTrue(self.window.ui.approveNormalizationButton.isEnabled())
        self.assertEqual(self.window._normalization_list_model.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
