"""数据库配置不得回退为源码或 EXE 内置凭据的回归测试。"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from config.database_settings import DatabaseSettings
from controllers.main_controller import MainController


class DatabaseSettingsSecurityTest(unittest.TestCase):
    """验证桌面客户端只能从外部运行时配置读取数据库连接信息。"""

    def test_runtime_environment_has_no_source_credential_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            settings = DatabaseSettings.from_runtime_environment()

        self.assertEqual("", settings.host)
        self.assertEqual("", settings.database)
        self.assertEqual("", settings.user)
        self.assertEqual("", settings.password)

    def test_runtime_environment_reads_only_external_application_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "FLOW_ANALYSIS_DB_HOST": "db.internal",
                "FLOW_ANALYSIS_DB_PORT": "5433",
                "FLOW_ANALYSIS_DB_NAME": "flow_analysis_test",
                "FLOW_ANALYSIS_DB_USER": "flow_analysis_app",
                "FLOW_ANALYSIS_DB_PASSWORD": "test-only-secret",
            },
            clear=True,
        ):
            settings = DatabaseSettings.from_runtime_environment()

        self.assertEqual("db.internal", settings.host)
        self.assertEqual(5433, settings.port)
        self.assertEqual("flow_analysis_test", settings.database)
        self.assertEqual("flow_analysis_app", settings.user)
        self.assertEqual("test-only-secret", settings.password)

    def test_validation_does_not_echo_password(self) -> None:
        password = "test-only-secret"
        with self.assertRaises(ValueError) as context:
            DatabaseSettings(password=password).validate()

        self.assertNotIn(password, str(context.exception))
        self.assertIn("FLOW_ANALYSIS_DB_HOST", str(context.exception))

    def test_invalid_port_reports_variable_name_without_value(self) -> None:
        with patch.dict(
            os.environ,
            {"FLOW_ANALYSIS_DB_PORT": "not-a-port"},
            clear=True,
        ):
            with self.assertRaises(ValueError) as context:
                DatabaseSettings.from_runtime_environment()

        self.assertIn("FLOW_ANALYSIS_DB_PORT", str(context.exception))
        self.assertNotIn("not-a-port", str(context.exception))

    def test_installed_appdata_env_is_loaded_in_a_clean_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            appdata = Path(temporary_directory)
            config_directory = appdata / "Flow Analysis"
            config_directory.mkdir()
            (config_directory / ".env").write_text(
                "FLOW_ANALYSIS_DB_HOST=appdata-db\n"
                "FLOW_ANALYSIS_DB_NAME=appdata-name\n"
                "FLOW_ANALYSIS_DB_USER=appdata-user\n"
                "FLOW_ANALYSIS_DB_PASSWORD=test-only-secret\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["APPDATA"] = str(appdata)
            for name in tuple(environment):
                if name.startswith("FLOW_ANALYSIS_DB_"):
                    environment.pop(name)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json; from config.database_settings import "
                    "DatabaseSettings; s=DatabaseSettings.from_runtime_environment(); "
                    "print(json.dumps({'host':s.host,'database':s.database,'user':s.user}))",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )

        self.assertEqual(
            {
                "host": "appdata-db",
                "database": "appdata-name",
                "user": "appdata-user",
            },
            json.loads(result.stdout),
        )

    def test_process_environment_overrides_installed_appdata_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            appdata = Path(temporary_directory)
            config_directory = appdata / "Flow Analysis"
            config_directory.mkdir()
            (config_directory / ".env").write_text(
                "FLOW_ANALYSIS_DB_HOST=appdata-db\n"
                "FLOW_ANALYSIS_DB_NAME=appdata-name\n"
                "FLOW_ANALYSIS_DB_USER=appdata-user\n"
                "FLOW_ANALYSIS_DB_PASSWORD=test-only-secret\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "APPDATA": str(appdata),
                    "FLOW_ANALYSIS_DB_HOST": "process-db",
                    "FLOW_ANALYSIS_DB_NAME": "process-name",
                    "FLOW_ANALYSIS_DB_USER": "process-user",
                    "FLOW_ANALYSIS_DB_PASSWORD": "process-test-only-secret",
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json; from config.database_settings import "
                    "DatabaseSettings; s=DatabaseSettings.from_runtime_environment(); "
                    "print(json.dumps({'host':s.host,'database':s.database,'user':s.user}))",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )

        self.assertEqual(
            {
                "host": "process-db",
                "database": "process-name",
                "user": "process-user",
            },
            json.loads(result.stdout),
        )

    def test_database_configuration_error_is_visible_without_password(self) -> None:
        class _Window:
            database_status = ""
            status = ""

            def set_database_status(self, value: str) -> None:
                self.database_status = value

            def set_status(self, value: str) -> None:
                self.status = value

        window = _Window()
        controller = SimpleNamespace(window=window, _database_ready=True)
        message = "缺少数据库运行时配置：FLOW_ANALYSIS_DB_PASSWORD"

        MainController._on_database_failed(controller, message)

        self.assertFalse(controller._database_ready)
        self.assertEqual("连接失败", window.database_status)
        self.assertIn("PostgreSQL 连接失败", window.status)
        self.assertIn("FLOW_ANALYSIS_DB_PASSWORD", window.status)
        self.assertNotIn("test-only-secret", window.status)
