"""正式标签缓存与 append-only 标签审计的 PostgreSQL 数据访问。"""

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg.types.json import Jsonb

from models.tagging import TagLabel
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    TaggingLabelDecisionRecord,
)
from repositories.base_repository import BaseRepository


class TaggingLabelRepository(BaseRepository):
    """只负责缓存/审计 SQL 与行模型映射，不执行打标业务判断。"""

    async def list_by_words(
        self,
        category_key: TaggingCategoryKey,
        taxonomy_version: int,
        words: Sequence[str],
    ) -> dict[str, TaggingLabelCacheRecord]:
        """以一次查询加载一个品类、版本下的全部当前缓存，避免 N+1。"""

        normalized_words = tuple(sorted(set(words)))
        if not normalized_words:
            return {}

        sql = """
            SELECT *
            FROM tagging_label_consensus
            WHERE category_key = %(category_key)s
              AND taxonomy_version = %(taxonomy_version)s
              AND word = ANY(%(words)s)
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    {
                        "category_key": category_key.value,
                        "taxonomy_version": taxonomy_version,
                        "words": list(normalized_words),
                    },
                )
                rows = await cursor.fetchall()

        return {
            record.word: record
            for row in rows
            if isinstance(row, Mapping)
            for record in (self._cache_record_from_row(row),)
        }

    async def upsert_current(
        self,
        record: TaggingLabelCacheRecord,
    ) -> TaggingLabelCacheRecord:
        """原子更新 current cache；唯一键冲突时保留同一行 identity。"""

        sql = """
            INSERT INTO tagging_label_consensus (
                id,
                category_key,
                word,
                taxonomy_version,
                label,
                reason,
                decision_source,
                representative_asin,
                product_context_source
            )
            VALUES (
                %(id)s,
                %(category_key)s,
                %(word)s,
                %(taxonomy_version)s,
                %(label)s,
                %(reason)s,
                %(decision_source)s,
                %(representative_asin)s,
                %(product_context_source)s
            )
            ON CONFLICT (category_key, word, taxonomy_version)
            DO UPDATE SET
                label = EXCLUDED.label,
                reason = EXCLUDED.reason,
                decision_source = EXCLUDED.decision_source,
                representative_asin = EXCLUDED.representative_asin,
                product_context_source = EXCLUDED.product_context_source,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, self._cache_parameters(record))
                row = await cursor.fetchone()

        if not isinstance(row, Mapping):
            raise RuntimeError("标签缓存写入后未返回记录")
        return self._cache_record_from_row(row)

    async def append_decision(
        self,
        record: TaggingLabelDecisionRecord,
    ) -> TaggingLabelDecisionRecord:
        """追加一条形成正式标签的审计事件，绝不覆盖旧审计。"""

        sql = """
            INSERT INTO tagging_label_decisions (
                id,
                cache_id,
                category_key,
                word,
                taxonomy_version,
                label,
                reason,
                decision_source,
                representative_asin,
                product_context_source,
                provider_results
            )
            VALUES (
                %(id)s,
                %(cache_id)s,
                %(category_key)s,
                %(word)s,
                %(taxonomy_version)s,
                %(label)s,
                %(reason)s,
                %(decision_source)s,
                %(representative_asin)s,
                %(product_context_source)s,
                %(provider_results)s
            )
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, self._decision_parameters(record))
                row = await cursor.fetchone()

        if not isinstance(row, Mapping):
            raise RuntimeError("标签审计写入后未返回记录")
        return self._decision_record_from_row(row)

    @staticmethod
    def _cache_parameters(record: TaggingLabelCacheRecord) -> dict[str, Any]:
        """将正式缓存 Model 转换为 SQL 参数，不传播裸 Row。"""

        return {
            "id": record.id,
            "category_key": record.category_key.value,
            "word": record.word,
            "taxonomy_version": record.taxonomy_version,
            "label": record.label.value,
            "reason": record.reason,
            "decision_source": record.decision_source.value,
            "representative_asin": record.representative_asin,
            "product_context_source": record.product_context_source,
        }

    @staticmethod
    def _decision_parameters(record: TaggingLabelDecisionRecord) -> dict[str, Any]:
        """将审计 Model 转换为 JSONB 安全 SQL 参数。"""

        return {
            "id": record.id,
            "cache_id": record.cache_id,
            "category_key": record.category_key.value,
            "word": record.word,
            "taxonomy_version": record.taxonomy_version,
            "label": record.label.value,
            "reason": record.reason,
            "decision_source": record.decision_source.value,
            "representative_asin": record.representative_asin,
            "product_context_source": record.product_context_source,
            "provider_results": Jsonb(dict(record.provider_results)),
        }

    @staticmethod
    def _cache_record_from_row(row: Mapping[str, Any]) -> TaggingLabelCacheRecord:
        """映射 current cache 行，并拒绝不符合正式枚举的数据。"""

        return TaggingLabelCacheRecord(
            id=row["id"],
            category_key=TaggingCategoryKey(row["category_key"]),
            word=str(row["word"]),
            taxonomy_version=int(row["taxonomy_version"]),
            label=TagLabel(row["label"]),
            reason=str(row["reason"]) if row.get("reason") is not None else None,
            decision_source=TaggingDecisionSource(row["decision_source"]),
            representative_asin=str(row["representative_asin"]),
            product_context_source=str(row["product_context_source"]),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _decision_record_from_row(
        row: Mapping[str, Any],
    ) -> TaggingLabelDecisionRecord:
        """映射审计行；Provider 结果只保留已白名单化的业务摘要。"""

        provider_results = row.get("provider_results")
        if not isinstance(provider_results, dict):
            raise ValueError("标签审计 provider_results 结构异常")

        return TaggingLabelDecisionRecord(
            id=row["id"],
            cache_id=row["cache_id"],
            category_key=TaggingCategoryKey(row["category_key"]),
            word=str(row["word"]),
            taxonomy_version=int(row["taxonomy_version"]),
            label=TagLabel(row["label"]),
            reason=str(row["reason"]) if row.get("reason") is not None else None,
            decision_source=TaggingDecisionSource(row["decision_source"]),
            representative_asin=str(row["representative_asin"]),
            product_context_source=str(row["product_context_source"]),
            provider_results={
                str(provider_id): dict(provider_result)
                for provider_id, provider_result in provider_results.items()
                if isinstance(provider_id, str)
                and isinstance(provider_result, Mapping)
            },
            created_at=row.get("created_at"),
        )
