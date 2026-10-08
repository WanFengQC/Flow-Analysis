"""从用户确认的参考工作簿生成不含业务数据的完整导出模板。"""

from __future__ import annotations

import argparse
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell


def prepare_template(source_path: Path, output_path: Path) -> None:
    """保留全部 Sheet 的结构样式，彻底清除旧业务值和链接。"""

    workbook = load_workbook(source_path, data_only=False)
    if "SUMMARY_result" not in workbook.sheetnames:
        raise ValueError("参考工作簿缺少 SUMMARY_result")

    for worksheet in workbook.worksheets:
        for cell in worksheet._cells.values():
            if isinstance(cell, MergedCell):
                continue
            cell.value = None
            cell.comment = None
            cell.hyperlink = None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def main() -> int:
    """生成可随发布物携带的样式模板。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    prepare_template(arguments.source, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
