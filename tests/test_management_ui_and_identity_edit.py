"""管理窗口展示与标签 identity 迁移的无数据库回归测试。"""

import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

from models.normalization_active_rule import NormalizationActiveRuleRecord
from models.normalization_rule import (
    NormalizationRuleType,
    normalization_rule_type_display_name,
)
from models.tagging import TagLabel
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
)
from services.tagging_label_management_service import (
    TaggingLabelIdentityExistsError,
    TaggingLabelManagementService,
)
from views.normalization_management_dialog import (
    NormalizationActiveRuleTableModel,
    NormalizationManagementDialog,
)
from views.tagging_label_management_dialog import TaggingLabelManagementDialog


class _FakeDatabase:
    """仅提供事务边界，绝不连接数据库。"""

    @asynccontextmanager
    async def transaction(self):
        yield object()


class _FakeTaggingRepository:
    """模拟 identity 唯一约束及审计，用于 Service 事务行为验证。"""

    records_by_id: dict = {}
    audits: list = []
    decisions: list = []

    def __init__(self, _database, _connection=None):
        pass

    @classmethod
    def reset(cls, record):
        cls.records_by_id = {record.id: record}
        cls.audits = []
        cls.decisions = []

    @staticmethod
    def _identity(record):
        return record.category_key, record.word, record.taxonomy_version

    async def get_current_by_id(self, cache_id, *, for_update=False):
        record = self.records_by_id.get(cache_id)
        return record if record and record.is_active else None

    async def get_by_identity(self, *, category_key, word, taxonomy_version, for_update=False):
        for record in self.records_by_id.values():
            if self._identity(record) == (category_key, word, taxonomy_version):
                return record
        return None

    async def insert_manual(self, record):
        self.records_by_id[record.id] = record
        return record

    async def reactivate_manual(self, *, previous, label, reason):
        current = self.records_by_id.get(previous.id)
        if current != previous or current.is_active:
            return None
        updated = replace(
            current,
            label=label,
            reason=reason,
            decision_source=TaggingDecisionSource.MANUAL_MANAGEMENT,
            representative_asin=None,
            product_context_source=None,
            revision=current.revision + 1,
            is_active=True,
            invalidated_at=None,
        )
        self.records_by_id[updated.id] = updated
        return updated

    async def update_manual(self, *, cache_id, revision, label, reason):
        current = self.records_by_id.get(cache_id)
        if current is None or not current.is_active or current.revision != revision:
            return None
        updated = replace(
            current,
            label=label,
            reason=reason,
            decision_source=TaggingDecisionSource.MANUAL_MANAGEMENT,
            revision=current.revision + 1,
        )
        self.records_by_id[updated.id] = updated
        return updated

    async def deactivate_current(self, *, cache_id, revision):
        current = self.records_by_id.get(cache_id)
        if current is None or not current.is_active or current.revision != revision:
            return None
        updated = replace(
            current,
            revision=current.revision + 1,
            is_active=False,
            invalidated_at=datetime.now(),
        )
        self.records_by_id[updated.id] = updated
        return updated

    async def append_decision(self, record):
        self.decisions.append(record)
        return record

    async def append_management_audit(self, record):
        self.audits.append(record)
        return record


def _label_record(*, category=TaggingCategoryKey.PILLOW, word="weighted"):
    return TaggingLabelCacheRecord(
        id=uuid4(), category_key=category, word=word, taxonomy_version=1,
        label=TagLabel.ATTRIBUTE, reason="原始人工原因",
        decision_source=TaggingDecisionSource.HUMAN_REVIEW,
        representative_asin="B0TEST0001", product_context_source="TEST",
    )


class TaggingIdentityMigrationTest(IsolatedAsyncioTestCase):
    """身份变更绝不覆盖目标缓存，且旧 identity 持续阻断迟到 AI。"""

    async def asyncSetUp(self):
        self.original = _label_record()
        _FakeTaggingRepository.reset(self.original)
        self.repository_patch = patch(
            "services.tagging_label_management_service.TaggingLabelManagementRepository",
            _FakeTaggingRepository,
        )
        self.repository_patch.start()
        self.service = TaggingLabelManagementService(_FakeDatabase())

    async def asyncTearDown(self):
        self.repository_patch.stop()

    async def test_identity_change_retires_old_cache_and_keeps_bidirectional_audit(self):
        updated = await self.service.update_manual_label(
            cache_id=self.original.id, expected_revision=1,
            category_key="stuffed_animals", word="plush", label=TagLabel.CORE_TERM.value,
            reason="人工更正品类和标准词",
        )

        old = _FakeTaggingRepository.records_by_id[self.original.id]
        self.assertFalse(old.is_active)
        self.assertEqual(old.decision_source, TaggingDecisionSource.HUMAN_REVIEW)
        self.assertNotEqual(updated.id, self.original.id)
        self.assertTrue(updated.is_active)
        self.assertEqual(updated.category_key, TaggingCategoryKey.STUFFED_ANIMALS)
        self.assertEqual(updated.word, "plush")
        self.assertEqual(len(_FakeTaggingRepository.decisions), 1)
        self.assertEqual(len(_FakeTaggingRepository.audits), 2)
        old_audit, target_audit = _FakeTaggingRepository.audits
        self.assertEqual(
            old_audit.after_snapshot["identityChange"]["migratedToCacheId"], str(updated.id)
        )
        self.assertEqual(
            target_audit.after_snapshot["identityChange"]["migratedFromCacheId"], str(self.original.id)
        )

    async def test_active_target_identity_is_never_silently_overwritten(self):
        target = _label_record(category=TaggingCategoryKey.STUFFED_ANIMALS, word="plush")
        _FakeTaggingRepository.records_by_id[target.id] = target

        with self.assertRaises(TaggingLabelIdentityExistsError):
            await self.service.update_manual_label(
                cache_id=self.original.id, expected_revision=1,
                category_key="stuffed_animals", word="plush", label=TagLabel.CORE_TERM.value,
                reason="冲突测试",
            )
        self.assertTrue(_FakeTaggingRepository.records_by_id[self.original.id].is_active)
        self.assertEqual(_FakeTaggingRepository.records_by_id[target.id], target)


class ManagementDisplayTest(TestCase):
    """UI 中文化、整行选择和历史 ASIN 显示只改变展示边界。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_normalization_type_display_and_source_columns(self):
        record = NormalizationActiveRuleRecord(
            id=uuid4(), rule_type=NormalizationRuleType.PHRASE,
            variants=("weighted animals",), canonical="weighted animal",
            source_candidate_id="hunter:not-a-user-visible-source",
            source_reason_types=("HISTORICAL_IMPORT",), supersedes_rule_id=None,
            revision=9, is_active=True, category_key=TaggingCategoryKey.PILLOW,
            source_asins=("B0ABCDEF12", "B0ABCDEF34"),
        )
        model = NormalizationActiveRuleTableModel()
        model.set_records([record])
        headers = [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())]
        self.assertEqual(headers, ["品类", "类型", "原词", "标准词", "来源", "更新时间"])
        self.assertEqual(model.data(model.index(0, 1)), "短语")
        self.assertEqual(model.data(model.index(0, 4)), "B0ABCDEF12（+1）")
        self.assertEqual(model.data(model.index(0, 4), Qt.ItemDataRole.ToolTipRole), "B0ABCDEF12\nB0ABCDEF34")
        self.assertEqual(normalization_rule_type_display_name("WORD"), "单词")
        self.assertEqual(normalization_rule_type_display_name("FUTURE"), "FUTURE")

    def test_management_tables_use_row_selection_with_soft_style(self):
        tagging = TaggingLabelManagementDialog()
        normalization = NormalizationManagementDialog()
        try:
            for table in (tagging.ui.labelsTableView, normalization.ui.rulesTableView):
                self.assertEqual(table.selectionBehavior(), QAbstractItemView.SelectionBehavior.SelectRows)
                self.assertEqual(table.selectionMode(), QAbstractItemView.SelectionMode.SingleSelection)
                self.assertIn("QTableView::item:selected", table.styleSheet())
                self.assertIn("#e2f1ef", table.styleSheet())
            self.assertEqual(normalization.ui.ruleTypeComboBox.itemText(1), "单词")
            self.assertEqual(normalization.ui.ruleTypeComboBox.itemData(1), "WORD")
            self.assertEqual(normalization.ui.ruleTypeComboBox.itemText(2), "短语")
            self.assertEqual(normalization.ui.ruleTypeComboBox.itemData(2), "PHRASE")
        finally:
            tagging.close()
            normalization.close()
