"""内部产品资料索引与单一 ProductContext 选择服务。"""

from collections.abc import Mapping, Sequence
from math import isfinite
from pathlib import Path
import re
from typing import Any

from config.settings import (
    PRODUCT_KNOWLEDGE_DOCUMENT_NAMES,
    PRODUCT_KNOWLEDGE_RESOURCE_DIR,
)
from models.product_context import ProductContext, ProductContextSelection


PRODUCT_KNOWLEDGE_BASE = "PRODUCT_KNOWLEDGE_BASE"
AMAZON_PRODUCT_SUMMARY = "AMAZON_PRODUCT_SUMMARY"

_ASIN_PATTERN = re.compile(r"\bB0[A-Z0-9]{8}\b")
_PROJECT_PATTERN = re.compile(r"项目(?:简称|代号)\s*:\s*([A-Za-z0-9_-]+)")
_PARENT_ASIN_PATTERN = re.compile(r"Parent ASIN\s*:\s*(B0[A-Z0-9]{8})")


class ProductKnowledgeService:
    """只读取本地产品资料，提供 ASIN 索引与确定性的背景选择。"""

    _DEFAULT_DOCUMENT_NAMES = PRODUCT_KNOWLEDGE_DOCUMENT_NAMES
    _SUM_METRICS = ("exposure", "clicks", "impressions", "searches")
    _RANK_METRIC = "abaWeeklyRank"

    def __init__(
        self,
        document_paths: Sequence[str | Path] | None = None,
    ) -> None:
        """在任务初始化时一次性加载资料；后续查询只读取内存索引。"""

        if document_paths is None:
            bundled_document_paths = tuple(
                PRODUCT_KNOWLEDGE_RESOURCE_DIR / document_name
                for document_name in self._DEFAULT_DOCUMENT_NAMES
            )
            # 已发布安装包优先读取随程序携带的固定版本资料，避免依赖用户
            # Downloads 目录；源码开发期则保留原来的下载目录作为回退。
            if all(path.is_file() for path in bundled_document_paths):
                document_paths = bundled_document_paths
            else:
                download_directory = Path.home() / "Downloads"
                document_paths = tuple(
                    download_directory / document_name
                    for document_name in self._DEFAULT_DOCUMENT_NAMES
                )

        self._document_paths = tuple(Path(path) for path in document_paths)
        self._contexts_by_asin: dict[str, ProductContext] = {}
        self._load_documents()

    @property
    def known_asins(self) -> tuple[str, ...]:
        """返回已建立索引的 ASIN，便于只读诊断和测试。"""

        return tuple(sorted(self._contexts_by_asin))

    def get_context_for_asin(self, asin: object) -> ProductContext | None:
        """按规范化 ASIN 查询内部产品背景；未命中时返回 None。"""

        normalized_asin = self._normalize_asin(asin)
        return self._contexts_by_asin.get(normalized_asin)

    def select_product_context(
        self,
        source_asin_stats: Mapping[str, Mapping[str, Any]],
    ) -> ProductContextSelection | None:
        """优先内部资料；全未命中时返回待获取 Amazon Summary 的描述。"""

        ranked_sources = self._rank_source_asins(source_asin_stats)
        if not ranked_sources:
            return None

        # 业务已明确本轮任务不存在 M/U 等跨项目竞争；这里仅在同一项目的
        # 已命中变体之间选代表项，不实现项目曝光比较或跨项目回退策略。
        for asin in ranked_sources:
            context = self.get_context_for_asin(asin)
            if context is not None:
                return ProductContextSelection(
                    source=PRODUCT_KNOWLEDGE_BASE,
                    representative_asin=asin,
                    needs_fetch=False,
                    context=context,
                )

        # 本次只给出 Amazon Product Summary 的唯一待获取目标，绝不执行
        # 快捷键、浏览器控制、DOM 抓取或任何网络请求。
        return ProductContextSelection(
            source=AMAZON_PRODUCT_SUMMARY,
            representative_asin=ranked_sources[0],
            needs_fetch=True,
            context=None,
        )

    def _load_documents(self) -> None:
        """读取每份资料并建立 Parent/Child ASIN 到 Context 的唯一索引。"""

        for document_path in self._document_paths:
            if not document_path.is_file():
                raise FileNotFoundError(
                    f"产品资料不存在：{document_path}"
                )

            document_text = document_path.read_text(encoding="utf-8")
            for context in self._parse_document(
                document_path,
                document_text,
            ):
                existing_context = self._contexts_by_asin.get(context.asin)
                if existing_context is not None:
                    raise ValueError(
                        "产品资料中存在重复 ASIN："
                        f"{context.asin}"
                    )
                self._contexts_by_asin[context.asin] = context

    @classmethod
    def _parse_document(
        cls,
        document_path: Path,
        document_text: str,
    ) -> list[ProductContext]:
        """解析真实 Markdown 文本的项目、Parent ASIN 与子体变体表。"""

        plain_text = document_text.replace("**", "")
        project_match = _PROJECT_PATTERN.search(plain_text)
        parent_match = _PARENT_ASIN_PATTERN.search(plain_text)
        if project_match is None or parent_match is None:
            raise ValueError(
                f"产品资料缺少项目或 Parent ASIN：{document_path}"
            )

        project = project_match.group(1).strip().upper()
        parent_asin = parent_match.group(1).strip().upper()
        shared_background = cls._extract_shared_background(document_text)
        contexts = [
            ProductContext(
                source=PRODUCT_KNOWLEDGE_BASE,
                project=project,
                asin=parent_asin,
                parent_asin=parent_asin,
                variation={},
                shared_background=shared_background,
                document_name=document_path.name,
            )
        ]

        for child_asin, variation in cls._parse_child_variations(
            document_text
        ).items():
            contexts.append(
                ProductContext(
                    source=PRODUCT_KNOWLEDGE_BASE,
                    project=project,
                    asin=child_asin,
                    parent_asin=parent_asin,
                    variation=variation,
                    shared_background=shared_background,
                    document_name=document_path.name,
                )
            )
        return contexts

    @staticmethod
    def _extract_shared_background(document_text: str) -> str:
        """移除 ASIN/变体矩阵，保留同项目共享的真实资料文本。"""

        sections = re.split(r"(?=^##\s+)", document_text, flags=re.MULTILINE)
        shared_sections = []
        for section in sections:
            heading = section.splitlines()[0].lower() if section else ""
            if "asin" in heading or "变体" in heading:
                continue
            if section.strip():
                shared_sections.append(section.strip())
        return "\n\n".join(shared_sections)

    @classmethod
    def _parse_child_variations(
        cls,
        document_text: str,
    ) -> dict[str, dict[str, str]]:
        """从资料中的 Markdown 表格提取每个 Child ASIN 的真实变体属性。"""

        child_variations: dict[str, dict[str, str]] = {}
        headers: list[str] | None = None
        for line in document_text.splitlines():
            stripped_line = line.strip()
            if not stripped_line.startswith("|"):
                continue

            cells = cls._split_table_row(stripped_line)
            if not cells or cls._is_table_separator(cells):
                continue

            if any("asin" in cell.lower() for cell in cells):
                headers = cells
                continue

            if headers is None or len(cells) != len(headers):
                continue

            for index, cell in enumerate(cells):
                asin_match = _ASIN_PATTERN.search(cell.upper())
                if asin_match is None:
                    continue

                asin = asin_match.group(0)
                child_variations[asin] = cls._build_variation(
                    headers,
                    cells,
                    index,
                )
        return child_variations

    @staticmethod
    def _split_table_row(line: str) -> list[str]:
        """清理 Markdown 表格单元格，不保留加粗等展示标记。"""

        return [
            re.sub(r"[*`]", "", cell).strip()
            for cell in line.strip().strip("|").split("|")
        ]

    @staticmethod
    def _is_table_separator(cells: Sequence[str]) -> bool:
        """识别 Markdown 的表头分隔行。"""

        return all(
            bool(re.fullmatch(r":?-{3,}:?", cell))
            for cell in cells
        )

    @classmethod
    def _build_variation(
        cls,
        headers: Sequence[str],
        cells: Sequence[str],
        asin_index: int,
    ) -> dict[str, str]:
        """将一个 Child ASIN 所在行转换为不猜测的变体属性。"""

        variation: dict[str, str] = {}
        for index, (header, cell) in enumerate(zip(headers, cells)):
            if index == asin_index:
                continue

            normalized_cell = cell.strip()
            if not normalized_cell or normalized_cell == "❌":
                continue

            normalized_header = header.lower()
            if "asin" in normalized_header:
                size_name = cls._size_name_from_asin_header(header)
                if size_name:
                    variation["Size"] = size_name
                continue

            variation_key = cls._variation_key_from_header(header)
            variation[variation_key] = normalized_cell
        return variation

    @staticmethod
    def _size_name_from_asin_header(header: str) -> str | None:
        """从 U 项目的 Size ASIN 列名中提取真实尺寸名称。"""

        size_match = re.search(r"size\s*:\s*(.+?)\s+asin", header, re.I)
        return size_match.group(1).strip() if size_match else None

    @staticmethod
    def _variation_key_from_header(header: str) -> str:
        """统一常见真实列名；未知列保留原列名而非臆造语义。"""

        normalized_header = header.lower()
        if "color" in normalized_header:
            return "Color"
        if "weight" in normalized_header:
            return "Item Weight"
        if "面料" in header or "material" in normalized_header:
            return "Material"
        if "包装" in header or "packag" in normalized_header:
            return "Packaging"
        return header.strip()

    @classmethod
    def _rank_source_asins(
        cls,
        source_asin_stats: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        """按已确认的五级指标和 ASIN 字符串生成稳定代表项顺序。"""

        normalized_stats: dict[str, Mapping[str, Any]] = {}
        for source_asin, stats in source_asin_stats.items():
            normalized_asin = cls._normalize_asin(source_asin)
            if normalized_asin and isinstance(stats, Mapping):
                normalized_stats[normalized_asin] = stats

        return sorted(
            normalized_stats,
            key=lambda asin: cls._source_asin_sort_key(
                asin,
                normalized_stats[asin],
            ),
        )

    @classmethod
    def _source_asin_sort_key(
        cls,
        asin: str,
        stats: Mapping[str, Any],
    ) -> tuple[Any, ...]:
        """None 永远低于有效值；ABA rank 的较小数值优先。"""

        return (
            *(
                cls._descending_optional_number(stats.get(metric))
                for metric in cls._SUM_METRICS
            ),
            cls._ascending_optional_number(stats.get(cls._RANK_METRIC)),
            asin,
        )

    @staticmethod
    def _descending_optional_number(value: Any) -> tuple[int, float]:
        """将高值优先且 None 最低的比较语义转换为排序键。"""

        numeric_value = ProductKnowledgeService._valid_number(value)
        return (1, 0.0) if numeric_value is None else (0, -numeric_value)

    @staticmethod
    def _ascending_optional_number(value: Any) -> tuple[int, float]:
        """将低 rank 优先且 None 最低的比较语义转换为排序键。"""

        numeric_value = ProductKnowledgeService._valid_number(value)
        return (1, 0.0) if numeric_value is None else (0, numeric_value)

    @staticmethod
    def _valid_number(value: Any) -> float | None:
        """不接受布尔、NaN、无穷或字符串，避免排序时伪造业务数值。"""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric_value = float(value)
        return numeric_value if isfinite(numeric_value) else None

    @staticmethod
    def _normalize_asin(asin: object) -> str:
        """统一 ASIN 大小写与空白，避免索引和来源统计出现双份键。"""

        return str(asin).strip().upper()
