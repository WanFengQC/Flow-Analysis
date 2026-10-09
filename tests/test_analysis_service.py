"""AnalysisService 的关键词级聚合公式与空值规则测试。"""

import unittest

from services.analysis_service import AnalysisService


class AnalysisServiceTest(unittest.TestCase):
    """验证 RAW 计算、公共字段 canonical 规则和关键词聚合结果。"""

    def test_weighted_traffic_example(self):
        """用户给定的 10000 + 1000 示例必须按绝对流量加权聚合。"""

        reversing_results = {
            "A": {
                "items": [
                    {
                        "keywords": "weighted plush",
                        "calculatedWeeklySearches": 10000,
                        "naturalRatio": 0.2,
                        "adRatio": 0.8,
                        "searches": 100000,
                        "clicks": 50000,
                        "impressions": 200000,
                    }
                ]
            },
            "B": {
                "items": [
                    {
                        "keywords": "weighted plush",
                        "calculatedWeeklySearches": 1000,
                        "naturalRatio": 0.8,
                        "adRatio": 0.2,
                        "searches": 100000,
                        "clicks": 50000,
                        "impressions": 200000,
                    }
                ]
            },
        }

        processed_data = AnalysisService().analyze_reversing_results(
            reversing_results,
            "202608",
        )
        result = processed_data["results"][0]

        self.assertEqual(len(processed_data["raw"]), 2)
        self.assertEqual(result["calculatedWeeklySearches"], 11000.0)
        self.assertEqual(result["naturalTraffic"], 2800.0)
        self.assertEqual(result["adTraffic"], 8200.0)
        self.assertAlmostEqual(
            result["naturalRatio"],
            2800 / 11000,
        )
        self.assertAlmostEqual(result["adRatio"], 8200 / 11000)
        self.assertTrue(result["naturalRatioComplete"])
        self.assertTrue(result["adRatioComplete"])
        self.assertEqual(result["sourceAsinCount"], 2)

        # 搜索词公共字段只保留 canonical value，不能跨 ASIN 累加。
        self.assertEqual(result["searches"], 100000)
        self.assertEqual(result["clicks"], 50000)
        self.assertEqual(result["impressions"], 200000)

    def test_missing_ratio_does_not_create_false_weighted_ratio(self):
        """比例缺失时仅保留可知流量，不生成覆盖不完整的整体比例。"""

        reversing_results = {
            "A": {
                "items": [
                    {
                        "keywords": "keyword",
                        "calculatedWeeklySearches": 1000,
                        "naturalRatio": None,
                        "adRatio": 0.7,
                    }
                ]
            }
        }

        processed_data = AnalysisService().analyze_reversing_results(
            reversing_results,
            "202608",
        )
        raw_row = processed_data["raw"][0]
        result = processed_data["results"][0]

        self.assertIsNone(raw_row["naturalTraffic"])
        self.assertEqual(raw_row["adTraffic"], 700.0)
        self.assertIsNone(result["naturalRatio"])
        self.assertFalse(result["naturalRatioComplete"])
        self.assertEqual(result["adRatio"], 0.7)
        self.assertTrue(result["adRatioComplete"])

    def test_months_are_physically_isolated_before_keyword_aggregation(self):
        """同词跨月必须留在各自月份桶内，绝不能产生跨月总值。"""

        reversing_results_by_month = {
            "202608": {
                "A": {
                    "items": [
                        {
                            "keywords": "weighted stuffed animals",
                            "calculatedWeeklySearches": 100,
                            "naturalRatio": 0.2,
                            "adRatio": 0.8,
                        }
                    ]
                },
                "B": {
                    "items": [
                        {
                            "keywords": "weighted stuffed animals",
                            "calculatedWeeklySearches": 50,
                            "naturalRatio": 0.4,
                            "adRatio": 0.6,
                        }
                    ]
                },
            },
            "202607": {
                "A": {
                    "items": [
                        {
                            "keywords": "weighted stuffed animals",
                            "calculatedWeeklySearches": 40,
                            "naturalRatio": 0.5,
                            "adRatio": 0.5,
                        }
                    ]
                }
            },
        }

        processed_data = (
            AnalysisService().analyze_monthly_reversing_results(
                reversing_results_by_month,
                ["202608", "202607"],
            )
        )

        self.assertEqual(
            list(processed_data["raw"]),
            ["202608", "202607"],
        )
        self.assertEqual(
            list(processed_data["results"]),
            ["202608", "202607"],
        )
        self.assertEqual(
            list(processed_data["diagnostics"]),
            ["202608", "202607"],
        )
        self.assertEqual(len(processed_data["raw"]["202608"]), 2)
        self.assertEqual(len(processed_data["raw"]["202607"]), 1)

        august_result = processed_data["results"]["202608"][0]
        july_result = processed_data["results"]["202607"][0]
        self.assertEqual(
            august_result["calculatedWeeklySearches"],
            150.0,
        )
        self.assertEqual(
            july_result["calculatedWeeklySearches"],
            40.0,
        )
        self.assertNotEqual(
            august_result["calculatedWeeklySearches"],
            190.0,
        )
        self.assertEqual(august_result["sourceAsinCount"], 2)
        self.assertEqual(july_result["sourceAsinCount"], 1)

    def test_seed_aliases_do_not_affect_default_tokenization(self):
        """没有人工 APPROVED rule 时，Seed 不能改变任何基础 token。"""

        tokens = AnalysisService.tokenize_keyword(
            "stuff animals gifts toys weight weighting plushies plushie plushy"
        )

        self.assertEqual(
            tokens,
            [
                "stuff",
                "animals",
                "gifts",
                "toys",
                "weight",
                "weighting",
                "plushies",
                "plushie",
                "plushy",
            ],
        )

    def test_phrase_seeds_do_not_affect_default_tokenization(self):
        """没有人工 APPROVED phrase rule 时，Seed 短语必须保持基础切分。"""

        tokens = AnalysisService.tokenize_keyword(
            "black cat body pillow teddy bear emotional support pillow fort"
        )

        self.assertEqual(
            tokens,
            [
                "black",
                "cat",
                "body",
                "pillow",
                "teddy",
                "bear",
                "emotional",
                "support",
                "pillow",
                "fort",
            ],
        )

    def test_repeated_token_counts_frequency_but_not_metrics_twice(self):
        """同一搜索词的重复 token 只影响频次，不重复累加 Weight 或 Total。"""

        results, _ = AnalysisService().analyze_keyword_results(
            [
                {
                    "keywords": "llama llama",
                    "calculatedWeeklySearches": 1000,
                    "clicks": 100,
                    "impressions": 1000,
                    "naturalRatio": 0.25,
                    "adRatio": 0.75,
                }
            ]
        )
        llama = results[0]

        self.assertEqual(llama["frequency"], 2)
        self.assertEqual(llama["matchingKeywordCount"], 1)
        self.assertEqual(llama["weight"], 4300.0)
        self.assertEqual(llama["total"], 100.0)
        self.assertEqual(llama["ratio"], 43.0)
        self.assertEqual(llama["naturalRatio"], 0.25)
        self.assertEqual(llama["adRatio"], 0.75)

    def test_source_asin_stats_follow_unique_token_semantics(self):
        """来源统计按 word/ASIN/搜索词累计，不改变既有词级指标。"""

        reversing_results = {
            "A": {
                "items": [
                    {
                        "keywords": "weighted weighted plush",
                        "calculatedWeeklySearches": 100,
                        "clicks": 10,
                        "impressions": 100,
                        "searches": 1000,
                        "searchesRank": 1200,
                        "naturalRatio": 0.2,
                        "adRatio": 0.8,
                    },
                    {
                        "keywords": "weighted bear",
                        "calculatedWeeklySearches": 50,
                        "clicks": 5,
                        "impressions": 300,
                        "searches": 2000,
                        "searchesRank": 800,
                        "naturalRatio": 0.3,
                        "adRatio": 0.7,
                    },
                ]
            },
            "B": {
                "items": [
                    {
                        "keywords": "weighted dog",
                        "calculatedWeeklySearches": 200,
                        "clicks": 20,
                        "impressions": 400,
                        "searches": 3000,
                        "searchesRank": 1500,
                        "naturalRatio": 0.4,
                        "adRatio": 0.6,
                    }
                ]
            },
        }

        monthly_result = AnalysisService().analyze_reversing_results(
            reversing_results,
            "202608",
        )
        first_keyword_result = monthly_result["results"][0]
        self.assertEqual(
            first_keyword_result["sourceAsinStats"]["A"],
            {
                "exposure": 100.0,
                "clicks": 10.0,
                "impressions": 100.0,
                "searches": 1000.0,
                "abaWeeklyRank": 1200.0,
            },
        )
        weighted = next(
            result
            for result in AnalysisService().analyze_keyword_results(
                monthly_result["results"]
            )[0]
            if result["word"] == "weighted"
        )

        # 同一搜索词中的第二个 weighted 只影响频次，不重复累计来源指标。
        self.assertEqual(weighted["frequency"], 4)
        self.assertEqual(weighted["matchingKeywordCount"], 3)
        self.assertAlmostEqual(weighted["weight"], 895.8333333333)
        self.assertAlmostEqual(weighted["total"], 35.0)
        self.assertEqual(
            weighted["sourceAsinStats"],
            {
                "A": {
                    "exposure": 150.0,
                    "clicks": 15.0,
                    "impressions": 400.0,
                    "searches": 3000.0,
                    "abaWeeklyRank": 800.0,
                },
                "B": {
                    "exposure": 200.0,
                    "clicks": 20.0,
                    "impressions": 400.0,
                    "searches": 3000.0,
                    "abaWeeklyRank": 1500.0,
                },
            },
        )

    def test_source_asin_stats_keep_all_unknown_metrics_as_none(self):
        """缺失来源字段不得被转换为零，以免影响未来代表 ASIN 排序。"""

        results, _ = AnalysisService().analyze_keyword_results(
            [
                {
                    "keywords": "unknown source",
                    "sourceAsinStats": {
                        "B000000001": {
                            "exposure": None,
                            "clicks": None,
                            "impressions": None,
                            "searches": None,
                            "abaWeeklyRank": None,
                        }
                    },
                }
            ]
        )

        unknown = next(result for result in results if result["word"] == "unknown")
        self.assertEqual(
            unknown["sourceAsinStats"]["B000000001"],
            {
                "exposure": None,
                "clicks": None,
                "impressions": None,
                "searches": None,
                "abaWeeklyRank": None,
            },
        )

    def test_weight_uses_row_level_formula(self):
        """Weight 必须先逐搜索词计算，再按包含该词的不同搜索词求和。"""

        results, _ = AnalysisService().analyze_keyword_results(
            [
                {
                    "keywords": "weighted plush",
                    "calculatedWeeklySearches": 1000,
                    "clicks": 100,
                    "impressions": 1000,
                    "naturalRatio": 0.5,
                    "adRatio": 0.5,
                }
            ]
        )

        for result in results:
            self.assertEqual(result["weight"], 4300.0)
            self.assertEqual(result["total"], 100.0)

    def test_zero_total_has_zero_ratio_not_blank(self):
        """真实 Total 为零时，占比必须是数值零而非缺失值。"""

        results, _ = AnalysisService().analyze_keyword_results(
            [
                {
                    "keywords": "zero ratio",
                    "calculatedWeeklySearches": 0,
                    "clicks": 0,
                    "impressions": 100,
                }
            ]
        )

        self.assertTrue(results)
        self.assertTrue(all(row["total"] == 0 for row in results))
        self.assertTrue(all(row["ratio"] == 0 for row in results))

    def test_monthly_word_results_are_physically_isolated(self):
        """同一单词跨月必须分别计算，不能产生跨月词级累计。"""

        processed_data = (
            AnalysisService().analyze_monthly_reversing_results(
                {
                    "202608": {
                        "A": {
                            "items": [
                                {
                                    "keywords": "stuff animals",
                                    "calculatedWeeklySearches": 100,
                                    "clicks": 10,
                                    "impressions": 100,
                                    "naturalRatio": 0.2,
                                    "adRatio": 0.8,
                                }
                            ]
                        }
                    },
                    "202607": {
                        "A": {
                            "items": [
                                {
                                    "keywords": "stuff animals",
                                    "calculatedWeeklySearches": 50,
                                    "clicks": 20,
                                    "impressions": 100,
                                    "naturalRatio": 0.5,
                                    "adRatio": 0.5,
                                }
                            ]
                        }
                    },
                },
                ["202608", "202607"],
            )
        )

        preview_results, _ = AnalysisService().analyze_monthly_word_results(
            processed_data["results"],
            ["202608", "202607"],
            approved_rules=None,
        )
        august_animals = next(
            result
            for result in preview_results["202608"]
            if result["word"] == "animals"
        )
        july_animals = next(
            result
            for result in preview_results["202607"]
            if result["word"] == "animals"
        )
        self.assertEqual(list(preview_results), ["202608", "202607"])
        self.assertEqual(august_animals["total"], 10.0)
        self.assertEqual(july_animals["total"], 20.0)

    def test_asin_word_results_are_built_in_analysis_layer_and_remain_isolated(self):
        """ASIN_result 所需词明细必须在分析阶段生成，不能由导出层伪造。"""

        processed_data = AnalysisService().analyze_monthly_reversing_results(
            {
                "202608": {
                    "B0FIRST": {
                        "items": [
                            {
                                "keywords": "weighted plush",
                                "calculatedWeeklySearches": 100,
                                "clicks": 10,
                                "impressions": 100,
                                "naturalRatio": 0.2,
                                "adRatio": 0.8,
                            }
                        ]
                    },
                    "B0SECOND": {
                        "items": [
                            {
                                "keywords": "soft toy",
                                "calculatedWeeklySearches": 50,
                                "clicks": 5,
                                "impressions": 50,
                                "naturalRatio": 0.6,
                                "adRatio": 0.4,
                            }
                        ]
                    },
                },
                "202607": {
                    "B0FIRST": {
                        "items": [
                            {
                                "keywords": "weighted animal",
                                "calculatedWeeklySearches": 40,
                                "clicks": 4,
                                "impressions": 40,
                                "naturalRatio": 0.5,
                                "adRatio": 0.5,
                            }
                        ]
                    }
                },
            },
            ["202608", "202607"],
        )

        asin_word_results, diagnostics = (
            AnalysisService().analyze_monthly_asin_word_results(
                processed_data["raw"],
                ["202608", "202607"],
            )
        )

        self.assertEqual(list(asin_word_results), ["202608", "202607"])
        self.assertEqual(set(asin_word_results["202608"]), {"B0FIRST", "B0SECOND"})
        self.assertEqual(set(asin_word_results["202607"]), {"B0FIRST"})
        self.assertEqual(
            {row["word"] for row in asin_word_results["202608"]["B0FIRST"]},
            {"weighted", "plush"},
        )
        self.assertEqual(
            {row["word"] for row in asin_word_results["202608"]["B0SECOND"]},
            {"soft", "toy"},
        )
        self.assertEqual(
            {row["word"] for row in asin_word_results["202607"]["B0FIRST"]},
            {"weighted", "animal"},
        )
        self.assertEqual(set(diagnostics["202608"]), {"B0FIRST", "B0SECOND"})


if __name__ == "__main__":
    unittest.main()
