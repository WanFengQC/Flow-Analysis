"""将人工审核动作转换为可审计的 PostgreSQL 决策事件。"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from models.normalization_review_decision import (
    NormalizationReviewDecision,
    NormalizationReviewDecisionRecord,
)
from models.normalization_rule import NormalizationRuleType
from repositories.normalization_review_repository import (
    NormalizationReviewRepository,
)
from services.normalization_rule_service import NormalizationRuleService


class NormalizationReviewService:
    """只保存明确人工决定；不读取历史记录影响当前候选或正式归一。"""

    # 仅为既有审计记录保留其历史 fingerprint 语义。新的候选发现已不再
    # 生成该 reason type，因此新候选不会走这个兼容分支。
    _LEGACY_PHRASE_CONCEPT_REASON = "PHRASE_CONCEPT_SEED"
    _ALLOWED_CONTEXT_FIELDS = {
        "market",
        "station",
        "relationQueryMonth",
        "analysisMonths",
        "sourceKeywordOrAsin",
        "selectedAsins",
    }

    def __init__(
        self,
        repository: NormalizationReviewRepository,
    ) -> None:
        """注入既有 DatabaseManager 支持的 Repository，不创建连接池。"""

        self._repository = repository

    async def persist_candidate_decision(
        self,
        candidate: Mapping[str, Any],
        decision: NormalizationReviewDecision,
        context_snapshot: Mapping[str, Any],
        normalization_revision: int,
    ) -> NormalizationReviewDecisionRecord:
        """构建一条 append-only 人工事件并写入 Repository。"""

        if decision not in {
            NormalizationReviewDecision.APPROVED,
            NormalizationReviewDecision.REJECTED,
            NormalizationReviewDecision.SKIPPED,
        }:
            raise ValueError("仅明确人工审核决定可以持久化")
        if normalization_revision < 0:
            raise ValueError("normalization_revision 不能小于 0")

        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id 不能为空")

        variants = self._original_variants(candidate.get("variants"))
        normalized_variants = self._normalized_variants(variants)
        reason_types = self._reason_types(candidate.get("reasonTypes"))
        rule_type = NormalizationRuleService.determine_rule_type(
            normalized_variants,
            reason_types,
        )
        suggested_canonical = self._optional_text(
            candidate.get("suggestedCanonical")
        )
        approved_canonical = (
            self._required_text(candidate.get("approvedCanonical"))
            if decision == NormalizationReviewDecision.APPROVED
            else None
        )
        candidate_fingerprint = self.candidate_fingerprint(
            rule_type=rule_type,
            normalized_variants=normalized_variants,
            reason_types=reason_types,
            suggested_canonical=suggested_canonical,
        )
        record = NormalizationReviewDecisionRecord(
            id=uuid4(),
            candidate_fingerprint=candidate_fingerprint,
            candidate_id=candidate_id.strip(),
            decision=decision,
            rule_type=rule_type,
            variants=variants,
            suggested_canonical=suggested_canonical,
            approved_canonical=approved_canonical,
            reason_types=reason_types,
            context_snapshot=self.context_snapshot(context_snapshot),
            normalization_revision=normalization_revision,
        )
        return await self._repository.save_decision(record)

    async def load_history_references(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """批量加载只读历史参考，绝不改写当前候选或审核决定。"""

        candidate_fingerprints: dict[str, str] = {}
        for candidate in candidates:
            candidate_id = candidate.get("id")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                continue
            candidate_fingerprints[candidate_id] = self.candidate_fingerprint_for_candidate(
                candidate
            )

        records_by_fingerprint = await self._repository.list_decisions_by_fingerprints(
            tuple(candidate_fingerprints.values())
        )
        return {
            candidate_id: self._history_reference(
                candidate_id,
                fingerprint,
                records_by_fingerprint.get(fingerprint, []),
            )
            for candidate_id, fingerprint in candidate_fingerprints.items()
        }

    @classmethod
    def candidate_fingerprint_for_candidate(
        cls,
        candidate: Mapping[str, Any],
    ) -> str:
        """复用审计写入的稳定 identity 规则，保证历史精确匹配。"""

        variants = cls._original_variants(candidate.get("variants"))
        normalized_variants = cls._normalized_variants(variants)
        reason_types = cls._reason_types(candidate.get("reasonTypes"))
        rule_type = NormalizationRuleService.determine_rule_type(
            normalized_variants,
            reason_types,
        )
        return cls.candidate_fingerprint(
            rule_type=rule_type,
            normalized_variants=normalized_variants,
            reason_types=reason_types,
            suggested_canonical=cls._optional_text(
                candidate.get("suggestedCanonical")
            ),
        )

    @classmethod
    def candidate_fingerprint(
        cls,
        *,
        rule_type: NormalizationRuleType,
        normalized_variants: Sequence[str],
        reason_types: Sequence[str],
        suggested_canonical: str | None,
    ) -> str:
        """按稳定业务 identity 计算 SHA-256，绝不混入任务动态统计字段。"""

        semantic_types = tuple(sorted(set(reason_types)))
        fingerprint_source: dict[str, Any] = {
            "ruleType": rule_type.value,
            # 排序只影响 identity 计算，实际 variants 的展示/审计顺序另行保存。
            "variants": tuple(sorted(set(normalized_variants))),
            "semanticTypes": semantic_types,
        }
        if cls._LEGACY_PHRASE_CONCEPT_REASON in semantic_types:
            # 仅复现已写入数据库的旧版 identity，确保历史只读匹配仍然准确；
            # 新候选不会包含该类型。
            fingerprint_source["suggestedCanonical"] = cls._normalize_text(
                suggested_canonical
            )

        canonical_json = json.dumps(
            fingerprint_source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical_json.encode("utf-8")).hexdigest()

    @classmethod
    def _history_reference(
        cls,
        candidate_id: str,
        fingerprint: str,
        records: Sequence[NormalizationReviewDecisionRecord],
    ) -> dict[str, Any]:
        """将 append-only 记录汇总为 View 专用只读参考数据。"""

        decision_counts = {
            decision.value: sum(record.decision == decision for record in records)
            for decision in NormalizationReviewDecision
        }
        approved_canonical_counts: dict[str, int] = {}
        latest_approved_canonical: str | None = None
        for record in records:
            if (
                record.decision == NormalizationReviewDecision.APPROVED
                and record.approved_canonical
            ):
                approved_canonical_counts[record.approved_canonical] = (
                    approved_canonical_counts.get(record.approved_canonical, 0) + 1
                )
                if latest_approved_canonical is None:
                    latest_approved_canonical = record.approved_canonical

        latest_record = records[0] if records else None
        return {
            "fingerprint": fingerprint,
            "totalDecisionCount": len(records),
            "latestDecision": (
                latest_record.decision.value if latest_record is not None else None
            ),
            "latestApprovedCanonical": latest_approved_canonical,
            "latestReviewedAt": cls._datetime_text(
                latest_record.created_at if latest_record is not None else None
            ),
            "decisionCounts": decision_counts,
            "approvedCanonicals": [
                {"canonical": canonical, "count": count}
                for canonical, count in sorted(
                    approved_canonical_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ],
            "recentDecisions": [
                cls._history_record(candidate_id, record)
                for record in records[:20]
            ],
            "hasDecisionConflict": len(
                [count for count in decision_counts.values() if count]
            ) > 1,
            "hasCanonicalConflict": len(approved_canonical_counts) > 1,
        }

    @classmethod
    def _history_record(
        cls,
        candidate_id: str,
        record: NormalizationReviewDecisionRecord,
    ) -> dict[str, Any]:
        """输出详情弹窗所需白名单字段，不传播原始数据库对象。"""

        return {
            "reviewedAt": cls._datetime_text(record.created_at),
            "decision": record.decision.value,
            "approvedCanonical": record.approved_canonical,
            "candidateId": candidate_id,
            "reasonTypes": list(record.reason_types),
            "context": cls.context_snapshot(record.context_snapshot),
        }

    @staticmethod
    def _datetime_text(value: object) -> str | None:
        """统一将数据库 datetime 变为 JSON/Qt 可展示的 ISO 文本。"""

        return value.isoformat() if hasattr(value, "isoformat") else None

    @classmethod
    def context_snapshot(
        cls,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """按白名单提取当前真实任务上下文，防止认证信息进入审计 JSON。"""

        snapshot: dict[str, Any] = {}
        for field in cls._ALLOWED_CONTEXT_FIELDS:
            value = context.get(field)
            if isinstance(value, str) and value.strip():
                snapshot[field] = value.strip()
            elif field in {"analysisMonths", "selectedAsins"} and isinstance(
                value,
                list,
            ):
                clean_values = [
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                ]
                if clean_values:
                    snapshot[field] = clean_values
        return snapshot

    @classmethod
    def _original_variants(cls, value: object) -> tuple[str, ...]:
        """保留 candidate 原始顺序用于审计，但拒绝空或非字符串 variant。"""

        if not isinstance(value, list):
            raise ValueError("variants 必须是非空字符串列表")
        variants = tuple(
            variant for variant in value if isinstance(variant, str) and variant.strip()
        )
        if not variants or len(variants) != len(value):
            raise ValueError("variants 包含无效值")
        return variants

    @classmethod
    def _normalized_variants(
        cls,
        variants: Sequence[str],
    ) -> tuple[str, ...]:
        """只规范大小写与空白；空格、连字符和词形差异必须保留。"""

        normalized_variants = tuple(
            cls._normalize_text(variant) for variant in variants
        )
        if not all(normalized_variants):
            raise ValueError("variants 不能包含空字符串")
        return normalized_variants

    @staticmethod
    def _reason_types(value: object) -> tuple[str, ...]:
        """保留候选发现来源，供未来审计其发现质量，不参与自动生效。"""

        if not isinstance(value, list):
            return ()
        return tuple(
            reason.strip()
            for reason in value
            if isinstance(reason, str) and reason.strip()
        )

    @staticmethod
    def _normalize_text(value: object) -> str:
        """仅做 lowercase、trim 与连续空白归并，避免 compact identity 误伤。"""

        return " ".join(value.lower().split()) if isinstance(value, str) else ""

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """将可选 canonical 统一为 trim 后字符串或 NULL。"""

        return value.strip() if isinstance(value, str) and value.strip() else None

    @classmethod
    def _required_text(cls, value: object) -> str:
        """APPROVED 必须有人工确认 canonical，不能回退机器建议。"""

        canonical = cls._optional_text(value)
        if canonical is None:
            raise ValueError("APPROVED 决定必须提供 approvedCanonical")
        return canonical
