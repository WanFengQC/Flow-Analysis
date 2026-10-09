"""SellerSprite API 业务错误分类测试。"""

import unittest

from services.api_service import ApiService


class ApiServiceErrorClassificationTest(unittest.TestCase):
    """确保 relation 数据源权限拒绝不会落入普通重试类别。"""

    def test_detects_known_data_source_permission_message(self) -> None:
        """已确认的中文权限消息必须被精确识别。"""

        self.assertTrue(
            ApiService._is_data_source_permission_error(
                "没有权限访问该数据源"
            )
        )

    def test_does_not_misclassify_unrelated_business_error(self) -> None:
        """其他业务错误不能误触发 Cookie 刷新。"""

        self.assertFalse(
            ApiService._is_data_source_permission_error(
                "请求参数不合法"
            )
        )
