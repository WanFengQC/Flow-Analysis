"""Flow Analysis 安装包更新的清单校验、下载和更新器启动基础设施。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class UpdateError(RuntimeError):
    """更新清单、下载文件或更新器启动不符合安全约束时抛出。"""


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    """经过签名校验后的单个发布版本描述。"""

    version: str
    channel: str
    package_url: str
    package_sha256: str
    package_size: int
    published_at: str
    release_notes: str
    raw_payload: dict[str, Any]


def canonical_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    """生成跨平台稳定的签名内容，签名字段本身不参与签名。"""

    unsigned_payload = {
        key: value
        for key, value in payload.items()
        if key != "signature"
    }
    return json.dumps(
        unsigned_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_semantic_version(value: str) -> tuple[int, int, int]:
    """仅接受正式三段数字版本，避免预发布版本产生不确定的升级顺序。"""

    parts = value.split(".")
    if len(parts) != 3 or any(
            not part.isdigit() for part in parts
    ):
        raise UpdateError(f"更新版本格式无效：{value!r}")

    parsed = tuple(int(part) for part in parts)
    if any(part < 0 for part in parsed):
        raise UpdateError(f"更新版本格式无效：{value!r}")
    return parsed  # type: ignore[return-value]


class UpdateService:
    """
    仅负责更新基础设施，不读取业务数据，也不直接操作 Qt 控件。

    实例由 ApplicationRuntime 唯一持有，复用一个 AsyncClient；Controller
    只订阅 Future 的结果并决定何时允许重启。
    """

    def __init__(
        self,
        *,
        current_version: str,
        manifest_url: str,
        channel: str,
        public_key_base64: str,
        download_dir: Path,
        allow_insecure_http: bool,
        timeout_seconds: float,
        manifest_max_bytes: int,
        package_max_bytes: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.current_version = current_version
        self.manifest_url = manifest_url
        self.channel = channel
        self.download_dir = Path(download_dir)
        self.allow_insecure_http = allow_insecure_http
        self.timeout_seconds = timeout_seconds
        self.manifest_max_bytes = manifest_max_bytes
        self.package_max_bytes = package_max_bytes
        self._client = client
        self._owns_client = client is None
        self._public_key = self._load_public_key(public_key_base64)

        parse_semantic_version(current_version)

    @property
    def is_enabled(self) -> bool:
        """只有完整配置并拥有公钥时才允许发起自动更新。"""

        return bool(self.manifest_url and self._public_key is not None)

    async def check_for_update(self) -> UpdateManifest | None:
        """下载、验签并比较内网发布清单；当前版本已最新时返回 None。"""

        if not self.is_enabled:
            return None

        self._validate_url(self.manifest_url)
        client = self._require_client()
        manifest_content = bytearray()
        try:
            async with client.stream("GET", self.manifest_url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    manifest_content.extend(chunk)
                    if len(manifest_content) > self.manifest_max_bytes:
                        raise UpdateError("更新清单超过允许大小")
        except httpx.HTTPError as exc:
            raise UpdateError("无法读取更新清单") from exc

        try:
            payload = json.loads(manifest_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("更新清单不是合法 JSON") from exc

        if not isinstance(payload, dict):
            raise UpdateError("更新清单必须是 JSON 对象")

        manifest = self._parse_and_verify_manifest(payload)
        if manifest.channel != self.channel:
            raise UpdateError("更新清单渠道不匹配")

        if (
            parse_semantic_version(manifest.version)
            <= parse_semantic_version(self.current_version)
        ):
            return None
        return manifest

    async def download_update(
        self,
        manifest: UpdateManifest,
    ) -> Path:
        """流式下载已验签的更新包，核验哈希后以原子替换形式落盘。"""

        if not self.is_enabled:
            raise UpdateError("自动更新未启用")
        self._validate_url(manifest.package_url)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        target_path = self.download_dir / (
            f"FlowAnalysisSetup-{manifest.version}.exe"
        )
        if self._is_matching_file(target_path, manifest):
            return target_path

        temporary_path = target_path.with_suffix(".exe.part")
        self._safe_remove(temporary_path)
        digest = hashlib.sha256()
        bytes_written = 0
        client = self._require_client()

        try:
            async with client.stream("GET", manifest.package_url) as response:
                response.raise_for_status()
                with temporary_path.open("wb") as file:
                    async for chunk in response.aiter_bytes():
                        bytes_written += len(chunk)
                        if bytes_written > manifest.package_size:
                            raise UpdateError("更新包大小超过清单声明")
                        digest.update(chunk)
                        await asyncio.to_thread(file.write, chunk)
                    await asyncio.to_thread(file.flush)
                    await asyncio.to_thread(os.fsync, file.fileno())
        except httpx.HTTPError as exc:
            self._safe_remove(temporary_path)
            raise UpdateError("更新包下载失败") from exc
        except Exception:
            self._safe_remove(temporary_path)
            raise

        if bytes_written != manifest.package_size:
            self._safe_remove(temporary_path)
            raise UpdateError("更新包大小与清单不一致")
        if digest.hexdigest().lower() != manifest.package_sha256:
            self._safe_remove(temporary_path)
            raise UpdateError("更新包哈希校验失败")

        temporary_path.replace(target_path)
        return target_path

    async def aclose(self) -> None:
        """由 ApplicationRuntime 在退出阶段关闭唯一的更新 HTTP Client。"""

        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    def launch_updater(
        self,
        installer_path: Path,
        package_sha256: str,
    ) -> Path:
        """从安装目录外启动一次性更新器，避免安装时锁住待替换的文件。"""

        if not getattr(sys, "frozen", False):
            raise UpdateError("仅安装包运行时允许自动应用更新")

        application_path = Path(sys.executable).resolve()
        updater_path = application_path.parent / "FlowAnalysisUpdater.exe"
        if not updater_path.is_file():
            raise UpdateError("更新器文件不存在")
        if not installer_path.is_file():
            raise UpdateError("已下载更新包不存在")

        # 更新器本身也属于安装包覆盖对象。若直接从安装目录运行，Inno Setup
        # 在静默安装期间可能无法替换仍被占用的 EXE，进而遗留旧更新器并触发
        # 多次重启。因此每次从用户更新缓存复制一个一次性运行副本。
        runner_path = self._create_updater_runner(updater_path)

        try:
            subprocess.Popen(
                [
                    str(runner_path),
                    "--parent-pid",
                    str(os.getpid()),
                    "--installer",
                    str(installer_path.resolve()),
                    "--sha256",
                    package_sha256,
                    "--restart-executable",
                    str(application_path),
                ],
                close_fds=True,
            )
        except OSError as exc:
            self._safe_remove(runner_path)
            raise UpdateError("无法启动独立更新器") from exc
        return runner_path

    def _create_updater_runner(self, updater_path: Path) -> Path:
        """创建安装目录外的更新器副本，并尽力清理已退出的旧副本。"""

        runner_directory = self.download_dir / "updater-runners"
        try:
            runner_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UpdateError("无法创建更新器运行目录") from exc

        # 旧副本仅用于执行已经结束的更新。正在运行的文件会因 Windows 文件锁
        # 删除失败，此处忽略即可，后续更新时会再次尝试清理。
        for stale_runner in runner_directory.glob("FlowAnalysisUpdater-*.exe"):
            self._safe_remove(stale_runner)

        runner_path = runner_directory / (
            f"FlowAnalysisUpdater-{uuid4().hex}.exe"
        )
        try:
            shutil.copy2(updater_path, runner_path)
        except OSError as exc:
            raise UpdateError("无法准备独立更新器") from exc
        if not runner_path.is_file():
            raise UpdateError("独立更新器准备失败")
        return runner_path

    def _require_client(self) -> httpx.AsyncClient:
        """懒创建并长期复用一个更新客户端，避免每次检查创建连接池。"""

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
                headers={"Accept": "application/json"},
            )
            self._owns_client = True
        return self._client

    def _parse_and_verify_manifest(
        self,
        payload: dict[str, Any],
    ) -> UpdateManifest:
        """验证签名后提取最小且固定的发布字段。"""

        signature = payload.get("signature")
        if (
            not isinstance(signature, dict)
            or signature.get("algorithm") != "ed25519"
            or not isinstance(signature.get("value"), str)
        ):
            raise UpdateError("更新清单缺少 Ed25519 签名")
        if self._public_key is None:
            raise UpdateError("更新公钥未配置")

        try:
            signature_bytes = base64.b64decode(
                signature["value"],
                validate=True,
            )
            self._public_key.verify(
                signature_bytes,
                canonical_manifest_bytes(payload),
            )
        except (ValueError, InvalidSignature) as exc:
            raise UpdateError("更新清单签名校验失败") from exc

        package = payload.get("package")
        if not isinstance(package, dict):
            raise UpdateError("更新清单缺少 package 对象")

        version = self._required_string(payload, "version")
        channel = self._required_string(payload, "channel")
        published_at = self._required_string(payload, "publishedAt")
        package_url = self._required_string(package, "url")
        package_sha256 = self._required_string(package, "sha256").lower()
        package_size = package.get("size")
        release_notes = payload.get("releaseNotes", "")

        parse_semantic_version(version)
        if len(package_sha256) != 64 or any(
                character not in "0123456789abcdef"
                for character in package_sha256
        ):
            raise UpdateError("更新包 SHA-256 格式无效")
        if (
            not isinstance(package_size, int)
            or isinstance(package_size, bool)
            or not 0 < package_size <= self.package_max_bytes
        ):
            raise UpdateError("更新包大小无效")
        if not isinstance(release_notes, str):
            raise UpdateError("更新说明格式无效")

        self._validate_url(package_url)
        return UpdateManifest(
            version=version,
            channel=channel,
            package_url=package_url,
            package_sha256=package_sha256,
            package_size=package_size,
            published_at=published_at,
            release_notes=release_notes,
            raw_payload=dict(payload),
        )

    def _validate_url(self, value: str) -> None:
        """限制发布源协议，HTTP 仅可由内网发布配置显式启用。"""

        parsed = urlparse(value)
        if not parsed.netloc or parsed.scheme not in {"http", "https"}:
            raise UpdateError("更新地址协议无效")
        if parsed.scheme == "http" and not self.allow_insecure_http:
            raise UpdateError("当前发布配置禁止非 HTTPS 更新地址")

    @staticmethod
    def _required_string(source: Mapping[str, Any], key: str) -> str:
        """读取非空字符串字段，避免宽松解析未知清单。"""

        value = source.get(key)
        if not isinstance(value, str) or not value.strip():
            raise UpdateError(f"更新清单字段无效：{key}")
        return value.strip()

    @staticmethod
    def _load_public_key(
        public_key_base64: str,
    ) -> Ed25519PublicKey | None:
        """将内置 Base64 公钥转换为校验对象；空值代表尚未发布首版。"""

        if not public_key_base64.strip():
            return None
        try:
            raw_key = base64.b64decode(
                public_key_base64,
                validate=True,
            )
            return Ed25519PublicKey.from_public_bytes(raw_key)
        except ValueError as exc:
            raise UpdateError("内置更新公钥格式无效") from exc

    @staticmethod
    def _safe_remove(path: Path) -> None:
        """清除不完整下载；失败不掩盖后续可诊断的主异常。"""

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _is_matching_file(
        path: Path,
        manifest: UpdateManifest,
    ) -> bool:
        """复用已完整下载且哈希一致的安装包，避免启动时重复下载。"""

        if not path.is_file() or path.stat().st_size != manifest.package_size:
            return False
        digest = hashlib.sha256()
        try:
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest().lower() == manifest.package_sha256
