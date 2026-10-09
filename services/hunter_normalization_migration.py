"""Hunter 历史归一化记录的纯内存预检。

本模块不连接数据库、不写入文件。正式导入器先使用本模块得到固定预览，
再在单一事务中保存规则、审计和迁移账本。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from models.tagging_label import TaggingCategoryKey
from services.normalization_rule_service import NormalizationRuleService


_LEGACY_CATEGORY_KEYS = {
    "Pillow": TaggingCategoryKey.PILLOW,
    "Stuffed Animals": TaggingCategoryKey.STUFFED_ANIMALS,
}


@dataclass(frozen=True, slots=True)
class LegacyNormalizationPreview:
    """不含敏感运行时配置的历史迁移预览。"""

    eligible: tuple[dict[str, Any], ...]
    deferred: tuple[dict[str, Any], ...]
    by_category: dict[TaggingCategoryKey, int]
    chain_messages: tuple[str, ...]

    @property
    def importable_count(self) -> int:
        return len(self.eligible)


def legacy_fingerprint(record: Mapping[str, Any], record_index: int) -> str:
    """用旧记录的不可变业务字段建立幂等 identity。"""

    source_scopes = record.get("source_scopes", [])
    material = "\x1f".join(
        (
            "hunter:traffic_analysis_word_cache_phrase_norm",
            str(record_index),
            str(record.get("category", "")),
            str(record.get("raw_phrase", "")).strip().lower(),
            str(record.get("normalized_phrase", "")).strip().lower(),
            str(record.get("status", "")),
            str(record.get("source", "")),
            json.dumps(source_scopes, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


def build_hunter_normalization_preview(
    records: Sequence[Mapping[str, Any]],
) -> LegacyNormalizationPreview:
    """仅接纳旧 manual_confirmed，并按旧品类独立使用正式规则校验器。"""

    candidates_by_category: dict[TaggingCategoryKey, list[dict[str, Any]]] = {
        key: [] for key in TaggingCategoryKey
    }
    for index, record in enumerate(records):
        if record.get("status") != "manual_confirmed":
            continue
        category_key = _LEGACY_CATEGORY_KEYS.get(record.get("category"))
        raw_phrase = record.get("raw_phrase")
        normalized_phrase = record.get("normalized_phrase")
        if category_key is None or not isinstance(raw_phrase, str) or not isinstance(normalized_phrase, str):
            continue
        candidates_by_category[category_key].append(
            {
                "id": f"legacy:{index}",
                "decision": "APPROVED",
                "variants": [raw_phrase],
                "approvedCanonical": normalized_phrase,
                "reasonTypes": ["HISTORICAL_IMPORT"],
                "legacy": dict(record),
                "legacyRecordIndex": index,
                "categoryKey": category_key,
                "sourceFingerprint": legacy_fingerprint(record, index),
            }
        )

    service = NormalizationRuleService()
    deferred_ids: set[str] = set()
    chain_messages: list[str] = []
    for category_key, candidates in candidates_by_category.items():
        result = service.build_approved_rules(candidates)
        for conflict in (*result.conflicts, *result.chain_references):
            deferred_ids.update(conflict.source_candidate_ids)
            chain_messages.append(
                f"{category_key.value}:{conflict.conflict_type}:{conflict.variant}"
            )

    eligible = tuple(
        candidate
        for candidates in candidates_by_category.values()
        for candidate in candidates
        if candidate["id"] not in deferred_ids
    )
    deferred = tuple(
        candidate
        for candidates in candidates_by_category.values()
        for candidate in candidates
        if candidate["id"] in deferred_ids
    )
    # 再次确认剔除链项后的每个品类确实可作为正式规则集激活。
    for category_key, candidates in candidates_by_category.items():
        allowed = [item for item in candidates if item["id"] not in deferred_ids]
        if service.build_approved_rules(allowed).approved_rules is None:
            raise ValueError(f"{category_key.value} 剔除冲突后仍无法建立规则集")
    return LegacyNormalizationPreview(
        eligible=eligible,
        deferred=deferred,
        by_category={key: len(value) for key, value in candidates_by_category.items()},
        chain_messages=tuple(sorted(set(chain_messages))),
    )


async def apply_hunter_normalization_preview(
    database: Any,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """在一个事务中写入规则、APPLY 审计和迁移账本。

    调用方必须已经完成用户确认。函数不接受 SQL 导出文件，只接受已审计的
    JSON 快照；目标表不存在时直接失败并由事务回滚。
    """

    preview = build_hunter_normalization_preview(records)
    candidate_by_index = {
        item["legacyRecordIndex"]: item
        for item in (*preview.eligible, *preview.deferred)
    }
    inserted_rules = 0
    inserted_ledger = 0
    async with database.transaction() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT current_database() AS name")
            current = await cursor.fetchone()
            if not isinstance(current, Mapping) or current.get("name") != "flow_analysis":
                raise RuntimeError("迁移拒绝：实际连接目标不是 flow_analysis")
            await cursor.execute("""
                SELECT to_regclass('public.normalization_legacy_rule_imports') AS name
            """)
            schema_row = await cursor.fetchone()
            if not isinstance(schema_row, Mapping) or not schema_row.get("name"):
                raise RuntimeError("迁移拒绝：尚未应用 006_add_normalization_rule_categories.sql")
            await cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext('flow_analysis_hunter_normalization_import'))"
            )
            await cursor.execute(
                "SELECT source_fingerprint FROM normalization_legacy_rule_imports FOR UPDATE"
            )
            existing = {
                str(row["source_fingerprint"])
                for row in await cursor.fetchall()
                if isinstance(row, Mapping)
            }
            await cursor.execute("""
                SELECT id, rule_type, variants, canonical, category_key
                FROM normalization_active_rules
                WHERE is_active = TRUE
                FOR UPDATE
            """)
            active_rows = [
                row for row in await cursor.fetchall() if isinstance(row, Mapping)
            ]
            existing_by_signature: dict[tuple[object, ...], object] = {}
            active_candidates: list[dict[str, Any]] = []
            for row in active_rows:
                variants = row.get("variants")
                if not isinstance(variants, list):
                    raise RuntimeError("现有归一规则 variants 结构异常")
                signature = (
                    row["rule_type"], tuple(sorted(str(item) for item in variants)),
                    str(row["canonical"]), row.get("category_key"),
                )
                existing_by_signature[signature] = row["id"]
                active_candidates.append({
                    "id": f"active:{row['id']}", "decision": "APPROVED",
                    "variants": variants, "approvedCanonical": row["canonical"],
                    "reasonTypes": [], "categoryKey": row.get("category_key"),
                })
            validator = NormalizationRuleService()
            for category_key in TaggingCategoryKey:
                scoped = [
                    item for item in active_candidates
                    if item["categoryKey"] in (None, category_key.value)
                ] + [
                    item for item in preview.eligible
                    if item["categoryKey"] == category_key
                ]
                if validator.build_approved_rules(scoped).approved_rules is None:
                    raise RuntimeError(
                        f"迁移拒绝：{category_key.value} 与现有规则冲突或形成链式引用"
                    )
            for index, legacy in enumerate(records):
                category_key = _LEGACY_CATEGORY_KEYS.get(legacy.get("category"))
                if category_key is None:
                    continue
                fingerprint = legacy_fingerprint(legacy, index)
                if fingerprint in existing:
                    continue
                candidate = candidate_by_index.get(index)
                status = str(legacy.get("status", ""))
                import_status = (
                    "IMPORTED" if candidate in preview.eligible else
                    "DEFERRED_CHAIN_CONFLICT" if candidate in preview.deferred else
                    "EXCLUDED_REJECTED" if status == "rejected" else
                    "EXCLUDED_PENDING"
                )
                rule_id = None
                conflict_reason = None
                if import_status == "IMPORTED":
                    assert candidate is not None
                    rule = validator.build_approved_rules([candidate]).rules[0]
                    signature = (
                        rule.rule_type.value, tuple(sorted(rule.variants)),
                        rule.canonical, category_key.value,
                    )
                    rule_id = existing_by_signature.get(signature)
                    if rule_id is None:
                        rule_id = uuid4()
                        await cursor.execute("""
                            INSERT INTO normalization_active_rules (
                                id, rule_type, variants, canonical, category_key,
                                source_candidate_id, source_reason_types,
                                supersedes_rule_id, revision, is_active, revoked_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 1, TRUE, NULL)
                        """, (
                            rule_id, rule.rule_type.value, Jsonb(list(rule.variants)),
                            rule.canonical, category_key.value, f"hunter:{fingerprint}",
                            Jsonb(["HISTORICAL_IMPORT"]),
                        ))
                        await cursor.execute("""
                            INSERT INTO normalization_active_rule_audits (
                                id, rule_id, action, rule_snapshot
                            ) VALUES (%s, %s, 'APPLY', %s)
                        """, (uuid4(), rule_id, Jsonb({
                            "categoryKey": category_key.value,
                            "legacyImport": {
                                "source": "hunter.traffic_analysis_word_cache_phrase_norm",
                                "sourceFingerprint": fingerprint,
                                "legacyStatus": status,
                                "legacySource": legacy.get("source"),
                                "sourceScopes": legacy.get("source_scopes", []),
                            },
                        })))
                        existing_by_signature[signature] = rule_id
                        inserted_rules += 1
                elif import_status == "DEFERRED_CHAIN_CONFLICT":
                    conflict_reason = "CHAIN_REFERENCE"
                await cursor.execute("""
                    INSERT INTO normalization_legacy_rule_imports (
                        id, source_fingerprint, source_system, source_record_index,
                        category_key, raw_phrase, normalized_phrase, legacy_status,
                        legacy_source, source_scopes, import_status, conflict_reason,
                        active_rule_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    uuid4(), fingerprint, "hunter.traffic_analysis_word_cache_phrase_norm",
                    index, category_key.value, str(legacy.get("raw_phrase", "")),
                    str(legacy.get("normalized_phrase", "")), status,
                    legacy.get("source"), Jsonb(legacy.get("source_scopes", [])),
                    import_status, conflict_reason, rule_id,
                ))
                inserted_ledger += 1
    return {"inserted_rules": inserted_rules, "inserted_ledger": inserted_ledger}
