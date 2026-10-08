"""一次性生成 Flow Analysis 更新清单的 Ed25519 离线签名密钥。"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    """在仓库外创建私钥，只把可公开的 Base64 公钥打印给发布配置使用。"""

    default_path = (
        Path.home()
        / ".flow-analysis-release"
        / "update_signing_private.pem"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=default_path,
    )
    arguments = parser.parse_args()
    output_path = arguments.output.expanduser().resolve()
    if output_path.exists():
        raise SystemExit("私钥文件已存在；拒绝覆盖")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    output_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    if os.name != "nt":
        output_path.chmod(0o600)

    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    print("更新公钥 Base64：")
    print(base64.b64encode(public_key).decode("ascii"))
    print(f"私钥已保存到：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
