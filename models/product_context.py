"""产品资料解析后的稳定数据结构，不包含 AI、UI 或持久化逻辑。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


@dataclass(frozen=True)
class ProductContext:
    """一个 Parent/Child ASIN 可复用的内部产品背景。"""

    source: str
    project: str
    asin: str
    parent_asin: str
    variation: Mapping[str, str]
    shared_background: str
    document_name: str

    def to_dict(self) -> dict[str, object]:
        """提供未来 tagging 输入可直接使用的无副作用字典快照。"""

        return {
            "source": self.source,
            "project": self.project,
            "asin": self.asin,
            "parentAsin": self.parent_asin,
            "variation": dict(self.variation),
            "sharedBackground": self.shared_background,
            "documentName": self.document_name,
        }


@dataclass(frozen=True)
class ProductContextSelection:
    """为一个 word 选择的唯一产品背景或待获取的 Amazon 兜底描述。"""

    source: str
    representative_asin: str
    needs_fetch: bool
    context: ProductContext | None

    def to_dict(self) -> dict[str, object]:
        """转换为未来业务链可消费的中性数据结构。"""

        return {
            "source": self.source,
            "representativeAsin": self.representative_asin,
            "needsFetch": self.needs_fetch,
            "context": (
                self.context.to_dict()
                if self.context is not None
                else None
            ),
        }


class AmazonProductSummaryStatus(StrEnum):
    """Amazon Product Summary 获取或解析的明确运行时状态。"""

    READY = "READY"
    PRODUCT_SUMMARY_NOT_FOUND = "PRODUCT_SUMMARY_NOT_FOUND"
    ASIN_MISMATCH = "ASIN_MISMATCH"
    AMAZON_CAPTCHA = "AMAZON_CAPTCHA"
    AMAZON_LOGIN_REQUIRED = "AMAZON_LOGIN_REQUIRED"
    PAGE_LOAD_FAILED = "PAGE_LOAD_FAILED"
    TIMEOUT = "TIMEOUT"
    PROFILE_IN_USE = "PROFILE_IN_USE"
    PRODUCT_CONTEXT_INCOMPLETE = "PRODUCT_CONTEXT_INCOMPLETE"


@dataclass(frozen=True)
class AmazonProductContext:
    """仅由 Amazon Product Summary pqv 区域解析出的临时产品背景。"""

    asin: str
    source: str
    title: str | None
    brand: str | None
    ratings: str | None
    price: str | None
    list_price: str | None
    about_this_item: tuple[str, ...]
    product_description: str | None
    options: Mapping[str, tuple[str, ...]]
    important_information: str | None
    summary_text: str

    def to_dict(self) -> dict[str, object]:
        """生成 AI 输入可用的结构化快照，不包含页面 HTML 或浏览器状态。"""

        return {
            "source": self.source,
            "asin": self.asin,
            "title": self.title,
            "brand": self.brand,
            "ratings": self.ratings,
            "price": self.price,
            "listPrice": self.list_price,
            "aboutThisItem": list(self.about_this_item),
            "productDescription": self.product_description,
            "options": {
                name: list(values)
                for name, values in self.options.items()
            },
            "importantInformation": self.important_information,
            "summaryText": self.summary_text,
        }


@dataclass(frozen=True)
class AmazonProductContextResult:
    """单个 ASIN 的临时抓取结果；失败不伪造空 ProductContext。"""

    asin: str
    status: AmazonProductSummaryStatus
    context: AmazonProductContext | None
    detail: str | None = None
