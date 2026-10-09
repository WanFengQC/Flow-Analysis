"""人工归一审核决策持久化的 Service / Controller 边界测试。"""

import asyncio
import os
from concurrent.futures import Future
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from models.normalization_review_decision import NormalizationReviewDecision
from repositories.exceptions import DatabaseUnavailableError
from services.normalization_review_service import NormalizationReviewService
from views.main_window import MainWindow


class _MemoryReviewRepository:
    """以 append-only 列表模拟 Repository，测试不依赖真实 PostgreSQL。"""

    def __init__(self) -> None:
        self.records = []

    async def save_decision(self, record):
        """保留写入顺序，模拟数据库 INSERT RETURNING。"""

        self.records.append(record)
        return record

    async def get_latest_decision_by_fingerprint(self, fingerprint):
        """模拟 created_at / id 降序语义下最后插入的同 fingerprint 决策。"""

        matches = [
            record
            for record in self.records
            if record.candidate_fingerprint == fingerprint
        ]
        return matches[-1] if matches else None


class _FailingAsyncRuntime:
    """返回失败 Future，模拟 AsyncRuntime 中的数据库写入错误。"""

    def submit(self, coroutine):
        """关闭未运行协程并立即返回异常，避免测试产生协程泄漏。"""

        coroutine.close()
        future = Future()
        future.set_exception(DatabaseUnavailableError("数据库不可用"))
        return future


class _PendingAsyncRuntime:
    """返回未完成 Future，验证 Controller 发起保存后不阻塞 UI 线程。"""

    def __init__(self) -> None:
        self.future = Future()

    def submit(self, coroutine):
        """本测试只验证非阻塞提交，因此关闭协程并保留 pending Future。"""

        coroutine.close()
        return self.future


class _RuntimeWithDatabase:
    """提供 Controller 所需的数据库占位与可替换 AsyncRuntime。"""

    def __init__(self, async_runtime) -> None:
        self.database = object()
        self.async_runtime = async_runtime


def _candidate(
    *,
    variants: list[str] | None = None,
    decision: str = "APPROVED",
    approved_canonical: str | None = "animal",
    reason_types: list[str] | None = None,
    candidate_id: str = "candidate-animal",
) -> dict:
    """构造候选，动态统计字段只用于 fingerprint 不受影响的回归验证。"""

    return {
        "id": candidate_id,
        "variants": variants or ["animal", "animals"],
        "decision": decision,
        "suggestedCanonical": "animal",
        "approvedCanonical": approved_canonical,
        "reasonTypes": reason_types or ["KNOWN_WORD_ALIAS_VARIANT"],
        "months": ["202608"],
        "totalFrequency": 11,
        "matchingKeywordCount": 7,
        "impactWeeklyExposure": 123.0,
        "evidence": [{"keywords": "animals"}],
    }


class NormalizationReviewPersistenceServiceTest(unittest.TestCase):
    """验证 fingerprint 与审计事件构建不混入运行时动态数据。"""

    def setUp(self) -> None:
        """每个测试隔离内存审计仓库。"""

        self.repository = _MemoryReviewRepository()
        self.service = NormalizationReviewService(self.repository)

    def test_variant_order_and_dynamic_data_do_not_change_fingerprint(self) -> None:
        """variant 顺序、月份、频次、影响与证据均不能改变稳定 identity。"""

        first = _candidate(variants=["animal", "animals"])
        second = _candidate(variants=["animals", "animal"])
        second.update(
            {
                "months": ["202607", "202606"],
                "totalFrequency": 999,
                "impactWeeklyExposure": 999999.0,
                "evidence": [{"keywords": "different"}],
                "id": "different-runtime-id",
            }
        )

        first_record = asyncio.run(
            self.service.persist_candidate_decision(
                first,
                NormalizationReviewDecision.APPROVED,
                {},
                1,
            )
        )
        second_record = asyncio.run(
            self.service.persist_candidate_decision(
                second,
                NormalizationReviewDecision.APPROVED,
                {},
                2,
            )
        )

        self.assertEqual(
            first_record.candidate_fingerprint,
            second_record.candidate_fingerprint,
        )

    def test_variant_identity_keeps_spaces_and_hyphens(self) -> None:
        """fingerprint 不得 compact 化 pillowfort、pillow fort 与 pillow-fort。"""

        compact = _candidate(variants=["pillowfort"])
        spaced = _candidate(variants=["pillow fort"])
        hyphenated = _candidate(variants=["pillow-fort"])
        fingerprints = {
            asyncio.run(
                self.service.persist_candidate_decision(
                    candidate,
                    NormalizationReviewDecision.REJECTED,
                    {},
                    1,
                )
            ).candidate_fingerprint
            for candidate in (compact, spaced, hyphenated)
        }

        self.assertEqual(len(fingerprints), 3)

    def test_legacy_phrase_concept_keeps_historical_fingerprint_compatibility(self) -> None:
        """旧版单短语记录仍按原 fingerprint 语义读取，不影响新候选路径。"""

        first = _candidate(
            variants=["black cat"],
            reason_types=["PHRASE_CONCEPT_SEED"],
        )
        first["suggestedCanonical"] = "black-cat"
        second = dict(first)
        second["suggestedCanonical"] = "blackcat"

        first_record = asyncio.run(
            self.service.persist_candidate_decision(
                first,
                NormalizationReviewDecision.APPROVED,
                {},
                1,
            )
        )
        second_record = asyncio.run(
            self.service.persist_candidate_decision(
                second,
                NormalizationReviewDecision.APPROVED,
                {},
                2,
            )
        )

        self.assertNotEqual(
            first_record.candidate_fingerprint,
            second_record.candidate_fingerprint,
        )

    def test_append_only_records_and_latest_decision(self) -> None:
        """同一候选先拒绝后批准必须保留两条历史，最新记录为 APPROVED。"""

        candidate = _candidate(decision="REJECTED", approved_canonical=None)
        rejected = asyncio.run(
            self.service.persist_candidate_decision(
                candidate,
                NormalizationReviewDecision.REJECTED,
                {"market": "COM"},
                1,
            )
        )
        candidate["decision"] = "APPROVED"
        candidate["approvedCanonical"] = "animal"
        approved = asyncio.run(
            self.service.persist_candidate_decision(
                candidate,
                NormalizationReviewDecision.APPROVED,
                {"market": "COM"},
                2,
            )
        )
        latest = asyncio.run(
            self.repository.get_latest_decision_by_fingerprint(
                rejected.candidate_fingerprint
            )
        )

        self.assertEqual(len(self.repository.records), 2)
        self.assertIsNone(rejected.approved_canonical)
        self.assertEqual(approved.approved_canonical, "animal")
        self.assertEqual(latest.decision, NormalizationReviewDecision.APPROVED)

    def test_approved_requires_canonical_and_context_is_whitelisted(self) -> None:
        """APPROVED 空 canonical 必须失败，context 也绝不能携带认证字段。"""

        with self.assertRaises(ValueError):
            asyncio.run(
                self.service.persist_candidate_decision(
                    _candidate(approved_canonical=" "),
                    NormalizationReviewDecision.APPROVED,
                    {},
                    1,
                )
            )

        record = asyncio.run(
            self.service.persist_candidate_decision(
                _candidate(decision="SKIPPED", approved_canonical=None),
                NormalizationReviewDecision.SKIPPED,
                {
                    "market": "COM",
                    "analysisMonths": ["202608"],
                    "Cookie": "forbidden",
                    "Authorization": "forbidden",
                    "headers": {"Cookie": "forbidden"},
                },
                3,
            )
        )
        self.assertIsNone(record.approved_canonical)
        self.assertEqual(record.context_snapshot, {"market": "COM", "analysisMonths": ["202608"]})


class NormalizationReviewPersistenceControllerTest(unittest.TestCase):
    """验证保存失败或在途时，当前内存审核决定仍然保持。"""

    @classmethod
    def setUpClass(cls) -> None:
        """整个模块复用一个 QApplication。"""

        cls._application = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        """释放每个测试创建的窗口。"""

        self.window.close()
        self.window.deleteLater()
        self._application.processEvents()

    def test_database_failure_does_not_roll_back_memory_decision(self) -> None:
        """数据库失败时 candidate 保持 APPROVED，保存状态转为 SAVE_FAILED。"""

        self.window = MainWindow()
        controller = MainController(
            self.window,
            _RuntimeWithDatabase(_FailingAsyncRuntime()),
        )
        candidate = _candidate(decision="PENDING", approved_canonical=None)
        controller._normalization_candidates = [candidate]
        self.window.set_normalization_candidates([candidate])

        controller._approve_normalization_candidate("candidate-animal", "animal")
        self._application.processEvents()

        self.assertEqual(candidate["decision"], "APPROVED")
        self.assertEqual(candidate["approvedCanonical"], "animal")
        self.assertEqual(controller._normalization_persistence_state, "SAVE_FAILED")
        self.assertFalse(
            self.window.ui.retryNormalizationPersistenceButton.isHidden()
        )

    def test_submit_returns_without_waiting_for_database_future(self) -> None:
        """审核动作只提交 AsyncRuntime Future，主线程立即回到可交互状态。"""

        async_runtime = _PendingAsyncRuntime()
        self.window = MainWindow()
        controller = MainController(
            self.window,
            _RuntimeWithDatabase(async_runtime),
        )
        candidate = _candidate(decision="PENDING", approved_canonical=None)
        controller._normalization_candidates = [candidate]
        self.window.set_normalization_candidates([candidate])

        controller._approve_normalization_candidate("candidate-animal", "animal")

        self.assertEqual(candidate["decision"], "APPROVED")
        self.assertEqual(controller._normalization_persistence_state, "SAVING")
        self.assertFalse(async_runtime.future.done())


if __name__ == "__main__":
    unittest.main()
