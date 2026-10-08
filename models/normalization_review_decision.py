"""人工归一审核决策的持久化数据模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from models.normalization_rule import NormalizationRuleType


class NormalizationReviewDecision(StrEnum):
    """人工审核可持久化的明确决策；PENDING 不属于人工决策事件。"""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class NormalizationReviewDecisionRecord:
    """一条 append-only 审核事件，不代表会被自动应用的正式规则。"""

    id: UUID
    candidate_fingerprint: str
    candidate_id: str
    decision: NormalizationReviewDecision
    rule_type: NormalizationRuleType
    variants: tuple[str, ...]
    suggested_canonical: str | None
    approved_canonical: str | None
    reason_types: tuple[str, ...]
    context_snapshot: dict[str, Any]
    normalization_revision: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
