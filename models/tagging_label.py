"""正式 AI 标签缓存、审计与 Pipeline 运行时的数据模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID

from models.tagging import TagLabel, TaggingInput
from models.tagging_provider import ProviderTaggingOutcome


class TaggingCategoryKey(StrEnum):
    """数据库缓存身份允许使用的稳定内部品类 key。"""

    PILLOW = "pillow"
    STUFFED_ANIMALS = "stuffed_animals"


class TaggingDecisionSource(StrEnum):
    """可以写入正式标签缓存的唯一来源。"""

    AI_CONSENSUS = "AI_CONSENSUS"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    # Hunter 的历史共识表混合保存了 AI 共识与人工确认，但没有逐条来源字段。
    # 导入时必须保留这一事实，不能伪造成当前项目新产生的任一来源。
    HISTORICAL_IMPORT = "HISTORICAL_IMPORT"
    # 管理窗口明确人工维护的缓存，不得伪装成 AI 或审核窗口结论。
    MANUAL_MANAGEMENT = "MANUAL_MANAGEMENT"


class TaggingPipelineStatus(StrEnum):
    """单个 ``month + word`` 本轮运行的最终状态。"""

    CACHE_HIT = "CACHE_HIT"
    AI_CONSENSUS = "AI_CONSENSUS"
    DISAGREEMENT = "DISAGREEMENT"
    INCOMPLETE = "INCOMPLETE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class TaggingReviewStatus(StrEnum):
    """人工审核项仅在当前运行时使用的短生命周期状态。"""

    PENDING = "PENDING"
    SAVING = "SAVING"


TAGGING_TAXONOMY_VERSION = 1

_CATEGORY_DISPLAY_NAMES = {
    TaggingCategoryKey.PILLOW: "Pillow",
    TaggingCategoryKey.STUFFED_ANIMALS: "Stuffed Animals",
}


def category_display_name(category_key: TaggingCategoryKey) -> str:
    """返回固定显示名；UI 文本永远不能反向充当缓存 identity。"""

    return _CATEGORY_DISPLAY_NAMES[category_key]


def require_category_key(value: object) -> TaggingCategoryKey:
    """只接受明确稳定 key，拒绝大小写猜测和 UI 文本模糊匹配。"""

    if not isinstance(value, str):
        raise ValueError("category_key 必须是已知稳定内部 key")
    try:
        return TaggingCategoryKey(value)
    except ValueError as exc:
        raise ValueError("未知 category_key") from exc


def normalize_cache_word(value: object) -> str:
    """缓存 identity 仅统一 trim/lowercase，不实施任何语义归一。"""

    if not isinstance(value, str):
        raise ValueError("word 必须是非空字符串")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("word 必须是非空字符串")
    return normalized


@dataclass(frozen=True, slots=True)
class TaggingLabelCacheRecord:
    """当前可复用的正式标签缓存行。"""

    id: UUID
    category_key: TaggingCategoryKey
    word: str
    taxonomy_version: int
    label: TagLabel
    reason: str | None
    decision_source: TaggingDecisionSource
    # 纯人工管理记录可以不关联本轮 AI 的代表 ASIN 或产品背景。
    representative_asin: str | None
    product_context_source: str | None
    revision: int = 1
    is_active: bool = True
    invalidated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaggingLabelDecisionRecord:
    """append-only 的正式标签形成事件；缓存读取不会创建该记录。"""

    id: UUID
    cache_id: UUID
    category_key: TaggingCategoryKey
    word: str
    taxonomy_version: int
    label: TagLabel
    reason: str | None
    decision_source: TaggingDecisionSource
    representative_asin: str | None
    product_context_source: str | None
    provider_results: Mapping[str, Mapping[str, Any]]
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaggingReviewItem:
    """三方分歧的仅内存审核数据，禁止序列化或持久化。"""

    item_id: str
    month: str
    word: str
    category_key: TaggingCategoryKey
    taxonomy_version: int
    top_phrases: tuple[str, ...]
    representative_asin: str
    product_context_source: str
    product_context: Mapping[str, Any] | None
    provider_results: Mapping[str, ProviderTaggingOutcome]
    status: TaggingReviewStatus = TaggingReviewStatus.PENDING


@dataclass(frozen=True, slots=True)
class TaggingPipelineResult:
    """一个月度正式 word 的本轮标签结果。

    ``provider_results`` 与 ``tagging_input`` 只服务当前进程内的
    INCOMPLETE 精准恢复；它们绝不进入 Final Result、Excel 或数据库。
    """

    month: str
    word: str
    category_key: TaggingCategoryKey
    taxonomy_version: int
    status: TaggingPipelineStatus
    label: TagLabel | None
    reason: str | None
    decision_source: TaggingDecisionSource | None
    # AI 请求开始时读取到的逻辑删除缓存版本。None 表示当时从未存在该
    # identity；用于阻止迟到 AI 复活已删除/已人工改写的缓存。
    cache_miss_revision: int | None = None
    provider_results: Mapping[str, ProviderTaggingOutcome] = field(
        default_factory=dict
    )
    tagging_input: TaggingInput | None = None


@dataclass(frozen=True, slots=True)
class TaggingPipelineRun:
    """一次正式标签处理的结果与仅内存分歧审核项。"""

    results: tuple[TaggingPipelineResult, ...]
    review_items: tuple[TaggingReviewItem, ...]
