"""人工 APPROVED 候选到正式归一规则的编译与执行测试。"""

import unittest

from models.normalization_rule import (
    ApprovedNormalizationRules,
    NormalizationRule,
    NormalizationRuleType,
)
from services.analysis_service import AnalysisService
from services.normalization_rule_service import NormalizationRuleService


def _candidate(
    candidate_id: str,
    variants: list[str],
    canonical: str,
    *,
    decision: str = "APPROVED",
    reason_types: list[str] | None = None,
) -> dict:
    """构造最小审核候选，测试只聚焦规则编译语义。"""

    return {
        "id": candidate_id,
        "variants": variants,
        "approvedCanonical": canonical,
        "decision": decision,
        "reasonTypes": reason_types or ["KNOWN_WORD_ALIAS_VARIANT"],
    }


class NormalizationRuleServiceTest(unittest.TestCase):
    """验证只有明确人工 APPROVED 才能改变正式 tokenization。"""

    def setUp(self) -> None:
        """每个测试使用新的规则编译器，避免候选状态互相污染。"""

        self.service = NormalizationRuleService()

    def test_approved_word_candidate_builds_rule_and_then_applies(self):
        """animals 仅在人工批准并显式传入规则后才变为 animal。"""

        result = self.service.build_approved_rules(
            [_candidate("animal-rule", ["animal", "animals"], "animal")]
        )
        self.assertFalse(result.conflicts)
        self.assertIsNotNone(result.approved_rules)
        self.assertEqual(
            AnalysisService.tokenize_keyword("animals"),
            ["animals"],
        )
        self.assertEqual(
            AnalysisService.tokenize_keyword(
                "animals",
                approved_rules=result.approved_rules,
            ),
            ["animal"],
        )

    def test_phrase_rule_is_pending_until_human_approval(self):
        """完整 phrase 变体未批准时不能折叠为整体 token。"""

        pending = _candidate(
            "black-cats",
            ["black cat", "black cats"],
            "black cat",
            decision="PENDING",
            reason_types=["PHRASE_TOKEN_VARIANT"],
        )
        pending_result = self.service.build_approved_rules([pending])
        self.assertEqual(pending_result.rules, ())
        self.assertEqual(
            AnalysisService.tokenize_keyword("black cats toy"),
            ["black", "cats", "toy"],
        )

        pending["decision"] = "APPROVED"
        approved_result = self.service.build_approved_rules([pending])
        self.assertEqual(
            approved_result.rules[0].rule_type,
            NormalizationRuleType.PHRASE,
        )
        self.assertEqual(
            AnalysisService.tokenize_keyword(
                "black cats toy",
                approved_rules=approved_result.approved_rules,
            ),
            ["black cat", "toy"],
        )

    def test_rejected_and_skipped_candidates_do_not_build_rules(self):
        """REJECTED 与 SKIPPED 仅代表审核状态，均不能进入可执行规则集。"""

        result = self.service.build_approved_rules(
            [
                _candidate(
                    "rejected",
                    ["black cat", "black cats"],
                    "black cat",
                    decision="REJECTED",
                    reason_types=["PHRASE_TOKEN_VARIANT"],
                ),
                _candidate(
                    "skipped",
                    ["animal", "animals"],
                    "animal",
                    decision="SKIPPED",
                ),
            ]
        )
        self.assertEqual(result.rules, ())
        self.assertEqual(result.approved_rules.rules, ())

    def test_variant_different_canonical_conflict_blocks_execution(self):
        """同一 variant 指向不同 canonical 时不得静默选择其一。"""

        result = self.service.build_approved_rules(
            [
                _candidate("first", ["animals"], "animal"),
                _candidate("second", ["animals"], "animals-general"),
            ]
        )
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(
            result.conflicts[0].conflict_type,
            "VARIANT_CANONICAL_CONFLICT",
        )
        self.assertIsNone(result.approved_rules)

    def test_chain_reference_blocks_executable_rules(self):
        """A→B 与 B→C 是不可执行链，不能给出可执行规则或推导 A→C。"""

        result = self.service.build_approved_rules(
            [
                _candidate("first", ["a"], "b"),
                _candidate("second", ["b"], "c"),
            ]
        )
        self.assertEqual(len(result.chain_references), 1)
        self.assertIsNone(result.approved_rules)

    def test_phrase_rules_are_longest_first_and_run_before_word_rules(self):
        """批准短语必须先保护整体 token，再处理剩余独立单词。"""

        rules = ApprovedNormalizationRules(
            rules=(
                NormalizationRule(
                    rule_type=NormalizationRuleType.PHRASE,
                    variants=("stuffed animal", "stuffed animals"),
                    canonical="stuffed-animal",
                    source_candidate_id="phrase-short",
                    source_reason_types=("PHRASE_TOKEN_VARIANT",),
                ),
                NormalizationRule(
                    rule_type=NormalizationRuleType.PHRASE,
                    variants=(
                        "weighted stuffed animal",
                        "weighted stuffed animals",
                    ),
                    canonical="weighted-stuffed-animal",
                    source_candidate_id="phrase-long",
                    source_reason_types=("PHRASE_TOKEN_VARIANT",),
                ),
                NormalizationRule(
                    rule_type=NormalizationRuleType.WORD,
                    variants=("animal", "animals"),
                    canonical="animal",
                    source_candidate_id="word",
                    source_reason_types=("KNOWN_WORD_ALIAS_VARIANT",),
                ),
            )
        )
        self.assertEqual(
            AnalysisService.tokenize_keyword(
                "weighted stuffed animal",
                approved_rules=rules,
            ),
            ["weighted-stuffed-animal"],
        )
        self.assertEqual(
            AnalysisService.tokenize_keyword(
                "stuffed animal",
                approved_rules=rules,
            ),
            ["stuffed-animal"],
        )

    def test_manual_approved_canonical_overrides_machine_suggestion(self):
        """正式 rule 只能采用 approvedCanonical，绝不回退到机器建议。"""

        candidate = _candidate(
            "stuffed-animal",
            ["stuffed animal", "stuffed animals"],
            "stuffed animal",
            reason_types=["PHRASE_TOKEN_VARIANT"],
        )
        candidate["suggestedCanonical"] = "stuff animal"
        result = self.service.build_approved_rules([candidate])

        self.assertEqual(result.rules[0].canonical, "stuffed animal")

    def test_approved_phrase_rule_never_creates_implicit_word_rule(self):
        """批准短语只生成短语映射，内部 animal/animals 仍需独立审核。"""

        phrase_result = self.service.build_approved_rules(
            [
                _candidate(
                    "phrase",
                    ["stuffed animal", "stuffed animals"],
                    "stuffed animal",
                    reason_types=["PHRASE_TOKEN_VARIANT"],
                )
            ]
        )
        self.assertIsNotNone(phrase_result.approved_rules)
        self.assertEqual(
            tuple(rule.rule_type for rule in phrase_result.rules),
            (NormalizationRuleType.PHRASE,),
        )
        self.assertEqual(
            AnalysisService.tokenize_keyword(
                "animals",
                approved_rules=phrase_result.approved_rules,
            ),
            ["animals"],
        )

        word_result = self.service.build_approved_rules(
            [
                _candidate(
                    "word",
                    ["animal", "animals"],
                    "animal",
                )
            ]
        )
        self.assertEqual(
            word_result.rules[0].rule_type,
            NormalizationRuleType.WORD,
        )


if __name__ == "__main__":
    unittest.main()
