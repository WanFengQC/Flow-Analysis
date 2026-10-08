"""标签管理当前缓存、历史和逻辑删除的数据访问。"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from models.tagging import TagLabel
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    TaggingLabelDecisionRecord,
)
from models.tagging_label_management import (
    TaggingLabelManagementAction,
    TaggingLabelManagementAuditRecord,
)
from repositories.base_repository import BaseRepository
from repositories.tagging_label_repository import TaggingLabelRepository


class TaggingLabelManagementRepository(BaseRepository):
    """管理页专用 SQL；不复用会覆盖当前值的旧式 UPSERT 语义。"""

    async def list_current(
        self,
        *,
        category_key: TaggingCategoryKey | None,
        label: TagLabel | None,
        decision_source: TaggingDecisionSource | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TaggingLabelCacheRecord], int]:
        """按稳定 identity 过滤 current cache；失效缓存不作为管理当前值展示。"""

        if limit <= 0 or offset < 0:
            raise ValueError("分页参数无效")
        where, parameters = self._where(
            category_key,
            label,
            decision_source,
            search,
        )
        rows_sql = f"""
            SELECT *
            FROM tagging_label_consensus
            {where}
            ORDER BY updated_at DESC, id DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        total_sql = f"SELECT COUNT(*) AS total FROM tagging_label_consensus {where}"
        parameters.update({"limit": limit, "offset": offset})
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(rows_sql, parameters)
                rows = await cursor.fetchall()
                await cursor.execute(total_sql, parameters)
                count_row = await cursor.fetchone()
        total = int(count_row["total"]) if isinstance(count_row, Mapping) else 0
        return (
            [
                TaggingLabelRepository._cache_record_from_row(row)
                for row in rows
                if isinstance(row, Mapping)
            ],
            total,
        )

    async def get_by_identity(
        self,
        *,
        category_key: TaggingCategoryKey,
        word: str,
        taxonomy_version: int,
        for_update: bool = False,
    ) -> TaggingLabelCacheRecord | None:
        """读取包含失效记录在内的 identity，阻止 AI 复活逻辑删除记录。"""

        sql = """
            SELECT *
            FROM tagging_label_consensus
            WHERE category_key = %(category_key)s
              AND word = %(word)s
              AND taxonomy_version = %(taxonomy_version)s
        """ + (" FOR UPDATE" if for_update else "")
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    {
                        "category_key": category_key.value,
                        "word": word,
                        "taxonomy_version": taxonomy_version,
                    },
                )
                row = await cursor.fetchone()
        return (
            TaggingLabelRepository._cache_record_from_row(row)
            if isinstance(row, Mapping)
            else None
        )

    async def get_current_by_id(
        self,
        cache_id: UUID,
        *,
        for_update: bool = False,
    ) -> TaggingLabelCacheRecord | None:
        """按缓存 id 读取当前有效标签，并可在管理事务内加行锁。"""

        sql = """
            SELECT *
            FROM tagging_label_consensus
            WHERE id = %(id)s AND is_active = TRUE
        """ + (" FOR UPDATE" if for_update else "")
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, {"id": cache_id})
                row = await cursor.fetchone()
        return (
            TaggingLabelRepository._cache_record_from_row(row)
            if isinstance(row, Mapping)
            else None
        )

    async def insert_manual(
        self,
        record: TaggingLabelCacheRecord,
    ) -> TaggingLabelCacheRecord:
        """插入从未出现过的人工管理 identity。"""

        sql = """
            INSERT INTO tagging_label_consensus (
                id, category_key, word, taxonomy_version, label, reason,
                decision_source, representative_asin, product_context_source,
                revision, is_active, invalidated_at
            ) VALUES (
                %(id)s, %(category_key)s, %(word)s, %(taxonomy_version)s,
                %(label)s, %(reason)s, %(decision_source)s,
                %(representative_asin)s, %(product_context_source)s,
                %(revision)s, %(is_active)s, %(invalidated_at)s
            )
            RETURNING *
        """
        return await self._write_cache(sql, TaggingLabelRepository._cache_parameters(record))

    async def reactivate_manual(
        self,
        *,
        previous: TaggingLabelCacheRecord,
        label: TagLabel,
        reason: str,
    ) -> TaggingLabelCacheRecord | None:
        """只有管理页明确新增时才可重用已删除 identity，不允许 AI 复活。"""

        sql = """
            UPDATE tagging_label_consensus
            SET label = %(label)s,
                reason = %(reason)s,
                decision_source = %(decision_source)s,
                representative_asin = NULL,
                product_context_source = NULL,
                is_active = TRUE,
                invalidated_at = NULL,
                revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s
              AND revision = %(revision)s
              AND is_active = FALSE
            RETURNING *
        """
        return await self._write_cache(
            sql,
            {
                "id": previous.id,
                "revision": previous.revision,
                "label": label.value,
                "reason": reason,
                "decision_source": TaggingDecisionSource.MANUAL_MANAGEMENT.value,
            },
            allow_missing=True,
        )

    async def update_manual(
        self,
        *,
        cache_id: UUID,
        revision: int,
        label: TagLabel,
        reason: str,
    ) -> TaggingLabelCacheRecord | None:
        """以 revision 条件更新人工当前标签，避免覆写其他管理操作。"""

        sql = """
            UPDATE tagging_label_consensus
            SET label = %(label)s,
                reason = %(reason)s,
                decision_source = %(decision_source)s,
                representative_asin = NULL,
                product_context_source = NULL,
                revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s
              AND revision = %(revision)s
              AND is_active = TRUE
            RETURNING *
        """
        return await self._write_cache(
            sql,
            {
                "id": cache_id,
                "revision": revision,
                "label": label.value,
                "reason": reason,
                "decision_source": TaggingDecisionSource.MANUAL_MANAGEMENT.value,
            },
            allow_missing=True,
        )

    async def deactivate_current(
        self,
        *,
        cache_id: UUID,
        revision: int,
    ) -> TaggingLabelCacheRecord | None:
        """逻辑删除当前缓存，唯一 identity 保留以拦截迟到 AI 写入。"""

        sql = """
            UPDATE tagging_label_consensus
            SET is_active = FALSE,
                invalidated_at = CURRENT_TIMESTAMP,
                revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s
              AND revision = %(revision)s
              AND is_active = TRUE
            RETURNING *
        """
        return await self._write_cache(
            sql,
            {"id": cache_id, "revision": revision},
            allow_missing=True,
        )

    async def append_decision(
        self,
        record: TaggingLabelDecisionRecord,
    ) -> TaggingLabelDecisionRecord:
        """复用正式标签形成事件表，记录管理页的人工标签版本。"""

        return await TaggingLabelRepository(
            self._database,
            self._external_connection,
        ).append_decision(record)

    async def append_management_audit(
        self,
        record: TaggingLabelManagementAuditRecord,
    ) -> TaggingLabelManagementAuditRecord:
        """追加 CREATE/UPDATE/DELETE 审计快照。"""

        sql = """
            INSERT INTO tagging_label_management_audits (
                id, cache_id, action, before_snapshot, after_snapshot
            ) VALUES (
                %(id)s, %(cache_id)s, %(action)s, %(before_snapshot)s, %(after_snapshot)s
            )
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    {
                        "id": record.id,
                        "cache_id": record.cache_id,
                        "action": record.action.value,
                        "before_snapshot": (
                            Jsonb(record.before_snapshot)
                            if record.before_snapshot is not None
                            else None
                        ),
                        "after_snapshot": (
                            Jsonb(record.after_snapshot)
                            if record.after_snapshot is not None
                            else None
                        ),
                    },
                )
                row = await cursor.fetchone()
        if not isinstance(row, Mapping):
            raise RuntimeError("标签管理审计写入后未返回记录")
        return self._management_audit_from_row(row)

    async def list_history(self, cache_id: UUID) -> list[dict[str, Any]]:
        """合并旧正式标签审计与新的管理审计，按时间供详情窗口只读展示。"""

        sql = """
            SELECT
                'LABEL_DECISION' AS event_type,
                id,
                cache_id,
                decision_source AS action,
                jsonb_build_object(
                    'label', label,
                    'reason', reason,
                    'representativeAsin', representative_asin,
                    'productContextSource', product_context_source
                ) AS snapshot,
                created_at
            FROM tagging_label_decisions
            WHERE cache_id = %(cache_id)s
            UNION ALL
            SELECT
                'MANAGEMENT' AS event_type,
                id,
                cache_id,
                action,
                jsonb_build_object(
                    'before', before_snapshot,
                    'after', after_snapshot
                ) AS snapshot,
                created_at
            FROM tagging_label_management_audits
            WHERE cache_id = %(cache_id)s
            ORDER BY created_at DESC, id DESC
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, {"cache_id": cache_id})
                rows = await cursor.fetchall()
        return [
            {
                "eventType": str(row["event_type"]),
                "id": str(row["id"]),
                "cacheId": str(row["cache_id"]),
                "action": str(row["action"]),
                "snapshot": dict(row["snapshot"])
                if isinstance(row.get("snapshot"), Mapping)
                else {},
                "createdAt": (
                    row["created_at"].isoformat()
                    if hasattr(row.get("created_at"), "isoformat")
                    else None
                ),
            }
            for row in rows
            if isinstance(row, Mapping)
        ]

    async def _write_cache(
        self,
        sql: str,
        parameters: Mapping[str, Any],
        *,
        allow_missing: bool = False,
    ) -> TaggingLabelCacheRecord | None:
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, parameters)
                row = await cursor.fetchone()
        if not isinstance(row, Mapping):
            if allow_missing:
                return None
            raise RuntimeError("标签缓存写入后未返回记录")
        return TaggingLabelRepository._cache_record_from_row(row)

    @staticmethod
    def _where(
        category_key: TaggingCategoryKey | None,
        label: TagLabel | None,
        decision_source: TaggingDecisionSource | None,
        search: str | None,
    ) -> tuple[str, dict[str, Any]]:
        clauses = ["is_active = TRUE"]
        parameters: dict[str, Any] = {}
        if category_key is not None:
            clauses.append("category_key = %(category_key)s")
            parameters["category_key"] = category_key.value
        if label is not None:
            clauses.append("label = %(label)s")
            parameters["label"] = label.value
        if decision_source is not None:
            clauses.append("decision_source = %(decision_source)s")
            parameters["decision_source"] = decision_source.value
        if search and search.strip():
            clauses.append("word ILIKE %(search)s")
            parameters["search"] = f"%{search.strip()}%"
        return "WHERE " + " AND ".join(clauses), parameters

    @staticmethod
    def _management_audit_from_row(
        row: Mapping[str, Any],
    ) -> TaggingLabelManagementAuditRecord:
        before = row.get("before_snapshot")
        after = row.get("after_snapshot")
        return TaggingLabelManagementAuditRecord(
            id=row["id"],
            cache_id=row["cache_id"],
            action=TaggingLabelManagementAction(row["action"]),
            before_snapshot=dict(before) if isinstance(before, Mapping) else None,
            after_snapshot=dict(after) if isinstance(after, Mapping) else None,
            created_at=row.get("created_at"),
        )
