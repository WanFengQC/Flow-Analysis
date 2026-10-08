"""AnalysisWorker 的无归一预览与候选发现顺序测试。"""

import unittest

from workers.analysis_worker import AnalysisWorker


class _AnalysisServiceSpy:
    """记录 RAW/RESULT 与预览词统计的调用顺序。"""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def analyze_monthly_reversing_results(self, _results, months):
        """模拟仅产生 RAW/RESULT 的第一阶段。"""

        self._calls.append("raw_result")
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
        """预览阶段必须明确不使用人工规则。"""

        self._calls.append("preview")
        assert approved_rules is None
        return (
            {month: [] for month in months},
            {month: {} for month in months},
        )

    def analyze_monthly_asin_word_results(
        self,
        _raw,
        months,
        approved_rules=None,
    ):
        """记录 ASIN 级预览发生在候选发现之前。"""

        self._calls.append("asin_preview")
        assert approved_rules is None
        return (
            {month: {} for month in months},
            {month: {} for month in months},
        )


class AnalysisWorkerOrderTest(unittest.TestCase):
    """先生成 Word Analysis 预览，再暂停等待 Word Filter。"""

    def test_unreviewed_preview_precedes_word_filter(self):
        """Worker 只能完成 Word Analysis，不能绕过用户筛选发现候选。"""

        calls: list[str] = []
        worker = AnalysisWorker(
            _AnalysisServiceSpy(calls),
            {"202608": {}},
            ["202608"],
        )
        payloads = []
        stages = []
        worker.stage_changed.connect(stages.append)
        worker.processed.connect(payloads.append)
        worker.run()

        self.assertEqual(
            calls,
            ["raw_result", "preview", "asin_preview"],
        )
        self.assertEqual(
            stages,
            [
                "BUILDING_RESULT",
                "ANALYZING_WORDS",
            ],
        )
        self.assertEqual(payloads[0]["word_preview_results"], {"202608": []})
        self.assertEqual(payloads[0]["asin_word_preview_results"], {"202608": {}})
        self.assertEqual(payloads[0]["word_results"], {})


if __name__ == "__main__":
    unittest.main()
