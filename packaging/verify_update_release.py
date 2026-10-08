"""验证已发布的 Flow Analysis 更新清单和安装包。"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path


# 允许脚本从项目根目录以外被直接执行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (  # noqa: E402
    UPDATE_ALLOW_INSECURE_HTTP,
    UPDATE_DOWNLOAD_DIR,
    UPDATE_HTTP_TIMEOUT_SECONDS,
    UPDATE_MANIFEST_MAX_BYTES,
    UPDATE_MANIFEST_PUBLIC_KEY_BASE64,
    UPDATE_PACKAGE_MAX_BYTES,
)
from infrastructure.update_service import UpdateError, UpdateService  # noqa: E402


async def verify_release(manifest_url: str, download_package: bool) -> None:
    """校验签名清单；按需下载并校验安装包的哈希与大小。"""

    service = UpdateService(
        manifest_url=manifest_url,
        channel="stable",
        current_version="0.0.0",
        public_key_base64=UPDATE_MANIFEST_PUBLIC_KEY_BASE64,
        download_dir=UPDATE_DOWNLOAD_DIR,
        allow_insecure_http=UPDATE_ALLOW_INSECURE_HTTP,
        timeout_seconds=UPDATE_HTTP_TIMEOUT_SECONDS,
        manifest_max_bytes=UPDATE_MANIFEST_MAX_BYTES,
        package_max_bytes=UPDATE_PACKAGE_MAX_BYTES,
    )
    try:
        manifest = await service.check_for_update()
        if manifest is None:
            raise UpdateError("发布清单没有提供比 0.0.0 更高的版本。")

        print(f"manifest_version={manifest.version}")
        print(f"installer_size={manifest.package_size}")
        print(f"installer_sha256={manifest.package_sha256}")

        if download_package:
            with tempfile.TemporaryDirectory(prefix="flow-analysis-update-verify-") as directory:
                # 发布验证不复用用户运行时目录，避免留下安装包缓存。
                service.download_dir = Path(directory)
                installer = await service.download_update(manifest)
                print(f"download_verified={installer.name}")
    finally:
        await service.aclose()


def parse_arguments() -> argparse.Namespace:
    """解析发布验证脚本参数。"""

    parser = argparse.ArgumentParser(description="验证 Flow Analysis 内网更新发布物")
    parser.add_argument("--manifest-url", required=True, help="签名更新清单的 URL")
    parser.add_argument(
        "--download-package",
        action="store_true",
        help="同时完整下载安装包并验证 SHA-256 与文件大小",
    )
    return parser.parse_args()


def main() -> int:
    """运行发布验证并返回适合自动化脚本使用的退出码。"""

    arguments = parse_arguments()
    try:
        asyncio.run(verify_release(arguments.manifest_url, arguments.download_package))
    except UpdateError as error:
        print(f"release_verification_failed={error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
