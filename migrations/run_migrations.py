"""显式执行 Flow Analysis PostgreSQL schema migration 的命令行入口。"""

from __future__ import annotations

from pathlib import Path
import sys

# 该文件作为 ``python migrations/run_migrations.py`` 独立入口运行时，
# Python 默认只把 migrations/ 放进 sys.path；显式加入项目根目录以复用
# 正式 ApplicationRuntime，而不是复制数据库连接配置。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.database_settings import DATABASE_SETTINGS
from infrastructure.application_runtime import ApplicationRuntime


MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent


async def apply_migrations(runtime: ApplicationRuntime) -> list[str]:
    """按文件名顺序执行幂等 SQL；此入口不在 GUI 启动期间自动调用。"""

    applied_migrations: list[str] = []
    migration_paths = sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql"))
    async with runtime.database.connection() as connection:
        async with connection.cursor() as cursor:
            for migration_path in migration_paths:
                # migration 是受版本控制的本地 SQL，执行时不插入任何认证或
                # 用户输入；每条语句均设计为可重复执行。
                sql = migration_path.read_text(encoding="utf-8")
                await cursor.execute(sql, prepare=False)
                applied_migrations.append(migration_path.name)
    return applied_migrations


def main() -> int:
    """以独立 CLI 进程创建基础设施并执行 migration，允许有限同步等待。"""

    runtime = ApplicationRuntime(DATABASE_SETTINGS)
    runtime.start().result(timeout=15.0)
    try:
        applied_migrations = runtime.async_runtime.submit(
            apply_migrations(runtime)
        ).result(timeout=30.0)
    finally:
        runtime.shutdown()

    for migration_name in applied_migrations:
        print(f"已执行 migration：{migration_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
