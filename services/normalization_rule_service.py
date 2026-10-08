"""将人工 APPROVED 候选编译为可执行归一规则，并阻断歧义关系。"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from models.normalization_rule import (
    ApprovedNormalizationRules,
    NormalizationRule,
    NormalizationRuleConflict,
    NormalizationRuleType,
)


_BASIC_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ApprovedRulesBuildResult:
    """保留审核规则、冲突与链式引用；冲突时不提供可执行规则集。"""

    rules: tuple[NormalizationRule, ...]
    conflicts: tuple[NormalizationRuleConflict, ...]
    chain_references: tuple[NormalizationRuleConflict, ...]
    approved_rules: ApprovedNormalizationRules | None


class NormalizationRuleService:
    """只编译人工决定，不读取 Seed、不自动批准也不递归归一。"""

    def build_approved_rules(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> ApprovedRulesBuildResult:
        """从 APPROVED 且 canonical 非空的候选构建规则并校验冲突。"""

        rules = tuple(
            rule
            for candidate in candidates
            if isinstance(candidate, Mapping)
            if (rule := self._build_rule(candidate)) is not None
        )
        conflicts = self._find_variant_conflicts(rules)
        chain_references = self._find_chain_references(rules)
        # 当前版本将 chain reference 也视为不可执行：A→B、B→C 时若继续
        # 运行，即使不递归，也会留下审核者难以预期的中间 canonical。必须先
        # 由人工消除链关系，才能形成正式规则集。
        approved_rules = (
            None
            if conflicts or chain_references
            else ApprovedNormalizationRules(rules=rules)
        )
        return ApprovedRulesBuildResult(
            rules=rules,
            conflicts=conflicts,
            chain_references=chain_references,
            approved_rules=approved_rules,
        )

    def _build_rule(
        self,
        candidate: Mapping[str, Any],
    ) -> NormalizationRule | None:
        """只接纳明确的人工作出批准和非空 canonical 的候选。"""

        if candidate.get("decision") != "APPROVED":
            return None
        canonical = self._normalize_text(candidate.get("approvedCanonical"))
        candidate_id = candidate.get("id")
        variants = tuple(
            self._normalize_text(variant)
            for variant in candidate.get("variants", [])
            if self._normalize_text(variant)
        )
        if not canonical or not variants or not isinstance(candidate_id, str):
            return None

        reason_types = tuple(
            reason
            for reason in candidate.get("reasonTypes", [])
            if isinstance(reason, str)
        )
        rule_type = self.determine_rule_type(variants, reason_types)
        return NormalizationRule(
            rule_type=rule_type,
            variants=tuple(dict.fromkeys(variants)),
            canonical=canonical,
            source_candidate_id=candidate_id,
            source_reason_types=reason_types,
        )

    @staticmethod
    def determine_rule_type(
        variants: tuple[str, ...],
        reason_types: tuple[str, ...],
    ) -> NormalizationRuleType:
        """统一判断 WORD / PHRASE，供审核持久化与正式规则复用相同语义。"""

        # 规则类型只由实际 variant 的表达形式决定，不能依赖已经废弃的
        # 候选来源。这样历史原因类型也不会影响新规则的类型判断。
        if any(
            len(_BASIC_TOKEN_PATTERN.findall(variant)) > 1
            for variant in variants
        ):
            return NormalizationRuleType.PHRASE
        return NormalizationRuleType.WORD

    def _find_variant_conflicts(
        self,
        rules: tuple[NormalizationRule, ...],
    ) -> tuple[NormalizationRuleConflict, ...]:
        """阻断同一规范 variant 指向不同 canonical 的不可判定规则集。"""

        mappings: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for rule in rules:
            for variant in rule.variants:
                mappings[variant][rule.canonical].add(rule.source_candidate_id)

        return tuple(
            NormalizationRuleConflict(
                conflict_type="VARIANT_CANONICAL_CONFLICT",
                variant=variant,
                canonical_values=tuple(sorted(canonicals)),
                source_candidate_ids=tuple(
                    sorted(
                        candidate_id
                        for source_ids in canonical_sources.values()
                        for candidate_id in source_ids
                    )
                ),
            )
            for variant, canonical_sources in mappings.items()
            if len(canonical_sources) > 1
            for canonicals in [set(canonical_sources)]
        )

    def _find_chain_references(
        self,
        rules: tuple[NormalizationRule, ...],
    ) -> tuple[NormalizationRuleConflict, ...]:
        """报告 A→B、B→C 引用，绝不递归推导 A→C。"""

        variant_to_rules: dict[str, list[NormalizationRule]] = defaultdict(list)
        for rule in rules:
            for variant in rule.variants:
                variant_to_rules[variant].append(rule)

        references = []
        for rule in rules:
            for referenced_rule in variant_to_rules.get(rule.canonical, []):
                if referenced_rule.canonical == rule.canonical:
                    continue
                references.append(
                    NormalizationRuleConflict(
                        conflict_type="CHAIN_REFERENCE",
                        variant=rule.canonical,
                        canonical_values=(rule.canonical, referenced_rule.canonical),
                        source_candidate_ids=(
                            rule.source_candidate_id,
                            referenced_rule.source_candidate_id,
                        ),
                    )
                )
        return tuple(references)

    @staticmethod
    def _normalize_text(value: object) -> str:
        """统一规则文本的大小写与空白，不删改连字符等人工输入边界。"""

        if not isinstance(value, str):
            return ""
        return " ".join(value.lower().split())
