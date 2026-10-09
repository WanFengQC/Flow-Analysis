"""AI 标签分歧人工审核的运行时状态与非阻塞保存测试。"""

import os
import unittest
from concurrent.futures import Future
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from controllers.main_controller import MainController
from models.tagging import TagLabel, TaggingDecision
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    TaggingPipelineResult,
    TaggingPipelineRun,
    TaggingPipelineStatus,
    TaggingReviewItem,
    TaggingReviewStatus,
)
from models.tagging_provider import ProviderTaggingResult
from views.main_window import MainWindow


def _provider_result(provider_id: str, label: TagLabel, reason: str):
    """构造已经通过协议校验的单 Provider 审核展示结果。"""

    return ProviderTaggingResult(
        provider_id=provider_id,
        model_id=f"{provider_id}-model",
        decisions=(
            TaggingDecision(
                item_id="pillow:1:weighted",
                label=label,
                reason=reason,
            ),
        ),
        latency_ms=1,
        attempt_count=1,
    )


def _review_item(item_id: str = "pillow:1:weighted:202608"):
    """构造一条真实形状的三方分歧运行时快照。"""

    return TaggingReviewItem(
        item_id=item_id,
        month="202608",
        word="weighted",
        category_key=TaggingCategoryKey.PILLOW,
        taxonomy_version=1,
        top_phrases=("weighted pillow",),
        representative_asin="B0TEST0001",
        product_context_source="PRODUCT_KNOWLEDGE_BASE",
        product_context={"title": "Weighted Pillow"},
        provider_results={
            "openai": _provider_result("openai", TagLabel.ATTRIBUTE, "GPT 理由"),
            "anthropic": _provider_result("anthropic", TagLabel.ATTRIBUTE, "Claude 理由"),
            "google": _provider_result("google", TagLabel.SPECIFICATION, "Gemini 理由"),
        },
    )


def _pipeline_run():
    """构造包括分歧、缓存命中和不完整项的最小 Pipeline 结果。"""

    item = _review_item()
    return TaggingPipelineRun(
        results=(
            TaggingPipelineResult(
                month=item.month,
                word=item.word,
                category_key=item.category_key,
                taxonomy_version=1,
                status=TaggingPipelineStatus.DISAGREEMENT,
                label=None,
                reason=None,
                decision_source=None,
            ),
            TaggingPipelineResult(
                month="202608",
                word="soft",
                category_key=TaggingCategoryKey.PILLOW,
                taxonomy_version=1,
                status=TaggingPipelineStatus.CACHE_HIT,
                label=TagLabel.ATTRIBUTE,
                reason="缓存理由",
                decision_source=TaggingDecisionSource.AI_CONSENSUS,
            ),
            TaggingPipelineResult(
                month="202608",
                word="failed",
                category_key=TaggingCategoryKey.PILLOW,
                taxonomy_version=1,
                status=TaggingPipelineStatus.INCOMPLETE,
                label=None,
                reason=None,
                decision_source=None,
            ),
        ),
        review_items=(item,),
    )


class _AsyncRuntime:
    """测试中同步完成协程，但仍以 Future 接口模拟 AsyncRuntime 回调。"""

    def submit(self, coroutine):
        future = Future()
        try:
            import asyncio

            future.set_result(asyncio.run(coroutine))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _Runtime:
    """避免测试连接真实 PostgreSQL；人工服务由测试显式注入。"""

    def __init__(self):
        self.async_runtime = _AsyncRuntime()


class _HumanReviewService:
    """记录人工写库参数，模拟成功或失败的正式事务入口。"""

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls = []

    async def record_human_review(self, **kwargs):
        self.calls.append(kwargs)
        if self.should_fail:
            raise RuntimeError("数据库不可用")
        return TaggingLabelCacheRecord(
            id=uuid4(),
            category_key=TaggingCategoryKey(kwargs["category_key"]),
            word=kwargs["word"],
            taxonomy_version=kwargs["taxonomy_version"],
            label=kwargs["label"],
            reason=kwargs["reason"],
            decision_source=TaggingDecisionSource.HUMAN_REVIEW,
            representative_asin=kwargs["representative_asin"],
            product_context_source=kwargs["product_context_source"],
        )


class TaggingReviewDialogTest(unittest.TestCase):
    """验证审核窗口不保存中间选择，且只有事务成功才结束一条审核项。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.controller = MainController(self.window, _Runtime())
        self.controller.tagging_service = _HumanReviewService()
        self.generation = self.controller.begin_tagging_review_generation()
        self.controller.accept_tagging_pipeline_run(self.generation, _pipeline_run())
        self.dialog = self.window._tagging_review_dialog

    def tearDown(self):
        self.controller._clear_tagging_review_state()
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_only_disagreement_opens_modeless_review_dialog(self):
        """CACHE_HIT 与 INCOMPLETE 不能混进人工审核工作集。"""

        self.assertFalse(self.dialog.isModal())
        self.assertTrue(self.dialog.isVisible())
        self.assertEqual(len(self.controller._tagging_review_items), 1)
        self.assertEqual(self.dialog.ui.taggingReviewListWidget.count(), 1)

    def test_provider_results_and_adoption_only_fill_form(self):
        """三家结果可读，采用 GPT 只回填表单且不会写缓存。"""

        self.assertEqual(
            self.dialog.ui.openaiProviderLabelValue.text(),
            TagLabel.ATTRIBUTE.value,
        )
        self.assertEqual(
            self.dialog.ui.googleProviderReasonTextEdit.toPlainText(),
            "Gemini 理由",
        )
        self.dialog.ui.adoptOpenaiButton.click()
        self.assertEqual(
            self.dialog.ui.taggingHumanLabelComboBox.currentData(),
            TagLabel.ATTRIBUTE.value,
        )
        self.assertEqual(
            self.dialog.ui.taggingHumanReasonTextEdit.toPlainText(),
            "GPT 理由",
        )
        self.assertEqual(self.controller.tagging_service.calls, [])

    def test_human_can_choose_different_label_and_success_updates_runtime(self):
        """人工第四种选择必须成功写库、追加审计入口并即时回填结果。"""

        item = next(iter(self.controller._tagging_review_items.values()))
        combo = self.dialog.ui.taggingHumanLabelComboBox
        combo.setCurrentIndex(combo.findData(TagLabel.SCENARIO.value))
        self.dialog.ui.taggingHumanReasonTextEdit.setPlainText("人工判断为使用场景")
        self.dialog.ui.confirmTaggingReviewButton.click()
        self.application.processEvents()

        self.assertEqual(len(self.controller.tagging_service.calls), 1)
        self.assertEqual(self.controller._tagging_review_items, {})
        updated = self.controller._tagging_result_index[(item.month, item.word)]
        self.assertEqual(updated.status, TaggingPipelineStatus.HUMAN_REVIEW)
        self.assertEqual(updated.label, TagLabel.SCENARIO)
        self.assertEqual(
            updated.decision_source,
            TaggingDecisionSource.HUMAN_REVIEW,
        )

    def test_database_failure_keeps_pending_item(self):
        """写库失败不能从 Dialog 或 Controller 的工作集中移除。"""

        self.controller.tagging_service = _HumanReviewService(should_fail=True)
        combo = self.dialog.ui.taggingHumanLabelComboBox
        combo.setCurrentIndex(combo.findData(TagLabel.ATTRIBUTE.value))
        self.dialog.ui.taggingHumanReasonTextEdit.setPlainText("人工原因")
        self.dialog.ui.confirmTaggingReviewButton.click()
        self.application.processEvents()

        item = next(iter(self.controller._tagging_review_items.values()))
        self.assertEqual(item.status, TaggingReviewStatus.PENDING)
        self.assertIn("保存失败", self.window.statusBar().currentMessage())

    def test_x_confirmation_controls_runtime_destruction(self):
        """取消关闭保持数据，确认关闭只清理当前运行时审核项。"""

        with patch.object(
            self.window,
            "confirm_discard_tagging_review",
            return_value=False,
        ):
            self.controller._request_close_tagging_review()
        self.assertEqual(len(self.controller._tagging_review_items), 1)

        with patch.object(
            self.window,
            "confirm_discard_tagging_review",
            return_value=True,
        ):
            self.controller._request_close_tagging_review()
        self.assertEqual(self.controller._tagging_review_items, {})
        self.assertFalse(self.dialog.isVisible())

    def test_unselected_label_does_not_emit_save(self):
        """默认“未选择”不能形成正式 HUMAN_REVIEW。"""

        self.dialog.ui.taggingHumanReasonTextEdit.setPlainText("人工原因")
        with patch.object(QMessageBox, "warning") as warning:
            self.dialog.ui.confirmTaggingReviewButton.click()
        self.assertTrue(warning.called)
        self.assertEqual(self.controller.tagging_service.calls, [])


if __name__ == "__main__":
    unittest.main()
