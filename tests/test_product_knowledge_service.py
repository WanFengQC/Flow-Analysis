"""产品资料索引和代表 ASIN 选择的纯数据测试。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from services.product_knowledge_service import (
    AMAZON_PRODUCT_SUMMARY,
    PRODUCT_KNOWLEDGE_BASE,
    ProductKnowledgeService,
)


_M_DOCUMENT = """# Product Knowledge Base - 项目代号: M

## 1. 基础属性
M 项目共享背景。

## 5. ASIN 与变体矩阵
- **项目简称:** M
- **Parent ASIN:** B0PARN0001

| Child ASIN | Color | Item Weight | 实际包装形态 |
| --- | --- | --- | --- |
| **B0TEST0001** | Leopard | 3.3 Pounds | Standard |
| **B0TEST0003** | Duck | 3.3 Pounds | Standard |
"""

_U_DOCUMENT = """# Product Knowledge Base - 项目代号: U

## 1. 基础属性
U 项目共享背景。

## 2. ASIN 与变体矩阵
- **项目简称:** U
- **Parent ASIN:** B0PARN0002

| Color (含面料属性) | 实际面料形态 | Size: Kids (Ages 3-8) ASIN | Size: Standard (Teens&Adults) ASIN |
| --- | --- | --- | --- |
| Avocado | 100% Polyester | B0TEST0004 | B0TEST0005 |
"""


class ProductKnowledgeServiceTest(unittest.TestCase):
    """验证产品资料命中优先级及严格的代表 ASIN 排序。"""

    def setUp(self) -> None:
        """为每个测试创建互不污染的真实格式产品资料。"""

        self._temporary_directory = TemporaryDirectory()
        directory = Path(self._temporary_directory.name)
        m_path = directory / "产品资料-M.txt"
        u_path = directory / "产品资料-U.txt"
        m_path.write_text(_M_DOCUMENT, encoding="utf-8")
        u_path.write_text(_U_DOCUMENT, encoding="utf-8")
        self.service = ProductKnowledgeService([u_path, m_path])

    def tearDown(self) -> None:
        """删除测试产品资料。"""

        self._temporary_directory.cleanup()

    def test_child_asin_context_comes_from_matching_product_document(self):
        """B0TEST0001 必须命中 M，而不是进入 Amazon fallback。"""

        context = self.service.get_context_for_asin(" b0test0001 ")

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.source, PRODUCT_KNOWLEDGE_BASE)
        self.assertEqual(context.project, "M")
        self.assertEqual(context.parent_asin, "B0PARN0001")
        self.assertEqual(
            dict(context.variation),
            {
                "Color": "Leopard",
                "Item Weight": "3.3 Pounds",
                "Packaging": "Standard",
            },
        )

    def test_default_load_prefers_bundled_product_documents(self):
        """安装包存在内置资料时，绝不能再依赖用户 Downloads 目录。"""

        directory = Path(self._temporary_directory.name)
        with patch(
            "services.product_knowledge_service.PRODUCT_KNOWLEDGE_RESOURCE_DIR",
            directory,
        ):
            service = ProductKnowledgeService()

        self.assertIn("B0TEST0001", service.known_asins)

    def test_internal_context_wins_over_unknown_higher_exposure(self):
        """只要存在内部资料，未知 ASIN 的更高曝光也不能触发 Amazon。"""

        selection = self.service.select_product_context(
            {
                "B0TEST0001": {"exposure": 10},
                "B000000001": {"exposure": 100000},
            }
        )

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.source, PRODUCT_KNOWLEDGE_BASE)
        self.assertEqual(selection.representative_asin, "B0TEST0001")
        self.assertFalse(selection.needs_fetch)
        self.assertIsNotNone(selection.context)

    def test_internal_variants_use_same_deterministic_selection_order(self):
        """同项目内部变体仍按来源统计选择唯一代表变体。"""

        selection = self.service.select_product_context(
            {
                "B0TEST0001": {"exposure": 100},
                "B0TEST0003": {"exposure": 200},
            }
        )

        self.assertIsNotNone(selection)
        assert selection is not None and selection.context is not None
        self.assertEqual(selection.representative_asin, "B0TEST0003")
        self.assertEqual(selection.context.project, "M")
        self.assertEqual(selection.context.variation["Color"], "Duck")

    def test_amazon_fallback_uses_complete_deterministic_sort_order(self):
        """未知来源按曝光、点击、展示、搜索、ABA、ASIN 的固定顺序选择。"""

        cases = (
            (
                "exposure",
                {
                    "B000000001": {"exposure": 1000, "clicks": 1},
                    "B000000002": {"exposure": 900, "clicks": 999},
                },
                "B000000001",
            ),
            (
                "clicks",
                {
                    "B000000001": {"exposure": 1000, "clicks": 50},
                    "B000000002": {"exposure": 1000, "clicks": 80},
                },
                "B000000002",
            ),
            (
                "impressions",
                {
                    "B000000001": {"exposure": 1000, "clicks": 50, "impressions": 40},
                    "B000000002": {"exposure": 1000, "clicks": 50, "impressions": 80},
                },
                "B000000002",
            ),
            (
                "searches",
                {
                    "B000000001": {"exposure": 1000, "clicks": 50, "impressions": 80, "searches": 10},
                    "B000000002": {"exposure": 1000, "clicks": 50, "impressions": 80, "searches": 20},
                },
                "B000000002",
            ),
            (
                "rank",
                {
                    "B000000001": {"exposure": 1000, "clicks": 50, "impressions": 80, "searches": 20, "abaWeeklyRank": 350},
                    "B000000002": {"exposure": 1000, "clicks": 50, "impressions": 80, "searches": 20, "abaWeeklyRank": 800},
                },
                "B000000001",
            ),
            (
                "none",
                {
                    "B000000001": {"exposure": None, "clicks": 100},
                    "B000000002": {"exposure": 500, "clicks": 1},
                },
                "B000000002",
            ),
            (
                "asin",
                {
                    "B000000002": {},
                    "B000000001": {},
                },
                "B000000001",
            ),
        )

        for case_name, source_stats, expected_asin in cases:
            with self.subTest(case_name=case_name):
                selection = self.service.select_product_context(source_stats)
                self.assertIsNotNone(selection)
                assert selection is not None
                self.assertEqual(selection.source, AMAZON_PRODUCT_SUMMARY)
                self.assertEqual(selection.representative_asin, expected_asin)
                self.assertTrue(selection.needs_fetch)
                self.assertIsNone(selection.context)


if __name__ == "__main__":
    unittest.main()
