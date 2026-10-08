"""人工归一审核决策的 PostgreSQL append-only 持久化。"""

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg.types.json import Jsonb

from models.normalization_review_decision import (
    NormalizationReviewDecision,
    NormalizationReviewDecisionRecord,
)
from models.normalization_rule import NormalizationRuleType
from repositories.base_repository import BaseRepository


class NormalizationReviewRepository(BaseRepository):
    """只执行审核历史记录的 INSERT / 查询及数据库行映射。"""

    async def save_decision(
        self,
        record: NormalizationReviewDecisionRecord,
    ) -> NormalizationReviewDecisionRecord:
        """追加一条人工审核事件，绝不 UPDATE 旧决定。"""

        sql = """
            INSERT INTO normalization_review_decisions (
                id,
                candidate_fingerprint,
                candidate_id,
                decision,
                rule_type,
                variants,
                suggested_canonical,
                approved_canonical,
                reason_types,
                context_snapshot,
                normalization_revision
            )
            VALUES (
                %(id)s,
                %(candidate_fingerprint)s,
                %(candidate_id)s,
                %(decision)s,
                %(rule_type)s,
                %(variants)s,
                %(suggested_canonical)s,
                %(approved_canonical)s,
                %(reason_types)s,
                %(context_snapshot)s,
                %(normalization_revision)s
            )
            RETURNING *
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    {
                        "id": record.id,
                        "candidate_fingerprint": record.candidate_fingerprint,
                        "candidate_id": record.candidate_id,
                        "decision": record.decision.value,
                        "rule_type": record.rule_type.value,
                        "variants": Jsonb(list(record.variants)),
                        "suggested_canonical": record.suggested_canonical,
                        "approved_canonical": record.approved_canonical,
                        "reason_types": Jsonb(list(record.reason_types)),
                        "context_snapshot": Jsonb(record.context_snapshot),
                        "normalization_revision": record.normalization_revision,
                    },
                )
                row = await cursor.fetchone()

        if not isinstance(row, Mapping):
            raise RuntimeError("审核决策写入后未返回记录")
        return self._record_from_row(row)

    async def get_latest_decision_by_fingerprint(
        self,
        candidate_fingerprint: str,
    ) -> NormalizationReviewDecisionRecord | None:
        """按 created_at、id 降序读取同一稳定 identity 的最新人工决定。"""

        sql = """
            SELECT *
            FROM normalization_review_decisions
            WHERE candidate_fingerprint = %(candidate_fingerprint)s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    {"candidate_fingerprint": candidate_fingerprint},
                )
                row = await cursor.fetchone()

        return self._record_from_row(row) if isinstance(row, Mapping) else None

    async def list_decisions(
        self,
        *,
        candidate_fingerprint: str | None = None,
        limit: int = 100,
    ) -> list[NormalizationReviewDecisionRecord]:
        """按最新优先读取审核历史；本次只提供查询，不参与候选自动处理。"""

        if limit <= 0:
            raise ValueError("limit 必须大于 0")

        where_clause = ""
        parameters: dict[str, Any] = {"limit": limit}
        if candidate_fingerprint is not None:
            where_clause = "WHERE candidate_fingerprint = %(candidate_fingerprint)s"
            parameters["candidate_fingerprint"] = candidate_fingerprint

        sql = f"""
            SELECT *
            FROM normalization_review_decisions
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, parameters)
                rows = await cursor.fetchall()

        return [
            self._record_from_row(row)
            for row in rows
            if isinstance(row, Mapping)
        ]

    async def list_decisions_by_fingerprints(
        self,
        candidate_fingerprints: Sequence[str],
    ) -> dict[str, list[NormalizationReviewDecisionRecord]]:
        """一次查询读取多个候选的完整历史，避免候选列表产生 N+1 查询。"""

        fingerprints = tuple(
            sorted(
                {
                    fingerprint.strip()
                    for fingerprint in candidate_fingerprints
                    if isinstance(fingerprint, str) and fingerprint.strip()
                }
            )
        )
        if not fingerprints:
            return {}

        sql = """
            SELECT *
            FROM normalization_review_decisions
            WHERE candidate_fingerprint = ANY(%(candidate_fingerprints)s)
            ORDER BY candidate_fingerprint, created_at DESC, id DESC
        """
        async with self.connection_scope() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    {"candidate_fingerprints": list(fingerprints)},
                )
                rows = await cursor.fetchall()

        records_by_fingerprint: dict[
            str,
            list[NormalizationReviewDecisionRecord],
        ] = {fingerprint: [] for fingerprint in fingerprints}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            record = self._record_from_row(row)
            records_by_fingerprint.setdefault(
                record.candidate_fingerprint,
                [],
            ).append(record)
        return records_by_fingerprint

    @staticmethod
    def _record_from_row(
        row: Mapping[str, Any],
    ) -> NormalizationReviewDecisionRecord:
        """将 psycopg dict_row 映射为稳定 Model，避免裸 Row 跨层传播。"""

        variants = row.get("variants")
        reason_types = row.get("reason_types")
        context_snapshot = row.get("context_snapshot")
        if not isinstance(variants, list) or not isinstance(reason_types, list):
            raise ValueError("审核决策 JSON 字段结构异常")
        if not isinstance(context_snapshot, dict):
            raise ValueError("审核决策上下文结构异常")

        return NormalizationReviewDecisionRecord(
            id=row["id"],
            candidate_fingerprint=str(row["candidate_fingerprint"]),
            candidate_id=str(row["candidate_id"]),
            decision=NormalizationReviewDecision(row["decision"]),
            rule_type=NormalizationRuleType(row["rule_type"]),
            variants=tuple(
                variant for variant in variants if isinstance(variant, str)
            ),
            suggested_canonical=(
                str(row["suggested_canonical"])
                if row.get("suggested_canonical") is not None
                else None
            ),
            approved_canonical=(
                str(row["approved_canonical"])
                if row.get("approved_canonical") is not None
                else None
            ),
            reason_types=tuple(
                reason for reason in reason_types if isinstance(reason, str)
            ),
            context_snapshot=dict(context_snapshot),
            normalization_revision=int(row["normalization_revision"]),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
