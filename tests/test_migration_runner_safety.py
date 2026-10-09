"""受控 migration CLI 的防误执行回归测试。"""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from config.database_settings import DatabaseSettings
from migrations import run_migrations


class MigrationRunnerSafetyTest(unittest.TestCase):
    """验证没有 --apply 时绝不构造运行时或连接数据库。"""

    def test_plan_lists_migrations_without_database_runtime(self) -> None:
        with patch.object(
            run_migrations,
            "ApplicationRuntime",
        ) as runtime_class:
            exit_code = run_migrations.main(
                [
                    "--database",
                    "flow_analysis",
                    "--confirm-database",
                    "flow_analysis",
                ]
            )

        self.assertEqual(0, exit_code)
        runtime_class.assert_not_called()

    def test_mismatched_confirmation_rejects_before_runtime(self) -> None:
        with patch.object(
            run_migrations,
            "ApplicationRuntime",
        ) as runtime_class:
            exit_code = run_migrations.main(
                [
                    "--database",
                    "flow_analysis",
                    "--confirm-database",
                    "another_database",
                    "--apply",
                ]
            )

        self.assertEqual(2, exit_code)
        runtime_class.assert_not_called()

    def test_apply_rejects_missing_runtime_database_configuration(self) -> None:
        unconfigured = DatabaseSettings()
        with (
            patch.object(run_migrations, "DATABASE_SETTINGS", unconfigured),
            patch.object(run_migrations, "ApplicationRuntime") as runtime_class,
        ):
            exit_code = run_migrations.main(
                [
                    "--database",
                    "flow_analysis",
                    "--confirm-database",
                    "flow_analysis",
                    "--apply",
                ]
            )

        self.assertEqual(2, exit_code)
        runtime_class.assert_not_called()

    def test_confirmed_target_replaces_only_database_name(self) -> None:
        configured = DatabaseSettings(
            host="db.internal",
            database="ignored_default",
            user="migration_user",
            password="test-only-secret",
        )
        arguments = run_migrations._build_parser().parse_args(
            [
                "--database",
                "flow_analysis_upgrade_check",
                "--confirm-database",
                "flow_analysis_upgrade_check",
                "--apply",
            ]
        )
        with patch.object(run_migrations, "DATABASE_SETTINGS", configured):
            settings = run_migrations._settings_for_confirmed_target(arguments)

        self.assertEqual("flow_analysis_upgrade_check", settings.database)
        self.assertEqual(configured.host, settings.host)
        self.assertEqual(configured.user, settings.user)
        self.assertEqual(configured.password, settings.password)
