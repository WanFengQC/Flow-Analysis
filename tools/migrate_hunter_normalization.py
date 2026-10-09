"""Hunter 归一化历史迁移入口；默认只输出 Dry Run，不执行连接或写入。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.database_settings import DATABASE_SETTINGS
from infrastructure.application_runtime import ApplicationRuntime
from services.hunter_normalization_migration import (
    apply_hunter_normalization_preview,
    build_hunter_normalization_preview,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hunter 归一化历史迁移预检")
    parser.add_argument(
        "--source", type=Path,
        default=PROJECT_ROOT / "runtime" / "hunter_normalization_review.json",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database")
    parser.add_argument("--confirm-database")
    arguments = parser.parse_args(argv)
    payload = json.loads(arguments.source.read_text(encoding="utf-8"))
    preview = build_hunter_normalization_preview(payload["records"])
    report = {
        "event": "hunter_normalization_dry_run",
        "eligible": preview.importable_count,
        "deferred_chain_conflicts": len(preview.deferred),
        "pillow_eligible": sum(1 for item in preview.eligible if item["categoryKey"].value == "pillow"),
        "stuffed_animals_eligible": sum(1 for item in preview.eligible if item["categoryKey"].value == "stuffed_animals"),
        "chains": list(preview.chain_messages),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not arguments.apply:
        return 0
    if arguments.database != "flow_analysis" or arguments.confirm_database != "flow_analysis":
        raise SystemExit("--apply 必须双重确认 flow_analysis")
    settings = replace(DATABASE_SETTINGS, database=arguments.database)
    settings.validate()
    runtime = ApplicationRuntime(settings)
    try:
        runtime.start().result(timeout=15)
        result = runtime.async_runtime.submit(
            apply_hunter_normalization_preview(runtime.database, payload["records"])
        ).result(timeout=60)
    finally:
        runtime.shutdown()
    print(json.dumps({"event": "hunter_normalization_applied", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
