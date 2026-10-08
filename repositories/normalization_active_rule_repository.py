"""归一化当前有效规则及其管理审计的数据访问层。"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from models.normalization_active_rule import (
    NormalizationActiveRuleAuditRecord,
    NormalizationActiveRuleRecord,
    NormalizationRuleAuditAction,
)
from models.normalization_rule import NormalizationRuleType
from repositories.base_repository import BaseRepository


class NormalizationActiveRuleRepository(BaseRepository):
    """只执行当前规则、版本审计的 SQL，不决定任何业务规则是否可用。"""

    async def acquire_management_lock(self) -> None:
        """串行化跨规则冲突校验，避免两个并发新增各自绕过全局校验。"""

        sql = "SELECT pg_advisory_xact_lock(hashtext('flow_analysis_normalization_active_rules'))"
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql)

    async def list_active_rules(
        self,
        *,
        search: str | None = None,
        rule_type: NormalizationRuleType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[NormalizationActiveRuleRecord], int]:
        """分页读取当前有效规则；搜索只作用于标准词和变体展示文本。"""

        if limit <= 0 or offset < 0:
            raise ValueError("分页参数无效")
        where, parameters = self._active_where(search, rule_type)
        select_sql = f"""
            SELECT *
            FROM normalization_active_rules
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        count_sql = f"""
            SELECT COUNT(*) AS total
            FROM normalization_active_rules
            {where}
        """
        parameters.update({"limit": limit, "offset": offset})
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(select_sql, parameters)
                rows = await cursor.fetchall()
                await cursor.execute(count_sql, parameters)
                count_row = await cursor.fetchone()
        total = int(count_row["total"]) if isinstance(count_row, Mapping) else 0
        return (
            [
                self._record_from_row(row)
                for row in rows
                if isinstance(row, Mapping)
            ],
            total,
        )

    async def list_all_active_for_validation(
        self,
    ) -> list[NormalizationActiveRuleRecord]:
        """在事务中读取完整有效规则集，用于复用既有冲突校验。"""

        sql = """
            SELECT *
            FROM normalization_active_rules
            WHERE is_active = TRUE
            ORDER BY created_at ASC, id ASC
            FOR UPDATE
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql)
                rows = await cursor.fetchall()
        return [
            self._record_from_row(row)
            for row in rows
            if isinstance(row, Mapping)
        ]

    async def get_active_rule(
        self,
        rule_id: UUID,
        *,
        for_update: bool = False,
    ) -> NormalizationActiveRuleRecord | None:
        """按 id 读取当前有效版本，可由 Service 在事务内要求行锁。"""

        sql = """
            SELECT *
            FROM normalization_active_rules
            WHERE id = %(id)s AND is_active = TRUE
        """ + (" FOR UPDATE" if for_update else "")
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, {"id": rule_id})
                row = await cursor.fetchone()
        return self._record_from_row(row) if isinstance(row, Mapping) else None

    async def insert_rule(
        self,
        record: NormalizationActiveRuleRecord,
    ) -> NormalizationActiveRuleRecord:
        """写入一个新规则版本；调用方已在事务中完成全局校验。"""

        sql = """
            INSERT INTO normalization_active_rules (
                id, rule_type, variants, canonical, source_candidate_id,
                source_reason_types, supersedes_rule_id, revision, is_active, revoked_at
            ) VALUES (
                %(id)s, %(rule_type)s, %(variants)s, %(canonical)s,
                %(source_candidate_id)s, %(source_reason_types)s,
                %(supersedes_rule_id)s, %(revision)s, %(is_active)s, %(revoked_at)s
            )
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, self._parameters(record))
                row = await cursor.fetchone()
        if not isinstance(row, Mapping):
            raise RuntimeError("归一化规则写入后未返回记录")
        return self._record_from_row(row)

    async def revoke_rule(
        self,
        rule_id: UUID,
        revision: int,
    ) -> NormalizationActiveRuleRecord | None:
        """逻辑撤销当前规则，乐观锁失败时不改变任何历史版本。"""

        sql = """
            UPDATE normalization_active_rules
            SET is_active = FALSE,
                revoked_at = CURRENT_TIMESTAMP,
                revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s
              AND revision = %(revision)s
              AND is_active = TRUE
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, {"id": rule_id, "revision": revision})
                row = await cursor.fetchone()
        return self._record_from_row(row) if isinstance(row, Mapping) else None

    async def append_audit(
        self,
        record: NormalizationActiveRuleAuditRecord,
    ) -> NormalizationActiveRuleAuditRecord:
        """追加当前规则管理审计，绝不修改既有审核历史。"""

        sql = """
            INSERT INTO normalization_active_rule_audits (
                id, rule_id, action, rule_snapshot
            ) VALUES (
                %(id)s, %(rule_id)s, %(action)s, %(rule_snapshot)s
            )
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    {
                        "id": record.id,
                        "rule_id": record.rule_id,
                        "action": record.action.value,
                        "rule_snapshot": Jsonb(record.rule_snapshot),
                    },
                )
                row = await cursor.fetchone()
        if not isinstance(row, Mapping):
            raise RuntimeError("归一化规则审计写入后未返回记录")
        return self._audit_from_row(row)

    async def list_history(
        self,
        rule_id: UUID,
        *,
        limit: int = 100,
    ) -> list[NormalizationActiveRuleAuditRecord]:
        """读取规则整条版本链的管理历史，UI 只展示不可变快照。"""

        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        sql = """
            WITH RECURSIVE ancestors AS (
                SELECT id, supersedes_rule_id
                FROM normalization_active_rules
                WHERE id = %(rule_id)s

                UNION ALL

                SELECT parent.id, parent.supersedes_rule_id
                FROM normalization_active_rules AS parent
                INNER JOIN ancestors AS child
                    ON child.supersedes_rule_id = parent.id
            ),
            root_rule AS (
                SELECT id
                FROM ancestors
                WHERE supersedes_rule_id IS NULL
                LIMIT 1
            ),
            version_chain AS (
                SELECT id
                FROM root_rule

                UNION ALL

                SELECT child.id
                FROM normalization_active_rules AS child
                INNER JOIN version_chain AS parent
                    ON child.supersedes_rule_id = parent.id
            )
            SELECT audit.*
            FROM normalization_active_rule_audits AS audit
            INNER JOIN version_chain
                ON version_chain.id = audit.rule_id
            ORDER BY audit.created_at DESC, audit.id DESC
            LIMIT %(limit)s
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, {"rule_id": rule_id, "limit": limit})
                rows = await cursor.fetchall()
        return [
            self._audit_from_row(row)
            for row in rows
            if isinstance(row, Mapping)
        ]

    @staticmethod
    def _active_where(
        search: str | None,
        rule_type: NormalizationRuleType | None,
    ) -> tuple[str, dict[str, Any]]:
        clauses = ["is_active = TRUE"]
        parameters: dict[str, Any] = {}
        if search and search.strip():
            clauses.append("(canonical ILIKE %(search)s OR variants::text ILIKE %(search)s)")
            parameters["search"] = f"%{search.strip()}%"
        if rule_type is not None:
            clauses.append("rule_type = %(rule_type)s")
            parameters["rule_type"] = rule_type.value
        return "WHERE " + " AND ".join(clauses), parameters

    @staticmethod
    def _parameters(record: NormalizationActiveRuleRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "rule_type": record.rule_type.value,
            "variants": Jsonb(list(record.variants)),
            "canonical": record.canonical,
            "source_candidate_id": record.source_candidate_id,
            "source_reason_types": Jsonb(list(record.source_reason_types)),
            "supersedes_rule_id": record.supersedes_rule_id,
            "revision": record.revision,
            "is_active": record.is_active,
            "revoked_at": record.revoked_at,
        }

    @staticmethod
    def _record_from_row(
        row: Mapping[str, Any],
    ) -> NormalizationActiveRuleRecord:
        variants = row.get("variants")
        reason_types = row.get("source_reason_types")
        if not isinstance(variants, list) or not isinstance(reason_types, list):
            raise ValueError("归一化规则 JSON 字段结构异常")
        return NormalizationActiveRuleRecord(
            id=row["id"],
            rule_type=NormalizationRuleType(row["rule_type"]),
            variants=tuple(
                value.strip()
                for value in variants
                if isinstance(value, str) and value.strip()
            ),
            canonical=str(row["canonical"]),
            source_candidate_id=(
                str(row["source_candidate_id"])
                if row.get("source_candidate_id") is not None
                else None
            ),
            source_reason_types=tuple(
                value.strip()
                for value in reason_types
                if isinstance(value, str) and value.strip()
            ),
            supersedes_rule_id=row.get("supersedes_rule_id"),
            revision=int(row["revision"]),
            is_active=bool(row["is_active"]),
            revoked_at=row.get("revoked_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _audit_from_row(
        row: Mapping[str, Any],
    ) -> NormalizationActiveRuleAuditRecord:
        snapshot = row.get("rule_snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("归一化规则审计快照结构异常")
        return NormalizationActiveRuleAuditRecord(
            id=row["id"],
            rule_id=row["rule_id"],
            action=NormalizationRuleAuditAction(row["action"]),
            rule_snapshot=dict(snapshot),
            created_at=row.get("created_at"),
        )
