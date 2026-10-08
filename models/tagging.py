"""AI Tagging 的固定标签、输入与输出数据模型。"""

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class TagLabel(StrEnum):
    """Flow Analysis 唯一允许采用的九个单标签结果。"""

    CORE_TERM = "1核心词"
    APPEARANCE = "2外形"
    ATTRIBUTE = "3属性"
    PAIN_POINT = "4痛点"
    SPECIFICATION = "5规格"
    AUDIENCE = "6受众"
    SCENARIO = "7场景"
    BRAND = "8品牌"
    INVALID = "无效词"


@dataclass(frozen=True, slots=True)
class TaggingInput:
    """一个 ``month + word`` 的只读 AI 打标输入快照。"""

    item_id: str
    month: str
    word: str
    category: str
    top_phrases: tuple[str, ...]
    product_context_source: str
    representative_asin: str
    product_context: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        """阻止空 identity 和外部可变引用进入固定的 Provider 输入。"""

        for field_name, value in (
            ("item_id", self.item_id),
            ("month", self.month),
            ("word", self.word),
            ("category", self.category),
            ("product_context_source", self.product_context_source),
            ("representative_asin", self.representative_asin),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 必须是非空字符串")

        if any(
            not isinstance(phrase, str) or not phrase.strip()
            for phrase in self.top_phrases
        ):
            raise ValueError("top_phrases 只能包含非空字符串")
        if (
            self.product_context is not None
            and not isinstance(self.product_context, Mapping)
        ):
            raise ValueError("product_context 必须是对象或 None")

        # frozen dataclass 仍可能持有外部传入的 dict；深拷贝确保后续调用方
        # 修改 Context 不会影响已经建立的本轮 AI 输入。
        object.__setattr__(
            self,
            "top_phrases",
            tuple(phrase.strip() for phrase in self.top_phrases),
        )
        object.__setattr__(
            self,
            "product_context",
            (
                deepcopy(dict(self.product_context))
                if self.product_context is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为 Prompt JSON 的 camelCase 数据，不暴露 sourceAsinStats。"""

        return {
            "itemId": self.item_id,
            "month": self.month,
            "word": self.word,
            "category": self.category,
            "topPhrases": list(self.top_phrases),
            "productContextSource": self.product_context_source,
            "representativeAsin": self.representative_asin,
            "productContext": deepcopy(self.product_context),
        }


@dataclass(frozen=True, slots=True)
class TaggingDecision:
    """经严格响应校验后的单标签 AI 判断，不含置信度或思维过程。"""

    item_id: str
    label: TagLabel
    reason: str

    def __post_init__(self) -> None:
        """保证决策字段可用于准确回填原始 TaggingInput。"""

        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("item_id 必须是非空字符串")
        if not isinstance(self.label, TagLabel):
            raise ValueError("label 必须是 TagLabel")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason 必须是非空字符串")
        object.__setattr__(self, "reason", self.reason.strip())

    def to_dict(self) -> dict[str, str]:
        """转换为固定 AI 响应行结构。"""

        return {
            "itemId": self.item_id,
            "label": self.label.value,
            "reason": self.reason,
        }
