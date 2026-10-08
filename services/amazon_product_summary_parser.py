"""Amazon Product Summary 的 ``pqv-*`` HTML 纯解析服务。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Iterable

from models.product_context import (
    AmazonProductContext,
    AmazonProductSummaryStatus,
)
from services.product_knowledge_service import AMAZON_PRODUCT_SUMMARY


class AmazonProductSummaryParseError(ValueError):
    """将缺失或不完整 Product Summary 显式报告给浏览器 Provider。"""

    def __init__(
        self,
        status: AmazonProductSummaryStatus,
        message: str,
    ) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class _HtmlNode:
    """仅满足 pqv 区域提取所需的轻量 HTML 节点，不依赖浏览器或第三方库。"""

    tag: str
    attributes: dict[str, str]
    parent: "_HtmlNode | None" = None
    children: list["_HtmlNode"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)


class _SummaryHtmlTreeParser(HTMLParser):
    """构建最小节点树，避免用超长正则匹配 Amazon 的任意嵌套结构。"""

    _VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("root", {})
        self._stack = [self.root]

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """建立子节点；属性名称统一小写以稳定读取 id。"""

        node = _HtmlNode(
            tag.lower(),
            {
                name.lower(): value or ""
                for name, value in attrs
            },
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(node)
        if node.tag not in self._VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """支持自闭合元素，不改变当前节点栈。"""

        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """容错回退至最近匹配标签，真实 HTML 的轻微不规范不应中断解析。"""

        normalized_tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized_tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        """保留文字片段，由上层统一做空白规范化。"""

        self._stack[-1].text_parts.append(data)


class AmazonProductSummaryParser:
    """从页面源码稳定截取 pqv Summary，并映射为 AmazonProductContext。"""

    _SUMMARY_BOUNDARY_PATTERN = re.compile(
        r"(?is)<h1\s+id=[\"']pqv-title[\"'][^>]*>.*?"
        r"(?=<div\s+id=[\"']pqv-feedback[\"'])"
    )
    _TITLE_PREFIX_PATTERN = re.compile(r"^\s*Product\s+Summary\s*:\s*", re.I)
    _BYLINE_PREFIX_PATTERN = re.compile(r"^\s*From\s+", re.I)

    def parse(
        self,
        html: str,
        requested_asin: str,
    ) -> AmazonProductContext:
        """解析单页 HTML；浏览器、网络和 URL 校验均不属于此纯函数。"""

        if not isinstance(html, str) or not html.strip():
            raise AmazonProductSummaryParseError(
                AmazonProductSummaryStatus.PRODUCT_SUMMARY_NOT_FOUND,
                "Amazon 页面未包含可解析的 Product Summary HTML",
            )
        asin = self._normalize_asin(requested_asin)
        summary_html = self._extract_summary_html(html)
        tree_parser = _SummaryHtmlTreeParser()
        tree_parser.feed(summary_html)
        tree_parser.close()

        title = self._clean_text(self._node_text(self._find_by_id(tree_parser.root, "pqv-title")))
        title = self._TITLE_PREFIX_PATTERN.sub("", title or "") or None
        summary_text = self._clean_text(self._node_text(tree_parser.root))
        if not title or not summary_text:
            raise AmazonProductSummaryParseError(
                AmazonProductSummaryStatus.PRODUCT_SUMMARY_NOT_FOUND,
                "Product Summary 缺少有效 title 或正文",
            )

        brand = self._clean_text(
            self._node_text(self._find_by_id(tree_parser.root, "pqv-byline"))
        )
        if brand:
            brand = self._BYLINE_PREFIX_PATTERN.sub("", brand) or None

        return AmazonProductContext(
            asin=asin,
            source=AMAZON_PRODUCT_SUMMARY,
            title=title,
            brand=brand,
            ratings=self._field_text(tree_parser.root, "pqv-ratings"),
            price=self._price_text(tree_parser.root),
            list_price=self._field_text(
                tree_parser.root,
                "pqv-price-list-price",
            ),
            about_this_item=tuple(
                self._list_texts(
                    self._find_by_id(
                        tree_parser.root,
                        "pqv-feature-bullets",
                    )
                )
            ),
            product_description=self._field_text(
                tree_parser.root,
                "pqv-description",
            ),
            options=self._extract_options(
                self._find_by_id(tree_parser.root, "pqv-options-available")
            ),
            important_information=self._field_text(
                tree_parser.root,
                "pqv-important-information",
            ),
            summary_text=summary_text,
        )

    def _extract_summary_html(self, html: str) -> str:
        """采用 pqv-title 到 pqv-feedback 的稳定边界，不依赖 class 或行号。"""

        match = self._SUMMARY_BOUNDARY_PATTERN.search(html)
        if match is None:
            raise AmazonProductSummaryParseError(
                AmazonProductSummaryStatus.PRODUCT_SUMMARY_NOT_FOUND,
                "未找到 pqv-title 到 pqv-feedback 的 Product Summary 边界",
            )
        return match.group(0)

    @classmethod
    def _find_by_id(
        cls,
        node: _HtmlNode,
        target_id: str,
    ) -> _HtmlNode | None:
        """按稳定 pqv ID 查找节点。"""

        if node.attributes.get("id") == target_id:
            return node
        for child in node.children:
            found = cls._find_by_id(child, target_id)
            if found is not None:
                return found
        return None

    @classmethod
    def _field_text(cls, root: _HtmlNode, field_id: str) -> str | None:
        """读取可选 pqv 字段，并剔除仅作展示的对应标题。"""

        node = cls._find_by_id(root, field_id)
        if node is None:
            return None
        # pqv-price 是 h2 标题，而价格文字位于同级容器；其余内容字段的
        # heading 位于内部。两种真实结构都不能把 "Price" 等标题当作值。
        if node.tag in {"h1", "h2", "h3"} and node.parent is not None:
            value = cls._node_text_excluding(node.parent, {id(node)})
        else:
            heading_id = f"{field_id}-heading"
            heading = cls._find_by_id(node, heading_id)
            excluded = {id(heading)} if heading is not None else set()
            value = cls._node_text_excluding(node, excluded)
        value = cls._clean_text(value)
        return value or None

    @classmethod
    def _price_text(cls, root: _HtmlNode) -> str | None:
        """从 pqv-price 所在区块提取首个真实价格文本，不拼入购买入口文案。"""

        price_heading = cls._find_by_id(root, "pqv-price")
        if price_heading is None or price_heading.parent is None:
            return None
        for node in cls._descendants_by_tag(price_heading.parent, "p"):
            value = cls._clean_text(cls._node_text(node))
            if value and (
                "$" in value
                or "€" in value
                or "£" in value
                or "¥" in value
                or "no featured offers" in value.lower()
            ):
                return value
        return None

    @classmethod
    def _node_text(cls, node: _HtmlNode | None) -> str:
        """递归提取可见文本，忽略 script/style 代码。"""

        if node is None or node.tag in {"script", "style"}:
            return ""
        parts = [*node.text_parts]
        for child in node.children:
            parts.append(cls._node_text(child))
        return " ".join(parts)

    @classmethod
    def _node_text_excluding(
        cls,
        node: _HtmlNode | None,
        excluded_nodes: set[int],
    ) -> str:
        """提取节点文字时忽略指定 heading，保留真实商品正文。"""

        if (
            node is None
            or id(node) in excluded_nodes
            or node.tag in {"script", "style"}
        ):
            return ""
        parts = [*node.text_parts]
        for child in node.children:
            parts.append(cls._node_text_excluding(child, excluded_nodes))
        return " ".join(parts)

    @classmethod
    def _list_texts(cls, node: _HtmlNode | None) -> Iterable[str]:
        """在指定 pqv 区块收集所有 li 文本并去除空值与重复项。"""

        if node is None:
            return ()
        values: list[str] = []
        for list_node in cls._descendants_by_tag(node, "li"):
            value = cls._clean_text(cls._node_text(list_node))
            if value and value not in values:
                values.append(value)
        return values

    @classmethod
    def _extract_options(
        cls,
        options_heading: _HtmlNode | None,
    ) -> dict[str, tuple[str, ...]]:
        """根据 Options 区域中的实际 h3/list 结构动态提取所有变体维度。"""

        if options_heading is None:
            return {}
        parent = options_heading.parent
        if parent is None:
            return {}
        try:
            start_index = parent.children.index(options_heading) + 1
        except ValueError:
            return {}

        options: dict[str, tuple[str, ...]] = {}
        current_name: str | None = None
        current_values: list[str] = []

        def commit_current_option() -> None:
            """保存当前动态维度；无可选值的标题不输出伪空数组。"""

            if current_name and current_values:
                options[current_name] = tuple(current_values)

        for sibling in parent.children[start_index:]:
            if sibling.tag == "h3":
                commit_current_option()
                current_name = cls._clean_text(cls._node_text(sibling)) or None
                current_values = []
                continue
            # pqv 选项区域后面仍可能有其它 section；遇到下一个 pqv 区块时
            # 立即结束，避免把反馈或重要信息错误当作某个 option 值。
            sibling_id = sibling.attributes.get("id", "")
            if sibling_id.startswith("pqv-"):
                break
            if current_name:
                for list_node in cls._descendants_including_self(sibling, "li"):
                    value = cls._clean_text(cls._node_text(list_node))
                    if value and value not in current_values:
                        current_values.append(value)
        commit_current_option()
        return options

    @classmethod
    def _descendants_by_tag(
        cls,
        node: _HtmlNode,
        tag: str,
    ) -> Iterable[_HtmlNode]:
        """按页面顺序遍历节点，供 fields/options 使用。"""

        for child in node.children:
            if child.tag == tag:
                yield child
            yield from cls._descendants_by_tag(child, tag)

    @classmethod
    def _descendants_including_self(
        cls,
        node: _HtmlNode,
        tag: str,
    ) -> Iterable[_HtmlNode]:
        """支持当前 sibling 本身就是 ul/li 的 Options 结构。"""

        if node.tag == tag:
            yield node
        yield from cls._descendants_by_tag(node, tag)

    @staticmethod
    def _clean_text(value: str) -> str:
        """HTML entity 已由 parser 处理；此处只规范空白，不改写商品文案。"""

        return re.sub(r"\s+", " ", unescape(value)).strip()

    @staticmethod
    def _normalize_asin(value: str) -> str:
        """Parser 仅接受明确请求的 ASIN，避免输出含空白或小写 identity。"""

        normalized = value.strip().upper() if isinstance(value, str) else ""
        if not re.fullmatch(r"B0[A-Z0-9]{8}", normalized):
            raise ValueError("requested_asin 必须是有效 ASIN")
        return normalized
