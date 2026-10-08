"""关联 ASIN 查询结果的商品展示卡片。"""

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)


def extract_variation_attributes(
    sku: Any,
) -> dict[str, str]:
    """从 SellerSprite sku 字符串中提取“属性名 → 属性值”。"""

    if not isinstance(sku, str) or not sku.strip():
        return {}

    attributes: dict[str, str] = {}
    for raw_segment in sku.split(" | "):
        segment = raw_segment.strip()
        if not segment or ":" not in segment:
            continue

        # 仅按第一个冒号切分，避免属性值中包含冒号时破坏原始值。
        raw_name, raw_value = segment.split(":", maxsplit=1)
        name = raw_name.strip()
        value = raw_value.strip()
        if name and value:
            attributes[name] = value

    return attributes


class RelationProductCard(QFrame):
    """只展示 relation/source 单条商品数据，不承担任何业务访问职责。"""

    selection_requested = Signal(str)

    _IMAGE_SIZE = 96

    def __init__(
        self,
        item: Mapping[str, Any],
        queried_asin: str,
        variation_attributes: Mapping[str, str] | None = None,
        parent=None,
    ) -> None:
        """根据已获取的数据创建商品卡片及其选择状态外观。"""

        super().__init__(parent)

        self._item = item
        self._asin = self._display_text(item.get("asin"), "")
        self._title = self._display_text(item.get("title"), "未提供标题")
        self._variation_attributes = dict(variation_attributes or {})

        self.setProperty("relationSelected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(300)
        self.setFixedHeight(166)
        # 每张卡片都填满 Grid 分配的列宽，高度始终固定，避免同一行因标题
        # 长短不同而出现卡片宽度不一或少量结果被拉高的视觉问题。
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            """
            RelationProductCard {
                background: #ffffff;
                border: 1px solid #e4e8ee;
                border-radius: 9px;
            }
            RelationProductCard[relationSelected="true"] {
                background: #f5f9ff;
                border: 1px solid #6aa6de;
            }
            QLabel#relationProductPriceLabel {
                color: #202938;
                font-size: 15px;
                font-weight: 600;
            }
            QLabel#relationProductAsinLabel {
                color: #778397;
                font-size: 11px;
            }
            QLabel#relationProductVariationLabel {
                color: #586779;
                font-size: 11px;
            }
            QLabel#currentAsinBadgeLabel {
                color: #2367b1;
                background: #eaf3ff;
                border: 1px solid #cde2fb;
                border-radius: 6px;
                padding: 1px 6px;
            }
            QLabel#variationBadgeLabel {
                color: #677383;
                background: #f1f3f5;
                border: 1px solid #e1e5e9;
                border-radius: 6px;
                padding: 1px 6px;
            }
            QLabel#selectionIndicatorLabel {
                color: #2a78bd;
                background: #eaf3ff;
                border: 1px solid #cde2fb;
                border-radius: 9px;
                font-weight: 700;
            }
            """
        )

        card_layout = QHBoxLayout(self)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(12)

        self.image_label = QLabel(self)
        self.image_label.setObjectName("relationProductImageLabel")
        self.image_label.setFixedSize(self._IMAGE_SIZE, self._IMAGE_SIZE)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._show_placeholder()
        card_layout.addWidget(self.image_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)

        is_current_asin = self._asin.upper() == queried_asin.upper()
        self.product_type_badge = QLabel(
            "当前 ASIN" if is_current_asin else "变体",
            self,
        )
        self.product_type_badge.setObjectName(
            "currentAsinBadgeLabel"
            if is_current_asin
            else "variationBadgeLabel"
        )
        self.product_type_badge.setFixedHeight(20)
        self.product_type_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_layout.addWidget(
            self.product_type_badge,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        self.title_label = QLabel(self)
        self.title_label.setObjectName("relationProductTitleLabel")
        title_font = QFont(self.title_label.font())
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        self.title_label.setToolTip(self._title)
        self.title_label.setMaximumHeight(
            QFontMetrics(title_font).lineSpacing() * 2
        )
        text_layout.addWidget(self.title_label)

        self.variation_label = QLabel(
            self._variation_display_text(),
            self,
        )
        self.variation_label.setObjectName(
            "relationProductVariationLabel"
        )
        self.variation_label.setToolTip(
            self._variation_display_text()
        )
        self.variation_label.setVisible(
            bool(self._variation_attributes)
        )
        text_layout.addWidget(self.variation_label)

        self.price_label = QLabel(
            self._format_price(item),
            self,
        )
        self.price_label.setObjectName("relationProductPriceLabel")
        text_layout.addWidget(self.price_label)

        self.rating_label = QLabel(
            self._format_rating(item),
            self,
        )
        self.rating_label.setObjectName("relationProductRatingLabel")
        text_layout.addWidget(self.rating_label)

        self.asin_label = QLabel(self._asin, self)
        self.asin_label.setObjectName("relationProductAsinLabel")
        text_layout.addWidget(self.asin_label)
        text_layout.addStretch(1)

        card_layout.addLayout(text_layout, 1)

        self.selection_indicator = QLabel("✓", self)
        self.selection_indicator.setObjectName("selectionIndicatorLabel")
        self.selection_indicator.setFixedSize(20, 20)
        self.selection_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selection_indicator.hide()
        card_layout.addWidget(
            self.selection_indicator,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        self._refresh_title()
        self._refresh_variation_text()

    @property
    def asin(self) -> str:
        """返回卡片对应的数据 ASIN，View 以此维护选择集合。"""

        return self._asin

    @property
    def image_url(self) -> str:
        """返回图片请求应使用的 URL，优先普通图片地址。"""

        image_url = self._display_text(
            self._item.get("imageUrl"),
            "",
        ).strip()
        if image_url:
            return image_url

        return self._display_text(
            self._item.get("bigImageUrl"),
            "",
        ).strip()

    def set_selected(self, selected: bool) -> None:
        """由 View 根据 selected_asins 更新纯展示选择状态。"""

        self.setProperty("relationSelected", selected)
        self.selection_indicator.setVisible(selected)

        # Qt 动态属性变更后需要重新 polish，选择边框才能立即刷新。
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_image_bytes(self, image_bytes: bytes) -> None:
        """在 Qt 主线程中将异步下载完成的图片更新到卡片。"""

        pixmap = QPixmap()
        if not pixmap.loadFromData(image_bytes):
            return

        scaled_pixmap = pixmap.scaled(
            self._IMAGE_SIZE,
            self._IMAGE_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled_pixmap)

    def mouseReleaseEvent(self, event) -> None:
        """整张卡片点击后请求 View 切换该 ASIN 的选择状态。"""

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._asin
        ):
            self.selection_requested.emit(self._asin)

        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        """卡片宽度变化时重新生成标题和变体文本的省略显示。"""

        super().resizeEvent(event)
        self._refresh_title()
        self._refresh_variation_text()

    def _refresh_title(self) -> None:
        """在卡片可用宽度内显示两行以内的标题，并保留完整 Tooltip。"""

        available_width = self.title_label.width()
        if available_width <= 0:
            self.title_label.setText(self._title)
            return

        display_title = self.title_label.fontMetrics().elidedText(
            self._title,
            Qt.TextElideMode.ElideRight,
            available_width * 2,
        )
        self.title_label.setText(display_title)

    def _refresh_variation_text(self) -> None:
        """根据当前卡片宽度省略过长变体信息，完整内容保留在 Tooltip。"""

        variation_text = self._variation_display_text()
        if not variation_text:
            return

        available_width = self.variation_label.width()
        if available_width <= 0:
            self.variation_label.setText(variation_text)
            return

        self.variation_label.setText(
            self.variation_label.fontMetrics().elidedText(
                variation_text,
                Qt.TextElideMode.ElideRight,
                available_width,
            )
        )

    def _variation_display_text(self) -> str:
        """把动态属性映射组合为单行变体展示文本。"""

        return " | ".join(
            f"{name}: {value}"
            for name, value in self._variation_attributes.items()
        )

    def _show_placeholder(self) -> None:
        """创建不依赖外部资源的浅色图片占位图。"""

        placeholder = QPixmap(self._IMAGE_SIZE, self._IMAGE_SIZE)
        placeholder.fill(QColor("#f1f4f8"))
        self.image_label.setPixmap(placeholder)

    @staticmethod
    def _display_text(value: Any, fallback: str) -> str:
        """将接口中的可选字段转换为用于显示的文本。"""

        if value is None:
            return fallback

        text = str(value).strip()
        return text or fallback

    @classmethod
    def _format_price(cls, item: Mapping[str, Any]) -> str:
        """组合接口已确认的 currency 与 price 字段。"""

        price = cls._display_text(item.get("price"), "--")
        currency = cls._display_text(item.get("currency"), "")
        return f"{currency} {price}".strip()

    @classmethod
    def _format_rating(cls, item: Mapping[str, Any]) -> str:
        """组合接口已确认的 rating 与 reviews 字段。"""

        rating = cls._display_text(item.get("rating"), "--")
        reviews = cls._display_text(item.get("reviews"), "--")
        return f"★ {rating} · {reviews} 条评价"
