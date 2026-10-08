"""Amazon Product Context Provider 的无浏览器状态与批次缓存测试。"""

import unittest

import asyncio

from models.product_context import AmazonProductSummaryStatus
from services.amazon_product_context_provider import AmazonProductContextProvider


class AmazonProductContextProviderTest(unittest.TestCase):
    """不访问 Amazon，验证错误页面不会被静默当成可用 Product Summary。"""

    def setUp(self):
        self.provider = AmazonProductContextProvider()

    def test_page_status_detects_captcha_login_and_asin_mismatch(self):
        """验证码、登录页和变体跳转均应终止该 ASIN，而不是重试或错误复用。"""

        self.assertEqual(
            self.provider._page_status(
                "https://www.amazon.com/dp/B09MT19SGB",
                "robot check Amazon captcha",
                "B09MT19SGB",
            ),
            AmazonProductSummaryStatus.AMAZON_CAPTCHA,
        )
        self.assertEqual(
            self.provider._page_status(
                "https://www.amazon.com/ap/signin",
                "Sign in to Amazon",
                "B09MT19SGB",
            ),
            AmazonProductSummaryStatus.AMAZON_LOGIN_REQUIRED,
        )
        self.assertEqual(
            self.provider._page_status(
                "https://www.amazon.com/dp/B0DSHYXD4G",
                "normal page",
                "B09MT19SGB",
            ),
            AmazonProductSummaryStatus.ASIN_MISMATCH,
        )

    def test_unique_asins_keep_input_order_and_reject_invalid_identity(self):
        """同一批次同 ASIN 只能抓一次，任何错误 identity 都不能形成请求。"""

        self.assertEqual(
            self.provider._normalize_unique_asins(
                ["b09mt19sgb", "B09MT19SGB", "B0DSHYXD4G"]
            ),
            ["B09MT19SGB", "B0DSHYXD4G"],
        )
        with self.assertRaises(ValueError):
            self.provider._normalize_unique_asins(["not-an-asin"])

    def test_us_delivery_correction_only_triggers_for_explicit_bad_locale(self):
        """英文美元环境不额外操作 Amazon UI，CNY/中国配送才允许尝试 ZIP。"""

        self.assertFalse(
            self.provider._needs_us_delivery_correction(
                "Price: $21.99, Deliver to New York"
            )
        )
        self.assertTrue(
            self.provider._needs_us_delivery_correction("Price: CNY 99")
        )

    def test_cancellation_prevents_new_asin_and_bridge_wait_is_safe(self):
        """取消后不得继续启动新 ASIN；空闲 bridge 必须可立即确认已清理。"""

        self.provider.begin_generation()
        self.provider.cancel_current_batch()
        with self.assertRaises(asyncio.CancelledError):
            self.provider._raise_if_cancelled()
        self.assertTrue(
            asyncio.run(self.provider.wait_for_bridge_cleanup(0.01))
        )


if __name__ == "__main__":
    unittest.main()
