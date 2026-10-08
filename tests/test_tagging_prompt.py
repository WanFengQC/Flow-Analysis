"""AI Tagging 固定标签与 Prompt 构造测试。"""

import json
import unittest

from models.tagging import TagLabel, TaggingInput
from services.tagging_prompt import (
    TAGGING_RESPONSE_SCHEMA,
    TaggingPromptBuilder,
)


def _input(
    item_id: str = "202608::weighted",
    word: str = "weighted",
) -> TaggingInput:
    """构造包含完整产品背景的稳定测试输入。"""

    return TaggingInput(
        item_id=item_id,
        month="202608",
        word=word,
        category="Stuffed Animals",
        top_phrases=("weighted stuffed animal", "weighted plush toy"),
        product_context_source="PRODUCT_KNOWLEDGE_BASE",
        representative_asin="B0DSHYXD4G",
        product_context={
            "project": "M",
            "variation": {"Color": "Leopard"},
        },
    )


class TaggingPromptTest(unittest.TestCase):
    """验证三个 Provider 将共享的固定输入协议。"""

    def test_tag_label_is_exactly_the_nine_business_labels(self):
        """标签集合不得因 Prompt 或代码演进而静默扩张。"""

        self.assertEqual(
            [label.value for label in TagLabel],
            [
                "1核心词",
                "2外形",
                "3属性",
                "4痛点",
                "5规格",
                "6受众",
                "7场景",
                "8品牌",
                "无效词",
            ],
        )

    def test_system_prompt_contains_context_priority_and_boundaries(self):
        """Prompt 必须明确业务语境优先于字面翻译。"""

        system_prompt = TaggingPromptBuilder.build_system_prompt()

        for label in TagLabel:
            self.assertIn(label.value, system_prompt)
        self.assertIn("topPhrases", system_prompt)
        self.assertIn("ProductContext", system_prompt)
        self.assertIn("weighted", system_prompt)
        self.assertIn("3.3 lb", system_prompt)
        self.assertIn("animal", system_prompt)
        self.assertIn("非英语", system_prompt)
        self.assertIn("只输出一个合法 JSON 对象", system_prompt)

    def test_user_prompt_uses_one_context_and_row_reference(self):
        """单次请求内 Context 单独发送，行仅保存稳定的 contextId 引用。"""

        prompt = TaggingPromptBuilder.build_user_prompt([_input()])
        payload = json.loads(prompt.split("\n", maxsplit=1)[1])

        self.assertEqual(set(payload), {"contexts", "rows"})
        self.assertEqual(
            payload["contexts"],
            {
                "ctx_1": {
                    "productContext": {
                        "project": "M",
                        "variation": {"Color": "Leopard"},
                    }
                }
            },
        )
        self.assertEqual(
            set(payload["rows"][0]),
            {
                "itemId",
                "month",
                "word",
                "category",
                "topPhrases",
                "productContextSource",
                "representativeAsin",
                "contextId",
            },
        )
        self.assertEqual(payload["rows"][0]["contextId"], "ctx_1")
        self.assertNotIn("productContext", payload["rows"][0])
        self.assertNotIn("sourceAsinStats", prompt)

    def test_same_context_is_serialized_once_per_request(self):
        """100 个同背景 row 只能引用同一份本请求内的完整 Context。"""

        prompt = TaggingPromptBuilder.build_user_prompt(
            [_input(f"202608::word-{index}", f"word-{index}") for index in range(100)]
        )
        payload = json.loads(prompt.split("\n", maxsplit=1)[1])

        self.assertEqual(len(payload["contexts"]), 1)
        self.assertEqual(
            {row["contextId"] for row in payload["rows"]},
            {"ctx_1"},
        )

    def test_split_requests_each_include_a_self_contained_context(self):
        """不同 child request 必须分别重建 Context，不能共享前一请求状态。"""

        inputs = [_input(f"202608::word-{index}", f"word-{index}") for index in range(100)]
        first_payload = json.loads(
            TaggingPromptBuilder.build_user_prompt(inputs[:50]).split("\n", 1)[1]
        )
        second_payload = json.loads(
            TaggingPromptBuilder.build_user_prompt(inputs[50:]).split("\n", 1)[1]
        )

        for payload in (first_payload, second_payload):
            self.assertEqual(len(payload["contexts"]), 1)
            self.assertEqual(
                payload["contexts"]["ctx_1"]["productContext"]["project"],
                "M",
            )
            self.assertTrue(
                all(row["contextId"] == "ctx_1" for row in payload["rows"])
            )

    def test_input_context_is_a_snapshot(self):
        """外部修改 Context 不得改变已经构建的输入。"""

        context = {"variation": {"Color": "Leopard"}}
        tagging_input = TaggingInput(
            item_id="202608::leopard",
            month="202608",
            word="leopard",
            category="Stuffed Animals",
            top_phrases=("leopard plush",),
            product_context_source="PRODUCT_KNOWLEDGE_BASE",
            representative_asin="B0DSHYXD4G",
            product_context=context,
        )
        context["variation"]["Color"] = "Duck"

        self.assertEqual(
            tagging_input.to_dict()["productContext"]["variation"]["Color"],
            "Leopard",
        )

    def test_response_schema_is_closed_and_uses_tag_label_values(self):
        """Schema 也必须拒绝 confidence 等未授权字段。"""

        row_schema = TAGGING_RESPONSE_SCHEMA["properties"]["rows"]["items"]
        self.assertFalse(TAGGING_RESPONSE_SCHEMA["additionalProperties"])
        self.assertFalse(row_schema["additionalProperties"])
        self.assertEqual(
            row_schema["properties"]["label"]["enum"],
            [label.value for label in TagLabel],
        )
        self.assertNotIn("confidence", row_schema["properties"])


if __name__ == "__main__":
    unittest.main()
