"""将 Hunter 历史标签共识缓存一次性、可重复地导入 Flow Analysis。"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


# 独立 CLI 运行时必须显式加入项目根目录；不能复制正式数据库配置。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.database_settings import DATABASE_SETTINGS, DatabaseSettings
from infrastructure.application_runtime import ApplicationRuntime
from models.tagging import TagLabel
from models.tagging_label import TaggingCategoryKey, TaggingDecisionSource


SOURCE_DATABASE = "hunter"
SOURCE_TABLE = "public.traffic_analysis_word_cache_consensus"
TARGET_TAXONOMY_VERSION = 1
HISTORICAL_PRODUCT_CONTEXT_SOURCE = "HUNTER_HISTORICAL"
HISTORICAL_UNKNOWN_ASIN = "HISTORICAL"
_ASIN_PATTERN = re.compile(r"\bB0[A-Z0-9]{8}\b")
_CATEGORY_MAPPING = {
    "Pillow": TaggingCategoryKey.PILLOW,
    "Stuffed Animals": TaggingCategoryKey.STUFFED_ANIMALS,
}


@dataclass(frozen=True, slots=True)
class HistoricalTaggingRecord:
    """已通过校验、可写入正式缓存的一条历史标签记录。"""

    category_key: TaggingCategoryKey
    word: str
    label: TagLabel
    reason: str | None
    representative_asin: str
    provider_results: Mapping[str, Mapping[str, str]]


def _first_non_empty_text(*values: object) -> str | None:
    """按既有 Hunter 规则优先取 GPT 理由，缺失时依次使用其余理由。"""

    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _representative_asin(value: object) -> str:
    """从旧版单/多 ASIN 字段提取一个稳定 ASIN，绝不猜造商品 ASIN。"""

    if not isinstance(value, str):
        return HISTORICAL_UNKNOWN_ASIN
    match = _ASIN_PATTERN.search(value.upper())
    return match.group(0) if match is not None else HISTORICAL_UNKNOWN_ASIN


def _provider_results(row: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """保留旧表可用理由，供历史审计追溯而不影响当前缓存读取。"""

    result: dict[str, dict[str, str]] = {}
    for provider_id, field_name in (
        ("openai", "gpt_reason"),
        ("google", "gemini_reason"),
        ("anthropic", "anthropic_reason"),
        ("deepseek", "deepseek_reason"),
    ):
        reason = row.get(field_name)
        if isinstance(reason, str) and reason.strip():
            result[provider_id] = {"reason": reason.strip()}
    return result


def map_source_row(row: Mapping[str, Any]) -> HistoricalTaggingRecord:
    """将一条 Hunter Row 严格映射为当前项目的明确数据结构。"""

    category = row.get("category")
    try:
        category_key = _CATEGORY_MAPPING[category]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"不支持的 Hunter 历史品类：{category!r}") from exc

    word = row.get("word")
    if not isinstance(word, str) or not (normalized_word := word.strip().lower()):
        raise ValueError("Hunter 历史标签存在空 word")

    label_value = row.get("label")
    try:
        label = TagLabel(label_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Hunter 历史标签不属于当前标签集：{label_value!r}") from exc

    return HistoricalTaggingRecord(
        category_key=category_key,
        word=normalized_word,
        label=label,
        reason=_first_non_empty_text(
            row.get("gpt_reason"),
            row.get("gemini_reason"),
            row.get("anthropic_reason"),
            row.get("deepseek_reason"),
        ),
        representative_asin=_representative_asin(row.get("source_asin")),
        provider_results=_provider_results(row),
    )


async def read_source_records(settings: DatabaseSettings) -> list[HistoricalTaggingRecord]:
    """只读加载 Hunter 历史缓存，并在任何目标写入前完成所有字段校验。"""

    source_connection = await AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=SOURCE_DATABASE,
        user=settings.user,
        password=settings.password,
        sslmode=settings.sslmode,
        connect_timeout=settings.connect_timeout,
        row_factory=dict_row,
    )
    try:
        async with source_connection.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT
                    category,
                    word,
                    source_asin,
                    label,
                    gpt_reason,
                    gemini_reason,
                    deepseek_reason,
                    anthropic_reason
                FROM {SOURCE_TABLE}
                ORDER BY category, lower(btrim(word))
                """
            )
            rows = await cursor.fetchall()
    finally:
        await source_connection.close()

    records = [map_source_row(row) for row in rows if isinstance(row, Mapping)]
    identities = {(record.category_key, record.word) for record in records}
    if len(identities) != len(records):
        raise ValueError("Hunter 历史标签存在重复的 category + word identity")
    return records


async def import_records(
    runtime: ApplicationRuntime,
    records: Sequence[HistoricalTaggingRecord],
    *,
    apply: bool,
) -> dict[str, int]:
    """以一个目标事务导入新 identity；已有当前缓存永不覆盖、不重复追加审计。"""

    category_counts = Counter(record.category_key.value for record in records)
    fallback_asin_count = sum(
        record.representative_asin == HISTORICAL_UNKNOWN_ASIN
        for record in records
    )
    report = {
        "source_records": len(records),
        "pillow_records": category_counts[TaggingCategoryKey.PILLOW.value],
        "stuffed_animals_records": category_counts[TaggingCategoryKey.STUFFED_ANIMALS.value],
        "fallback_representative_asin_records": fallback_asin_count,
        "existing_records_skipped": 0,
        "imported_records": 0,
        "audit_records": 0,
    }
    if not apply:
        return report

    async with runtime.database.transaction() as connection:
        async with connection.cursor() as cursor:
            # 与普通打标写入互斥，确保“查现有 → 导入 → 写审计”在同一原子边界内。
            await cursor.execute(
                "LOCK TABLE tagging_label_consensus IN SHARE ROW EXCLUSIVE MODE"
            )
            await cursor.execute(
                """
                SELECT category_key, word
                FROM tagging_label_consensus
                WHERE taxonomy_version = %(taxonomy_version)s
                  AND category_key = ANY(%(category_keys)s)
                """,
                {
                    "taxonomy_version": TARGET_TAXONOMY_VERSION,
                    "category_keys": [
                        TaggingCategoryKey.PILLOW.value,
                        TaggingCategoryKey.STUFFED_ANIMALS.value,
                    ],
                },
            )
            existing_identities = {
                (row["category_key"], row["word"])
                for row in await cursor.fetchall()
                if isinstance(row, Mapping)
            }
            new_records = [
                record
                for record in records
                if (record.category_key.value, record.word)
                not in existing_identities
            ]
            report["existing_records_skipped"] = len(records) - len(new_records)
            if not new_records:
                return report

            prepared_records = [
                (record, uuid4(), uuid4())
                for record in new_records
            ]
            await cursor.executemany(
                """
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
                ) VALUES (
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
                """,
                [
                    {
                        "id": cache_id,
                        "category_key": record.category_key.value,
                        "word": record.word,
                        "taxonomy_version": TARGET_TAXONOMY_VERSION,
                        "label": record.label.value,
                        "reason": record.reason,
                        "decision_source": TaggingDecisionSource.HISTORICAL_IMPORT.value,
                        "representative_asin": record.representative_asin,
                        "product_context_source": HISTORICAL_PRODUCT_CONTEXT_SOURCE,
                    }
                    for record, cache_id, _ in prepared_records
                ],
            )
            await cursor.executemany(
                """
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
                ) VALUES (
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
                """,
                [
                    {
                        "id": decision_id,
                        "cache_id": cache_id,
                        "category_key": record.category_key.value,
                        "word": record.word,
                        "taxonomy_version": TARGET_TAXONOMY_VERSION,
                        "label": record.label.value,
                        "reason": record.reason,
                        "decision_source": TaggingDecisionSource.HISTORICAL_IMPORT.value,
                        "representative_asin": record.representative_asin,
                        "product_context_source": HISTORICAL_PRODUCT_CONTEXT_SOURCE,
                        "provider_results": Jsonb({
                            "historical_import": {
                                "source_database": SOURCE_DATABASE,
                                "source_table": SOURCE_TABLE,
                                "provider_reasons": dict(record.provider_results),
                            }
                        }),
                    }
                    for record, cache_id, decision_id in prepared_records
                ],
            )
            report["imported_records"] = len(prepared_records)
            report["audit_records"] = len(prepared_records)
    return report


async def run_import(
    runtime: ApplicationRuntime,
    *,
    apply: bool,
) -> dict[str, int]:
    """在唯一 AsyncRuntime 中完成源库读取与当前库事务写入。"""

    records = await read_source_records(DATABASE_SETTINGS)
    return await import_records(runtime, records, apply=apply)


def main() -> int:
    """默认 dry-run；只有显式 --apply 才写当前 Flow Analysis 数据库。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="将已校验的 Hunter 历史标签写入当前数据库",
    )
    arguments = parser.parse_args()
    # 迁移 CLI 不在 Qt 主线程运行；仍复用正式 ApplicationRuntime 的唯一
    # AsyncRuntime，不能为源库读取另行创建 EventLoop 或连接池。
    runtime = ApplicationRuntime(DATABASE_SETTINGS)
    runtime.start().result(timeout=15.0)
    try:
        report = runtime.async_runtime.submit(
            run_import(runtime, apply=arguments.apply)
        ).result(timeout=60.0)
    finally:
        runtime.shutdown()
    mode = "已导入" if arguments.apply else "预检"
    print(mode)
    for name, value in report.items():
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
