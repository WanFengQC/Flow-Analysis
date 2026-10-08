"""为已构建的 Flow Analysis 安装包生成并签名内网更新清单。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    # 发布脚本从 packaging/ 目录执行时，显式保留项目根目录的导入能力。
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.update_service import (
    canonical_manifest_bytes,
    parse_semantic_version,
)


def _calculate_sha256(path: Path) -> str:
    """以流式方式计算安装包哈希。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    """生成 stable.json；私钥只读取，不复制到发布目录。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--release-notes", default="")
    arguments = parser.parse_args()

    parse_semantic_version(arguments.version)
    installer_path = arguments.installer.resolve()
    if not installer_path.is_file():
        raise SystemExit("安装包不存在")
    base_url = arguments.base_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise SystemExit("发布基础地址必须是 HTTP 或 HTTPS")

    private_key = serialization.load_pem_private_key(
        arguments.private_key.read_bytes(),
        password=None,
    )
    package_name = installer_path.name
    payload = {
        "schemaVersion": 1,
        "channel": arguments.channel,
        "version": arguments.version,
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "releaseNotes": arguments.release_notes,
        "package": {
            "url": f"{base_url}/releases/{package_name}",
            "sha256": _calculate_sha256(installer_path),
            "size": installer_path.stat().st_size,
        },
    }
    signature = private_key.sign(canonical_manifest_bytes(payload))
    payload["signature"] = {
        "algorithm": "ed25519",
        "value": base64.b64encode(signature).decode("ascii"),
    }

    output_path = arguments.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
