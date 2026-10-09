"""受控执行 Flow Analysis PostgreSQL schema migration 的命令行入口。

默认只输出执行计划，绝不连接数据库。实际 DDL 必须显式指定并二次确认目标
数据库名称，避免误用桌面应用的运行时配置直接迁移正式库。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

# 该文件作为 ``python migrations/run_migrations.py`` 独立入口运行时，
# Python 默认只把 migrations/ 放进 sys.path；显式加入项目根目录以复用
# 连接池与异常边界，而不是复制数据库连接配置。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.database_settings import DATABASE_SETTINGS, DatabaseSettings
from infrastructure.application_runtime import ApplicationRuntime


MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """一次受控迁移的无敏感执行计划。"""

    database: str
    migrations: tuple[Path, ...]

    def as_audit_record(self, event: str) -> dict[str, object]:
        """构造可保存到发布工单的安全审计记录。"""

        return {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_database": self.database,
            "migration_count": len(self.migrations),
            "migrations": [path.name for path in self.migrations],
        }


def discover_migrations() -> tuple[Path, ...]:
    """按版本号顺序返回受版本控制的 SQL 文件。"""

    return tuple(
        sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql"))
    )


def build_plan(database: str) -> MigrationPlan:
    """构造计划但不读取连接配置、更不建立数据库连接。"""

    target_database = database.strip()
    if not target_database:
        raise ValueError("必须提供非空的目标数据库名称")
    migrations = discover_migrations()
    if not migrations:
        raise RuntimeError("未找到 migration SQL 文件")
    return MigrationPlan(target_database, migrations)


def _write_audit(record: dict[str, object]) -> None:
    """输出不含主机、用户、密码或 SQL 正文的 JSON 审计记录。"""

    print(json.dumps(record, ensure_ascii=False, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    """构建明确区分计划与实际执行的 CLI 参数。"""

    parser = argparse.ArgumentParser(
        description="Flow Analysis 受控 PostgreSQL migration 工具"
    )
    parser.add_argument(
        "--database",
        required=True,
        help="必须显式填写的目标数据库名称；不会使用默认值。",
    )
    parser.add_argument(
        "--confirm-database",
        required=True,
        help="再次填写目标数据库名称，必须与 --database 完全一致。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行 DDL；省略时只输出计划，不连接数据库。",
    )
    return parser


def _settings_for_confirmed_target(
    arguments: argparse.Namespace,
) -> DatabaseSettings:
    """只在双重确认后构造连接配置，拒绝隐式目标库。"""

    target_database = arguments.database.strip()
    if target_database != arguments.confirm_database.strip():
        raise ValueError(
            "目标数据库二次确认不一致；拒绝执行 migration"
        )

    settings = replace(DATABASE_SETTINGS, database=target_database)
    settings.validate()
    return settings


async def apply_migrations(
    runtime: ApplicationRuntime,
    plan: MigrationPlan,
) -> list[str]:
    """验证实际连接目标后，在一个事务中执行全部 SQL。"""

    applied_migrations: list[str] = []
    async with runtime.database.transaction() as connection:
        async with connection.cursor() as cursor:
            # 不能仅信任命令行字符串：必须由 PostgreSQL 返回当前实际数据库。
            await cursor.execute("SELECT current_database()")
            row = await cursor.fetchone()
            actual_database = (
                row["current_database"]
                if isinstance(row, Mapping)
                else (row[0] if row is not None else None)
            )
            if actual_database != plan.database:
                raise RuntimeError("实际连接目标与确认的数据库名称不一致")

            for migration_path in plan.migrations:
                sql = migration_path.read_text(encoding="utf-8")
                await cursor.execute(sql, prepare=False)
                applied_migrations.append(migration_path.name)
    return applied_migrations


def main(argv: list[str] | None = None) -> int:
    """默认打印计划；仅 --apply 才会在双重确认后建立连接并执行 DDL。"""

    arguments = _build_parser().parse_args(argv)
    plan = build_plan(arguments.database)
    _write_audit(plan.as_audit_record("migration_plan"))

    if not arguments.apply:
        _write_audit(
            {
                **plan.as_audit_record("migration_not_applied"),
                "reason": "未提供 --apply；未建立数据库连接",
            }
        )
        return 0

    try:
        settings = _settings_for_confirmed_target(arguments)
    except ValueError as exc:
        _write_audit(
            {
                **plan.as_audit_record("migration_rejected"),
                "reason": str(exc),
            }
        )
        return 2

    runtime = ApplicationRuntime(settings)
    try:
        _write_audit(plan.as_audit_record("migration_started"))
        runtime.start().result(timeout=15.0)
        applied_migrations = runtime.async_runtime.submit(
            apply_migrations(runtime, plan)
        ).result(timeout=30.0)
    except Exception as exc:
        # 仅保留异常类型，避免底层连接错误意外回显认证信息或连接字符串。
        _write_audit(
            {
                **plan.as_audit_record("migration_failed"),
                "error_type": type(exc).__name__,
            }
        )
        return 1
    finally:
        runtime.shutdown()

    _write_audit(
        {
            **plan.as_audit_record("migration_succeeded"),
            "applied_migrations": applied_migrations,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
