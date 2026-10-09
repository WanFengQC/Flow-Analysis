"""唯一 Final Analysis Dataset 的构造与标签状态映射测试。"""

import unittest

from models.tagging import TagLabel
from models.tagging_label import (
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingPipelineResult,
    TaggingPipelineStatus,
)
from services.final_analysis_service import FinalAnalysisService


def _tagging_result(
    word: str,
    status: TaggingPipelineStatus,
    label: TagLabel | None = None,
) -> TaggingPipelineResult:
    """构造一个最小标签结果，避免测试访问外部 Provider 或数据库。"""

    return TaggingPipelineResult(
        month="202608",
        word=word,
        category_key=TaggingCategoryKey.STUFFED_ANIMALS,
        taxonomy_version=1,
        status=status,
        label=label,
        reason="正式标签原因" if label is not None else None,
        decision_source=(
            TaggingDecisionSource.AI_CONSENSUS
            if label is not None
            else None
        ),
    )


class FinalAnalysisServiceTest(unittest.TestCase):
    """验证最终行只来自正式词结果与已得到的 TaggingResult。"""

    def setUp(self) -> None:
        self.service = FinalAnalysisService()
        self.word_results = {
            "202608": [
                {
                    "word": "cache",
                    "frequency": 2,
                    "weight": 12.5,
                    "total": 3.0,
                    "ratio": 0.25,
                    "naturalRatio": 0.1,
                    "adRatio": 0.15,
                    "topPhrases": ["cache plush", "cache toy"],
                    "matchingKeywordCount": 2,
                    "customMetric": 9,
                    "sourceAsinStats": {"B0TEST": {"exposure": 12.5}},
                },
                {"word": "consensus", "frequency": 1},
                {"word": "human", "frequency": 1},
                {"word": "disagreement", "frequency": 1},
                {"word": "incomplete", "frequency": 1},
            ]
        }
        self.tagging_results = {
            ("202608", "cache"): _tagging_result(
                "cache",
                TaggingPipelineStatus.CACHE_HIT,
                TagLabel.CORE_TERM,
            ),
            ("202608", "consensus"): _tagging_result(
                "consensus",
                TaggingPipelineStatus.AI_CONSENSUS,
                TagLabel.ATTRIBUTE,
            ),
            ("202608", "human"): _tagging_result(
                "human",
                TaggingPipelineStatus.HUMAN_REVIEW,
                TagLabel.AUDIENCE,
            ),
            ("202608", "disagreement"): _tagging_result(
                "disagreement",
                TaggingPipelineStatus.DISAGREEMENT,
            ),
            ("202608", "incomplete"): _tagging_result(
                "incomplete",
                TaggingPipelineStatus.INCOMPLETE,
            ),
        }

    def test_build_preserves_formal_word_order_and_business_fields(self):
        """最终行沿用正式 Word Result 顺序，不重算并保留已有业务字段。"""

        rows = self.service.build_final_analysis_rows(
            self.word_results,
            self.tagging_results,
        )

        self.assertEqual(
            [row["word"] for row in rows],
            ["cache", "consensus", "human", "disagreement", "incomplete"],
        )
        self.assertEqual(rows[0]["month"], "202608")
        self.assertEqual(rows[0]["weight"], 12.5)
        self.assertEqual(rows[0]["topPhrases"], ["cache plush", "cache toy"])
        self.assertEqual(rows[0]["customMetric"], 9)
        self.assertNotIn("sourceAsinStats", rows[0])

    def test_tagging_statuses_map_to_final_label_fields(self):
        """五种真实 Pipeline 状态必须映射为最终标签、来源与原因。"""

        rows_by_word = {
            row["word"]: row
            for row in self.service.build_final_analysis_rows(
                self.word_results,
                self.tagging_results,
            )
        }

        self.assertEqual(rows_by_word["cache"]["label"], "1核心词")
        self.assertEqual(rows_by_word["cache"]["labelSource"], "历史缓存")
        self.assertEqual(rows_by_word["consensus"]["labelSource"], "AI共识")
        self.assertEqual(rows_by_word["human"]["labelSource"], "人工审核")
        self.assertIsNone(rows_by_word["disagreement"]["label"])
        self.assertEqual(rows_by_word["disagreement"]["labelSource"], "待人工审核")
        self.assertIsNone(rows_by_word["incomplete"]["label"])
        self.assertEqual(rows_by_word["incomplete"]["labelSource"], "AI失败")

    def test_partial_tagging_never_drops_formal_word_rows(self):
        """部分 AI 未完成时，所有正式词仍必须保留为最终行。"""

        rows = self.service.build_final_analysis_rows(
            self.word_results,
            {
                ("202608", "incomplete"): self.tagging_results[
                    ("202608", "incomplete")
                ]
            },
        )

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["labelSource"], "未完成")
        self.assertEqual(rows[-1]["labelSource"], "AI失败")

    def test_human_review_can_update_existing_final_row_without_rebuild(self):
        """人工审核保存后只更新对应标签字段，不影响其他最终行的顺序。"""

        rows = self.service.build_final_analysis_rows(
            self.word_results,
            self.tagging_results,
        )
        updated = self.service.update_tagging_fields(
            rows,
            _tagging_result(
                "disagreement",
                TaggingPipelineStatus.HUMAN_REVIEW,
                TagLabel.SCENARIO,
            ),
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["label"], "7场景")
        self.assertEqual(updated["labelSource"], "人工审核")
        self.assertEqual([row["word"] for row in rows][3], "disagreement")


if __name__ == "__main__":
    unittest.main()
