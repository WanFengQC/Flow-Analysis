"""正式词结果的标签缓存查询、AI 调用与正式决定持久化编排。"""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator
from uuid import uuid4

from psycopg import AsyncConnection

from models.tagging import TagLabel, TaggingInput
from models.product_context import AmazonProductSummaryStatus
from models.tagging_label import (
    TAGGING_TAXONOMY_VERSION,
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    TaggingLabelDecisionRecord,
    TaggingPipelineResult,
    TaggingPipelineRun,
    TaggingPipelineStatus,
    TaggingReviewItem,
    TaggingReviewStatus,
    category_display_name,
    normalize_cache_word,
    require_category_key,
)
from models.tagging_provider import (
    ProviderTaggingFailure,
    ProviderTaggingOutcome,
    ProviderTaggingResult,
    TaggingConsensusResult,
    TaggingConsensusStatus,
)
from repositories.database import DatabaseManager
from repositories.tagging_label_repository import TaggingLabelRepository
from services.ai_service import AiService
from services.amazon_product_context_provider import (
    AmazonProductContextProvider,
)
from services.product_knowledge_service import ProductKnowledgeService
from services.tagging_consensus_service import TaggingConsensusService


@dataclass(frozen=True, slots=True)
class _WordWorkItem:
    """Pipeline 内部使用的月度正式 word 只读引用。"""

    month: str
    word: str
    word_result: Mapping[str, Any]


class TaggingService:
    """在 AI 前查询缓存，只把正式共识或人工结论写入 PostgreSQL。"""

    # SellerSprite 使用空字符串表示“最近30天”的请求范围。该值是 API
    # 参数，不是缺失月份；在 AI Prompt 中必须转换为可读、非空的时间范围。
    _RECENT_30_DAYS_REQUEST_MONTH = ""
    _RECENT_30_DAYS_TAGGING_MONTH = "最近30天"

    _RETRYABLE_FAILURE_TYPES = frozenset({
        # TIMEOUT 仅用于兼容历史失败记录；AiService 新产生的超时会使用细分类。
        "TIMEOUT",
        "CONNECT_TIMEOUT",
        "WRITE_TIMEOUT",
        "POOL_TIMEOUT",
        "NETWORK_ERROR",
        "RATE_LIMITED",
        "SERVER_ERROR",
        "INVALID_RESPONSE",
    })

    def __init__(
        self,
        database: DatabaseManager | None,
        ai_service: AiService,
        product_knowledge_service: ProductKnowledgeService | None,
        amazon_product_context_provider: AmazonProductContextProvider | None = None,
        repository_factory: Callable[
            [AsyncConnection | None],
            TaggingLabelRepository,
        ] | None = None,
    ) -> None:
        """注入既有基础设施，不创建 EventLoop、连接池或 HTTP Client。"""

        self._database = database
        self._ai_service = ai_service
        self._product_knowledge_service = product_knowledge_service
        self._amazon_product_context_provider = (
            amazon_product_context_provider
        )
        self._repository_factory = repository_factory or self._default_repository

    async def tag_monthly_word_results(
        self,
        word_results_by_month: Mapping[str, Sequence[Mapping[str, Any]]],
        category_key: str,
        taxonomy_version: int = TAGGING_TAXONOMY_VERSION,
        progress_callback: Callable[[str], None] | None = None,
    ) -> TaggingPipelineRun:
        """处理正式词结果：缓存命中优先，miss 才准备背景并调用三家 AI。"""

        self._report_progress(progress_callback, "正在查询历史标签缓存...")
        stable_category_key = require_category_key(category_key)
        self._validate_taxonomy_version(taxonomy_version)
        work_items = self._build_work_items(word_results_by_month)
        if not work_items:
            return TaggingPipelineRun(results=(), review_items=())

        # 本轮所有正式 canonical word 一次查询；month 不属于 cache identity。
        cache_by_word = await self._repository_for(None).list_by_words(
            stable_category_key,
            taxonomy_version,
            tuple(item.word for item in work_items),
        )

        results_by_identity: dict[tuple[str, str], TaggingPipelineResult] = {}
        misses_by_word: dict[str, list[_WordWorkItem]] = defaultdict(list)
        for item in work_items:
            cached_label = cache_by_word.get(item.word)
            if cached_label is not None:
                results_by_identity[(item.month, item.word)] = (
                    self._cache_hit_result(
                        item,
                        stable_category_key,
                        taxonomy_version,
                        cached_label,
                    )
                )
            else:
                misses_by_word[item.word].append(item)

        # 同一 cache identity 在多个自然月出现时，只选择第一条稳定正式词结果
        # 请求一次 AI，并将该正式结论回填至本轮同一 identity 的所有月份。
        # 所有 Amazon fallback 先按 ASIN 汇总，再由 Provider 用一个浏览器
        # Context 与一个 Page 串行获取，绝不在单词循环中反复启动浏览器。
        selections_by_word: dict[str, Any] = {}
        amazon_asins: list[str] = []
        self._report_progress(progress_callback, "正在准备产品背景...")
        for word, grouped_items in misses_by_word.items():
            selection = self._select_product_context(grouped_items[0])
            if selection is None:
                continue
            selections_by_word[word] = selection
            if selection.needs_fetch:
                amazon_asins.append(selection.representative_asin)

        amazon_contexts: Mapping[str, Any] = {}
        if amazon_asins and self._amazon_product_context_provider is not None:
            self._report_progress(
                progress_callback,
                "正在获取 Amazon 产品背景...",
            )
            if hasattr(self._amazon_product_context_provider, "begin_generation"):
                self._amazon_product_context_provider.begin_generation()
            try:
                amazon_contexts = await self._amazon_product_context_provider.fetch_contexts(
                    amazon_asins
                )
            finally:
                # Provider 的 cache 仅服务于这次 tagging generation；AI 输入与
                # 可能的 ReviewItem 各自保存不可变的运行时 context 快照。
                self._amazon_product_context_provider.clear_generation_cache()

        ai_inputs: list[TaggingInput] = []
        input_groups: dict[str, list[_WordWorkItem]] = {}
        for word, grouped_items in misses_by_word.items():
            input_item = self._build_ai_input(
                grouped_items[0],
                stable_category_key,
                taxonomy_version,
                selections_by_word.get(word),
                amazon_contexts,
            )
            if input_item is None:
                for item in grouped_items:
                    results_by_identity[(item.month, item.word)] = (
                        self._incomplete_result(
                            item,
                            stable_category_key,
                            taxonomy_version,
                        )
                    )
                continue
            ai_inputs.append(input_item)
            input_groups[input_item.item_id] = grouped_items

        review_items: list[TaggingReviewItem] = []
        if ai_inputs:
            self._report_progress(progress_callback, "正在进行 AI 三模型判断...")
            consensus_results = await self._ai_service.tag_inputs(ai_inputs)
            consensus_by_id = {
                result.item_id: result for result in consensus_results
            }
            if set(consensus_by_id) != set(input_groups):
                raise RuntimeError("AI Tagging 返回的共识结果与请求输入不一致")

            for input_item in ai_inputs:
                consensus_result = consensus_by_id[input_item.item_id]
                grouped_items = input_groups[input_item.item_id]
                await self._apply_consensus_result(
                    consensus_result,
                    grouped_items,
                    stable_category_key,
                    taxonomy_version,
                    results_by_identity,
                    review_items,
                    input_item,
                )

        self._report_progress(progress_callback, "正在处理共识结果...")
        ordered_results = tuple(
            results_by_identity[(item.month, item.word)]
            for item in work_items
        )
        return TaggingPipelineRun(
            results=ordered_results,
            review_items=tuple(review_items),
        )

    @classmethod
    def retryable_provider_ids(
        cls,
        result: TaggingPipelineResult,
    ) -> tuple[str, ...]:
        """返回当前 INCOMPLETE 中可恢复的 Provider，不把配置错误加入重试。"""

        if (
            result.status != TaggingPipelineStatus.INCOMPLETE
            or result.tagging_input is None
        ):
            return ()
        return tuple(
            provider_id
            for provider_id, outcome in result.provider_results.items()
            if isinstance(outcome, ProviderTaggingFailure)
            and outcome.failure_type.value in cls._RETRYABLE_FAILURE_TYPES
        )

    @classmethod
    def failed_provider_categories(
        cls,
        result: TaggingPipelineResult,
    ) -> dict[str, str]:
        """仅提供安全失败分类，供恢复入口提示而非写入主数据表。"""

        return {
            provider_id: outcome.failure_type.value
            for provider_id, outcome in result.provider_results.items()
            if isinstance(outcome, ProviderTaggingFailure)
        }

    async def retry_incomplete_results(
        self,
        results: Sequence[TaggingPipelineResult],
        progress_callback: Callable[[str], None] | None = None,
    ) -> TaggingPipelineRun:
        """只补齐当前 generation 中失败的 Provider，不重走分析或背景准备。

        每个 ``TaggingPipelineResult`` 已持有首次请求的不可变 TaggingInput
        与局部 Provider 结果。本方法既不查历史缓存，也不访问 Amazon。
        """

        groups: dict[
            tuple[str, ...],
            dict[str, list[TaggingPipelineResult]],
        ] = defaultdict(dict)
        for result in results:
            if result.status != TaggingPipelineStatus.INCOMPLETE:
                continue
            retry_ids = self.retryable_provider_ids(result)
            input_snapshot = result.tagging_input
            if not retry_ids or input_snapshot is None:
                continue
            grouped_results = groups[retry_ids].setdefault(
                input_snapshot.item_id,
                [],
            )
            grouped_results.append(result)

        if not groups:
            return TaggingPipelineRun(results=(), review_items=())

        self._report_progress(progress_callback, "正在重试 AI 失败项...")
        updated_results: list[TaggingPipelineResult] = []
        review_items: list[TaggingReviewItem] = []
        consensus_service = TaggingConsensusService()
        for provider_ids, grouped_by_input in groups.items():
            input_snapshots = tuple(
                grouped[0].tagging_input
                for grouped in grouped_by_input.values()
                if grouped and grouped[0].tagging_input is not None
            )
            retry_outcomes_by_item = await self._ai_service.tag_inputs_for_providers(
                input_snapshots,
                provider_ids,
            )
            if set(retry_outcomes_by_item) != set(grouped_by_input):
                raise RuntimeError("AI 精准重试返回与原始输入不一致")

            for item_id, grouped_results in grouped_by_input.items():
                original = grouped_results[0]
                input_snapshot = original.tagging_input
                if input_snapshot is None:
                    raise RuntimeError("AI 精准重试缺少输入快照")
                merged_provider_results = dict(original.provider_results)
                merged_provider_results.update(retry_outcomes_by_item[item_id])
                consensus_result = consensus_service.build_results(
                    (input_snapshot,),
                    tuple(merged_provider_results.values()),
                )[0]
                updated_results.extend(
                    await self._apply_retry_consensus_result(
                        consensus_result,
                        grouped_results,
                        input_snapshot,
                        review_items,
                    )
                )

        return TaggingPipelineRun(
            results=tuple(updated_results),
            review_items=tuple(review_items),
        )

    async def _apply_retry_consensus_result(
        self,
        consensus_result: TaggingConsensusResult,
        previous_results: Sequence[TaggingPipelineResult],
        tagging_input: TaggingInput,
        review_items: list[TaggingReviewItem],
    ) -> list[TaggingPipelineResult]:
        """将局部补齐结果重新走既有共识、审核和正式缓存边界。"""

        if not previous_results:
            return []
        template = previous_results[0]
        if consensus_result.status == TaggingConsensusStatus.CONSENSUS:
            if (
                consensus_result.consensus_label is None
                or consensus_result.consensus_reason is None
            ):
                raise RuntimeError("CONSENSUS 缺少正式标签或理由")
            await self._persist_formal_decision(
                category_key=template.category_key,
                word=template.word,
                taxonomy_version=template.taxonomy_version,
                label=consensus_result.consensus_label,
                reason=consensus_result.consensus_reason,
                decision_source=TaggingDecisionSource.AI_CONSENSUS,
                representative_asin=tagging_input.representative_asin,
                product_context_source=tagging_input.product_context_source,
                provider_results=self._provider_audit_snapshot(consensus_result),
            )
            return [
                replace(
                    result,
                    status=TaggingPipelineStatus.AI_CONSENSUS,
                    label=consensus_result.consensus_label,
                    reason=consensus_result.consensus_reason,
                    decision_source=TaggingDecisionSource.AI_CONSENSUS,
                    provider_results=dict(consensus_result.provider_results),
                )
                for result in previous_results
            ]

        if consensus_result.status == TaggingConsensusStatus.DISAGREEMENT:
            updated_results = []
            for result in previous_results:
                updated_results.append(
                    replace(
                        result,
                        status=TaggingPipelineStatus.DISAGREEMENT,
                        label=None,
                        reason=None,
                        decision_source=None,
                        provider_results=dict(consensus_result.provider_results),
                    )
                )
                review_items.append(
                    self._build_review_item(result, tagging_input, consensus_result)
                )
            return updated_results

        if consensus_result.status == TaggingConsensusStatus.INCOMPLETE:
            return [
                replace(
                    result,
                    status=TaggingPipelineStatus.INCOMPLETE,
                    label=None,
                    reason=None,
                    decision_source=None,
                    provider_results=dict(consensus_result.provider_results),
                )
                for result in previous_results
            ]

        raise RuntimeError("未知 Tagging 共识状态")

    def cancel_current_task(self) -> None:
        """转发取消到 Amazon Provider，AI 协程由 Future 取消传播。"""

        if self._amazon_product_context_provider is not None:
            cancel = getattr(
                self._amazon_product_context_provider,
                "cancel_current_batch",
                None,
            )
            if callable(cancel):
                cancel()

    async def wait_for_external_cleanup(self, timeout: float) -> bool:
        """退出阶段等待可选的 Windows Playwright bridge 完成清理。"""

        if self._amazon_product_context_provider is None:
            return True
        wait_for_cleanup = getattr(
            self._amazon_product_context_provider,
            "wait_for_bridge_cleanup",
            None,
        )
        if not callable(wait_for_cleanup):
            return True
        return await wait_for_cleanup(timeout)

    @staticmethod
    def _report_progress(
        progress_callback: Callable[[str], None] | None,
        message: str,
    ) -> None:
        """进度只用于 Controller 状态回传，不携带任何敏感上下文。"""

        if progress_callback is not None:
            progress_callback(message)

    async def record_human_review(
        self,
        *,
        category_key: str,
        word: str,
        label: TagLabel,
        reason: str,
        representative_asin: str,
        product_context_source: str,
        taxonomy_version: int = TAGGING_TAXONOMY_VERSION,
    ) -> TaggingLabelCacheRecord:
        """为未来审核 UI 预留明确人工结论的正式缓存写入入口。"""

        stable_category_key = require_category_key(category_key)
        normalized_word = normalize_cache_word(word)
        self._validate_taxonomy_version(taxonomy_version)
        self._validate_formal_label_fields(
            label,
            reason,
            representative_asin,
            product_context_source,
        )
        return await self._persist_formal_decision(
            category_key=stable_category_key,
            word=normalized_word,
            taxonomy_version=taxonomy_version,
            label=label,
            reason=reason.strip(),
            decision_source=TaggingDecisionSource.HUMAN_REVIEW,
            representative_asin=representative_asin.strip().upper(),
            product_context_source=product_context_source.strip(),
            provider_results={},
        )

    async def _apply_consensus_result(
        self,
        consensus_result: TaggingConsensusResult,
        grouped_items: Sequence[_WordWorkItem],
        category_key: TaggingCategoryKey,
        taxonomy_version: int,
        results_by_identity: dict[tuple[str, str], TaggingPipelineResult],
        review_items: list[TaggingReviewItem],
        tagging_input: TaggingInput,
    ) -> None:
        """将共识、分歧和不完整结果严格走向不同生命周期。"""

        if consensus_result.status == TaggingConsensusStatus.CONSENSUS:
            if (
                consensus_result.consensus_label is None
                or consensus_result.consensus_reason is None
            ):
                raise RuntimeError("CONSENSUS 缺少正式标签或理由")
            await self._persist_formal_decision(
                category_key=category_key,
                word=grouped_items[0].word,
                taxonomy_version=taxonomy_version,
                label=consensus_result.consensus_label,
                reason=consensus_result.consensus_reason,
                decision_source=TaggingDecisionSource.AI_CONSENSUS,
                representative_asin=tagging_input.representative_asin,
                product_context_source=tagging_input.product_context_source,
                provider_results=self._provider_audit_snapshot(consensus_result),
            )
            for item in grouped_items:
                results_by_identity[(item.month, item.word)] = (
                    TaggingPipelineResult(
                        month=item.month,
                        word=item.word,
                        category_key=category_key,
                        taxonomy_version=taxonomy_version,
                        status=TaggingPipelineStatus.AI_CONSENSUS,
                        label=consensus_result.consensus_label,
                        reason=consensus_result.consensus_reason,
                        decision_source=TaggingDecisionSource.AI_CONSENSUS,
                        provider_results=dict(consensus_result.provider_results),
                        tagging_input=tagging_input,
                    )
                )
            return

        if consensus_result.status == TaggingConsensusStatus.DISAGREEMENT:
            for item in grouped_items:
                results_by_identity[(item.month, item.word)] = (
                    TaggingPipelineResult(
                        month=item.month,
                        word=item.word,
                        category_key=category_key,
                        taxonomy_version=taxonomy_version,
                        status=TaggingPipelineStatus.DISAGREEMENT,
                        label=None,
                        reason=None,
                        decision_source=None,
                        provider_results=dict(consensus_result.provider_results),
                        tagging_input=tagging_input,
                    )
                )
                # 仅将内存结果交给未来 Dialog；不写 JSON、DB 或诊断文件。
                review_items.append(
                    self._build_review_item(
                        results_by_identity[(item.month, item.word)],
                        tagging_input,
                        consensus_result,
                    )
                )
            return

        if consensus_result.status == TaggingConsensusStatus.INCOMPLETE:
            for item in grouped_items:
                results_by_identity[(item.month, item.word)] = (
                    self._incomplete_result(
                        item,
                        category_key,
                        taxonomy_version,
                        provider_results=consensus_result.provider_results,
                        tagging_input=tagging_input,
                    )
                )
            return

        raise RuntimeError("未知 Tagging 共识状态")

    def _build_ai_input(
        self,
        item: _WordWorkItem,
        category_key: TaggingCategoryKey,
        taxonomy_version: int,
        selection: Any,
        amazon_contexts: Mapping[str, Any],
    ) -> TaggingInput | None:
        """仅在得到一份有效内部或 Amazon 背景后构造 AI 输入。"""

        if selection is None:
            return None
        product_context: Mapping[str, Any] | None
        if selection.needs_fetch:
            amazon_result = amazon_contexts.get(selection.representative_asin)
            if (
                amazon_result is None
                or amazon_result.status != AmazonProductSummaryStatus.READY
                or amazon_result.context is None
            ):
                # Amazon 获取失败必须转为现有 INCOMPLETE，绝不能用空背景请求
                # 三家 AI；失败详情由 Provider 作为运行时诊断保留。
                return None
            product_context = amazon_result.context.to_dict()
        elif selection.context is not None:
            product_context = selection.context.to_dict()
        else:
            return None

        top_phrases = item.word_result.get("topPhrases")
        safe_top_phrases = tuple(
            phrase.strip()
            for phrase in top_phrases
            if isinstance(phrase, str) and phrase.strip()
        ) if isinstance(top_phrases, list) else ()

        return TaggingInput(
            item_id=(
                f"{category_key.value}:{taxonomy_version}:{item.word}"
            ),
            # Pipeline Result 继续保留原始月份 key，确保能回填至 ``""``
            # 对应的最近30天结果；只有对 AI 的展示输入改为明确时间范围。
            month=self._tagging_month(item.month),
            word=item.word,
            category=category_display_name(category_key),
            top_phrases=safe_top_phrases,
            product_context_source=selection.source,
            representative_asin=selection.representative_asin,
            product_context=product_context,
        )

    def _select_product_context(
        self,
        item: _WordWorkItem,
    ) -> Any | None:
        """选择单一背景来源；这里不触发浏览器或 AI，只确定业务优先级。"""

        source_asin_stats = item.word_result.get("sourceAsinStats")
        if not isinstance(source_asin_stats, Mapping):
            return None
        # 人工审核保存只需要数据库与既有 AI 结果；当 Service 仅用于该
        # 入口时不构造产品资料服务，任何新的 AI 请求则明确视为不完整。
        if self._product_knowledge_service is None:
            return None
        selection = self._product_knowledge_service.select_product_context(
            source_asin_stats,
        )
        if selection is None:
            return None
        return selection

    async def _persist_formal_decision(
        self,
        *,
        category_key: TaggingCategoryKey,
        word: str,
        taxonomy_version: int,
        label: TagLabel,
        reason: str,
        decision_source: TaggingDecisionSource,
        representative_asin: str,
        product_context_source: str,
        provider_results: Mapping[str, Mapping[str, Any]],
    ) -> TaggingLabelCacheRecord:
        """同一事务更新 current cache 并追加审计，避免出现半成品正式决定。"""

        self._validate_formal_label_fields(
            label,
            reason,
            representative_asin,
            product_context_source,
        )
        async with self._transaction_scope() as connection:
            repository = self._repository_for(connection)
            requested_cache_record = TaggingLabelCacheRecord(
                id=uuid4(),
                category_key=category_key,
                word=word,
                taxonomy_version=taxonomy_version,
                label=label,
                reason=reason.strip(),
                decision_source=decision_source,
                representative_asin=representative_asin.strip().upper(),
                product_context_source=product_context_source.strip(),
            )
            current_cache_record = await repository.upsert_current(
                requested_cache_record
            )
            await repository.append_decision(
                TaggingLabelDecisionRecord(
                    id=uuid4(),
                    cache_id=current_cache_record.id,
                    category_key=category_key,
                    word=word,
                    taxonomy_version=taxonomy_version,
                    label=label,
                    reason=reason.strip(),
                    decision_source=decision_source,
                    representative_asin=representative_asin.strip().upper(),
                    product_context_source=product_context_source.strip(),
                    provider_results=provider_results,
                )
            )
        return current_cache_record

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[AsyncConnection | None]:
        """生产环境必须使用 DatabaseManager transaction；测试可注入无数据库实现。"""

        if self._database is None:
            yield None
            return
        async with self._database.transaction() as connection:
            yield connection

    def _repository_for(
        self,
        connection: AsyncConnection | None,
    ) -> TaggingLabelRepository:
        """按是否在事务内构造 Repository，保证多个 SQL 复用同一连接。"""

        return self._repository_factory(connection)

    def _default_repository(
        self,
        connection: AsyncConnection | None,
    ) -> TaggingLabelRepository:
        """生产默认 Repository 工厂，不允许跳过正式 DatabaseManager。"""

        if self._database is None:
            raise RuntimeError("TaggingService 缺少 DatabaseManager")
        return TaggingLabelRepository(self._database, connection)

    @classmethod
    def _build_work_items(
        cls,
        word_results_by_month: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> list[_WordWorkItem]:
        """从正式 Word Result 提取 canonical word，拒绝无效月度 identity。"""

        work_items: list[_WordWorkItem] = []
        seen_identities: set[tuple[str, str]] = set()
        for month, word_results in word_results_by_month.items():
            if (
                not isinstance(month, str)
                or (
                    not month.strip()
                    and month != cls._RECENT_30_DAYS_REQUEST_MONTH
                )
            ):
                raise ValueError("month 必须是自然月或最近30天请求范围")
            if not isinstance(word_results, Sequence):
                raise ValueError("每个月的 word results 必须是序列")
            for word_result in word_results:
                if not isinstance(word_result, Mapping):
                    raise ValueError("word result 必须是对象")
                word = normalize_cache_word(word_result.get("word"))
                identity = (month, word)
                if identity in seen_identities:
                    raise ValueError("同一 month 中不允许重复正式 word")
                seen_identities.add(identity)
                work_items.append(
                    _WordWorkItem(
                        month=month,
                        word=word,
                        word_result=word_result,
                    )
                )
        return work_items

    @classmethod
    def _tagging_month(cls, month: str) -> str:
        """将 SellerSprite 相对日期 sentinel 映射为 AI 可读的时间范围。"""

        if month == cls._RECENT_30_DAYS_REQUEST_MONTH:
            return cls._RECENT_30_DAYS_TAGGING_MONTH
        return month

    @staticmethod
    def _cache_hit_result(
        item: _WordWorkItem,
        category_key: TaggingCategoryKey,
        taxonomy_version: int,
        cached_record: TaggingLabelCacheRecord,
    ) -> TaggingPipelineResult:
        """缓存命中直接复用正式 label/reason，绝不触发上下文或 AI。"""

        return TaggingPipelineResult(
            month=item.month,
            word=item.word,
            category_key=category_key,
            taxonomy_version=taxonomy_version,
            status=TaggingPipelineStatus.CACHE_HIT,
            label=cached_record.label,
            reason=cached_record.reason,
            decision_source=cached_record.decision_source,
        )

    @staticmethod
    def _incomplete_result(
        item: _WordWorkItem,
        category_key: TaggingCategoryKey,
        taxonomy_version: int,
        *,
        provider_results: Mapping[str, ProviderTaggingOutcome] | None = None,
        tagging_input: TaggingInput | None = None,
    ) -> TaggingPipelineResult:
        """未得到三方完整合法结论时只保留运行时失败状态。"""

        return TaggingPipelineResult(
            month=item.month,
            word=item.word,
            category_key=category_key,
            taxonomy_version=taxonomy_version,
            status=TaggingPipelineStatus.INCOMPLETE,
            label=None,
            reason=None,
            decision_source=None,
            provider_results=dict(provider_results or {}),
            tagging_input=tagging_input,
        )

    @staticmethod
    def _build_review_item(
        result: TaggingPipelineResult,
        tagging_input: TaggingInput,
        consensus_result: TaggingConsensusResult,
    ) -> TaggingReviewItem:
        """从同一输入快照构造现有审核 Dialog 所需的纯运行时对象。"""

        return TaggingReviewItem(
            # 同一 word 可能在多个自然月出现；审核 identity 必须包含 month，
            # 避免本轮 Dialog 的两条独立业务结果相互覆盖。
            item_id=f"{tagging_input.item_id}:{result.month}",
            month=result.month,
            word=result.word,
            category_key=result.category_key,
            taxonomy_version=result.taxonomy_version,
            top_phrases=tagging_input.top_phrases,
            representative_asin=tagging_input.representative_asin,
            product_context_source=tagging_input.product_context_source,
            # ProductContext 只随当前内存审核项存在，绝不写进任何正式数据表。
            product_context=tagging_input.product_context,
            provider_results=consensus_result.provider_results,
            status=TaggingReviewStatus.PENDING,
        )

    @staticmethod
    def _provider_audit_snapshot(
        consensus_result: TaggingConsensusResult,
    ) -> dict[str, dict[str, Any]]:
        """仅保存三家已验证 label/reason/model，不保存 Header 或原始响应。"""

        provider_snapshot: dict[str, dict[str, Any]] = {}
        for provider_id, outcome in consensus_result.provider_results.items():
            if not isinstance(outcome, ProviderTaggingResult):
                raise ValueError("AI_CONSENSUS 不允许含失败 Provider")
            if len(outcome.decisions) != 1:
                raise ValueError("AI_CONSENSUS Provider 决策数量异常")
            decision = outcome.decisions[0]
            provider_snapshot[provider_id] = {
                "modelId": outcome.model_id,
                "label": decision.label.value,
                "reason": decision.reason,
            }
        if set(provider_snapshot) != {"openai", "anthropic", "google"}:
            raise ValueError("AI_CONSENSUS 必须保存三家 Provider 的审计摘要")
        return provider_snapshot

    @staticmethod
    def _validate_taxonomy_version(taxonomy_version: int) -> None:
        """版本只能由显式业务升级，调用方不得隐式传入无效值。"""

        if (
            isinstance(taxonomy_version, bool)
            or not isinstance(taxonomy_version, int)
            or taxonomy_version < 1
        ):
            raise ValueError("taxonomy_version 必须是正整数")

    @staticmethod
    def _validate_formal_label_fields(
        label: TagLabel,
        reason: str,
        representative_asin: str,
        product_context_source: str,
    ) -> None:
        """正式结论必须完整标识来源，但不持久化完整 ProductContext。"""

        if not isinstance(label, TagLabel):
            raise ValueError("label 必须是 TagLabel")
        for field_name, value in (
            ("reason", reason),
            ("representative_asin", representative_asin),
            ("product_context_source", product_context_source),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 必须是非空字符串")
