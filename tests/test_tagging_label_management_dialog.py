"""标签管理界面的来源中文展示与稳定查询值回归测试。"""

import os
import unittest
from datetime import datetime
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from models.tagging import TagLabel
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    tagging_decision_source_display_name,
)
from repositories.tagging_label_management_repository import (
    TaggingLabelManagementRepository,
)
from views.tagging_label_management_dialog import (
    TaggingLabelManagementDialog,
    TaggingLabelTableModel,
)


def _record(source: TaggingDecisionSource) -> TaggingLabelCacheRecord:
    """构造一条只用于 UI 显示验证的正式缓存记录。"""

    return TaggingLabelCacheRecord(
        id=uuid4(),
        category_key=TaggingCategoryKey.PILLOW,
        word="weighted",
        taxonomy_version=7,
        label=TagLabel.ATTRIBUTE,
        reason="测试原因",
        decision_source=source,
        representative_asin="B0TEST0001",
        product_context_source="PRODUCT_KNOWLEDGE_BASE",
        updated_at=datetime(2026, 10, 9, 12, 0),
    )


class TaggingDecisionSourceDisplayTest(unittest.TestCase):
    """来源枚举只能在 UI 边界转换为中文。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog = TaggingLabelManagementDialog()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.application.processEvents()

    def test_all_sources_have_expected_chinese_display_names(self):
        expected = {
            TaggingDecisionSource.AI_CONSENSUS: "AI 共识",
            TaggingDecisionSource.HUMAN_REVIEW: "人工审核",
            TaggingDecisionSource.HISTORICAL_IMPORT: "历史导入",
            TaggingDecisionSource.MANUAL_MANAGEMENT: "人工管理",
        }

        self.assertEqual(
            {source: tagging_decision_source_display_name(source) for source in expected},
            expected,
        )

    def test_source_filter_displays_chinese_and_keeps_english_item_data(self):
        combo = self.dialog.ui.sourceComboBox
        expected = {
            "AI_CONSENSUS": "AI 共识",
            "HUMAN_REVIEW": "人工审核",
            "HISTORICAL_IMPORT": "历史导入",
            "MANUAL_MANAGEMENT": "人工管理",
        }

        self.assertEqual(combo.itemText(0), "全部来源")
        self.assertIsNone(combo.itemData(0))
        actual = {
            str(combo.itemData(index)): combo.itemText(index)
            for index in range(1, combo.count())
        }
        self.assertEqual(actual, expected)

    def test_source_filter_emits_english_value_for_service_query(self):
        requests = []
        self.dialog.refresh_requested.connect(
            lambda category, label, source, search, page: requests.append(
                (category, label, source, search, page)
            )
        )
        combo = self.dialog.ui.sourceComboBox
        combo.setCurrentIndex(combo.findData(TaggingDecisionSource.MANUAL_MANAGEMENT.value))
        self.dialog._emit_refresh()

        self.assertEqual(requests[-1][2], "MANUAL_MANAGEMENT")

    def test_english_filter_value_preserves_repository_query_semantics(self):
        where, parameters = TaggingLabelManagementRepository._where(
            category_key=None,
            label=None,
            decision_source=TaggingDecisionSource.HISTORICAL_IMPORT,
            search=None,
        )

        self.assertIn("decision_source = %(decision_source)s", where)
        self.assertEqual(parameters["decision_source"], "HISTORICAL_IMPORT")

    def test_table_uses_source_display_name_without_internal_versions(self):
        model = TaggingLabelTableModel()
        model.set_records([_record(TaggingDecisionSource.HUMAN_REVIEW)])

        source_index = model.index(0, 4)
        updated_at_index = model.index(0, 5)
        self.assertEqual(model.columnCount(), 6)
        self.assertEqual(
            model.data(source_index, Qt.ItemDataRole.DisplayRole),
            "人工审核",
        )
        self.assertEqual(
            model.headerData(5, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole),
            "更新时间",
        )
        self.assertEqual(
            model.data(updated_at_index, Qt.ItemDataRole.DisplayRole),
            "2026-10-09 12:00",
        )
        headers = [
            model.headerData(index, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            for index in range(model.columnCount())
        ]
        self.assertNotIn("分类体系版本", headers)
        self.assertNotIn("版本", headers)

    def test_unknown_source_is_not_mapped_to_a_known_source(self):
        self.assertEqual(
            tagging_decision_source_display_name("FUTURE_SOURCE"),
            "FUTURE_SOURCE",
        )

    def test_history_display_converts_source_but_not_management_action(self):
        self.assertEqual(
            self.dialog._history_action_display("AI_CONSENSUS"),
            "AI 共识",
        )
        self.assertEqual(self.dialog._history_action_display("CREATE"), "CREATE")
        self.assertEqual(
            self.dialog._history_snapshot_display(
                {"after": {"decisionSource": "MANUAL_MANAGEMENT"}}
            ),
            {"after": {"decisionSource": "人工管理"}},
        )


if __name__ == "__main__":
    unittest.main()
