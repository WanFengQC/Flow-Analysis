"""NormalizationCandidateService 的只读候选发现测试。"""

import copy
import unittest

from services.analysis_service import AnalysisService
from services.normalization_candidate_service import (
    NormalizationCandidateService,
)
from services.normalization_seeds import WORD_ALIAS_SEEDS


class NormalizationCandidateServiceTest(unittest.TestCase):
    """验证候选只观察实际关键词，不改变正式归一和统计结果。"""

    def setUp(self):
        """为每个测试提供独立候选服务实例。"""

        self.service = NormalizationCandidateService()

    @staticmethod
    def _result(keywords: str, weekly_exposure: float = 100.0):
        """构造最小搜索词 RESULT，保持测试关注点在候选发现。"""

        return {
            "keywords": keywords,
            "calculatedWeeklySearches": weekly_exposure,
        }

    def test_compact_variants_form_one_pending_group(self):
        """jellycat、jelly cat、jelly-cat 必须合为一个审核候选组。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("jellycat stuffed animal", 100),
                    self._result("jelly cat plush", 80),
                    self._result("jelly-cat pillow", 60),
                ]
            }
        )

        jelly_candidate = next(
            candidate
            for candidate in candidates
            if set(candidate["variants"])
            == {"jellycat", "jelly cat", "jelly-cat"}
        )
        self.assertEqual(jelly_candidate["suggestedCanonical"], "jellycat")
        self.assertEqual(
            jelly_candidate["reasonTypes"],
            ["COMPACT_SIGNATURE_MATCH"],
        )
        self.assertEqual(jelly_candidate["confidence"], 1.0)
        self.assertEqual(jelly_candidate["decision"], "PENDING")

    def test_candidate_discovery_never_mutates_formal_aliases(self):
        """候选服务不得向 Seed 自动写规则。"""

        original_word_aliases = copy.deepcopy(WORD_ALIAS_SEEDS)

        self.service.discover_candidates(
            {
                "202608": [
                    self._result("body pillow"),
                    self._result("body pillows"),
                ]
            }
        )

        self.assertEqual(WORD_ALIAS_SEEDS, original_word_aliases)

    def test_candidate_discovery_does_not_change_word_analysis_input_or_output(self):
        """候选存在不能改变已有正式单词统计的结果。"""

        monthly_results = {
            "202608": [
                {
                    "keywords": "body pillow",
                    "calculatedWeeklySearches": 100,
                    "clicks": 10,
                    "impressions": 100,
                    "naturalRatio": 0.2,
                    "adRatio": 0.8,
                },
                {
                    "keywords": "body pillows",
                    "calculatedWeeklySearches": 50,
                    "clicks": 10,
                    "impressions": 100,
                    "naturalRatio": 0.2,
                    "adRatio": 0.8,
                },
            ]
        }
        input_snapshot = copy.deepcopy(monthly_results)
        analysis_service = AnalysisService()
        before_results, _ = analysis_service.analyze_keyword_results(
            monthly_results["202608"]
        )

        self.service.discover_candidates(monthly_results)
        after_results, _ = analysis_service.analyze_keyword_results(
            monthly_results["202608"]
        )

        self.assertEqual(monthly_results, input_snapshot)
        self.assertEqual(before_results, after_results)

    def test_unrelated_prefix_words_do_not_form_character_candidate(self):
        """cat 与 caterpillar 不能仅因共享前缀被错误聚为候选。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("cat plush"),
                    self._result("caterpillar plush"),
                ]
            }
        )

        self.assertFalse(
            any(
                {"cat", "caterpillar"}.issubset(candidate["variants"])
                for candidate in candidates
            )
        )

    def test_plush_forms_are_not_automatically_approved_or_mutated(self):
        """plush、plushie、plushy 不会被候选服务写成正式同义词。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("plush toy"),
                    self._result("plushie toy"),
                    self._result("plushy toy"),
                ]
            }
        )

        plush_groups = [
            candidate
            for candidate in candidates
            if len({"plush", "plushie", "plushy"} & set(candidate["variants"]))
            >= 2
        ]
        self.assertFalse(plush_groups)

    def test_phrase_variants_are_pending_candidates_not_formal_rules(self):
        """实际 body pillow/body pillows 与黑猫变体仅生成 PENDING 候选。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("body pillow", 200),
                    self._result("body pillows", 150),
                    self._result("black cat", 100),
                    self._result("black cats", 80),
                ]
            }
        )

        body_candidate = next(
            candidate
            for candidate in candidates
            if set(candidate["variants"])
            == {"body pillow", "body pillows"}
        )
        black_candidate = next(
            candidate
            for candidate in candidates
            if set(candidate["variants"])
            == {"black cat", "black cats"}
        )
        self.assertEqual(body_candidate["suggestedCanonical"], "body pillow")
        self.assertEqual(body_candidate["decision"], "PENDING")
        self.assertIn("PHRASE_TOKEN_VARIANT", body_candidate["reasonTypes"])
        self.assertEqual(black_candidate["suggestedCanonical"], "black cat")
        self.assertEqual(black_candidate["decision"], "PENDING")

    def test_phrase_candidates_require_complete_observed_expressions(self):
        """完整表达可候选，长关键词的残缺前缀和中间切片不得入选。"""

        original_word_aliases = copy.deepcopy(WORD_ALIAS_SEEDS)
        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("body pillow", 200),
                    self._result("body pillows", 150),
                    self._result("stuffed animal", 120),
                    self._result("stuffed animals", 110),
                    self._result(
                        "weighted stuffed animals for adults",
                        100,
                    ),
                    self._result("weighted stuffed animals cow", 90),
                ]
            }
        )

        phrase_groups = {
            frozenset(candidate["variants"])
            for candidate in candidates
            if "PHRASE_TOKEN_VARIANT" in candidate["reasonTypes"]
        }
        self.assertIn(
            frozenset({"body pillow", "body pillows"}),
            phrase_groups,
        )
        self.assertIn(
            frozenset({"stuffed animal", "stuffed animals"}),
            phrase_groups,
        )
        self.assertFalse(
            any(
                {
                    "animals for",
                    "weighted stuffed",
                    "weighted stuffed animals for",
                    "stuffed animals for",
                }
                & group
                for group in phrase_groups
            )
        )
        self.assertNotIn(
            frozenset(
                {
                    "weighted stuffed animals for",
                    "weighted stuffed animals cow",
                }
            ),
            phrase_groups,
        )
        self.assertTrue(
            all(candidate["decision"] == "PENDING" for candidate in candidates)
        )
        self.assertEqual(WORD_ALIAS_SEEDS, original_word_aliases)

    def test_zero_impact_phrase_candidate_is_not_emitted(self):
        """无周曝光且无强证据的 PHRASE 候选不应占用审核队列。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("body pillow", 0),
                    self._result("body pillows", 0),
                ]
            }
        )

        self.assertFalse(
            any(
                set(candidate["variants"])
                == {"body pillow", "body pillows"}
                for candidate in candidates
            )
        )

    def test_candidate_includes_read_only_variant_review_evidence(self):
        """审核展示字段必须按 variant 分开，但不能改变候选判断或 alias。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("animal", 100),
                    self._result("animals", 50),
                ],
                "202607": [
                    self._result("animal", 80),
                ],
            }
        )
        candidate = next(
            item
            for item in candidates
            if set(item["variants"]) == {"animal", "animals"}
        )
        details = {
            detail["variant"]: detail
            for detail in candidate["variantDetails"]
        }

        self.assertEqual(details["animal"]["frequency"], 2)
        self.assertEqual(
            details["animal"]["matchingKeywordCount"],
            2,
        )
        self.assertEqual(
            details["animal"]["impactWeeklyExposure"],
            180.0,
        )
        self.assertEqual(
            details["animal"]["monthlyStats"][0]["month"],
            "202608",
        )
        self.assertEqual(
            details["animal"]["evidence"],
            ["animal", "animal"],
        )
        self.assertEqual(candidate["decision"], "PENDING")

    def test_observed_phrase_no_longer_creates_concept_seed_candidate(self):
        """单独出现的黑猫短语不能再由已删除的 Seed 产生概念候选。"""

        candidates = self.service.discover_candidates(
            {"202608": [self._result("black cat toy", 100)]}
        )
        self.assertFalse(
            any(
                item["variants"] == ["black cat"]
                or "PHRASE_CONCEPT_SEED" in item["reasonTypes"]
                for item in candidates
            )
        )

    def test_strict_singular_plural_accepts_only_forward_regular_forms(self):
        """严格单复数只接受可正向生成的 +s、+es 和辅音 y→ies。"""

        is_pair = self.service.is_strict_singular_plural
        for singular, plural in (
            ("animal", "animals"),
            ("box", "boxes"),
            ("wish", "wishes"),
            ("watch", "watches"),
            ("baby", "babies"),
            ("toy", "toys"),
            ("plushie", "plushies"),
        ):
            self.assertTrue(is_pair(singular, plural))
            self.assertTrue(is_pair(plural, singular))

    def test_strict_singular_plural_rejects_stems_and_non_plural_forms(self):
        """不允许以删尾、词干或旧 Word Seed 猜测单复数。"""

        is_pair = self.service.is_strict_singular_plural
        for left, right in (
            ("plush", "plushies"),
            ("stress", "stres"),
            ("class", "clas"),
            ("glass", "glas"),
            ("stuff", "stuffed"),
            ("weight", "weighted"),
        ):
            self.assertFalse(is_pair(left, right))

    def test_phrase_variants_allow_multiple_strict_plural_positions(self):
        """完整 phrase 可在多个 token 位置同时发生严格单复数变化。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("stuffed animal for adult", 100),
                    self._result("stuffed animals for adults", 90),
                    self._result("weighted stuffed animal", 80),
                    self._result("weighted stuffed animals", 70),
                    self._result("duck stuffed animal", 60),
                    self._result("duck stuffed animals", 50),
                ]
            }
        )
        phrase_groups = {
            frozenset(candidate["variants"])
            for candidate in candidates
            if "PHRASE_TOKEN_VARIANT" in candidate["reasonTypes"]
        }
        self.assertIn(
            frozenset({"stuffed animal for adult", "stuffed animals for adults"}),
            phrase_groups,
        )
        self.assertIn(
            frozenset({"weighted stuffed animal", "weighted stuffed animals"}),
            phrase_groups,
        )
        self.assertIn(
            frozenset({"duck stuffed animal", "duck stuffed animals"}),
            phrase_groups,
        )

    def test_word_alias_seeds_cannot_create_phrase_token_candidates(self):
        """派生词 Seed 只服务独立 WORD 候选，不能推导短语等价。"""

        forbidden_pairs = (
            ("weight stuffed animal", "weighted stuffed animal"),
            ("stuff animal", "stuffed animal"),
            ("weighted stuff animals", "weighted stuffed animals"),
            ("weighted plush", "weighted plushies"),
            ("weighting stuffed animal", "weighted stuffed animal"),
        )
        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result(phrase, 100 - index)
                    for index, pair in enumerate(forbidden_pairs)
                    for phrase in pair
                ]
            }
        )
        phrase_groups = {
            frozenset(candidate["variants"])
            for candidate in candidates
            if "PHRASE_TOKEN_VARIANT" in candidate["reasonTypes"]
        }
        self.assertTrue(
            all(frozenset(pair) not in phrase_groups for pair in forbidden_pairs)
        )

    def test_allowed_words_exclude_filtered_terms_from_candidates(self):
        """Word Filter 白名单不能让未通过词重新进入候选组。"""

        candidates = self.service.discover_candidates(
            {
                "202608": [
                    self._result("animal animals"),
                    self._result("cat cats"),
                ]
            },
            allowed_words_by_month={"202608": {"animal", "animals"}},
        )

        flattened_variants = {
            variant for candidate in candidates for variant in candidate["variants"]
        }
        self.assertIn("animal", flattened_variants)
        self.assertIn("animals", flattened_variants)
        self.assertNotIn("cat", flattened_variants)
        self.assertNotIn("cats", flattened_variants)


if __name__ == "__main__":
    unittest.main()
