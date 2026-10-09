"""归一候选人工审核 UI 的 View / Controller 边界测试。"""

import copy
import os
import unittest

# 测试不依赖实际桌面服务，必须在导入 Qt 前固定离屏平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from services.normalization_seeds import WORD_ALIAS_SEEDS
from views.main_window import MainWindow
from views.normalization_candidate_list_model import (
    NormalizationCandidateListModel,
)


class _AsyncRuntimeStub:
    """提供 Controller 初始化所需的最小异步运行时接口。"""

    def submit(self, _coroutine):
        """审核测试不提交任何协程。"""

        raise AssertionError("审核测试不应提交异步任务")


class _ApplicationRuntimeStub:
    """提供 Controller 初始化所需的应用运行时形状。"""

    def __init__(self) -> None:
        self.async_runtime = _AsyncRuntimeStub()


def _candidate(
    candidate_id: str,
    canonical: str,
    variants: list[str],
    *,
    reason_type: str = "KNOWN_WORD_ALIAS_VARIANT",
    impact: float = 100.0,
    frequency: int = 10,
    decision: str = "PENDING",
) -> dict:
    """构造包含审核页所需只读字段的最小候选。"""

    return {
        "id": candidate_id,
        "suggestedCanonical": canonical,
        "variants": variants,
        "variantDetails": [
            {
                "variant": variant,
                "frequency": frequency // len(variants),
                "matchingKeywordCount": 1,
                "impactWeeklyExposure": impact / len(variants),
                "months": ["202608"],
                "monthlyStats": [
                    {
                        "month": "202608",
                        "frequency": frequency // len(variants),
                        "matchingKeywordCount": 1,
                        "impactWeeklyExposure": impact / len(variants),
                    }
                ],
                "evidence": [f"{variant} keyword"],
            }
            for variant in variants
        ],
        "reasonTypes": [reason_type],
        "confidence": 0.95,
        "months": ["202608"],
        "totalFrequency": frequency,
        "matchingKeywordCount": 2,
        "impactWeeklyExposure": impact,
        "evidence": [f"{canonical} keyword"],
        "decision": decision,
    }


class NormalizationReviewTest(unittest.TestCase):
    """验证审核操作只更新当前候选状态，不触碰正式分析规则。"""

    @classmethod
    def setUpClass(cls) -> None:
        """整个模块共享 QApplication，避免重复创建 Qt 应用。"""

        cls._application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """准备带一个跨组重复 variant 的独立候选集合。"""

        self.window = MainWindow()
        self.controller = MainController(
            self.window,
            _ApplicationRuntimeStub(),
        )
        self.candidates = [
            _candidate(
                "candidate-animal",
                "animal",
                ["animal", "animals"],
                impact=300,
                frequency=30,
            ),
            _candidate(
                "candidate-animals",
                "animals",
                ["animals", "animalia"],
                reason_type="COMPACT_SIGNATURE_MATCH",
                impact=200,
                frequency=20,
            ),
            _candidate(
                "candidate-pillowfort",
                "pillowfort",
                ["pillowfort", "pillow fort"],
                reason_type="PHRASE_TOKEN_VARIANT",
                impact=100,
                frequency=10,
            ),
        ]
        self.controller._normalization_candidates = self.candidates
        self.original_word_aliases = copy.deepcopy(WORD_ALIAS_SEEDS)
        self.original_word_results = {"202608": [{"word": "animal"}]}
        self.controller._analysis_word_results = copy.deepcopy(
            self.original_word_results
        )
        self.window.set_normalization_candidates(self.candidates)
        self._application.processEvents()

    def tearDown(self) -> None:
        """关闭窗口并处理延迟删除，避免 Qt 控件跨测试残留。"""

        self.window.close()
        self.window.deleteLater()
        self._application.processEvents()

    def test_approve_preserves_suggestion_and_stores_manual_canonical(self):
        """批准必须只更新 decision/approvedCanonical，不覆写机器建议。"""

        self.window.ui.normalizationCanonicalLineEdit.setText("animals")
        self.window.ui.approveNormalizationButton.click()
        self._application.processEvents()

        candidate = self.candidates[0]
        self.assertEqual(candidate["decision"], "APPROVED")
        self.assertEqual(candidate["approvedCanonical"], "animals")
        self.assertEqual(candidate["suggestedCanonical"], "animal")

    def test_rejected_candidate_can_be_approved_again(self):
        """拒绝后不锁定按钮，仍可重新审核并批准。"""

        self.window.ui.rejectNormalizationButton.click()
        self._application.processEvents()
        self.assertEqual(self.candidates[0]["decision"], "REJECTED")

        self._set_combo_data(
            self.window.ui.normalizationDecisionComboBox,
            "REJECTED",
        )
        self._application.processEvents()
        self.window.ui.normalizationCanonicalLineEdit.setText("animal")
        self.window.ui.approveNormalizationButton.click()
        self._application.processEvents()
        self.assertEqual(self.candidates[0]["decision"], "APPROVED")
        self.assertEqual(self.candidates[0]["approvedCanonical"], "animal")

    def test_search_reason_decision_and_impact_sort(self):
        """View 必须支持按 variant 搜索、类型/状态过滤及影响降序。"""

        self.window.ui.normalizationSearchLineEdit.setText("animalia")
        self._application.processEvents()
        self.assertEqual(self.window._normalization_list_model.rowCount(), 1)
        self.assertEqual(
            self.window._normalization_list_model.index(0, 0).data(
                NormalizationCandidateListModel.CandidateIdRole
            ),
            "candidate-animals",
        )

        self.window.ui.normalizationSearchLineEdit.clear()
        self._set_combo_data(
            self.window.ui.normalizationReasonComboBox,
            "PHRASE_TOKEN_VARIANT",
        )
        self._application.processEvents()
        self.assertEqual(self.window._normalization_list_model.rowCount(), 1)

        self.window.ui.normalizationReasonComboBox.setCurrentIndex(0)
        self.controller._skip_normalization_candidate("candidate-pillowfort")
        self._set_combo_data(
            self.window.ui.normalizationDecisionComboBox,
            "SKIPPED",
        )
        self._application.processEvents()
        self.assertEqual(self.window._normalization_list_model.rowCount(), 1)

        self.window.ui.normalizationDecisionComboBox.setCurrentIndex(0)
        self._set_combo_data(
            self.window.ui.normalizationSortComboBox,
            "impact",
        )
        self._application.processEvents()
        first_id = self.window._normalization_list_model.index(0, 0).data(
            NormalizationCandidateListModel.CandidateIdRole
        )
        self.assertEqual(first_id, "candidate-animal")

    def test_conflict_index_and_review_do_not_modify_aliases_or_word_results(self):
        """跨组索引应找到共享 variant，审核不能触发 alias 或词结果变化。"""

        conflicts = self.window._normalization_conflicts_by_id[
            "candidate-animal"
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["candidateId"], "candidate-animals")

        self.controller._approve_normalization_candidate(
            "candidate-animal",
            "animal",
        )
        self.controller._reject_normalization_candidate("candidate-animals")
        self.controller._skip_normalization_candidate("candidate-pillowfort")

        self.assertEqual(WORD_ALIAS_SEEDS, self.original_word_aliases)
        self.assertEqual(
            self.controller._analysis_word_results,
            self.original_word_results,
        )

    def test_shortcut_wrapper_ignores_text_inputs_but_accepts_list_focus(self):
        """A 快捷键在文本输入时无效，列表焦点下可按正常审核路径批准。"""

        self.window.ui.normalizationCanonicalLineEdit.setText("animal")
        self.window.ui.normalizationCanonicalLineEdit.setFocus()
        self.window._request_normalization_approval_from_shortcut()
        self.assertEqual(self.candidates[0]["decision"], "PENDING")

        self.window.ui.normalizationCandidateListView.setFocus()
        self.window._request_normalization_approval_from_shortcut()
        self._application.processEvents()
        self.assertEqual(self.candidates[0]["decision"], "APPROVED")

    def _set_combo_data(self, combo_box, value: object) -> None:
        """按 itemData 定位固定筛选项，兼容当前 PySide6 的 QComboBox API。"""

        index = combo_box.findData(value)
        self.assertGreaterEqual(index, 0)
        combo_box.setCurrentIndex(index)


if __name__ == "__main__":
    unittest.main()
