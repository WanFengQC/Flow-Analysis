"""归一化管理页使用的当前规则与 append-only 管理审计模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from models.normalization_rule import NormalizationRule, NormalizationRuleType


class NormalizationRuleAuditAction(StrEnum):
    """当前规则的管理动作；审核决定历史不在这里重复表达。"""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    REVOKE = "REVOKE"
    APPLY = "APPLY"


@dataclass(frozen=True, slots=True)
class NormalizationActiveRuleRecord:
    """一条当前或历史版本的持久化归一规则。"""

    id: UUID
    rule_type: NormalizationRuleType
    variants: tuple[str, ...]
    canonical: str
    source_candidate_id: str | None
    source_reason_types: tuple[str, ...]
    supersedes_rule_id: UUID | None
    revision: int
    is_active: bool
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_normalization_rule(self) -> NormalizationRule:
        """转换为现有正式规则模型，供既有归一计算链直接复用。"""

        return NormalizationRule(
            rule_type=self.rule_type,
            variants=self.variants,
            canonical=self.canonical,
            source_candidate_id=self.source_candidate_id or str(self.id),
            source_reason_types=self.source_reason_types,
        )


@dataclass(frozen=True, slots=True)
class NormalizationActiveRuleAuditRecord:
    """管理规则的不可变事件，用于管理页历史详情。"""

    id: UUID
    rule_id: UUID
    action: NormalizationRuleAuditAction
    rule_snapshot: dict[str, object]
    created_at: datetime | None = None
