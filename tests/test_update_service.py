"""验证更新清单必须验签，且安装包必须完成大小与哈希校验。"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from infrastructure.update_service import (
    UpdateError,
    UpdateService,
    canonical_manifest_bytes,
)
import infrastructure.update_service as update_service_module


class UpdateServiceTest(unittest.IsolatedAsyncioTestCase):
    """以本地 MockTransport 验证更新流程，禁止测试访问真实内网服务器。"""

    def setUp(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key_base64 = base64.b64encode(public_key).decode("ascii")
        self.package_content = b"signed-flow-analysis-installer"
        self.package_url = "http://updates.example.test/releases/setup.exe"
        self.manifest_url = "http://updates.example.test/stable.json"

    def _signed_payload(self) -> dict[str, object]:
        """构造与正式发布脚本一致的签名清单。"""

        payload: dict[str, object] = {
            "schemaVersion": 1,
            "channel": "stable",
            "version": "0.1.1",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "releaseNotes": "test release",
            "package": {
                "url": self.package_url,
                "sha256": hashlib.sha256(
                    self.package_content
                ).hexdigest(),
                "size": len(self.package_content),
            },
        }
        payload["signature"] = {
            "algorithm": "ed25519",
            "value": base64.b64encode(
                self.private_key.sign(canonical_manifest_bytes(payload))
            ).decode("ascii"),
        }
        return payload

    def _service(
        self,
        handler,
        directory: Path,
    ) -> UpdateService:
        """创建使用 MockTransport 的服务，避免真实网络请求。"""

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        return UpdateService(
            current_version="0.1.0",
            manifest_url=self.manifest_url,
            channel="stable",
            public_key_base64=self.public_key_base64,
            download_dir=directory,
            allow_insecure_http=True,
            timeout_seconds=1.0,
            manifest_max_bytes=1024,
            package_max_bytes=1024 * 1024,
            client=client,
        )

    async def test_signed_manifest_downloads_verified_package(self) -> None:
        """合法签名、大小和 SHA-256 一致时才保留安装包。"""

        payload = self._signed_payload()

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == self.manifest_url:
                return httpx.Response(200, json=payload)
            if str(request.url) == self.package_url:
                return httpx.Response(200, content=self.package_content)
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as temporary_directory:
            service = self._service(handler, Path(temporary_directory))
            manifest = await service.check_for_update()
            self.assertIsNotNone(manifest)
            installer = await service.download_update(manifest)
            self.assertEqual(installer.read_bytes(), self.package_content)
            self.assertFalse(installer.with_suffix(".exe.part").exists())
            await service.aclose()

    async def test_tampered_manifest_is_rejected_before_download(self) -> None:
        """任何签名失配都必须阻止客户端读取 package URL。"""

        payload = self._signed_payload()
        payload["version"] = "9.9.9"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        with tempfile.TemporaryDirectory() as temporary_directory:
            service = self._service(handler, Path(temporary_directory))
            with self.assertRaises(UpdateError):
                await service.check_for_update()
            await service.aclose()

    async def test_hash_mismatch_removes_partial_package(self) -> None:
        """下载内容被篡改时不能留下可被更新器误用的半成品。"""

        payload = self._signed_payload()

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == self.manifest_url:
                return httpx.Response(200, json=payload)
            return httpx.Response(200, content=b"tampered")

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            service = self._service(handler, directory)
            manifest = await service.check_for_update()
            self.assertIsNotNone(manifest)
            with self.assertRaises(UpdateError):
                await service.download_update(manifest)
            self.assertEqual(list(directory.iterdir()), [])
            await service.aclose()

    async def test_current_version_never_reinstalls_same_release(self) -> None:
        """当前版本与清单相同或更高时，不下载也不触发重启。"""

        payload = self._signed_payload()
        payload["version"] = "0.1.0"
        payload["signature"] = {
            "algorithm": "ed25519",
            "value": base64.b64encode(
                self.private_key.sign(canonical_manifest_bytes(payload))
            ).decode("ascii"),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        with tempfile.TemporaryDirectory() as temporary_directory:
            service = self._service(handler, Path(temporary_directory))
            self.assertIsNone(await service.check_for_update())
            await service.aclose()

    async def test_updater_runs_from_cache_not_install_directory(self) -> None:
        """更新器运行副本必须位于缓存，不能锁住安装目录内的原文件。"""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            install_directory = root / "installed"
            install_directory.mkdir()
            application_path = install_directory / "FlowAnalysis.exe"
            application_path.write_bytes(b"application")
            installed_updater = install_directory / "FlowAnalysisUpdater.exe"
            installed_updater.write_bytes(b"updater")
            installer_path = root / "FlowAnalysisSetup-3.0.0.exe"
            installer_path.write_bytes(b"installer")
            download_directory = root / "updates"
            stale_runner = (
                download_directory
                / "updater-runners"
                / "FlowAnalysisUpdater-stale.exe"
            )
            stale_runner.parent.mkdir(parents=True)
            stale_runner.write_bytes(b"stale")
            service = self._service(handler, download_directory)

            with (
                patch.object(
                    update_service_module.sys,
                    "frozen",
                    True,
                    create=True,
                ),
                patch.object(
                    update_service_module.sys,
                    "executable",
                    str(application_path),
                ),
                patch.object(
                    update_service_module.subprocess,
                    "Popen",
                ) as popen,
            ):
                runner_path = service.launch_updater(
                    installer_path,
                    "a" * 64,
                )

            self.assertTrue(runner_path.is_file())
            self.assertNotEqual(runner_path, installed_updater)
            self.assertEqual(
                runner_path.parent,
                download_directory / "updater-runners",
            )
            self.assertFalse(stale_runner.exists())
            command = popen.call_args.args[0]
            self.assertEqual(command[0], str(runner_path))
            self.assertNotIn(str(install_directory), command[0])
            await service.aclose()
