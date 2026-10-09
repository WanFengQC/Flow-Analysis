"""发布构建不得把数据库凭据或更新私钥写入 EXE 的回归测试。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingSecretBoundaryTest(unittest.TestCase):
    """验证构建期嵌入配置白名单与私钥边界。"""

    def test_embedded_runtime_config_contains_only_ai_key(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_release.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        embedded_payloads: list[set[str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "dumps"
                and node.args
                and isinstance(node.args[0], ast.Dict)
            ):
                continue
            keys = {
                key.value
                for key in node.args[0].keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if "UNIAPI_API_KEY" in keys:
                embedded_payloads.append(keys)

        self.assertEqual([{"UNIAPI_API_KEY"}], embedded_payloads)

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

    def test_database_settings_cannot_read_packaged_runtime_config(self) -> None:
        source = (PROJECT_ROOT / "config" / "database_settings.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("_PACKAGED_RUNTIME_CONFIG", source)
        self.assertNotIn("_runtime_setting", source)
