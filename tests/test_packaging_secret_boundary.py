"""发布构建不得把数据库凭据或更新私钥写入 EXE 的回归测试。"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingSecretBoundaryTest(unittest.TestCase):
    """验证构建期数据库注入与私钥边界。"""

    def test_embedded_runtime_config_contains_ai_and_database_whitelist(self) -> None:
        module = self._build_release_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            protected_config = root / "release.env"
            protected_config.write_text(
                "UNIAPI_API_KEY=isolated-ai-test-key\n"
                "FLOW_ANALYSIS_DB_HOST=isolated-db\n"
                "FLOW_ANALYSIS_DB_PORT=5433\n"
                "FLOW_ANALYSIS_DB_NAME=flow_analysis_isolated\n"
                "FLOW_ANALYSIS_DB_USER=isolated-user\n"
                "FLOW_ANALYSIS_DB_PASSWORD=isolated-test-secret\n",
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ,
                    {"FLOW_ANALYSIS_BUILD_CONFIG_PATH": str(protected_config)},
                    clear=True,
                ),
                patch.object(module, "BUILD_ROOT", root),
            ):
                path = module._create_embedded_runtime_config()
                payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            {"UNIAPI_API_KEY", *module.DATABASE_CONFIG_KEYS},
            set(payload),
        )
        self.assertEqual("isolated-db", payload["FLOW_ANALYSIS_DB_HOST"])

    def test_build_runtime_config_requires_protected_config_file(self) -> None:
        module = self._build_release_module()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "FLOW_ANALYSIS_BUILD_CONFIG_PATH"):
                module._load_build_runtime_config()

    def test_build_runtime_config_reads_all_required_values_from_protected_file(self) -> None:
        module = self._build_release_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "release.env"
            path.write_text(
                "UNIAPI_API_KEY=isolated-ai-test-key\n"
                "FLOW_ANALYSIS_DB_HOST=isolated-db\n"
                "FLOW_ANALYSIS_DB_PORT=5433\n"
                "FLOW_ANALYSIS_DB_NAME=flow_analysis_isolated\n"
                "FLOW_ANALYSIS_DB_USER=isolated-user\n"
                "FLOW_ANALYSIS_DB_PASSWORD=isolated-test-secret\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"FLOW_ANALYSIS_BUILD_CONFIG_PATH": str(path)},
                clear=True,
            ):
                values = module._load_build_runtime_config()

        self.assertEqual("isolated-db", values["FLOW_ANALYSIS_DB_HOST"])
        self.assertEqual("flow_analysis_isolated", values["FLOW_ANALYSIS_DB_NAME"])
        self.assertEqual("isolated-user", values["FLOW_ANALYSIS_DB_USER"])
        self.assertEqual("isolated-test-secret", values["FLOW_ANALYSIS_DB_PASSWORD"])
        self.assertEqual("isolated-ai-test-key", values["UNIAPI_API_KEY"])

    def test_update_manifest_tool_reads_private_key_but_never_packages_it(self) -> None:
        manifest_source = (
            PROJECT_ROOT / "packaging" / "create_update_manifest.py"
        ).read_text(encoding="utf-8")
        build_source = (PROJECT_ROOT / "packaging" / "build_release.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("--private-key", manifest_source)
        self.assertNotIn("private-key", build_source)
        self.assertNotIn("private_key", build_source)

    def test_database_settings_reads_packaged_config_only_for_frozen_release(self) -> None:
        source = (PROJECT_ROOT / "config" / "database_settings.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("packaged_database_runtime_config", source)
        self.assertIn("FLOW_ANALYSIS_ENABLE_EXTERNAL_DB_OVERRIDE", source)

    @staticmethod
    def _build_release_module():
        """每个测试独立加载构建脚本，避免污染正式模块状态。"""

        path = PROJECT_ROOT / "packaging" / "build_release.py"
        spec = importlib.util.spec_from_file_location("flow_analysis_build_release_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
