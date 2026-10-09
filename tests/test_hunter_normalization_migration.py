"""Hunter 归一化迁移预检不能依赖正式数据库。"""

import json
from pathlib import Path

from models.tagging_label import TaggingCategoryKey
from services.hunter_normalization_migration import (
    build_hunter_normalization_preview,
)


def test_hunter_snapshot_keeps_category_isolation_and_defers_only_chains():
    payload = json.loads(
        (Path(__file__).parents[1] / "runtime" / "hunter_normalization_review.json").read_text(
            encoding="utf-8"
        )
    )

    preview = build_hunter_normalization_preview(payload["records"])

    assert preview.importable_count == 287
    assert len(preview.deferred) == 8
    assert sum(
        item["categoryKey"] == TaggingCategoryKey.PILLOW
        for item in preview.eligible
    ) == 52
    assert sum(
        item["categoryKey"] == TaggingCategoryKey.STUFFED_ANIMALS
        for item in preview.eligible
    ) == 235
    deferred_pairs = {
        (item["legacy"]["raw_phrase"], item["legacy"]["normalized_phrase"])
        for item in preview.deferred
    }
    assert deferred_pairs == {
        ("filling", "filled"), ("filled", "fill"),
        ("microwaveable", "microwave"), ("microwave", "microwavable"),
        ("plushy s", "plushys"), ("plushys", "plushy"),
        ("stuffy s", "stuffys"), ("stuffys", "stuffy"),
    }


def test_different_categories_can_keep_different_canonicals():
    records = [
        {"status": "manual_confirmed", "category": "Pillow", "raw_phrase": "adult", "normalized_phrase": "adults", "source_scopes": []},
        {"status": "manual_confirmed", "category": "Stuffed Animals", "raw_phrase": "adult", "normalized_phrase": "adult", "source_scopes": []},
    ]

    preview = build_hunter_normalization_preview(records)

    assert preview.importable_count == 2
    assert not preview.deferred
