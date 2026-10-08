"""人工批准后才可参与正式归一的内存规则结构。"""

from dataclasses import dataclass
from enum import StrEnum


class NormalizationRuleType(StrEnum):
    """区分必须优先执行的 phrase 规则与普通 word 规则。"""

    WORD = "WORD"
    PHRASE = "PHRASE"


@dataclass(frozen=True)
class NormalizationRule:
    """一条可审计的人工批准归一规则，不含任何持久化或 UI 逻辑。"""

    rule_type: NormalizationRuleType
    variants: tuple[str, ...]
    canonical: str
    source_candidate_id: str
    source_reason_types: tuple[str, ...]


@dataclass(frozen=True)
class NormalizationRuleConflict:
    """同一 variant 被不同人工批准 canonical 指向时的阻断信息。"""

    conflict_type: str
    variant: str
    canonical_values: tuple[str, ...]
    source_candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class ApprovedNormalizationRules:
    """已通过冲突校验、可明确传给正式 tokenization 的规则集合。"""

    rules: tuple[NormalizationRule, ...]
