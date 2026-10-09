"""归一化/标签管理新增边界的无数据库回归测试。"""

from unittest import IsolatedAsyncioTestCase, TestCase

from models.tagging import TagLabel
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
)
from services.normalization_management_service import (
    NormalizationManagementService,
    NormalizationRuleValidationError,
)
from services.tagging_service import TaggingService


class _NoWriteRepository:
    """模拟人工管理已抢先创建/删除 identity 时的条件写入冲突。"""

    def __init__(self) -> None:
        self.audit = []

    async def insert_if_cache_miss(self, _record, **_kwargs):
        return None

    async def append_decision(self, record):
        self.audit.append(record)
        return record


class TaggingManagementGuardTest(IsolatedAsyncioTestCase):
    """迟到 AI 无法覆盖或复活人工管理 identity。"""

    async def test_late_write_conflict_does_not_append_ai_audit(self):
        repository = _NoWriteRepository()
        service = TaggingService(
            database=None,
            ai_service=object(),
            product_knowledge_service=None,
            repository_factory=lambda _connection: repository,
        )

        saved = await service._persist_formal_decision(
            category_key=TaggingCategoryKey.STUFFED_ANIMALS,
            word="weighted",
            taxonomy_version=1,
            label=TagLabel.ATTRIBUTE,
            reason="模型共识。",
            decision_source=TaggingDecisionSource.AI_CONSENSUS,
            representative_asin="B0TEST0001",
            product_context_source="PRODUCT_KNOWLEDGE_BASE",
            provider_results={},
        )

        self.assertIsNone(saved)
        self.assertEqual(repository.audit, [])


class NormalizationManagementGuardTest(TestCase):
    """管理新增必须复用既有全局冲突校验，而不是只验证单条输入。"""

    def test_variant_conflict_is_blocked_before_active_rule_write(self):
        service = NormalizationManagementService(database=None)  # type: ignore[arg-type]
        result = service._build_rules(
            [
                {
                    "id": "active:one",
                    "decision": "APPROVED",
                    "variants": ["animals"],
                    "approvedCanonical": "animal",
                    "reasonTypes": [],
                },
                {
                    "id": "manual:new",
                    "decision": "APPROVED",
                    "variants": ["animals"],
                    "approvedCanonical": "creature",
                    "reasonTypes": ["MANUAL_MANAGEMENT"],
                },
            ]
        )

        with self.assertRaises(NormalizationRuleValidationError):
            service._require_valid(result)
