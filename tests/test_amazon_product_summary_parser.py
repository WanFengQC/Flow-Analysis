"""Amazon Product Summary pqv 解析的离线单元测试。"""

import unittest

from models.product_context import AmazonProductSummaryStatus
from services.amazon_product_summary_parser import (
    AmazonProductSummaryParseError,
    AmazonProductSummaryParser,
)


def _summary_html(options_html: str, important_html: str = "") -> str:
    """按真实 pqv 锚点构造最小匿名 fixture，不把完整 Amazon 页面放进项目。"""

    return f"""
    <html><body>
      <h1 id="pqv-title">Product Summary: Cozy Pillow for Teens&amp;Adults</h1>
      <p id="pqv-byline">From Example Brand</p>
      <p id="pqv-ratings">4.7 out of 5 stars, 1,972 ratings</p>
      <div><h2 id="pqv-price">Price</h2><p>One-time purchase: $21.99</p></div>
      <div id="pqv-feature-bullets">
        <h2 id="pqv-feature-bullets-heading">About this Item</h2>
        <ul><li><span>Deep Touch Pressure &amp; comfort</span></li><li><span>Washable cover</span></li></ul>
      </div>
      <div id="pqv-description"><h2 id="pqv-description-heading">Product Description</h2><p>Soft support for travel.</p></div>
      <div>{options_html}</div>
      {important_html}
      <div id="pqv-feedback">Feedback</div>
    </body></html>
    """


class AmazonProductSummaryParserTest(unittest.TestCase):
    """验证 parser 独立于 Playwright，并只依赖 pqv 稳定锚点。"""

    def setUp(self):
        self.parser = AmazonProductSummaryParser()

    def test_u_shape_style_options_are_dynamic_and_entities_are_decoded(self):
        """U 型枕样式支持 Color/Size，不把维度名称硬编码到品类。"""

        html = _summary_html(
            """
            <h2 id="pqv-options-available">Options Available</h2>
            <h3>Color</h3><ul><li>Blue</li><li>Black Cat</li></ul>
            <h3>Size</h3><ul><li>Kids</li><li>Standard (Teens&amp;Adults)</li></ul>
            """,
            """
            <div id="pqv-important-information">
              <h2 id="pqv-important-information-heading">Important Information</h2>
              <p>Small parts.</p>
            </div>
            """,
        )
        context = self.parser.parse(html, "B0TEST0002")

        self.assertEqual(context.title, "Cozy Pillow for Teens&Adults")
        self.assertEqual(context.brand, "Example Brand")
        self.assertEqual(
            context.about_this_item,
            ("Deep Touch Pressure & comfort", "Washable cover"),
        )
        self.assertEqual(
            context.options,
            {
                "Color": ("Blue", "Black Cat"),
                "Size": ("Kids", "Standard (Teens&Adults)"),
            },
        )
        self.assertEqual(context.important_information, "Small parts.")
        self.assertEqual(context.price, "One-time purchase: $21.99")
        self.assertIn("Soft support for travel.", context.summary_text)

    def test_weighted_plush_options_are_not_category_hardcoded(self):
        """加重毛绒样本按 HTML h3 动态读取 Item Weight/Color/Set name。"""

        html = _summary_html(
            """
            <h2 id="pqv-options-available">Options Available</h2>
            <h3>Item Weight</h3><ul><li>3.3 Pounds</li><li>5 Pounds</li></ul>
            <h3>Color</h3><ul><li>White Bear</li><li>Leopard</li></ul>
            <h3>Set name</h3><ul><li>Single</li><li>Two Pack</li></ul>
            """
        )
        context = self.parser.parse(html, "B0TEST0001")

        self.assertEqual(
            context.options,
            {
                "Item Weight": ("3.3 Pounds", "5 Pounds"),
                "Color": ("White Bear", "Leopard"),
                "Set name": ("Single", "Two Pack"),
            },
        )

    def test_missing_pqv_title_is_explicit_not_found(self):
        """不存在 pqv-title 不是空摘要，而是可用于 Provider 重试的明确失败。"""

        with self.assertRaises(AmazonProductSummaryParseError) as context:
            self.parser.parse("<html><body>no summary</body></html>", "B0TEST0002")
        self.assertEqual(
            context.exception.status,
            AmazonProductSummaryStatus.PRODUCT_SUMMARY_NOT_FOUND,
        )


if __name__ == "__main__":
    unittest.main()
