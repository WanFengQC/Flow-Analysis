"""Word Filter 与归一候选入口的 Controller 集成测试。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from controllers.main_controller import AnalysisPipelineState, MainController
from views.main_window import MainWindow


class _AsyncRuntimeStub:
    def submit(self, _coroutine):
        raise AssertionError("本测试不得提交网络或数据库任务")


class _RuntimeStub:
    def __init__(self):
        self.async_runtime = _AsyncRuntimeStub()


class WordFilterControllerTest(unittest.TestCase):
    """筛选后只有通过的词能够用于候选发现。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.controller = MainController(self.window, _RuntimeStub())
        self.controller._analysis_job_active = True
        self.controller._preparation_months = ["202608"]
        self.controller._analysis_results = {
            "202608": [
                {"keywords": "animal animals", "calculatedWeeklySearches": 100},
                {"keywords": "cat cats", "calculatedWeeklySearches": 90},
            ]
        }
        self.controller._analysis_word_results = {
            "202608": [
                {
                    "word": "animal",
                    "frequency": 3,
                    "weight": 100,
                    "total": 20,
                    "matchingKeywordCount": 1,
                    "sourceAsinStats": {"A": {"exposure": 100}},
                },
                {
                    "word": "animals",
                    "frequency": 3,
                    "weight": 100,
                    "total": 20,
                    "matchingKeywordCount": 1,
                    "sourceAsinStats": {"A": {"exposure": 100}},
                },
                {
                    "word": "cat",
                    "frequency": 1,
                    "weight": 20,
                    "total": 5,
                    "matchingKeywordCount": 1,
                    "sourceAsinStats": {"A": {"exposure": 90}},
                },
                {
                    "word": "cats",
                    "frequency": 1,
                    "weight": 20,
                    "total": 5,
                    "matchingKeywordCount": 1,
                    "sourceAsinStats": {"A": {"exposure": 90}},
                },
            ]
        }
        self.controller._analysis_asin_word_results = {"202608": {"A": []}}

    def tearDown(self):
        self.window.close()

    def test_filter_preserves_original_and_limits_candidates(self):
        """频次门槛不回写原始词，cat/cats 也不能进入候选组。"""

        self.window.ui.frequencyMinLineEdit.setText("3")
        self.controller._apply_word_filter()

        self.assertEqual(len(self.controller._analysis_word_results["202608"]), 4)
        self.assertEqual(
            [row["word"] for row in self.controller._filtered_word_results["202608"]],
            ["animal", "animals"],
        )
        candidate_variants = {
            variant
            for candidate in self.controller._normalization_candidates
            for variant in candidate["variants"]
        }
        self.assertNotIn("cat", candidate_variants)
        self.assertNotIn("cats", candidate_variants)
        self.assertEqual(
            self.controller._analysis_pipeline_state,
            AnalysisPipelineState.WAITING_NORMALIZATION_REVIEW,
        )

    def test_ui_exposes_only_original_query_filter_metrics(self):
        """侧栏只保留曝光、ABA、月搜索量和词频四组输入。"""

        conditions = self.window.word_filter_conditions()
        self.assertEqual(
            set(conditions) - {"enabled"},
            {
                "weeklyExposureMin",
                "weeklyExposureMax",
                "abaWeeklyRankMin",
                "abaWeeklyRankMax",
                "monthlySearchesMin",
                "monthlySearchesMax",
                "frequencyMin",
                "frequencyMax",
            },
        )
        self.assertFalse(hasattr(self.window.ui, "weightMinLineEdit"))
        self.assertFalse(hasattr(self.window.ui, "totalMinLineEdit"))
        self.assertFalse(
            hasattr(self.window.ui, "matchingKeywordCountMinLineEdit")
        )


if __name__ == "__main__":
    unittest.main()
