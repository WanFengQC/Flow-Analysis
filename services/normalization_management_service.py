"""归一化当前有效规则的校验、版本化管理和正式快照服务。"""

from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from models.normalization_active_rule import (
    NormalizationActiveRuleAuditRecord,
    NormalizationActiveRuleRecord,
    NormalizationRuleAuditAction,
)
from models.normalization_rule import (
    ApprovedNormalizationRules,
    NormalizationRule,
    NormalizationRuleType,
)
from repositories.database import DatabaseManager
from repositories.normalization_active_rule_repository import (
    NormalizationActiveRuleRepository,
)
from services.normalization_rule_service import (
    ApprovedRulesBuildResult,
    NormalizationRuleService,
)


class NormalizationRuleValidationError(ValueError):
    """规则集合不满足冲突或链式引用约束时抛出的明确业务错误。"""

    def __init__(self, result: ApprovedRulesBuildResult) -> None:
        self.result = result
        details = [
            f"{conflict.conflict_type}: {conflict.variant}"
            for conflict in (*result.conflicts, *result.chain_references)
        ]
        super().__init__("归一化规则校验失败：" + "；".join(details))


class NormalizationRuleVersionConflictError(RuntimeError):
    """管理页提交的规则已被其他管理操作更新或撤销。"""


class NormalizationManagementService:
    """只管理显式生效规则；审核历史和机器候选不在本服务自动激活。"""

    _MANUAL_REASON_TYPE = "MANUAL_MANAGEMENT"

    def __init__(
        self,
        database: DatabaseManager,
        rule_service: NormalizationRuleService | None = None,
    ) -> None:
        self._database = database
        self._rule_service = rule_service or NormalizationRuleService()

    async def list_current_rules(
        self,
        *,
        search: str | None = None,
        rule_type: NormalizationRuleType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[NormalizationActiveRuleRecord], int]:
        """查询管理页当前规则，历史版本不和当前生效集合混在一起。"""

        return await NormalizationActiveRuleRepository(
            self._database
        ).list_active_rules(
            search=search,
            rule_type=rule_type,
            limit=limit,
            offset=offset,
        )

    async def list_history(
        self,
        rule_id: UUID,
    ) -> list[NormalizationActiveRuleAuditRecord]:
        """读取一个规则版本的管理审计。"""

        return await NormalizationActiveRuleRepository(
            self._database
        ).list_history(rule_id)

    async def load_effective_rules(self) -> ApprovedNormalizationRules:
        """构建新分析可冻结的数据库当前规则快照。"""

        async with self._database.transaction() as connection:
            repository = NormalizationActiveRuleRepository(
                self._database,
                connection,
            )
            active_records = await repository.list_all_active_for_validation()
            result = self._build_rules_from_records(active_records)
            self._require_valid(result)
            assert result.approved_rules is not None
            return result.approved_rules

    async def build_effective_rules_with_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> ApprovedNormalizationRules:
        """将当前任务已批准候选与数据库有效规则一起做完整校验。

        此方法只构建当前任务快照，绝不因候选的 APPROVED 状态自动写库。
        """

        async with self._database.transaction() as connection:
            repository = NormalizationActiveRuleRepository(
                self._database,
                connection,
            )
            active_records = await repository.list_all_active_for_validation()
            result = self._build_rules(
                [
                    *self._candidates_from_records(active_records),
                    *self._approved_candidates(candidates),
                ]
            )
            self._require_valid(result)
            assert result.approved_rules is not None
            return result.approved_rules

    async def create_manual_rule(
        self,
        *,
        rule_type: NormalizationRuleType,
        variants: Sequence[str],
        canonical: str,
    ) -> NormalizationActiveRuleRecord:
        """创建一条用户明确确认的当前有效规则，并校验完整现有集合。"""

        clean_variants = self._normalize_variants(variants)
        clean_canonical = self._normalize_text(canonical, "canonical")
        self._require_declared_type(rule_type, clean_variants)
        new_candidate = self._rule_candidate(
            candidate_id="manual:new",
            variants=clean_variants,
            canonical=clean_canonical,
            reason_types=(self._MANUAL_REASON_TYPE,),
        )
        async with self._database.transaction() as connection:
            repository = NormalizationActiveRuleRepository(
                self._database,
                connection,
            )
            await repository.acquire_management_lock()
            active_records = await repository.list_all_active_for_validation()
            self._require_valid(
                self._build_rules(
                    [
                        *self._candidates_from_records(active_records),
                        new_candidate,
                    ]
                )
            )
            inserted = await repository.insert_rule(
                NormalizationActiveRuleRecord(
                    id=uuid4(),
                    rule_type=rule_type,
                    variants=clean_variants,
                    canonical=clean_canonical,
                    source_candidate_id=None,
                    source_reason_types=(self._MANUAL_REASON_TYPE,),
                    supersedes_rule_id=None,
                    revision=1,
                    is_active=True,
                )
            )
            await self._append_audit(
                repository,
                inserted,
                NormalizationRuleAuditAction.CREATE,
            )
            return inserted

    async def update_manual_rule(
        self,
        *,
        rule_id: UUID,
        expected_revision: int,
        rule_type: NormalizationRuleType,
        variants: Sequence[str],
        canonical: str,
    ) -> NormalizationActiveRuleRecord:
        """版本化替换规则，旧版本逻辑撤销且审计完整保留。"""

        clean_variants = self._normalize_variants(variants)
        clean_canonical = self._normalize_text(canonical, "canonical")
        self._require_declared_type(rule_type, clean_variants)
        async with self._database.transaction() as connection:
            repository = NormalizationActiveRuleRepository(
                self._database,
                connection,
            )
            await repository.acquire_management_lock()
            previous = await repository.get_active_rule(rule_id, for_update=True)
            if previous is None or previous.revision != expected_revision:
                raise NormalizationRuleVersionConflictError("规则已被更新，请刷新后重试")
            active_records = await repository.list_all_active_for_validation()
            remaining_records = [
                record for record in active_records if record.id != rule_id
            ]
            self._require_valid(
                self._build_rules(
                    [
                        *self._candidates_from_records(remaining_records),
                        self._rule_candidate(
                            candidate_id=f"manual:update:{rule_id}",
                            variants=clean_variants,
                            canonical=clean_canonical,
                            reason_types=(self._MANUAL_REASON_TYPE,),
                        ),
                    ]
                )
            )
            revoked = await repository.revoke_rule(rule_id, expected_revision)
            if revoked is None:
                raise NormalizationRuleVersionConflictError("规则已被更新，请刷新后重试")
            await self._append_audit(
                repository,
                revoked,
                NormalizationRuleAuditAction.REVOKE,
            )
            inserted = await repository.insert_rule(
                NormalizationActiveRuleRecord(
                    id=uuid4(),
                    rule_type=rule_type,
                    variants=clean_variants,
                    canonical=clean_canonical,
                    source_candidate_id=previous.source_candidate_id,
                    source_reason_types=(self._MANUAL_REASON_TYPE,),
                    supersedes_rule_id=previous.id,
                    revision=1,
                    is_active=True,
                )
            )
            await self._append_audit(
                repository,
                inserted,
                NormalizationRuleAuditAction.UPDATE,
            )
            return inserted

    async def revoke_rule(
        self,
        *,
        rule_id: UUID,
        expected_revision: int,
    ) -> NormalizationActiveRuleRecord:
        """逻辑撤销一条当前规则，历史版本和审核事件均不删除。"""

        async with self._database.transaction() as connection:
            repository = NormalizationActiveRuleRepository(
                self._database,
                connection,
            )
            await repository.acquire_management_lock()
            revoked = await repository.revoke_rule(rule_id, expected_revision)
            if revoked is None:
                raise NormalizationRuleVersionConflictError("规则已被更新，请刷新后重试")
            await self._append_audit(
                repository,
                revoked,
                NormalizationRuleAuditAction.REVOKE,
            )
            return revoked

    async def activate_applied_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> tuple[NormalizationActiveRuleRecord, ...]:
        """仅在用户完成审核并成功应用后发布当前已批准候选。

        和分析 worker 一样先校验整个集合；任意冲突都会阻断本次全局生效，
        不会出现只写入一半候选规则的情况。
        """

        approved_candidates = self._approved_candidates(candidates)
        if not approved_candidates:
            return ()
        async with self._database.transaction() as connection:
            repository = NormalizationActiveRuleRepository(
                self._database,
                connection,
            )
            await repository.acquire_management_lock()
            active_records = await repository.list_all_active_for_validation()
            result = self._build_rules(
                [
                    *self._candidates_from_records(active_records),
                    *approved_candidates,
                ]
            )
            self._require_valid(result)
            active_signatures = {
                self._rule_signature(record.to_normalization_rule())
                for record in active_records
            }
            inserted: list[NormalizationActiveRuleRecord] = []
            for rule in result.rules:
                if not rule.source_candidate_id.startswith("candidate:"):
                    continue
                if self._rule_signature(rule) in active_signatures:
                    continue
                record = await repository.insert_rule(
                    NormalizationActiveRuleRecord(
                        id=uuid4(),
                        rule_type=rule.rule_type,
                        variants=rule.variants,
                        canonical=rule.canonical,
                        source_candidate_id=rule.source_candidate_id.removeprefix("candidate:"),
                        source_reason_types=rule.source_reason_types,
                        supersedes_rule_id=None,
                        revision=1,
                        is_active=True,
                    )
                )
                await self._append_audit(
                    repository,
                    record,
                    NormalizationRuleAuditAction.APPLY,
                )
                inserted.append(record)
            return tuple(inserted)

    @staticmethod
    def _rule_signature(rule: NormalizationRule) -> tuple[object, ...]:
        return (
            rule.rule_type.value,
            tuple(sorted(rule.variants)),
            rule.canonical,
        )

    async def _append_audit(
        self,
        repository: NormalizationActiveRuleRepository,
        record: NormalizationActiveRuleRecord,
        action: NormalizationRuleAuditAction,
    ) -> None:
        await repository.append_audit(
            NormalizationActiveRuleAuditRecord(
                id=uuid4(),
                rule_id=record.id,
                action=action,
                rule_snapshot=self._record_snapshot(record),
            )
        )

    def _build_rules_from_records(
        self,
        records: Sequence[NormalizationActiveRuleRecord],
    ) -> ApprovedRulesBuildResult:
        return self._build_rules(self._candidates_from_records(records))

    def _build_rules(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> ApprovedRulesBuildResult:
        return self._rule_service.build_approved_rules(candidates)

    @staticmethod
    def _require_valid(result: ApprovedRulesBuildResult) -> None:
        if result.approved_rules is None:
            raise NormalizationRuleValidationError(result)

    @classmethod
    def _candidates_from_records(
        cls,
        records: Sequence[NormalizationActiveRuleRecord],
    ) -> list[dict[str, Any]]:
        return [
            cls._rule_candidate(
                candidate_id=f"active:{record.id}",
                variants=record.variants,
                canonical=record.canonical,
                reason_types=record.source_reason_types,
            )
            for record in records
            if record.is_active
        ]

    @classmethod
    def _approved_candidates(
        cls,
        candidates: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.get("decision") != "APPROVED":
                continue
            candidate_id = candidate.get("id")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                continue
            variants = candidate.get("variants")
            canonical = candidate.get("approvedCanonical")
            if not isinstance(variants, list) or not isinstance(canonical, str):
                continue
            prepared.append(
                cls._rule_candidate(
                    candidate_id=f"candidate:{candidate_id.strip()}",
                    variants=cls._normalize_variants(variants),
                    canonical=cls._normalize_text(canonical, "approvedCanonical"),
                    reason_types=tuple(
                        item.strip()
                        for item in candidate.get("reasonTypes", [])
                        if isinstance(item, str) and item.strip()
                    ),
                )
            )
        return prepared

    @staticmethod
    def _rule_candidate(
        *,
        candidate_id: str,
        variants: Sequence[str],
        canonical: str,
        reason_types: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "id": candidate_id,
            "decision": "APPROVED",
            "variants": list(variants),
            "approvedCanonical": canonical,
            "reasonTypes": list(reason_types),
        }

    @classmethod
    def _require_declared_type(
        cls,
        declared_type: NormalizationRuleType,
        variants: tuple[str, ...],
    ) -> None:
        actual_type = NormalizationRuleService.determine_rule_type(variants, ())
        if actual_type != declared_type:
            raise ValueError("规则类型必须与变体表达形式一致")

    @classmethod
    def _normalize_variants(cls, variants: Sequence[object]) -> tuple[str, ...]:
        if not isinstance(variants, Sequence) or isinstance(variants, (str, bytes)):
            raise ValueError("variants 必须是非空字符串列表")
        normalized = tuple(
            dict.fromkeys(
                cls._normalize_text(variant, "variant")
                for variant in variants
            )
        )
        if not normalized:
            raise ValueError("至少需要一个 variant")
        return normalized

    @staticmethod
    def _normalize_text(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} 必须是非空字符串")
        normalized = " ".join(value.lower().split())
        if not normalized:
            raise ValueError(f"{field_name} 必须是非空字符串")
        return normalized

    @staticmethod
    def _record_snapshot(record: NormalizationActiveRuleRecord) -> dict[str, object]:
        return {
            "id": str(record.id),
            "ruleType": record.rule_type.value,
            "variants": list(record.variants),
            "canonical": record.canonical,
            "sourceCandidateId": record.source_candidate_id,
            "sourceReasonTypes": list(record.source_reason_types),
            "supersedesRuleId": (
                str(record.supersedes_rule_id)
                if record.supersedes_rule_id is not None
                else None
            ),
            "revision": record.revision,
            "isActive": record.is_active,
        }
