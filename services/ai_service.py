"""通过 UniAPI 统一调用三家 AI Tagging Provider 并生成严格共识。"""

import asyncio
import json
import logging
import math
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from config.settings import (
    TAGGING_BATCH_SIZE,
    TAGGING_CLAUDE_CONCURRENCY,
    TAGGING_CLAUDE_MODEL,
    TAGGING_CLAUDE_REASONING_PROFILE,
    TAGGING_CLAUDE_REQUEST_OPTIONS,
    TAGGING_GEMINI_CONCURRENCY,
    TAGGING_GEMINI_MODEL,
    TAGGING_GEMINI_REASONING_PROFILE,
    TAGGING_GEMINI_REQUEST_OPTIONS,
    TAGGING_GPT_CONCURRENCY,
    TAGGING_GPT_MODEL,
    TAGGING_GPT_REASONING_PROFILE,
    TAGGING_GPT_REQUEST_OPTIONS,
    TAGGING_MAX_ATTEMPTS,
    TAGGING_HTTP_CONNECT_TIMEOUT_SECONDS,
    TAGGING_HTTP_POOL_TIMEOUT_SECONDS,
    TAGGING_HTTP_READ_TIMEOUT_SECONDS,
    TAGGING_HTTP_WRITE_TIMEOUT_SECONDS,
    TAGGING_WRITE_TIMEOUT_MAX_ATTEMPTS,
    TAGGING_WRITE_TIMEOUT_RETRY_DELAY_SECONDS,
    TAGGING_RETRY_BASE_DELAY_SECONDS,
    TAGGING_RETRY_JITTER_SECONDS,
    TAGGING_RETRY_MAX_DELAY_SECONDS,
    TAGGING_PROMPT_MAX_ESTIMATED_TOKENS,
    TAGGING_PROMPT_MAX_SERIALIZED_BYTES,
    TAGGING_RESPONSE_ESTIMATED_TOKENS_PER_ROW,
    UNIAPI_API_KEY,
    UNIAPI_BASE_URL,
    UNIAPI_RPM_LIMIT,
)
from models.tagging import TaggingInput
from models.tagging_provider import (
    ProviderTaggingFailure,
    ProviderTaggingOutcome,
    ProviderTaggingResult,
    TaggingFailureType,
    TaggingProviderConfig,
    TaggingConsensusResult,
)
from services.tagging_consensus_service import TaggingConsensusService
from services.tagging_prompt import TaggingPromptBuilder
from services.tagging_response_validator import (
    TaggingResponseValidationError,
    TaggingResponseValidator,
)


logger = logging.getLogger(__name__)


class AiServiceConfigurationError(RuntimeError):
    """真正开始 AI 打标时才报告的缺失或无效配置。"""


class _ProviderCallError(RuntimeError):
    """内部可分类失败，绝不携带认证信息或远端原始响应正文。"""

    def __init__(
        self,
        failure_type: TaggingFailureType,
        retryable: bool,
    ) -> None:
        super().__init__(failure_type.value)
        self.failure_type = failure_type
        self.retryable = retryable


class _BatchSizeRelatedError(RuntimeError):
    """仅表示可通过拆分多个输入项解决的请求/响应容量问题。"""


class _GlobalRequestRateLimiter:
    """同一 UniAPI 账户全部 Provider 共享的滑动窗口 HTTP 请求限流器。"""

    _WINDOW_SECONDS = 60.0

    def __init__(
        self,
        requests_per_minute: int,
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        """保存单账户 RPM 上限；不在此处产生任何网络请求。"""

        if (
            isinstance(requests_per_minute, bool)
            or not isinstance(requests_per_minute, int)
            or requests_per_minute <= 0
        ):
            raise ValueError("requests_per_minute 必须是正整数")
        self._requests_per_minute = requests_per_minute
        self._monotonic = monotonic
        self._sleep = sleep
        self._request_times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """在发送一条 HTTP 请求前取得全局额度，取消时绝不继续等待或发送。"""

        while True:
            AiService._raise_if_cancelling()
            async with self._lock:
                now = self._monotonic()
                cutoff = now - self._WINDOW_SECONDS
                while self._request_times and self._request_times[0] <= cutoff:
                    self._request_times.popleft()
                if len(self._request_times) < self._requests_per_minute:
                    self._request_times.append(now)
                    return
                wait_seconds = max(
                    0.0,
                    self._WINDOW_SECONDS - (now - self._request_times[0]),
                )
            # 锁外等待，确保其他 Provider 能正确观察同一个共享窗口。
            await self._sleep(wait_seconds)


def default_tagging_provider_configs() -> tuple[TaggingProviderConfig, ...]:
    """从唯一 settings 模块生成三个稳定业务 Provider 配置。"""

    return (
        TaggingProviderConfig(
            provider_id="openai",
            display_name="GPT-6 Sol",
            model_id=TAGGING_GPT_MODEL,
            concurrency_limit=TAGGING_GPT_CONCURRENCY,
            reasoning_profile=TAGGING_GPT_REASONING_PROFILE,
            request_options=TAGGING_GPT_REQUEST_OPTIONS,
        ),
        TaggingProviderConfig(
            provider_id="anthropic",
            display_name="Claude Sonnet 5",
            model_id=TAGGING_CLAUDE_MODEL,
            concurrency_limit=TAGGING_CLAUDE_CONCURRENCY,
            reasoning_profile=TAGGING_CLAUDE_REASONING_PROFILE,
            request_options=TAGGING_CLAUDE_REQUEST_OPTIONS,
        ),
        TaggingProviderConfig(
            provider_id="google",
            display_name="Gemini 3.8 Flash",
            model_id=TAGGING_GEMINI_MODEL,
            concurrency_limit=TAGGING_GEMINI_CONCURRENCY,
            reasoning_profile=TAGGING_GEMINI_REASONING_PROFILE,
            request_options=TAGGING_GEMINI_REQUEST_OPTIONS,
        ),
    )


class AiService:
    """长生命周期 UniAPI 客户端；只提供具有业务意义的批量打标入口。"""

    _EXPECTED_PROVIDER_IDS = frozenset({"openai", "anthropic", "google"})
    _RETRYABLE_SERVER_STATUS_CODES = frozenset({500, 502, 503, 504})
    _CAUTIOUS_TIMEOUT_FAILURE_TYPES = frozenset({
        TaggingFailureType.WRITE_TIMEOUT,
    })

    def __init__(
        self,
        *,
        api_key: str = UNIAPI_API_KEY,
        provider_configs: Sequence[TaggingProviderConfig] | None = None,
        client: httpx.AsyncClient | None = None,
        batch_size: int = TAGGING_BATCH_SIZE,
        max_attempts: int = TAGGING_MAX_ATTEMPTS,
        retry_base_delay_seconds: float = TAGGING_RETRY_BASE_DELAY_SECONDS,
        retry_max_delay_seconds: float = TAGGING_RETRY_MAX_DELAY_SECONDS,
        retry_jitter_seconds: float = TAGGING_RETRY_JITTER_SECONDS,
        write_timeout_max_attempts: int = TAGGING_WRITE_TIMEOUT_MAX_ATTEMPTS,
        write_timeout_retry_delay_seconds: float = (
            TAGGING_WRITE_TIMEOUT_RETRY_DELAY_SECONDS
        ),
        requests_per_minute: int = UNIAPI_RPM_LIMIT,
        prompt_max_serialized_bytes: int = TAGGING_PROMPT_MAX_SERIALIZED_BYTES,
        prompt_max_estimated_tokens: int = TAGGING_PROMPT_MAX_ESTIMATED_TOKENS,
        response_estimated_tokens_per_row: int = TAGGING_RESPONSE_ESTIMATED_TOKENS_PER_ROW,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """创建一次长期复用的 HTTP Client，不在构造阶段执行网络 I/O。"""

        self._api_key = api_key.strip() if isinstance(api_key, str) else ""
        self._provider_configs = tuple(
            provider_configs or default_tagging_provider_configs()
        )
        self._validate_provider_configs(self._provider_configs)
        self._validate_positive_integer(batch_size, "batch_size")
        self._validate_positive_integer(max_attempts, "max_attempts")
        self._validate_positive_integer(
            write_timeout_max_attempts,
            "write_timeout_max_attempts",
        )
        self._validate_positive_integer(
            prompt_max_serialized_bytes,
            "prompt_max_serialized_bytes",
        )
        self._validate_positive_integer(
            prompt_max_estimated_tokens,
            "prompt_max_estimated_tokens",
        )
        self._validate_positive_integer(
            response_estimated_tokens_per_row,
            "response_estimated_tokens_per_row",
        )
        self._validate_retry_settings(
            retry_base_delay_seconds,
            retry_max_delay_seconds,
            retry_jitter_seconds,
        )
        self._validate_non_negative_finite_number(
            write_timeout_retry_delay_seconds,
            "write_timeout_retry_delay_seconds",
        )

        # 只有已配置 Key 时才组装 Authorization，避免错误发送空 Bearer Header。
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # 一个应用实例只持有一个 AsyncClient。read=None 必须原样交给 httpx，
        # 让模型长时间生成 JSON；绝不能回退为普通 API 的默认读取超时。
        self._http_timeout = httpx.Timeout(
            connect=TAGGING_HTTP_CONNECT_TIMEOUT_SECONDS,
            read=TAGGING_HTTP_READ_TIMEOUT_SECONDS,
            write=TAGGING_HTTP_WRITE_TIMEOUT_SECONDS,
            pool=TAGGING_HTTP_POOL_TIMEOUT_SECONDS,
        )
        # 外部注入 Client 仅用于 mock 测试；正式运行始终使用上述独立超时。
        self._client = client or httpx.AsyncClient(
            base_url=f"{UNIAPI_BASE_URL.rstrip('/')}/",
            headers=headers,
            timeout=self._http_timeout,
            follow_redirects=False,
        )
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._retry_jitter_seconds = retry_jitter_seconds
        self._write_timeout_max_attempts = min(
            max_attempts,
            write_timeout_max_attempts,
        )
        self._write_timeout_retry_delay_seconds = write_timeout_retry_delay_seconds
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._prompt_max_serialized_bytes = prompt_max_serialized_bytes
        self._prompt_max_estimated_tokens = prompt_max_estimated_tokens
        self._response_estimated_tokens_per_row = (
            response_estimated_tokens_per_row
        )
        self._validator = TaggingResponseValidator()
        self._consensus_service = TaggingConsensusService()

        # 每家模型单独受限，任意一方慢或限流都不会错误阻塞其他 Provider 的槽位。
        self._provider_semaphores = {
            config.provider_id: asyncio.Semaphore(config.concurrency_limit)
            for config in self._provider_configs
        }
        # 重试同样属于真实 HTTP 出站请求，必须消耗同一个账户级额度。
        self._request_rate_limiter = _GlobalRequestRateLimiter(
            requests_per_minute,
            monotonic=monotonic,
            sleep=sleep,
        )

    async def aclose(self) -> None:
        """在 AsyncRuntime EventLoop 中关闭唯一 UniAPI HTTP Client。"""

        await self._client.aclose()

    def validate_tagging_configuration(self) -> None:
        """在启动外部产品背景前显式校验 AI 打标配置。

        这个公开入口只检查本地配置，不发送 HTTP 请求。Controller 必须先
        调用它，避免 Amazon 背景已经获取完成后才发现 UniAPI 不可用。
        """

        self._ensure_tagging_configuration()

    async def tag_inputs(
        self,
        inputs: Sequence[TaggingInput],
    ) -> list[TaggingConsensusResult]:
        """按安全批次并行完成三方打标，并按输入顺序返回严格共识结果。"""

        normalized_inputs, batch_results = await self._tag_inputs_with_providers(
            inputs,
            self._provider_configs,
        )

        consensus_results: list[TaggingConsensusResult] = []
        for recovered_batches in batch_results:
            for batch, provider_outcomes in recovered_batches:
                consensus_results.extend(
                    self._consensus_service.build_results(
                        batch,
                        provider_outcomes,
                    )
                )

        return consensus_results

    async def tag_inputs_for_providers(
        self,
        inputs: Sequence[TaggingInput],
        provider_ids: Sequence[str],
    ) -> dict[str, dict[str, ProviderTaggingOutcome]]:
        """仅请求指定失败 Provider，并按输入保留每家局部结果。

        此入口只供当前 generation 的 INCOMPLETE 恢复使用。它继续复用
        相同 AsyncClient、Provider semaphore 与全局 RPM limiter，绝不另开
        绕过限流的通道。
        """

        selected_configs = self._provider_configs_for(provider_ids)
        normalized_inputs, batch_results = await self._tag_inputs_with_providers(
            inputs,
            selected_configs,
        )
        outcomes_by_item: dict[str, dict[str, ProviderTaggingOutcome]] = {
            tagging_input.item_id: {} for tagging_input in normalized_inputs
        }
        for batch_outcomes in batch_results:
            for batch, provider_outcomes in batch_outcomes:
                self._merge_partial_provider_outcomes(
                    outcomes_by_item,
                    batch,
                    provider_outcomes,
                )
        return outcomes_by_item

    async def _tag_inputs_with_providers(
        self,
        inputs: Sequence[TaggingInput],
        provider_configs: Sequence[TaggingProviderConfig],
    ) -> tuple[
        tuple[TaggingInput, ...],
        list[list[tuple[tuple[TaggingInput, ...], list[ProviderTaggingOutcome]]]],
    ]:
        """复用全量与局部重试共同的安全批处理、并发和取消语义。"""

        self._ensure_tagging_configuration()
        normalized_inputs = tuple(inputs)
        self._validate_inputs(normalized_inputs)
        batches = self._build_safe_batches(normalized_inputs, provider_configs)
        batch_tasks = [
            asyncio.create_task(
                self._tag_batch_with_size_recovery(batch, provider_configs)
            )
            for batch in batches
        ]
        try:
            batch_results = await asyncio.gather(*batch_tasks)
        except BaseException:
            # 父任务取消或异常时，尚未取得 Provider Semaphore 的批次绝不能
            # 继续发送；主动取消并等待子协程结束，避免后台残留请求。
            for task in batch_tasks:
                task.cancel()
            await asyncio.gather(*batch_tasks, return_exceptions=True)
            raise
        return normalized_inputs, list(batch_results)

    def _provider_configs_for(
        self,
        provider_ids: Sequence[str],
    ) -> tuple[TaggingProviderConfig, ...]:
        """按稳定 Provider identity 选择局部恢复目标，拒绝未知或重复项。"""

        normalized_ids = tuple(provider_ids)
        if not normalized_ids or len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("重试 Provider 必须是非空且不重复的集合")
        configs_by_id = {
            config.provider_id: config for config in self._provider_configs
        }
        try:
            return tuple(configs_by_id[provider_id] for provider_id in normalized_ids)
        except KeyError as exc:
            raise ValueError("存在未知的重试 Provider") from exc

    @staticmethod
    def _merge_partial_provider_outcomes(
        outcomes_by_item: dict[str, dict[str, ProviderTaggingOutcome]],
        inputs: Sequence[TaggingInput],
        provider_outcomes: Sequence[ProviderTaggingOutcome],
    ) -> None:
        """将批级返回拆回 item，失败摘要复制到该批每一条输入。"""

        expected_ids = {tagging_input.item_id for tagging_input in inputs}
        for outcome in provider_outcomes:
            if isinstance(outcome, ProviderTaggingFailure):
                for item_id in expected_ids:
                    outcomes_by_item[item_id][outcome.provider_id] = outcome
                continue
            decisions_by_id = {
                decision.item_id: decision for decision in outcome.decisions
            }
            if set(decisions_by_id) != expected_ids:
                raise RuntimeError("局部 Provider 返回与请求输入不一致")
            for item_id, decision in decisions_by_id.items():
                outcomes_by_item[item_id][outcome.provider_id] = ProviderTaggingResult(
                    provider_id=outcome.provider_id,
                    model_id=outcome.model_id,
                    decisions=(decision,),
                    latency_ms=outcome.latency_ms,
                    attempt_count=outcome.attempt_count,
                )

    def _build_safe_batches(
        self,
        inputs: Sequence[TaggingInput],
        provider_configs: Sequence[TaggingProviderConfig] | None = None,
    ) -> list[tuple[TaggingInput, ...]]:
        """按目标大小切片后，根据真实 Prompt 大小递归缩小超限批次。"""

        batches: list[tuple[TaggingInput, ...]] = []
        for batch_start in range(0, len(inputs), self._batch_size):
            target_batch = tuple(
                inputs[batch_start:batch_start + self._batch_size]
            )
            batches.extend(
                self._split_batch_for_safety(target_batch, provider_configs)
            )
        return batches

    def _split_batch_for_safety(
        self,
        inputs: tuple[TaggingInput, ...],
        provider_configs: Sequence[TaggingProviderConfig] | None = None,
    ) -> list[tuple[TaggingInput, ...]]:
        """若实际 Prompt 或显式输出预算超限，按顺序二分且不重排输入。"""

        if self._batch_fits_safety_limits(inputs, provider_configs) or len(inputs) == 1:
            return [inputs]
        midpoint = len(inputs) // 2
        return [
            *self._split_batch_for_safety(inputs[:midpoint], provider_configs),
            *self._split_batch_for_safety(inputs[midpoint:], provider_configs),
        ]

    def _batch_fits_safety_limits(
        self,
        inputs: Sequence[TaggingInput],
        provider_configs: Sequence[TaggingProviderConfig] | None = None,
    ) -> bool:
        """不修改 Prompt 协议，只使用其真实序列化结果判断是否应缩批。"""

        prompt_size = len(
            TaggingPromptBuilder.build_user_prompt(inputs).encode("utf-8")
        )
        estimated_tokens = math.ceil(prompt_size / 3)
        if (
            prompt_size > self._prompt_max_serialized_bytes
            or estimated_tokens > self._prompt_max_estimated_tokens
        ):
            return False

        required_output_tokens = (
            len(inputs) * self._response_estimated_tokens_per_row
        )
        return all(
            output_limit is None or output_limit >= required_output_tokens
            for output_limit in (
                self._configured_output_token_limit(config)
                for config in (provider_configs or self._provider_configs)
            )
        )

    @staticmethod
    def _configured_output_token_limit(
        config: TaggingProviderConfig,
    ) -> int | None:
        """只读取 Provider 已明确配置的输出上限，绝不注入通用不兼容字段。"""

        configured_limits = [
            value
            for name in ("max_completion_tokens", "max_tokens")
            if isinstance(value := config.request_options.get(name), int)
            and not isinstance(value, bool)
            and value > 0
        ]
        return min(configured_limits) if configured_limits else None

    async def _tag_batch_with_size_recovery(
        self,
        inputs: tuple[TaggingInput, ...],
        provider_configs: Sequence[TaggingProviderConfig] | None = None,
    ) -> list[tuple[tuple[TaggingInput, ...], list[ProviderTaggingOutcome]]]:
        """容量型失败只二分重试；网络/超时仍由 Provider 原有限次重试处理。"""

        self._raise_if_cancelling()
        try:
            return [(inputs, await self._tag_batch(inputs, provider_configs))]
        except _BatchSizeRelatedError:
            if len(inputs) == 1:
                # 单条再拆没有意义，交由调用方明确失败而不是无限重试或静默丢词。
                raise
            midpoint = len(inputs) // 2
            left, right = await asyncio.gather(
                self._tag_batch_with_size_recovery(inputs[:midpoint], provider_configs),
                self._tag_batch_with_size_recovery(inputs[midpoint:], provider_configs),
            )
            return [*left, *right]

    async def _tag_batch(
        self,
        inputs: Sequence[TaggingInput],
        provider_configs: Sequence[TaggingProviderConfig] | None = None,
    ) -> list[ProviderTaggingOutcome]:
        """让三家 Provider 同时接收同一 system prompt 和 user JSON。"""

        system_prompt = TaggingPromptBuilder.build_system_prompt()
        user_prompt = TaggingPromptBuilder.build_user_prompt(inputs)
        outcomes = await asyncio.gather(
            *(
                self._tag_provider_batch(
                    config,
                    inputs,
                    system_prompt,
                    user_prompt,
                )
                for config in (provider_configs or self._provider_configs)
            )
        )
        return list(outcomes)

    async def _tag_provider_batch(
        self,
        config: TaggingProviderConfig,
        inputs: Sequence[TaggingInput],
        system_prompt: str,
        user_prompt: str,
    ) -> ProviderTaggingOutcome:
        """调用一个 UniAPI 逻辑 Provider，并按安全分类执行有限重试。"""

        semaphore = self._provider_semaphores[config.provider_id]
        total_latency_ms = 0

        for attempt_count in range(1, self._max_attempts + 1):
            started_at = time.perf_counter()
            elapsed_before_timeout_ms: int | None = None
            failure: _ProviderCallError
            try:
                async with semaphore:
                    # Semaphore 限制活跃 HTTP；全局 limiter 限制三家累计 RPM。
                    # 额度在真正发送前取得，因此重试也不会绕过账户级上限。
                    await self._request_rate_limiter.acquire()
                    self._raise_if_cancelling()
                    content = await self._request_assistant_content(
                        config,
                        system_prompt,
                        user_prompt,
                    )
                latency_ms = self._elapsed_ms(started_at)
                total_latency_ms += latency_ms
                try:
                    decisions = self._validator.validate(content, inputs)
                except TaggingResponseValidationError as exc:
                    if (
                        len(inputs) > 1
                        and self._is_size_related_validation_failure(
                            content,
                            inputs,
                        )
                    ):
                        raise _BatchSizeRelatedError from exc
                    raise _ProviderCallError(
                        TaggingFailureType.INVALID_RESPONSE,
                        retryable=True,
                    ) from exc
                self._log_provider_success(
                    config,
                    len(inputs),
                    attempt_count,
                    latency_ms,
                )
                return ProviderTaggingResult(
                    provider_id=config.provider_id,
                    model_id=config.model_id,
                    decisions=tuple(decisions),
                    latency_ms=total_latency_ms,
                    attempt_count=attempt_count,
                )
            except asyncio.CancelledError:
                # 取消是控制流，不转换为业务失败状态，也不吞掉。
                raise
            except _BatchSizeRelatedError:
                # 100→50→25 的容量恢复由 batch 层统一完成；此处绝不能把
                # 同一个超大请求原样重试四次。
                raise
            except TaggingResponseValidationError:
                total_latency_ms += self._elapsed_ms(started_at)
                failure = _ProviderCallError(
                    TaggingFailureType.INVALID_RESPONSE,
                    retryable=True,
                )
            except _ProviderCallError as caught_error:
                total_latency_ms += self._elapsed_ms(started_at)
                failure = caught_error
            except httpx.TimeoutException as timeout_error:
                elapsed_before_timeout_ms = self._elapsed_ms(started_at)
                total_latency_ms += elapsed_before_timeout_ms
                failure = self._timeout_failure(timeout_error)
            except httpx.NetworkError:
                total_latency_ms += self._elapsed_ms(started_at)
                failure = _ProviderCallError(
                    TaggingFailureType.NETWORK_ERROR,
                    retryable=True,
                )

            self._log_provider_failure(
                config,
                len(inputs),
                attempt_count,
                failure.failure_type,
                elapsed_before_timeout_ms=elapsed_before_timeout_ms,
                configured_read_timeout=self._configured_read_timeout_seconds(),
            )
            if not self._should_retry_failure(failure, attempt_count):
                return ProviderTaggingFailure(
                    provider_id=config.provider_id,
                    model_id=config.model_id,
                    failure_type=failure.failure_type,
                    attempt_count=attempt_count,
                    latency_ms=total_latency_ms,
                )

            await self._sleep(
                self._retry_delay_seconds_for_failure(failure, attempt_count)
            )

        raise AssertionError("Provider 重试循环不应自然结束")

    async def _request_assistant_content(
        self,
        config: TaggingProviderConfig,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """通过固定 OpenAI-compatible 路由请求并提取纯 assistant content。"""

        payload: dict[str, Any] = {
            "model": config.model_id,
            # 严格 JSON Validator 必须等待完整响应；本阶段明确保持非流式模式。
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # 仅合并经该 Provider 明确配置的兼容选项，例如已验证可用的
        # reasoning 或 temperature。reasoning_profile 本身不能盲发成 API 字段。
        payload.update(config.request_options)

        response = await self._client.post(
            "chat/completions",
            json=payload,
        )
        self._raise_for_response_status(response)

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise _ProviderCallError(
                TaggingFailureType.INVALID_RESPONSE,
                retryable=True,
            ) from exc

        if not isinstance(response_payload, dict):
            raise _ProviderCallError(
                TaggingFailureType.INVALID_RESPONSE,
                retryable=True,
            )

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _ProviderCallError(
                TaggingFailureType.INVALID_RESPONSE,
                retryable=True,
            )
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise _ProviderCallError(
                TaggingFailureType.INVALID_RESPONSE,
                retryable=True,
            )
        finish_reason = first_choice.get("finish_reason")
        if finish_reason == "length":
            # 不记录响应正文；只留下安全的截断信号以确认 Provider 输出上限问题。
            logger.warning(
                "AI Tagging provider=%s model=%s status=output_truncated "
                "finish_reason=length",
                config.provider_id,
                config.model_id,
            )
            raise _BatchSizeRelatedError
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise _ProviderCallError(
                TaggingFailureType.INVALID_RESPONSE,
                retryable=True,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise _ProviderCallError(
                TaggingFailureType.INVALID_RESPONSE,
                retryable=True,
            )
        return content

    @classmethod
    def _raise_for_response_status(cls, response: httpx.Response) -> None:
        """将 HTTP 层状态映射为明确、可重试性不同的 Provider 失败。"""

        status_code = response.status_code
        if 200 <= status_code < 300:
            return
        if status_code == 413:
            raise _BatchSizeRelatedError
        if status_code == 400 and cls._response_indicates_size_limit(response):
            raise _BatchSizeRelatedError
        # UniAPI 使用 402 明确表示账户可用额度不足。这不是请求参数错误，
        # 更不是可通过拆批或自动重试解决的问题，必须让 UI/日志准确提示。
        if status_code == 402:
            raise _ProviderCallError(
                TaggingFailureType.QUOTA_EXHAUSTED,
                retryable=False,
            )
        if status_code in {401, 403}:
            raise _ProviderCallError(
                TaggingFailureType.AUTH_ERROR,
                retryable=False,
            )
        if status_code == 404:
            raise _ProviderCallError(
                TaggingFailureType.MODEL_NOT_AVAILABLE,
                retryable=False,
            )
        if status_code == 429:
            raise _ProviderCallError(
                TaggingFailureType.RATE_LIMITED,
                retryable=True,
            )
        if status_code in cls._RETRYABLE_SERVER_STATUS_CODES:
            raise _ProviderCallError(
                TaggingFailureType.SERVER_ERROR,
                retryable=True,
            )
        raise _ProviderCallError(
            TaggingFailureType.INVALID_REQUEST,
            retryable=False,
        )

    @staticmethod
    def _response_indicates_size_limit(response: httpx.Response) -> bool:
        """仅用有限关键字识别 400 的上下文/实体大小错误，绝不记录正文。"""

        try:
            message = response.text.lower()
        except Exception:
            return False
        size_markers = (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "request too large",
            "entity too large",
            "payload too large",
        )
        return any(marker in message for marker in size_markers)

    @staticmethod
    def _is_size_related_validation_failure(
        content: str,
        inputs: Sequence[TaggingInput],
    ) -> bool:
        """只将截断或明显少行视作容量问题，普通协议错误仍沿用原重试。"""

        stripped_content = content.rstrip()
        # 仅以 JSON 开头但没有正常结束的内容才像输出截断；例如 ``not-json``
        # 是普通协议错误，应沿用既有重试而不是错误拆批。
        if stripped_content.startswith("{") and not stripped_content.endswith("}"):
            return True
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return False
        rows = payload.get("rows") if isinstance(payload, dict) else None
        return (
            isinstance(rows, list)
            and len(inputs) >= 8
            and len(rows) * 2 < len(inputs)
        )

    def _ensure_tagging_configuration(self) -> None:
        """仅在业务入口检查 Key/model_id，避免未配置时阻断应用启动。"""

        missing_settings: list[str] = []
        if not self._api_key:
            missing_settings.append("UNIAPI_API_KEY")
        for config in self._provider_configs:
            if not config.model_id:
                missing_settings.append(f"{config.provider_id} model_id")
        if missing_settings:
            raise AiServiceConfigurationError(
                "AI Tagging 配置缺失：" + ", ".join(missing_settings)
            )

    @classmethod
    def _validate_provider_configs(
        cls,
        configs: Sequence[TaggingProviderConfig],
    ) -> None:
        """拒绝漏 Provider、重复 Provider 或不稳定 identity。"""

        provider_ids = [config.provider_id for config in configs]
        if set(provider_ids) != cls._EXPECTED_PROVIDER_IDS:
            raise ValueError("AI Tagging 必须且只能配置三家固定 Provider")
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("AI Tagging Provider 不允许重复")

    @staticmethod
    def _validate_positive_integer(value: int, name: str) -> None:
        """统一校验批次大小和尝试次数。"""

        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须是正整数")

    @staticmethod
    def _validate_retry_settings(
        base_delay: float,
        max_delay: float,
        jitter: float,
    ) -> None:
        """重试参数必须是非负有限值，且最大延迟不能小于基础延迟。"""

        if min(base_delay, max_delay, jitter) < 0:
            raise ValueError("重试延迟不能为负数")
        if max_delay < base_delay:
            raise ValueError("最大重试延迟不能小于基础重试延迟")

    @staticmethod
    def _validate_non_negative_finite_number(value: float, name: str) -> None:
        """校验超时后的谨慎等待时间，拒绝 NaN、无穷和负数。"""

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{name} 必须是非负有限数值")

    @staticmethod
    def _validate_inputs(inputs: Sequence[TaggingInput]) -> None:
        """避免空批和重复 identity 进入任一 Provider。"""

        if not inputs:
            raise ValueError("TaggingInput 不能为空")
        item_ids = [tagging_input.item_id for tagging_input in inputs]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("AI Tagging 输入 item_id 不能重复")

    @staticmethod
    def _raise_if_cancelling() -> None:
        """在下一批启动前响应父任务取消。"""

        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise asyncio.CancelledError

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        """返回不泄露请求内容的毫秒级运行时诊断数据。"""

        return max(0, round((time.perf_counter() - started_at) * 1000))

    def _retry_delay_seconds(self, attempt_count: int) -> float:
        """计算有限指数退避与小抖动，不在 Qt 线程中阻塞。"""

        exponential_delay = min(
            self._retry_base_delay_seconds * (2 ** (attempt_count - 1)),
            self._retry_max_delay_seconds,
        )
        return exponential_delay + self._random_uniform(
            0.0,
            self._retry_jitter_seconds,
        )

    @staticmethod
    def _timeout_failure(timeout_error: httpx.TimeoutException) -> _ProviderCallError:
        """把 httpx 超时细分为可诊断的安全业务失败类型。"""

        if isinstance(timeout_error, httpx.ConnectTimeout):
            failure_type = TaggingFailureType.CONNECT_TIMEOUT
        elif isinstance(timeout_error, httpx.ReadTimeout):
            failure_type = TaggingFailureType.READ_TIMEOUT
        elif isinstance(timeout_error, httpx.WriteTimeout):
            failure_type = TaggingFailureType.WRITE_TIMEOUT
        elif isinstance(timeout_error, httpx.PoolTimeout):
            failure_type = TaggingFailureType.POOL_TIMEOUT
        else:
            # 兼容 httpx 未来新增的 TimeoutException 子类，仍保留可检索分类。
            failure_type = TaggingFailureType.TIMEOUT
        # 正式 Client 的 read=None 不会自行抛出 ReadTimeout。若下游 transport
        # 仍给出该异常，绝不重新提交可能已在上游继续执行的长生成请求。
        return _ProviderCallError(
            failure_type,
            retryable=failure_type is not TaggingFailureType.READ_TIMEOUT,
        )

    def _should_retry_failure(
        self,
        failure: _ProviderCallError,
        attempt_count: int,
    ) -> bool:
        """决定当前失败是否还能安全重试，读取/写入超时采用更严格上限。"""

        if not failure.retryable:
            return False
        attempt_limit = self._max_attempts
        if failure.failure_type in self._CAUTIOUS_TIMEOUT_FAILURE_TYPES:
            attempt_limit = self._write_timeout_max_attempts
        return attempt_count < attempt_limit

    def _retry_delay_seconds_for_failure(
        self,
        failure: _ProviderCallError,
        attempt_count: int,
    ) -> float:
        """写入超时后保留冷静期，避免服务端仍执行时立即重复提交。"""

        normal_delay = self._retry_delay_seconds(attempt_count)
        if failure.failure_type in self._CAUTIOUS_TIMEOUT_FAILURE_TYPES:
            return max(normal_delay, self._write_timeout_retry_delay_seconds)
        return normal_delay

    def _configured_read_timeout_seconds(self) -> float | None:
        """返回当前实际 Client 的读取超时，用于无敏感诊断日志。"""

        read_timeout = self._client.timeout.read
        if isinstance(read_timeout, (int, float)) and math.isfinite(read_timeout):
            return float(read_timeout)
        return None

    @staticmethod
    def _log_provider_success(
        config: TaggingProviderConfig,
        batch_size: int,
        attempt_count: int,
        latency_ms: int,
    ) -> None:
        """记录无敏感内容的正常调用摘要。"""

        logger.info(
            "AI Tagging provider=%s model=%s batch_size=%s attempt=%s "
            "latency_ms=%s status=success",
            config.provider_id,
            config.model_id,
            batch_size,
            attempt_count,
            latency_ms,
        )

    @staticmethod
    def _log_provider_failure(
        config: TaggingProviderConfig,
        batch_size: int,
        attempt_count: int,
        failure_type: TaggingFailureType,
        *,
        elapsed_before_timeout_ms: int | None,
        configured_read_timeout: float | None,
    ) -> None:
        """只记录失败分类，不记录 Key、Header、Prompt 或原始响应。"""

        timeout_types = {
            TaggingFailureType.TIMEOUT,
            TaggingFailureType.CONNECT_TIMEOUT,
            TaggingFailureType.READ_TIMEOUT,
            TaggingFailureType.WRITE_TIMEOUT,
            TaggingFailureType.POOL_TIMEOUT,
        }
        if failure_type in timeout_types:
            logger.warning(
                "AI Tagging provider=%s model=%s batch_size=%s attempt=%s "
                "status=timeout timeout_type=%s elapsed_before_timeout_ms=%s "
                "configured_read_timeout=%s",
                config.provider_id,
                config.model_id,
                batch_size,
                attempt_count,
                failure_type.value,
                elapsed_before_timeout_ms,
                configured_read_timeout,
            )
            return
        logger.warning(
            "AI Tagging provider=%s model=%s batch_size=%s attempt=%s "
            "status=failed error_category=%s",
            config.provider_id,
            config.model_id,
            batch_size,
            attempt_count,
            failure_type.value,
        )
