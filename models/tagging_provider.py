"""AI Tagging Provider 调用与三模型共识的运行时数据模型。"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from models.tagging import TagLabel, TaggingDecision


class TaggingFailureType(StrEnum):
    """不泄露远端响应内容的 Provider 调用失败分类。"""

    # 保留旧值以兼容已存在的历史记录；新请求必须写入更精确的超时类型。
    TIMEOUT = "TIMEOUT"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    READ_TIMEOUT = "READ_TIMEOUT"
    WRITE_TIMEOUT = "WRITE_TIMEOUT"
    POOL_TIMEOUT = "POOL_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_ERROR = "AUTH_ERROR"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"


class TaggingConsensusStatus(StrEnum):
    """严格三方一致性结果，不提供多数投票状态。"""

    CONSENSUS = "CONSENSUS"
    DISAGREEMENT = "DISAGREEMENT"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class TaggingProviderConfig:
    """单个 UniAPI 业务模型的稳定配置。"""

    provider_id: str
    display_name: str
    model_id: str
    concurrency_limit: int
    reasoning_profile: str = "medium"
    request_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验稳定身份与不会被通用请求体覆盖的专属选项。"""

        for field_name, value in (
            ("provider_id", self.provider_id),
            ("display_name", self.display_name),
            ("reasoning_profile", self.reasoning_profile),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 必须是非空字符串")
        if not isinstance(self.model_id, str):
            raise ValueError("model_id 必须是字符串")
        if isinstance(self.concurrency_limit, bool) or self.concurrency_limit <= 0:
            raise ValueError("concurrency_limit 必须是正整数")
        if not isinstance(self.request_options, Mapping):
            raise ValueError("request_options 必须是对象")

        # stream 模式会破坏当前必须收到完整 JSON 后才校验的协议。
        prohibited_options = {"model", "messages", "stream"}
        if prohibited_options.intersection(self.request_options):
            raise ValueError("request_options 不允许覆盖 model 或 messages")

        object.__setattr__(self, "provider_id", self.provider_id.strip())
        object.__setattr__(self, "display_name", self.display_name.strip())
        object.__setattr__(self, "model_id", self.model_id.strip())
        object.__setattr__(
            self,
            "reasoning_profile",
            self.reasoning_profile.strip(),
        )
        object.__setattr__(self, "request_options", dict(self.request_options))


@dataclass(frozen=True, slots=True)
class ProviderTaggingResult:
    """一个 Provider 完整通过协议校验后的单批决策。"""

    provider_id: str
    model_id: str
    decisions: tuple[TaggingDecision, ...]
    latency_ms: int
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ProviderTaggingFailure:
    """一个 Provider 重试耗尽或不可重试时的安全失败摘要。"""

    provider_id: str
    model_id: str
    failure_type: TaggingFailureType
    attempt_count: int
    latency_ms: int


ProviderTaggingOutcome = ProviderTaggingResult | ProviderTaggingFailure


@dataclass(frozen=True, slots=True)
class TaggingConsensusResult:
    """每个输入项的严格三方共识或运行时诊断状态。"""

    item_id: str
    status: TaggingConsensusStatus
    provider_results: Mapping[str, ProviderTaggingOutcome]
    consensus_label: TagLabel | None
    consensus_reason: str | None
