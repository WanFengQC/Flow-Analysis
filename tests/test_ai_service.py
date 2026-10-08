"""UniAPI 三模型 Tagging Client 与严格共识的无网络单元测试。"""

import asyncio
import inspect
import json
import logging
import time
import unittest
from collections.abc import Awaitable, Callable
from unittest.mock import patch

import httpx

from config.settings import (
    TAGGING_BATCH_SIZE,
    TAGGING_CLAUDE_CONCURRENCY,
    TAGGING_GEMINI_CONCURRENCY,
    TAGGING_GPT_CONCURRENCY,
    TAGGING_HTTP_CONNECT_TIMEOUT_SECONDS,
    TAGGING_HTTP_POOL_TIMEOUT_SECONDS,
    TAGGING_HTTP_READ_TIMEOUT_SECONDS,
    TAGGING_HTTP_WRITE_TIMEOUT_SECONDS,
    UNIAPI_RPM_LIMIT,
)
from models.tagging import TagLabel, TaggingInput
from models.tagging_provider import (
    ProviderTaggingFailure,
    TaggingConsensusStatus,
    TaggingFailureType,
    TaggingProviderConfig,
)
from services.ai_service import (
    AiService,
    default_tagging_provider_configs,
)
from services.tagging_prompt import TaggingPromptBuilder


def _inputs() -> list[TaggingInput]:
    """构造包含不同 ProductContext 的最小真实业务输入。"""

    base = {
        "month": "202608",
        "category": "Stuffed Animals",
        "top_phrases": ("weighted stuffed animal",),
        "product_context_source": "PRODUCT_KNOWLEDGE_BASE",
        "representative_asin": "B0DSHYXD4G",
    }
    return [
        TaggingInput(
            item_id="202608::weighted",
            word="weighted",
            product_context={"project": "M", "variation": "Leopard"},
            **base,
        ),
        TaggingInput(
            item_id="202608::leopard",
            word="leopard",
            product_context={"project": "U", "variation": "Avocado"},
            **base,
        ),
    ]


def _many_inputs(count: int) -> list[TaggingInput]:
    """构造共享背景的大批输入，专门验证批次、限流与协议完整性。"""

    return [
        TaggingInput(
            item_id=f"202608::word-{index}",
            month="202608",
            word=f"word-{index}",
            category="Stuffed Animals",
            top_phrases=("weighted stuffed animal",),
            product_context_source="PRODUCT_KNOWLEDGE_BASE",
            representative_asin="B0DSHYXD4G",
            product_context={
                "project": "M",
                "description": "用于批次吞吐测试的共享产品背景。",
            },
        )
        for index in range(count)
    ]


def _configs(
    openai_concurrency: int = 2,
    other_provider_concurrency: int = 2,
) -> tuple[TaggingProviderConfig, ...]:
    """提供显式测试 model_id，避免依赖真实环境配置。"""

    return (
        TaggingProviderConfig(
            "openai",
            "GPT-6 Sol",
            "uni-gpt-6-sol",
            openai_concurrency,
        ),
        TaggingProviderConfig(
            "anthropic",
            "Claude Sonnet 5",
            "uni-claude-sonnet-5",
            other_provider_concurrency,
        ),
        TaggingProviderConfig(
            "google",
            "Gemini 3.8 Flash",
            "uni-gemini-3.8-flash",
            other_provider_concurrency,
        ),
    )


def _content(
    request: httpx.Request,
    labels: tuple[str, str] = ("3属性", "2外形"),
    reasons: tuple[str, str] = ("表示加重属性。", "表示动物造型。"),
    reverse_rows: bool = False,
) -> str:
    """根据真实请求中的 rows 返回严格 JSON，便于检查 model 与输入。"""

    payload = json.loads(request.content)
    rows = json.loads(payload["messages"][1]["content"].split("\n", 1)[1])["rows"]
    response_rows = [
        {
            "itemId": rows[0]["itemId"],
            "label": labels[0],
            "reason": reasons[0],
        },
        {
            "itemId": rows[1]["itemId"],
            "label": labels[1],
            "reason": reasons[1],
        },
    ]
    if reverse_rows:
        response_rows.reverse()
    return json.dumps({"rows": response_rows}, ensure_ascii=False)


def _ok_response(request: httpx.Request, **kwargs: object) -> httpx.Response:
    """构造 OpenAI-compatible 成功包络。"""

    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": _content(request, **kwargs)}}]},
    )


def _all_rows_response(request: httpx.Request) -> httpx.Response:
    """按请求中的任意 rows 数量构造严格合法响应。"""

    payload = json.loads(request.content)
    rows = json.loads(payload["messages"][1]["content"].split("\n", 1)[1])["rows"]
    content = json.dumps(
        {
            "rows": [
                {
                    "itemId": row["itemId"],
                    "label": "3属性",
                    "reason": "测试中的有效业务理由。",
                }
                for row in rows
            ]
        },
        ensure_ascii=False,
    )
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class AiServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证网络边界、重试边界与三方共识都没有隐式业务裁决。"""

    async def asyncSetUp(self) -> None:
        """为每个测试建立隔离 MockTransport。"""

        self.inputs = _inputs()
        self.requests: list[httpx.Request] = []

    def _service(
        self,
        handler: Callable[[httpx.Request], object],
        *,
        batch_size: int = 10,
        openai_concurrency: int = 2,
        other_provider_concurrency: int = 2,
        max_attempts: int = 4,
        write_timeout_max_attempts: int = 2,
        write_timeout_retry_delay_seconds: float = 0,
        requests_per_minute: int = UNIAPI_RPM_LIMIT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] | None = None,
    ) -> AiService:
        """建立使用同一个 mock AsyncClient 的真实 AiService。"""

        async def wrapped_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            result = handler(request)
            if asyncio.iscoroutine(result):
                return await result
            if isinstance(result, BaseException):
                raise result
            assert isinstance(result, httpx.Response)
            return result

        client = httpx.AsyncClient(
            base_url="https://api.uniapi.io/v1/",
            transport=httpx.MockTransport(wrapped_handler),
        )
        return AiService(
            api_key="test-key-not-logged",
            provider_configs=_configs(
                openai_concurrency,
                other_provider_concurrency,
            ),
            client=client,
            batch_size=batch_size,
            max_attempts=max_attempts,
            retry_base_delay_seconds=0,
            retry_max_delay_seconds=0,
            retry_jitter_seconds=0,
            write_timeout_max_attempts=write_timeout_max_attempts,
            write_timeout_retry_delay_seconds=write_timeout_retry_delay_seconds,
            requests_per_minute=requests_per_minute,
            sleep=sleep,
            random_uniform=lambda _minimum, _maximum: 0,
            monotonic=monotonic or time.monotonic,
        )

    async def test_default_client_uses_dedicated_long_ai_timeout(self):
        """AI Client 的长生成读取阶段必须真正关闭本地超时。"""

        service = AiService(
            api_key="test-key-not-logged",
            provider_configs=_configs(),
        )
        try:
            timeout = service._client.timeout
            self.assertEqual(timeout.connect, TAGGING_HTTP_CONNECT_TIMEOUT_SECONDS)
            self.assertEqual(timeout.read, TAGGING_HTTP_READ_TIMEOUT_SECONDS)
            self.assertEqual(timeout.write, TAGGING_HTTP_WRITE_TIMEOUT_SECONDS)
            self.assertEqual(timeout.pool, TAGGING_HTTP_POOL_TIMEOUT_SECONDS)
            self.assertIsNone(TAGGING_HTTP_READ_TIMEOUT_SECONDS)
            self.assertIsNone(timeout.read)
        finally:
            await service.aclose()

    def test_ai_request_path_has_no_outer_hard_timeout(self):
        """AiService 内不能用 asyncio 总时长上限提前取消长生成请求。"""

        source = inspect.getsource(AiService)
        self.assertNotIn("asyncio.wait_for(", source)
        self.assertNotIn("asyncio.timeout(", source)

    async def test_explicit_claude_output_limit_reduces_shared_batch_size(self):
        """某家已确认输出上限较小时，三家都按最小安全批次传输相同输入。"""

        configs = list(_configs())
        configs[1] = TaggingProviderConfig(
            "anthropic",
            "Claude Sonnet 5",
            "uni-claude-sonnet-5",
            2,
            request_options={"max_tokens": 4096},
        )
        client = httpx.AsyncClient(
            base_url="https://api.uniapi.io/v1/",
            transport=httpx.MockTransport(_all_rows_response),
        )
        service = AiService(
            api_key="test-key-not-logged",
            provider_configs=configs,
            client=client,
            batch_size=100,
        )
        try:
            batches = service._build_safe_batches(_many_inputs(100))
            self.assertEqual([len(batch) for batch in batches], [50, 50])
        finally:
            await service.aclose()

    async def test_three_providers_start_concurrently_and_reuse_same_input(self):
        """同一批三路请求必须并发，且每家看到完全相同业务 rows。"""

        started_models: set[str] = set()
        all_started = asyncio.Event()
        release = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            model_id = json.loads(request.content)["model"]
            started_models.add(model_id)
            if len(started_models) == 3:
                all_started.set()
            await release.wait()
            return _ok_response(request)

        service = self._service(handler)
        task = asyncio.create_task(service.tag_inputs(self.inputs))
        await asyncio.wait_for(all_started.wait(), timeout=1)
        self.assertEqual(len(started_models), 3)
        release.set()
        results = await task
        await service.aclose()

        self.assertTrue(
            all(result.status == TaggingConsensusStatus.CONSENSUS for result in results)
        )
        request_rows = {
            json.loads(request.content)["messages"][1]["content"]
            for request in self.requests
        }
        self.assertEqual(len(request_rows), 1)
        self.assertTrue(
            all(
                request.url.scheme in {"http", "https"}
                and request.url.path.endswith("/chat/completions")
                for request in self.requests
            )
        )

    async def test_250_inputs_split_to_100_100_50_and_each_provider_receives_same_batch(self):
        """目标批次为 100；多批可并行，但三方不能看到不同的业务切片。"""

        batch_rows_by_model: dict[str, list[tuple[str, ...]]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            rows = json.loads(
                payload["messages"][1]["content"].split("\n", 1)[1]
            )["rows"]
            batch_rows_by_model.setdefault(payload["model"], []).append(
                tuple(row["itemId"] for row in rows)
            )
            return _all_rows_response(request)

        service = self._service(handler, batch_size=100)
        results = await service.tag_inputs(_many_inputs(250))
        await service.aclose()

        self.assertEqual(len(results), 250)
        batch_sizes = sorted(
            len(batch) for batch in batch_rows_by_model["uni-gpt-6-sol"]
        )
        self.assertEqual(batch_sizes, [50, 100, 100])
        self.assertEqual(
            batch_rows_by_model["uni-gpt-6-sol"],
            batch_rows_by_model["uni-claude-sonnet-5"],
        )
        self.assertEqual(
            batch_rows_by_model["uni-gpt-6-sol"],
            batch_rows_by_model["uni-gemini-3.8-flash"],
        )

    async def test_each_provider_and_child_batch_has_its_own_complete_context(self):
        """拆批后每家 Provider 都收到可独立执行的 contexts + rows。"""

        payloads_by_model: dict[str, list[dict[str, object]]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload = json.loads(request.content)
            prompt_payload = json.loads(
                request_payload["messages"][1]["content"].split("\n", 1)[1]
            )
            payloads_by_model.setdefault(request_payload["model"], []).append(
                prompt_payload
            )
            return _all_rows_response(request)

        service = self._service(handler, batch_size=50)
        results = await service.tag_inputs(_many_inputs(100))
        await service.aclose()

        self.assertEqual(len(results), 100)
        for provider_payloads in payloads_by_model.values():
            self.assertEqual(len(provider_payloads), 2)
            for prompt_payload in provider_payloads:
                self.assertEqual(set(prompt_payload["contexts"]), {"ctx_1"})
                self.assertEqual(
                    prompt_payload["contexts"]["ctx_1"]["productContext"]["project"],
                    "M",
                )
                self.assertTrue(
                    all(
                        row["contextId"] == "ctx_1"
                        for row in prompt_payload["rows"]
                    )
                )

        expected_batches = sorted(
            tuple(row["itemId"] for row in payload["rows"])
            for payload in payloads_by_model["uni-gpt-6-sol"]
        )
        for model_id in ("uni-claude-sonnet-5", "uni-gemini-3.8-flash"):
            self.assertEqual(
                sorted(
                    tuple(row["itemId"] for row in payload["rows"])
                    for payload in payloads_by_model[model_id]
                ),
                expected_batches,
            )

    async def test_provider_semaphores_cap_each_provider_at_eight(self):
        """10 个批次并行时，每家 Provider 的活跃 HTTP 请求最多为 8。"""

        active_by_model: dict[str, int] = {}
        maximum_by_model: dict[str, int] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            active_by_model[model] = active_by_model.get(model, 0) + 1
            maximum_by_model[model] = max(
                maximum_by_model.get(model, 0),
                active_by_model[model],
            )
            await asyncio.sleep(0.01)
            active_by_model[model] -= 1
            return _all_rows_response(request)

        service = self._service(
            handler,
            batch_size=100,
            openai_concurrency=8,
            other_provider_concurrency=8,
        )
        await service.tag_inputs(_many_inputs(1_000))
        await service.aclose()

        self.assertEqual(set(maximum_by_model), {
            "uni-gpt-6-sol",
            "uni-claude-sonnet-5",
            "uni-gemini-3.8-flash",
        })
        self.assertTrue(all(value <= 8 for value in maximum_by_model.values()))
        self.assertEqual(max(maximum_by_model.values()), 8)

    async def test_same_labels_create_consensus_and_use_openai_reason(self):
        """三个 reason 不同但 label 相同，仍是共识且理由稳定取 OpenAI。"""

        reasons_by_model = {
            "uni-gpt-6-sol": ("OpenAI 理由", "OpenAI 外形理由"),
            "uni-claude-sonnet-5": ("Claude 理由", "Claude 外形理由"),
            "uni-gemini-3.8-flash": ("Gemini 理由", "Gemini 外形理由"),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            model_id = json.loads(request.content)["model"]
            return _ok_response(request, reasons=reasons_by_model[model_id])

        service = self._service(handler)
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        self.assertEqual(results[0].status, TaggingConsensusStatus.CONSENSUS)
        self.assertEqual(results[0].consensus_label, TagLabel.ATTRIBUTE)
        self.assertEqual(results[0].consensus_reason, "OpenAI 理由")

    async def test_partial_retry_only_sends_requested_provider_and_reuses_limiter_path(self):
        """局部恢复只发送失败 Google，仍通过原有 batch 与 semaphore 路径。"""

        active_count = 0
        maximum_active_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_count, maximum_active_count
            model_id = json.loads(request.content)["model"]
            self.assertEqual(model_id, "uni-gemini-3.8-flash")
            active_count += 1
            maximum_active_count = max(maximum_active_count, active_count)
            await asyncio.sleep(0.01)
            active_count -= 1
            return _all_rows_response(request)

        service = self._service(
            handler,
            batch_size=1,
            other_provider_concurrency=1,
        )
        outcomes = await service.tag_inputs_for_providers(
            self.inputs,
            ("google",),
        )
        await service.aclose()

        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(
            set(provider_outcomes) == {"google"}
            for provider_outcomes in outcomes.values()
        ))
        self.assertLessEqual(maximum_active_count, 1)

    async def test_two_vs_one_is_disagreement_not_majority_vote(self):
        """2/3 相同也不能形成正式标签。"""

        def handler(request: httpx.Request) -> httpx.Response:
            model_id = json.loads(request.content)["model"]
            labels = ("3属性", "2外形")
            if model_id == "uni-gemini-3.8-flash":
                labels = ("5规格", "2外形")
            return _ok_response(request, labels=labels)

        service = self._service(handler)
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        self.assertEqual(results[0].status, TaggingConsensusStatus.DISAGREEMENT)
        self.assertIsNone(results[0].consensus_label)
        self.assertEqual(results[1].status, TaggingConsensusStatus.CONSENSUS)

    async def test_timeout_and_rate_limit_retry_then_succeed(self):
        """连接超时与 429 都属于有限可恢复失败，后续成功必须保留尝试次数。"""

        attempts: dict[str, int] = {}

        def handler(request: httpx.Request) -> object:
            model_id = json.loads(request.content)["model"]
            attempts[model_id] = attempts.get(model_id, 0) + 1
            if model_id == "uni-gpt-6-sol" and attempts[model_id] == 1:
                return httpx.ConnectTimeout("mock timeout", request=request)
            if model_id == "uni-claude-sonnet-5" and attempts[model_id] == 1:
                return httpx.Response(429)
            return _ok_response(request)

        service = self._service(handler)
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        self.assertEqual(attempts["uni-gpt-6-sol"], 2)
        self.assertEqual(attempts["uni-claude-sonnet-5"], 2)
        self.assertEqual(results[0].status, TaggingConsensusStatus.CONSENSUS)
        self.assertEqual(
            results[0].provider_results["openai"].attempt_count,
            2,
        )

    async def test_auth_and_missing_model_never_retry_and_mark_incomplete(self):
        """401 与 404 是配置类失败，不能机械重试或偷偷切换模型。"""

        def handler(request: httpx.Request) -> httpx.Response:
            model_id = json.loads(request.content)["model"]
            if model_id == "uni-gpt-6-sol":
                return httpx.Response(401)
            if model_id == "uni-claude-sonnet-5":
                return httpx.Response(404)
            return _ok_response(request)

        service = self._service(handler)
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        openai_failure = results[0].provider_results["openai"]
        claude_failure = results[0].provider_results["anthropic"]
        self.assertIsInstance(openai_failure, ProviderTaggingFailure)
        self.assertIsInstance(claude_failure, ProviderTaggingFailure)
        assert isinstance(openai_failure, ProviderTaggingFailure)
        assert isinstance(claude_failure, ProviderTaggingFailure)
        self.assertEqual(openai_failure.failure_type, TaggingFailureType.AUTH_ERROR)
        self.assertEqual(claude_failure.failure_type, TaggingFailureType.MODEL_NOT_AVAILABLE)
        self.assertEqual(openai_failure.attempt_count, 1)
        self.assertEqual(claude_failure.attempt_count, 1)
        self.assertEqual(results[0].status, TaggingConsensusStatus.INCOMPLETE)
        self.assertEqual(
            [json.loads(request.content)["model"] for request in self.requests].count(
                "uni-gpt-6-sol"
            ),
            1,
        )

    async def test_quota_exhausted_never_retries_and_marks_incomplete(self):
        """HTTP 402 必须准确显示为额度不足，不能伪装成无效请求。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if json.loads(request.content)["model"] == "uni-gpt-6-sol":
                return httpx.Response(402)
            return _ok_response(request)

        service = self._service(handler)
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        failure = results[0].provider_results["openai"]
        self.assertIsInstance(failure, ProviderTaggingFailure)
        assert isinstance(failure, ProviderTaggingFailure)
        self.assertEqual(
            failure.failure_type,
            TaggingFailureType.QUOTA_EXHAUSTED,
        )
        self.assertEqual(failure.attempt_count, 1)
        self.assertEqual(results[0].status, TaggingConsensusStatus.INCOMPLETE)

    async def test_invalid_protocol_variants_retry_and_validator_restores_order(self):
        """非法 JSON、非法 label、漏 item 都不能修复，只能重试。"""

        for invalid_kind in ("json", "label", "missing"):
            with self.subTest(invalid_kind=invalid_kind):
                attempts = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal attempts
                    model_id = json.loads(request.content)["model"]
                    if model_id == "uni-gpt-6-sol":
                        attempts += 1
                        if attempts == 1:
                            if invalid_kind == "json":
                                content = "not-json"
                            elif invalid_kind == "label":
                                content = _content(request, labels=("属性", "2外形"))
                            else:
                                response_payload = json.loads(_content(request))
                                response_payload["rows"] = response_payload["rows"][:1]
                                content = json.dumps(response_payload, ensure_ascii=False)
                            return httpx.Response(
                                200,
                                json={"choices": [{"message": {"content": content}}]},
                            )
                    return _ok_response(request, reverse_rows=True)

                service = self._service(handler)
                results = await service.tag_inputs(self.inputs)
                await service.aclose()
                self.assertEqual(attempts, 2)
                self.assertEqual(results[0].status, TaggingConsensusStatus.CONSENSUS)
                self.assertEqual(
                    [
                        decision.item_id
                        for decision in results[0].provider_results["openai"].decisions
                    ],
                    ["202608::weighted"],
                )

    async def test_retry_exhaustion_is_incomplete_not_disagreement(self):
        """一个 Provider 持续无效时，成功的两家不能凑成多数结论。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if json.loads(request.content)["model"] == "uni-gpt-6-sol":
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "not-json"}}]},
                )
            return _ok_response(request)

        service = self._service(handler)
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        failure = results[0].provider_results["openai"]
        self.assertIsInstance(failure, ProviderTaggingFailure)
        assert isinstance(failure, ProviderTaggingFailure)
        self.assertEqual(failure.failure_type, TaggingFailureType.INVALID_RESPONSE)
        self.assertEqual(failure.attempt_count, 4)
        self.assertEqual(results[0].status, TaggingConsensusStatus.INCOMPLETE)

    async def test_per_provider_semaphore_limits_concurrent_requests(self):
        """同一 Provider 的并发调用不会超过自己的配置槽位。"""

        active_count = 0
        max_active_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_count, max_active_count
            if json.loads(request.content)["model"] == "uni-gpt-6-sol":
                active_count += 1
                max_active_count = max(max_active_count, active_count)
                await asyncio.sleep(0.02)
                active_count -= 1
            return _ok_response(request)

        service = self._service(handler, openai_concurrency=1)
        config = service._provider_configs[0]
        system_prompt = TaggingPromptBuilder.build_system_prompt()
        user_prompt = TaggingPromptBuilder.build_user_prompt(self.inputs)
        await asyncio.gather(
            service._tag_provider_batch(config, self.inputs, system_prompt, user_prompt),
            service._tag_provider_batch(config, self.inputs, system_prompt, user_prompt),
        )
        await service.aclose()
        self.assertEqual(max_active_count, 1)

    async def test_global_rpm_limiter_is_shared_by_all_three_providers(self):
        """三家总和共享 RPM，而不是各自错误地获得完整额度。"""

        now = 0.0
        sleep_calls: list[float] = []
        request_times: list[float] = []

        async def advance_clock(wait_seconds: float) -> None:
            nonlocal now
            sleep_calls.append(wait_seconds)
            now += wait_seconds

        def handler(request: httpx.Request) -> httpx.Response:
            request_times.append(now)
            return _all_rows_response(request)

        client = httpx.AsyncClient(
            base_url="https://api.uniapi.io/v1/",
            transport=httpx.MockTransport(handler),
        )
        service = AiService(
            api_key="test-key-not-logged",
            provider_configs=_configs(1, 1),
            client=client,
            batch_size=1,
            requests_per_minute=3,
            retry_base_delay_seconds=0,
            retry_max_delay_seconds=0,
            retry_jitter_seconds=0,
            sleep=advance_clock,
            monotonic=lambda: now,
        )
        await service.tag_inputs(self.inputs)
        await service.aclose()

        self.assertEqual(len(request_times), 6)
        self.assertEqual(request_times.count(0.0), 3)
        self.assertEqual(request_times.count(60.0), 3)
        self.assertTrue(any(wait >= 60.0 for wait in sleep_calls))

    async def test_response_size_failure_splits_100_to_50_without_duplicate_results(self):
        """413 表示容量问题，必须拆批而非将同一 100 条原样重试四次。"""

        requested_sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            rows = json.loads(
                payload["messages"][1]["content"].split("\n", 1)[1]
            )["rows"]
            requested_sizes.append(len(rows))
            if payload["model"] == "uni-gpt-6-sol" and len(rows) == 100:
                return httpx.Response(413)
            return _all_rows_response(request)

        service = self._service(handler, batch_size=100)
        results = await service.tag_inputs(_many_inputs(100))
        await service.aclose()

        self.assertEqual(len(results), 100)
        self.assertEqual(len({result.item_id for result in results}), 100)
        self.assertEqual(requested_sizes.count(100), 3)
        self.assertEqual(requested_sizes.count(50), 6)

    async def test_truncated_completion_splits_batch_instead_of_repeating_100_rows(self):
        """finish_reason=length 是输出截断，必须触发容量恢复而非普通重试。"""

        requested_sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            rows = json.loads(
                payload["messages"][1]["content"].split("\n", 1)[1]
            )["rows"]
            requested_sizes.append(len(rows))
            if payload["model"] == "uni-gpt-6-sol" and len(rows) == 100:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"content": "{}"},
                            }
                        ]
                    },
                )
            return _all_rows_response(request)

        service = self._service(handler, batch_size=100)
        results = await service.tag_inputs(_many_inputs(100))
        await service.aclose()

        self.assertEqual(len(results), 100)
        self.assertEqual(requested_sizes.count(100), 3)
        self.assertEqual(requested_sizes.count(50), 6)

    async def test_read_timeout_is_not_retried_to_avoid_duplicate_long_generation(self):
        """下游异常 ReadTimeout 不重试，避免重提可能仍在上游执行的请求。"""

        attempts = 0
        requested_sizes: list[int] = []

        def handler(request: httpx.Request) -> object:
            nonlocal attempts
            payload = json.loads(request.content)
            rows = json.loads(
                payload["messages"][1]["content"].split("\n", 1)[1]
            )["rows"]
            requested_sizes.append(len(rows))
            if payload["model"] == "uni-gpt-6-sol" and attempts == 0:
                attempts += 1
                return httpx.ReadTimeout("mock timeout", request=request)
            return _all_rows_response(request)

        with self.assertLogs("services.ai_service", level="WARNING") as logs:
            service = self._service(handler, batch_size=100)
            results = await service.tag_inputs(_many_inputs(100))
        await service.aclose()

        self.assertEqual(len(results), 100)
        self.assertEqual(requested_sizes.count(100), 3)
        self.assertEqual(attempts, 1)
        self.assertTrue(any("timeout_type=READ_TIMEOUT" in line for line in logs.output))

    async def test_connect_timeout_is_classified_and_uses_normal_retry_limit(self):
        """连接超时通常尚未送达上游，按正常有限重试并保留精确分类。"""

        def handler(request: httpx.Request) -> object:
            if json.loads(request.content)["model"] == "uni-gpt-6-sol":
                return httpx.ConnectTimeout("mock connect timeout", request=request)
            return _ok_response(request)

        service = self._service(handler, max_attempts=3)
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        failure = results[0].provider_results["openai"]
        self.assertIsInstance(failure, ProviderTaggingFailure)
        assert isinstance(failure, ProviderTaggingFailure)
        self.assertEqual(failure.failure_type, TaggingFailureType.CONNECT_TIMEOUT)
        self.assertEqual(failure.attempt_count, 3)

    async def test_write_timeout_is_classified_and_waits_before_single_retry(self):
        """写入超时只允许一次延迟重试，避免可能送达时快速重复提交。"""

        sleep_calls: list[float] = []

        async def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        def handler(request: httpx.Request) -> object:
            if json.loads(request.content)["model"] == "uni-gpt-6-sol":
                return httpx.WriteTimeout("mock write timeout", request=request)
            return _ok_response(request)

        service = self._service(
            handler,
            max_attempts=4,
            write_timeout_max_attempts=2,
            write_timeout_retry_delay_seconds=30,
            sleep=record_sleep,
        )
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        failure = results[0].provider_results["openai"]
        self.assertIsInstance(failure, ProviderTaggingFailure)
        assert isinstance(failure, ProviderTaggingFailure)
        self.assertEqual(failure.failure_type, TaggingFailureType.WRITE_TIMEOUT)
        self.assertEqual(failure.attempt_count, 2)
        self.assertIn(30, sleep_calls)

    async def test_write_timeout_retry_still_consumes_global_rpm_limit(self):
        """写入超时后的谨慎重试同样必须通过全局 RPM limiter。"""

        now = 0.0
        sleep_calls: list[float] = []
        openai_attempts = 0

        async def advance_clock(seconds: float) -> None:
            nonlocal now
            sleep_calls.append(seconds)
            now += seconds

        def handler(request: httpx.Request) -> object:
            nonlocal openai_attempts
            if json.loads(request.content)["model"] == "uni-gpt-6-sol":
                openai_attempts += 1
                if openai_attempts == 1:
                    return httpx.WriteTimeout("mock write timeout", request=request)
            return _ok_response(request)

        service = self._service(
            handler,
            requests_per_minute=3,
            write_timeout_retry_delay_seconds=0,
            sleep=advance_clock,
            monotonic=lambda: now,
        )
        results = await service.tag_inputs(self.inputs)
        await service.aclose()

        self.assertEqual(openai_attempts, 2)
        self.assertTrue(any(seconds >= 60 for seconds in sleep_calls))
        self.assertEqual(results[0].status, TaggingConsensusStatus.CONSENSUS)

    async def test_cancellation_does_not_start_next_batch(self):
        """取消中的父任务不会继续开始第二批 HTTP 请求。"""

        first_batch_started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            first_batch_started.set()
            await asyncio.Event().wait()
            return _ok_response(request)

        service = self._service(
            handler,
            batch_size=1,
            openai_concurrency=1,
            other_provider_concurrency=1,
        )
        task = asyncio.create_task(service.tag_inputs(self.inputs))
        await asyncio.wait_for(first_batch_started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await service.aclose()

        payloads = [json.loads(request.content) for request in self.requests]
        input_ids = {
            json.loads(payload["messages"][1]["content"].split("\n", 1)[1])["rows"][0]["itemId"]
            for payload in payloads
        }
        self.assertEqual(input_ids, {"202608::weighted"})

    async def test_long_running_request_waits_without_timeout_and_accepts_cancellation(self):
        """read=None 时长请求保持等待，取消会传播到正在 await 的 HTTP 调用。"""

        request_started = asyncio.Event()
        request_cancelled = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            request_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                request_cancelled.set()
                raise
            return _ok_response(request)

        service = self._service(handler, batch_size=1)
        task = asyncio.create_task(service.tag_inputs(self.inputs))
        await asyncio.wait_for(request_started.wait(), timeout=1)
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await service.aclose()
        self.assertTrue(request_cancelled.is_set())

    async def test_model_ids_and_request_options_come_from_provider_config(self):
        """HTTP 请求只使用配置 model_id，并保留明确配置的专属选项。"""

        configs = list(_configs())
        configs[0] = TaggingProviderConfig(
            "openai",
            "GPT-6 Sol",
            "configured-gpt-id",
            2,
            request_options={"temperature": 0},
        )

        captured_payloads: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_payloads.append(json.loads(request.content))
            return _ok_response(request)

        client = httpx.AsyncClient(
            base_url="https://api.uniapi.io/v1/",
            transport=httpx.MockTransport(handler),
        )
        service = AiService(
            api_key="test-key-not-logged",
            provider_configs=configs,
            client=client,
            retry_base_delay_seconds=0,
            retry_max_delay_seconds=0,
            retry_jitter_seconds=0,
        )
        await service.tag_inputs(self.inputs)
        await service.aclose()

        openai_payload = next(
            payload
            for payload in captured_payloads
            if payload["model"] == "configured-gpt-id"
        )
        self.assertEqual(openai_payload["temperature"], 0)
        self.assertIs(openai_payload["stream"], False)
        self.assertEqual(
            {payload["model"] for payload in captured_payloads},
            {
                "configured-gpt-id",
                "uni-claude-sonnet-5",
                "uni-gemini-3.8-flash",
            },
        )

    def test_default_provider_configuration_reads_settings_without_guessing_ids(self):
        """默认业务名称固定，实际 model_id 永远从 settings 常量读取。"""

        with patch("services.ai_service.TAGGING_GPT_MODEL", "real-gpt-id"), patch(
            "services.ai_service.TAGGING_CLAUDE_MODEL",
            "real-claude-id",
        ), patch("services.ai_service.TAGGING_GEMINI_MODEL", "real-gemini-id"):
            configs = default_tagging_provider_configs()

        self.assertEqual(
            [(config.provider_id, config.display_name, config.model_id) for config in configs],
            [
                ("openai", "GPT-6 Sol", "real-gpt-id"),
                ("anthropic", "Claude Sonnet 5", "real-claude-id"),
                ("google", "Gemini 3.8 Flash", "real-gemini-id"),
            ],
        )

    def test_default_batch_size_is_50(self):
        """正式吞吐配置必须使用当前业务指定的目标批次。"""

        self.assertEqual(TAGGING_BATCH_SIZE, 50)
        self.assertEqual(
            (
                TAGGING_GPT_CONCURRENCY,
                TAGGING_CLAUDE_CONCURRENCY,
                TAGGING_GEMINI_CONCURRENCY,
            ),
            (8, 8, 8),
        )
        self.assertEqual(UNIAPI_RPM_LIMIT, 540)
        service = AiService(
            api_key="test-key-not-logged",
            provider_configs=_configs(),
        )
        self.addAsyncCleanup(service.aclose)
        self.assertEqual(service._batch_size, 50)

    def test_oversized_serialized_prompt_is_recursively_split_before_request(self):
        """预检必须根据真实 Prompt 二分，而不是强制发送 100 条超大输入。"""

        inputs = tuple(_many_inputs(100))
        prompt_for_fifty = max(
            len(
                TaggingPromptBuilder.build_user_prompt(inputs[:50]).encode(
                    "utf-8"
                )
            ),
            len(
                TaggingPromptBuilder.build_user_prompt(inputs[50:]).encode(
                    "utf-8"
                )
            ),
        )
        service = AiService(
            api_key="test-key-not-logged",
            provider_configs=_configs(),
            batch_size=100,
            prompt_max_serialized_bytes=prompt_for_fifty,
            prompt_max_estimated_tokens=1_000_000,
        )
        self.addAsyncCleanup(service.aclose)
        batches = service._build_safe_batches(inputs)

        self.assertEqual([len(batch) for batch in batches], [50, 50])

    def test_prompt_deduplicates_product_context_within_one_request(self):
        """同一请求只保留一份背景，100 条输入通过 contextId 引用它。"""

        prompt = TaggingPromptBuilder.build_user_prompt(_many_inputs(100))
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(len(payload["contexts"]), 1)
        self.assertEqual(
            {row["contextId"] for row in payload["rows"]},
            {"ctx_1"},
        )

    def test_logs_never_include_api_key(self):
        """日志只允许 Provider/模型/批次/状态，不得泄露 API Key。"""

        with self.assertLogs("services.ai_service", level=logging.INFO) as logs:
            AiService._log_provider_success(
                _configs()[0],
                batch_size=2,
                attempt_count=1,
                latency_ms=10,
            )
        self.assertNotIn("test-key-not-logged", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
