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
        *,
        include_inactive: bool = False,
    ) -> dict[str, TaggingLabelCacheRecord]:
        """以一次查询加载一个品类、版本下的全部当前缓存，避免 N+1。"""

        normalized_words = tuple(sorted(set(words)))
        if not normalized_words:
            return {}

        active_clause = "" if include_inactive else "AND is_active = TRUE"
        sql = f"""
            SELECT *
            FROM tagging_label_consensus
            WHERE category_key = %(category_key)s
              AND taxonomy_version = %(taxonomy_version)s
              AND word = ANY(%(words)s)
              {active_clause}
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

    async def insert_if_cache_miss(
        self,
        record: TaggingLabelCacheRecord,
        *,
        expected_inactive_revision: int | None = None,
    ) -> TaggingLabelCacheRecord | None:
        """仅在本轮读取时确实不存在 identity 时写入 AI/审核结论。

        管理窗口在 AI 请求期间创建、修改或逻辑删除同一 identity 时，唯一键
        冲突会返回 ``None``。调用方必须放弃该迟到结果，且绝不能追加 AI
        “已采用”审计；因此不会覆盖人工决定或复活逻辑删除缓存。
        """

        if expected_inactive_revision is not None:
            return await self._reactivate_after_known_cache_miss(
                record,
                expected_inactive_revision,
            )

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
                product_context_source,
                revision,
                is_active,
                invalidated_at
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
                %(product_context_source)s,
                %(revision)s,
                %(is_active)s,
                %(invalidated_at)s
            )
            ON CONFLICT (category_key, word, taxonomy_version)
            DO NOTHING
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, self._cache_parameters(record))
                row = await cursor.fetchone()

        return self._cache_record_from_row(row) if isinstance(row, Mapping) else None

    async def _reactivate_after_known_cache_miss(
        self,
        record: TaggingLabelCacheRecord,
        expected_inactive_revision: int,
    ) -> TaggingLabelCacheRecord | None:
        """仅允许新请求按其读到的失效 revision 重建缓存。

        若删除发生在 AI 发起后，或用户后来手工写入，revision/is_active 条件
        都不再匹配，迟到结果只能安全丢弃。
        """

        sql = """
            UPDATE tagging_label_consensus
            SET label = %(label)s,
                reason = %(reason)s,
                decision_source = %(decision_source)s,
                representative_asin = %(representative_asin)s,
                product_context_source = %(product_context_source)s,
                is_active = TRUE,
                invalidated_at = NULL,
                revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE category_key = %(category_key)s
              AND word = %(word)s
              AND taxonomy_version = %(taxonomy_version)s
              AND is_active = FALSE
              AND revision = %(expected_inactive_revision)s
            RETURNING *
        """
        parameters = self._cache_parameters(record)
        parameters["expected_inactive_revision"] = expected_inactive_revision
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, parameters)
                row = await cursor.fetchone()
        return self._cache_record_from_row(row) if isinstance(row, Mapping) else None

    async def upsert_current(
        self,
        record: TaggingLabelCacheRecord,
    ) -> TaggingLabelCacheRecord | None:
        """兼容旧调用名，但不再允许无条件覆盖人工当前缓存。"""

        return await self.insert_if_cache_miss(record)

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
            "revision": record.revision,
            "is_active": record.is_active,
            "invalidated_at": record.invalidated_at,
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
            representative_asin=(
                str(row["representative_asin"])
                if row.get("representative_asin") is not None
                else None
            ),
            product_context_source=(
                str(row["product_context_source"])
                if row.get("product_context_source") is not None
                else None
            ),
            revision=int(row.get("revision", 1)),
            is_active=bool(row.get("is_active", True)),
            invalidated_at=row.get("invalidated_at"),
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
            representative_asin=(
                str(row["representative_asin"])
                if row.get("representative_asin") is not None
                else None
            ),
            product_context_source=(
                str(row["product_context_source"])
                if row.get("product_context_source") is not None
                else None
            ),
            provider_results={
                str(provider_id): dict(provider_result)
                for provider_id, provider_result in provider_results.items()
                if isinstance(provider_id, str)
                and isinstance(provider_result, Mapping)
            },
            created_at=row.get("created_at"),
        )
