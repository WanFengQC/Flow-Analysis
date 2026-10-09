"""正式标签缓存与 Tagging Pipeline 的纯 mock 测试。"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from models.product_context import (
    AmazonProductContext,
    AmazonProductContextResult,
    AmazonProductSummaryStatus,
    ProductContextSelection,
)
from models.tagging import TagLabel, TaggingDecision, TaggingInput
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    TaggingLabelDecisionRecord,
    TaggingPipelineStatus,
    TaggingReviewStatus,
)
from models.tagging_provider import (
    ProviderTaggingFailure,
    ProviderTaggingResult,
    TaggingConsensusResult,
    TaggingConsensusStatus,
    TaggingFailureType,
)
from services.product_knowledge_service import AMAZON_PRODUCT_SUMMARY
from services.tagging_service import TaggingService


class _MemoryTaggingRepository:
    """模拟正式 Repository，验证 Pipeline 不访问真实 PostgreSQL。"""

    def __init__(self) -> None:
        self.current: dict[tuple[str, str, int], TaggingLabelCacheRecord] = {}
        self.audit: list[TaggingLabelDecisionRecord] = []
        self.list_calls: list[tuple[str, int, tuple[str, ...]]] = []
        self.connections: list[object | None] = []

    async def list_by_words(self, category_key, taxonomy_version, words):
        self.list_calls.append((category_key.value, taxonomy_version, tuple(words)))
        return {
            word: self.current[(category_key.value, word, taxonomy_version)]
            for word in words
            if (category_key.value, word, taxonomy_version) in self.current
        }

    async def upsert_current(self, record):
        key = (record.category_key.value, record.word, record.taxonomy_version)
        existing = self.current.get(key)
        stored = replace(record, id=existing.id) if existing is not None else record
        self.current[key] = stored
        return stored

    async def append_decision(self, record):
        self.audit.append(record)
        return record


class _FakeProductKnowledgeService:
    """只记录 cache miss 是否请求了产品背景选择。"""

    def __init__(self) -> None:
        self.calls: list[Mapping[str, Mapping[str, Any]]] = []

    def select_product_context(self, source_asin_stats):
        self.calls.append(source_asin_stats)
        return ProductContextSelection(
            source=AMAZON_PRODUCT_SUMMARY,
            representative_asin="B0TEST0001",
            needs_fetch=True,
            context=None,
        )


class _FakeAmazonProductContextProvider:
    """模拟单批次 Amazon fallback，不启动浏览器或连接真实 Amazon。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.clear_count = 0

    async def fetch_contexts(self, asins):
        self.calls.append(tuple(asins))
        return {
            asin: AmazonProductContextResult(
                asin=asin,
                status=AmazonProductSummaryStatus.READY,
                context=AmazonProductContext(
                    asin=asin,
                    source=AMAZON_PRODUCT_SUMMARY,
                    title="Amazon 摘要标题",
                    brand="Amazon Brand",
                    ratings=None,
                    price=None,
                    list_price=None,
                    about_this_item=(),
                    product_description=None,
                    options={},
                    important_information=None,
                    summary_text="Amazon Product Summary",
                ),
            )
            for asin in asins
        }

    def clear_generation_cache(self):
        self.clear_count += 1


class _FakeAiService:
    """按测试预设返回严格共识、分歧或不完整结果。"""

    def __init__(self, status: TaggingConsensusStatus) -> None:
        self.status = status
        self.calls: list[tuple[TaggingInput, ...]] = []

    async def tag_inputs(self, inputs: Sequence[TaggingInput]):
        self.calls.append(tuple(inputs))
        return [self._result(tagging_input) for tagging_input in inputs]

    def _result(self, tagging_input: TaggingInput) -> TaggingConsensusResult:
        provider_results = {
            provider_id: ProviderTaggingResult(
                provider_id=provider_id,
                model_id=f"{provider_id}-configured-model",
                decisions=(
                    TaggingDecision(
                        item_id=tagging_input.item_id,
                        label=TagLabel.ATTRIBUTE,
                        reason=f"{provider_id} 理由",
                    ),
                ),
                latency_ms=1,
                attempt_count=1,
            )
            for provider_id in ("openai", "anthropic", "google")
        }
        if self.status == TaggingConsensusStatus.DISAGREEMENT:
            provider_results["google"] = ProviderTaggingResult(
                provider_id="google",
                model_id="google-configured-model",
                decisions=(
                    TaggingDecision(
                        item_id=tagging_input.item_id,
                        label=TagLabel.SPECIFICATION,
                        reason="Google 不同理由",
                    ),
                ),
                latency_ms=1,
                attempt_count=1,
            )
        if self.status == TaggingConsensusStatus.INCOMPLETE:
            provider_results["google"] = ProviderTaggingFailure(
                provider_id="google",
                model_id="google-configured-model",
                failure_type=TaggingFailureType.TIMEOUT,
                attempt_count=4,
                latency_ms=1,
            )
        return TaggingConsensusResult(
            item_id=tagging_input.item_id,
            status=self.status,
            provider_results=provider_results,
            consensus_label=(
                TagLabel.ATTRIBUTE
                if self.status == TaggingConsensusStatus.CONSENSUS
                else None
            ),
            consensus_reason=(
                "openai 理由"
                if self.status == TaggingConsensusStatus.CONSENSUS
                else None
            ),
        )


class _RetryAiService:
    """仅模拟失败 Provider 的局部恢复，记录绝不能被重复调用的成功方。"""

    def __init__(
        self,
        *,
        labels: Mapping[str, TagLabel] | None = None,
        failures: Mapping[str, TaggingFailureType] | None = None,
    ) -> None:
        self.labels = dict(labels or {})
        self.failures = dict(failures or {})
        self.calls: list[tuple[tuple[str, ...], tuple[TaggingInput, ...]]] = []

    async def tag_inputs_for_providers(self, inputs, provider_ids):
        snapshots = tuple(inputs)
        ids = tuple(provider_ids)
        self.calls.append((ids, snapshots))
        outcomes_by_item = {snapshot.item_id: {} for snapshot in snapshots}
        for provider_id in ids:
            for snapshot in snapshots:
                failure_type = self.failures.get(provider_id)
                if failure_type is not None:
                    outcome = ProviderTaggingFailure(
                        provider_id=provider_id,
                        model_id=f"{provider_id}-configured-model",
                        failure_type=failure_type,
                        attempt_count=4,
                        latency_ms=1,
                    )
                else:
                    outcome = ProviderTaggingResult(
                        provider_id=provider_id,
                        model_id=f"{provider_id}-configured-model",
                        decisions=(
                            TaggingDecision(
                                item_id=snapshot.item_id,
                                label=self.labels.get(
                                    provider_id,
                                    TagLabel.ATTRIBUTE,
                                ),
                                reason=f"{provider_id} 重试理由",
                            ),
                        ),
                        latency_ms=1,
                        attempt_count=1,
                    )
                outcomes_by_item[snapshot.item_id][provider_id] = outcome
        return outcomes_by_item


def _word_results(*months: str, word: str = "weighted") -> dict[str, list[dict[str, Any]]]:
    """构造已经正式归一后的最小 Word Result，不接触 RAW 或分析公式。"""

    return {
        month: [
            {
                "word": word,
                "topPhrases": ["weighted stuffed animal"],
                "sourceAsinStats": {
                    "B0TEST0001": {"exposure": 100.0},
                },
            }
        ]
        for month in months
    }


def _cache_record(
    category_key: TaggingCategoryKey,
    word: str,
    taxonomy_version: int = 1,
    label: TagLabel = TagLabel.ATTRIBUTE,
    source: TaggingDecisionSource = TaggingDecisionSource.AI_CONSENSUS,
) -> TaggingLabelCacheRecord:
    """创建一个已存在的正式缓存行。"""

    return TaggingLabelCacheRecord(
        id=uuid4(),
        category_key=category_key,
        word=word,
        taxonomy_version=taxonomy_version,
        label=label,
        reason="历史正式理由",
        decision_source=source,
        representative_asin="B0TEST0001",
        product_context_source="PRODUCT_KNOWLEDGE_BASE",
    )


class TaggingServiceTest(IsolatedAsyncioTestCase):
    """验证缓存身份、AI miss、审计和人工覆盖的严格业务边界。"""

    def setUp(self) -> None:
        """每个测试使用独立内存缓存和独立 fake 服务。"""

        self.repository = _MemoryTaggingRepository()
        self.product_service = _FakeProductKnowledgeService()
        self.amazon_provider = _FakeAmazonProductContextProvider()

    def _service(self, ai_status=TaggingConsensusStatus.CONSENSUS):
        ai_service = _FakeAiService(ai_status)
        service = TaggingService(
            database=None,
            ai_service=ai_service,
            product_knowledge_service=self.product_service,
            amazon_product_context_provider=self.amazon_provider,
            repository_factory=lambda connection: self.repository,
        )
        return service, ai_service

    async def test_cache_hit_is_category_scoped_and_skips_context_and_ai(self):
        """stuffed_animals 的 cache hit 不访问 ProductContext 或任何 Provider。"""

        record = _cache_record(TaggingCategoryKey.STUFFED_ANIMALS, "weighted")
        self.repository.current[("stuffed_animals", "weighted", 1)] = record
        service, ai_service = self._service()

        run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )

        self.assertEqual(run.results[0].status, TaggingPipelineStatus.CACHE_HIT)
        self.assertEqual(run.results[0].label, TagLabel.ATTRIBUTE)
        self.assertEqual(self.product_service.calls, [])
        self.assertEqual(ai_service.calls, [])
        self.assertEqual(self.repository.audit, [])

    async def test_cross_category_and_taxonomy_version_never_reuse_cache(self):
        """Pillow/v1 缓存不能命中 Stuffed Animals 或 taxonomy v2。"""

        self.repository.current[("pillow", "weighted", 1)] = _cache_record(
            TaggingCategoryKey.PILLOW,
            "weighted",
        )
        service, ai_service = self._service()

        stuffed_run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )
        version_two_run = await service.tag_monthly_word_results(
            _word_results("202609"),
            "pillow",
            taxonomy_version=2,
        )

        self.assertEqual(stuffed_run.results[0].status, TaggingPipelineStatus.AI_CONSENSUS)
        self.assertEqual(version_two_run.results[0].status, TaggingPipelineStatus.AI_CONSENSUS)
        self.assertEqual(len(ai_service.calls), 2)
        self.assertEqual(
            [call[:2] for call in self.repository.list_calls],
            [("stuffed_animals", 1), ("pillow", 2)],
        )

    async def test_month_is_not_cache_key_and_batch_lookup_has_no_n_plus_one(self):
        """同一 category/word 跨月复用，所有 word 只执行一次 batch lookup。"""

        self.repository.current[("stuffed_animals", "weighted", 1)] = _cache_record(
            TaggingCategoryKey.STUFFED_ANIMALS,
            "weighted",
        )
        service, ai_service = self._service()

        run = await service.tag_monthly_word_results(
            _word_results("202608", "202607", "202606"),
            "stuffed_animals",
        )

        self.assertEqual(len(run.results), 3)
        self.assertTrue(all(
            result.status == TaggingPipelineStatus.CACHE_HIT
            for result in run.results
        ))
        self.assertEqual(len(self.repository.list_calls), 1)
        self.assertEqual(ai_service.calls, [])

    async def test_consensus_writes_current_cache_and_append_only_audit(self):
        """3/3 一致时才以一个事务语义形成 current cache 与审计事件。"""

        service, _ = self._service()
        run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )

        self.assertEqual(run.results[0].status, TaggingPipelineStatus.AI_CONSENSUS)
        self.assertEqual(len(self.repository.current), 1)
        self.assertEqual(len(self.repository.audit), 1)
        audit = self.repository.audit[0]
        self.assertEqual(audit.decision_source, TaggingDecisionSource.AI_CONSENSUS)
        self.assertEqual(set(audit.provider_results), {"openai", "anthropic", "google"})
        self.assertNotIn("productContext", audit.provider_results["openai"])

    async def test_disagreement_and_incomplete_never_write_cache_or_audit(self):
        """分歧只生成内存 review item；不完整也不留下失败缓存。"""

        disagreement_service, _ = self._service(TaggingConsensusStatus.DISAGREEMENT)
        disagreement_run = await disagreement_service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )
        self.assertEqual(
            disagreement_run.results[0].status,
            TaggingPipelineStatus.DISAGREEMENT,
        )
        self.assertEqual(len(disagreement_run.review_items), 1)
        review_item = disagreement_run.review_items[0]
        self.assertEqual(review_item.month, "202608")
        self.assertEqual(review_item.word, "weighted")
        self.assertEqual(review_item.top_phrases, ("weighted stuffed animal",))
        self.assertEqual(review_item.representative_asin, "B0TEST0001")
        self.assertEqual(review_item.status, TaggingReviewStatus.PENDING)
        self.assertEqual(
            set(review_item.provider_results),
            {"openai", "anthropic", "google"},
        )
        self.assertEqual(len(self.repository.current), 0)
        self.assertEqual(len(self.repository.audit), 0)

    async def test_incomplete_retry_only_calls_failed_provider_and_persists_consensus(self):
        """GPT/Claude 成功后仅补 Google，补齐 3/3 共识才写 cache 与审计。"""

        service, _ = self._service(TaggingConsensusStatus.INCOMPLETE)
        initial_run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )
        initial = initial_run.results[0]
        self.assertIsNotNone(initial.tagging_input)
        self.assertEqual(
            set(initial.provider_results), {"openai", "anthropic", "google"}
        )
        original_openai = initial.provider_results["openai"]
        original_claude = initial.provider_results["anthropic"]

        retry_ai = _RetryAiService(labels={"google": TagLabel.ATTRIBUTE})
        service._ai_service = retry_ai
        retry_run = await service.retry_incomplete_results(initial_run.results)

        self.assertEqual(
            [provider_ids for provider_ids, _ in retry_ai.calls],
            [("google",)],
        )
        recovered = retry_run.results[0]
        self.assertEqual(recovered.status, TaggingPipelineStatus.AI_CONSENSUS)
        self.assertEqual(recovered.provider_results["openai"], original_openai)
        self.assertEqual(recovered.provider_results["anthropic"], original_claude)
        self.assertEqual(len(self.repository.current), 1)
        self.assertEqual(len(self.repository.audit), 1)

    async def test_incomplete_retry_disagreement_keeps_cache_empty_and_creates_review_item(self):
        """局部补齐后标签不一致必须进入既有人工审核，而非写正式标签。"""

        service, _ = self._service(TaggingConsensusStatus.INCOMPLETE)
        initial_run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )
        service._ai_service = _RetryAiService(
            labels={"google": TagLabel.SPECIFICATION}
        )

        retry_run = await service.retry_incomplete_results(initial_run.results)

        self.assertEqual(
            retry_run.results[0].status,
            TaggingPipelineStatus.DISAGREEMENT,
        )
        self.assertEqual(len(retry_run.review_items), 1)
        self.assertEqual(len(self.repository.current), 0)
        self.assertEqual(len(self.repository.audit), 0)

    async def test_incomplete_retry_failure_keeps_partial_success_results(self):
        """失败 Provider 再次耗尽后，首次成功的 Provider 结果必须继续保留。"""

        service, _ = self._service(TaggingConsensusStatus.INCOMPLETE)
        initial_run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )
        original = initial_run.results[0]
        service._ai_service = _RetryAiService(
            failures={"google": TaggingFailureType.TIMEOUT}
        )

        retry_run = await service.retry_incomplete_results(initial_run.results)

        retried = retry_run.results[0]
        self.assertEqual(retried.status, TaggingPipelineStatus.INCOMPLETE)
        self.assertEqual(retried.provider_results["openai"], original.provider_results["openai"])
        self.assertEqual(retried.provider_results["anthropic"], original.provider_results["anthropic"])
        self.assertIsInstance(
            retried.provider_results["google"],
            ProviderTaggingFailure,
        )

    async def test_incomplete_retry_groups_same_failed_provider_into_one_batch(self):
        """多个只缺 Google 的词必须合并为一个局部 Provider 批次。"""

        service, _ = self._service(TaggingConsensusStatus.INCOMPLETE)
        words = {
            "202608": [
                {
                    "word": word,
                    "topPhrases": [f"{word} stuffed animal"],
                    "sourceAsinStats": {"B0TEST0001": {"exposure": 100.0}},
                }
                for word in ("weighted", "soft")
            ]
        }
        initial_run = await service.tag_monthly_word_results(
            words,
            "stuffed_animals",
        )
        retry_ai = _RetryAiService(labels={"google": TagLabel.ATTRIBUTE})
        service._ai_service = retry_ai

        retry_run = await service.retry_incomplete_results(initial_run.results)

        self.assertEqual(len(retry_ai.calls), 1)
        self.assertEqual(retry_ai.calls[0][0], ("google",))
        self.assertEqual(len(retry_ai.calls[0][1]), 2)
        self.assertTrue(all(
            result.status == TaggingPipelineStatus.AI_CONSENSUS
            for result in retry_run.results
        ))

    async def test_incomplete_retry_only_targets_two_failed_providers(self):
        """两家失败时只补这两家，原先成功的 Claude 绝不能被重复请求。"""

        service, _ = self._service(TaggingConsensusStatus.INCOMPLETE)
        initial_run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )
        initial = initial_run.results[0]
        provider_results = dict(initial.provider_results)
        provider_results["openai"] = ProviderTaggingFailure(
            provider_id="openai",
            model_id="openai-configured-model",
            failure_type=TaggingFailureType.TIMEOUT,
            attempt_count=4,
            latency_ms=1,
        )
        two_failed = replace(initial, provider_results=provider_results)
        retry_ai = _RetryAiService(
            labels={
                "openai": TagLabel.ATTRIBUTE,
                "google": TagLabel.ATTRIBUTE,
            }
        )
        service._ai_service = retry_ai

        retry_run = await service.retry_incomplete_results((two_failed,))

        self.assertEqual(retry_ai.calls[0][0], ("openai", "google"))
        self.assertEqual(
            retry_run.results[0].status,
            TaggingPipelineStatus.AI_CONSENSUS,
        )

    async def test_human_review_replaces_current_cache_but_keeps_ai_audit(self):
        """人工正式结论覆盖 current cache，旧 AI 决定仍作为不可变审计保留。"""

        service, _ = self._service()
        await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )
        updated_cache = await service.record_human_review(
            category_key="stuffed_animals",
            word=" WEIGHTED ",
            label=TagLabel.SPECIFICATION,
            reason="人工确认此语境为规格。",
            representative_asin="B0TEST0001",
            product_context_source="PRODUCT_KNOWLEDGE_BASE",
        )

        self.assertEqual(updated_cache.word, "weighted")
        self.assertEqual(updated_cache.label, TagLabel.SPECIFICATION)
        self.assertEqual(updated_cache.decision_source, TaggingDecisionSource.HUMAN_REVIEW)
        self.assertEqual(len(self.repository.current), 1)
        self.assertEqual(len(self.repository.audit), 2)
        self.assertEqual(
            [record.decision_source for record in self.repository.audit],
            [TaggingDecisionSource.AI_CONSENSUS, TaggingDecisionSource.HUMAN_REVIEW],
        )

    async def test_same_miss_identity_across_months_calls_ai_once(self):
        """同一任务跨月相同 word 只形成一个正式决定，回填每个月结果。"""

        service, ai_service = self._service()
        run = await service.tag_monthly_word_results(
            _word_results("202608", "202607"),
            "stuffed_animals",
        )

        self.assertEqual(len(ai_service.calls), 1)
        self.assertEqual(len(ai_service.calls[0]), 1)
        self.assertEqual(len(self.repository.audit), 1)
        self.assertEqual(
            [result.status for result in run.results],
            [TaggingPipelineStatus.AI_CONSENSUS] * 2,
        )
        self.assertEqual(
            self.amazon_provider.calls,
            [("B0TEST0001",)],
        )
        self.assertEqual(self.amazon_provider.clear_count, 1)

    async def test_recent_30_days_sentinel_uses_a_non_empty_ai_month(self):
        """SellerSprite 的空月份参数必须在 AI 输入中显示为最近30天。"""

        service, ai_service = self._service()
        run = await service.tag_monthly_word_results(
            _word_results(""),
            "stuffed_animals",
        )

        self.assertEqual(run.results[0].month, "")
        self.assertEqual(run.results[0].status, TaggingPipelineStatus.AI_CONSENSUS)
        self.assertEqual(ai_service.calls[0][0].month, "最近30天")

    async def test_whitespace_month_is_still_rejected(self):
        """只允许精确空字符串作为最近30天 API sentinel，不能接受空白月份。"""

        service, ai_service = self._service()
        with self.assertRaisesRegex(
            ValueError,
            "month 必须是自然月或最近30天请求范围",
        ):
            await service.tag_monthly_word_results(
                _word_results("   "),
                "stuffed_animals",
            )

        self.assertEqual(ai_service.calls, [])

    async def test_amazon_context_failure_never_calls_ai_with_empty_context(self):
        """Amazon fallback 未 READY 时必须保留 INCOMPLETE，不能脱离产品背景打标。"""

        class _FailedAmazonProvider:
            async def fetch_contexts(self, asins):
                return {
                    asin: AmazonProductContextResult(
                        asin=asin,
                        status=AmazonProductSummaryStatus.AMAZON_CAPTCHA,
                        context=None,
                    )
                    for asin in asins
                }

            def clear_generation_cache(self):
                return None

        ai_service = _FakeAiService(TaggingConsensusStatus.CONSENSUS)
        service = TaggingService(
            database=None,
            ai_service=ai_service,
            product_knowledge_service=self.product_service,
            amazon_product_context_provider=_FailedAmazonProvider(),
            repository_factory=lambda connection: self.repository,
        )

        run = await service.tag_monthly_word_results(
            _word_results("202608"),
            "stuffed_animals",
        )

        self.assertEqual(run.results[0].status, TaggingPipelineStatus.INCOMPLETE)
        self.assertEqual(ai_service.calls, [])

    async def test_unknown_category_key_is_rejected_without_guessing(self):
        """未知 key 不能被 UI 文本、大小写或相似字符串静默纠正。"""

        service, ai_service = self._service()
        with self.assertRaises(ValueError):
            await service.tag_monthly_word_results(
                _word_results("202608"),
                "Stuffed Animals",
            )
        self.assertEqual(self.repository.list_calls, [])
        self.assertEqual(ai_service.calls, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
