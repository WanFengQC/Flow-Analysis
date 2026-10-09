"""Word Filter 的独立筛选、保留原始结果与月份隔离测试。"""

import unittest

from services.word_filter_service import WordFilterService


class WordFilterServiceTest(unittest.TestCase):
    """验证过滤不会修改原始 Word Result，也不会跨月混合。"""

    def setUp(self) -> None:
        self.service = WordFilterService()
        self.source = {
            "202608": [
                {
                    "word": "weighted",
                    "frequency": 3,
                    "sourceAsinStats": {
                        "A": {
                            "exposure": 80.0,
                            "abaWeeklyRank": 120.0,
                            "searches": 2000.0,
                        }
                    },
                },
                {
                    "word": "toy",
                    "frequency": 1,
                    "sourceAsinStats": {
                        "A": {
                            "exposure": 10.0,
                            "abaWeeklyRank": 90.0,
                            "searches": 3000.0,
                        }
                    },
                },
            ],
            "202607": [
                {
                    "word": "animal",
                    "frequency": 4,
                    "sourceAsinStats": {
                        "B": {
                            "exposure": 90.0,
                            "abaWeeklyRank": 300.0,
                            "searches": 1800.0,
                        }
                    },
                }
            ],
        }

    def test_and_conditions_filter_without_mutating_original(self) -> None:
        """多个上下限必须 AND，且筛选快照不能回写原始列表。"""

        filtered, diagnostics = self.service.filter_monthly_word_results(
            self.source,
            {
                "enabled": True,
                "frequencyMin": 2,
                "weeklyExposureMin": 50,
                "weeklyExposureMax": "",
                "abaWeeklyRankMax": 200,
                "monthlySearchesMin": 1000,
            },
        )

        self.assertEqual([row["word"] for row in filtered["202608"]], ["weighted"])
        self.assertEqual(filtered["202607"], [])
        self.assertEqual(diagnostics["total"], 3)
        self.assertEqual(diagnostics["filtered"], 1)
        self.assertEqual(len(self.source["202608"]), 2)
        filtered["202608"][0]["word"] = "changed"
        self.assertEqual(self.source["202608"][0]["word"], "weighted")

    def test_disabled_filter_retains_all_months(self) -> None:
        """未启用时仍创建独立快照，不跨月重排或合并。"""

        filtered, diagnostics = self.service.filter_monthly_word_results(
            self.source,
            {"enabled": False},
        )

        self.assertEqual(list(filtered), ["202608", "202607"])
        self.assertEqual(diagnostics["filtered"], 3)
        self.assertIsNot(filtered["202608"][0], self.source["202608"][0])

    def test_invalid_range_is_rejected(self) -> None:
        """错误边界必须显式失败，不能静默生成不可理解的空筛选。"""

        with self.assertRaises(ValueError):
            self.service.filter_monthly_word_results(
                self.source,
                {"enabled": True, "frequencyMin": 5, "frequencyMax": 1},
            )


if __name__ == "__main__":
    unittest.main()
