"""构建 Flow Analysis 的 PyInstaller 目录应用、独立更新器和 Inno Setup 安装包。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    # 脚本从 packaging/ 目录执行时，显式保留项目根目录的导入能力。
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    APP_VERSION,
    PRODUCT_KNOWLEDGE_DOCUMENT_NAMES,
    UNIAPI_API_KEY,
)


BUILD_ROOT = PROJECT_ROOT / "build" / "packaging"
DIST_ROOT = PROJECT_ROOT / "dist"
PYINSTALLER_DIST_DIR = DIST_ROOT / "pyinstaller"
INNO_SCRIPT = PROJECT_ROOT / "packaging" / "FlowAnalysis.iss"
PRODUCT_KNOWLEDGE_SOURCE_DIR = Path.home() / "Downloads"
EMBEDDED_RUNTIME_CONFIG_NAME = "embedded_runtime_config.json"


def _find_iscc() -> Path:
    """定位当前 Windows 用户或系统范围安装的 Inno Setup 编译器。"""

    candidates = [
        Path.home()
        / "AppData"
        / "Local"
        / "Programs"
        / "Inno Setup 6"
        / "ISCC.exe",
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("未找到 Inno Setup 6 的 ISCC.exe")


def _run(command: list[str]) -> None:
    """以项目根目录为工作目录执行构建命令，失败立即中止。"""

    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def _clean_build_directories() -> None:
    """只清理本脚本明确管理的构建目录，绝不触碰用户运行数据。"""

    for path in (BUILD_ROOT, PYINSTALLER_DIST_DIR, DIST_ROOT / "installer"):
        if path.exists():
            shutil.rmtree(path)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)


def _product_knowledge_data_arguments() -> list[str]:
    """返回必须打入安装包的只读产品资料；缺失时禁止生成不可用安装包。"""

    arguments: list[str] = []
    for document_name in PRODUCT_KNOWLEDGE_DOCUMENT_NAMES:
        source_path = PRODUCT_KNOWLEDGE_SOURCE_DIR / document_name
        if not source_path.is_file():
            raise RuntimeError(f"缺少发布所需产品资料：{source_path}")
        arguments.extend(
            ["--add-data", f"{source_path};resources/product_knowledge"]
        )
    return arguments


def _create_embedded_runtime_config() -> Path:
    """在被忽略的构建目录生成仅供 EXE 使用的敏感运行配置。"""

    api_key = UNIAPI_API_KEY.strip()
    if not api_key:
        raise RuntimeError(
            "未配置 UNIAPI_API_KEY，无法构建内置 AI 配置的发布包"
        )

    config_path = BUILD_ROOT / EMBEDDED_RUNTIME_CONFIG_NAME
    config_path.write_text(
        json.dumps({"UNIAPI_API_KEY": api_key}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path


def main() -> int:
    """生成发布成品；版本必须与 config/settings.py 的唯一版本一致。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=APP_VERSION)
    arguments = parser.parse_args()
    if arguments.version != APP_VERSION:
        raise SystemExit(
            "发布版本必须先更新 config/settings.py 中的 APP_VERSION"
        )

    product_knowledge_data_arguments = _product_knowledge_data_arguments()
    _clean_build_directories()
    embedded_runtime_config = _create_embedded_runtime_config()
    work_path = BUILD_ROOT / "pyinstaller-work"
    spec_path = BUILD_ROOT / "pyinstaller-spec"
    application_dist = PYINSTALLER_DIST_DIR / "FlowAnalysis"

    common_arguments = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath",
        str(work_path),
        "--specpath",
        str(spec_path),
        "--distpath",
        str(PYINSTALLER_DIST_DIR),
        "--paths",
        str(PROJECT_ROOT),
    ]
    application_data_arguments = [
        # 完整 Excel 导出模板属于运行时资源，打包后仍必须按原样读取。
        "--add-data",
        (
            f"{PROJECT_ROOT / 'resources' / 'excel_templates' / 'analysis_export_template.xlsx'}"
            ";resources/excel_templates"
        ),
        *product_knowledge_data_arguments,
        # 这个文件只位于被忽略的 build/ 中；它会被打入 EXE，但绝不进入源码。
        "--add-data",
        f"{embedded_runtime_config};resources",
    ]
    _run(
        common_arguments
        + application_data_arguments
        + [
            "--onedir",
            "--noconsole",
            "--name",
            "FlowAnalysis",
            "--collect-all",
            "psycopg",
            "--collect-all",
            "psycopg_binary",
            "--collect-all",
            "cryptography",
            str(PROJECT_ROOT / "main.py"),
        ]
    )
    _run(
        common_arguments
        + [
            "--onefile",
            "--noconsole",
            "--name",
            "FlowAnalysisUpdater",
            str(PROJECT_ROOT / "infrastructure" / "update_launcher.py"),
        ]
    )

    updater_path = PYINSTALLER_DIST_DIR / "FlowAnalysisUpdater.exe"
    if not application_dist.is_dir() or not updater_path.is_file():
        raise RuntimeError("PyInstaller 未生成完整应用或独立更新器")

    # 本机 PyInstaller 会把与 Qt 无关的 ICU 运行库放进应用根目录。它会
    # 覆盖 Windows 自带 ICU，导致 PySide6 的 QtCore 加载时出现“找不到指定
    # 的程序”。源码运行使用系统 ICU，因此发布物也必须保持相同依赖解析。
    for library_name in ("icuuc.dll", "icudt78.dll"):
        bundled_library = application_dist / "_internal" / library_name
        if bundled_library.is_file():
            bundled_library.unlink()

    shutil.copy2(updater_path, application_dist / updater_path.name)

    _run(
        [
            str(_find_iscc()),
            f"/DMyAppVersion={APP_VERSION}",
            str(INNO_SCRIPT),
        ]
    )
    installer_path = DIST_ROOT / "installer" / (
        f"FlowAnalysisSetup-{APP_VERSION}.exe"
    )
    if not installer_path.is_file():
        raise RuntimeError("Inno Setup 未生成安装包")
    print(installer_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
