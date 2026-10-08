"""AI Tagging 响应校验器测试。"""

import json
import unittest

from models.tagging import TagLabel, TaggingInput
from services.tagging_response_validator import (
    TaggingResponseValidationError,
    TaggingResponseValidator,
)


def _inputs() -> list[TaggingInput]:
    """构造两项输入，验证响应乱序也能恢复原始任务顺序。"""

    base_fields = {
        "month": "202608",
        "category": "Stuffed Animals",
        "top_phrases": ("weighted stuffed animal",),
        "product_context_source": "PRODUCT_KNOWLEDGE_BASE",
        "representative_asin": "B0DSHYXD4G",
        "product_context": {"project": "M"},
    }
    return [
        TaggingInput(
            item_id="202608::weighted",
            word="weighted",
            **base_fields,
        ),
        TaggingInput(
            item_id="202608::leopard",
            word="leopard",
            **base_fields,
        ),
    ]


class TaggingResponseValidatorTest(unittest.TestCase):
    """验证 AI 输出只能完整且精确地对应当前请求。"""

    def setUp(self) -> None:
        """每个测试拥有独立且固定的请求输入。"""

        self.validator = TaggingResponseValidator()
        self.inputs = _inputs()

    def test_valid_out_of_order_rows_restore_input_order(self):
        """允许 Provider 乱序返回，但不能改变程序的 item 顺序。"""

        raw_response = json.dumps(
            {
                "rows": [
                    {
                        "itemId": "202608::leopard",
                        "label": "2外形",
                        "reason": "表示动物造型。",
                    },
                    {
                        "itemId": "202608::weighted",
                        "label": "3属性",
                        "reason": "表示加重功能。",
                    },
                ]
            },
            ensure_ascii=False,
        )

        decisions = self.validator.validate(raw_response, self.inputs)

        self.assertEqual(
            [decision.item_id for decision in decisions],
            ["202608::weighted", "202608::leopard"],
        )
        self.assertEqual(decisions[0].label, TagLabel.ATTRIBUTE)
        self.assertEqual(decisions[1].label, TagLabel.APPEARANCE)

    def test_validator_accepts_only_complete_json_code_fence(self):
        """兼容 Claude 的完整 JSON 围栏，内部协议仍必须严格校验。"""

        raw_json = json.dumps(
            {
                "rows": [
                    {
                        "itemId": "202608::weighted",
                        "label": "3属性",
                        "reason": "表示加重功能。",
                    },
                    {
                        "itemId": "202608::leopard",
                        "label": "2外形",
                        "reason": "表示动物造型。",
                    },
                ]
            },
            ensure_ascii=False,
        )

        decisions = self.validator.validate(
            f"```json\n{raw_json}\n```",
            self.inputs,
        )

        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0].label, TagLabel.ATTRIBUTE)

    def test_validator_rejects_prose_outside_json_code_fence(self):
        """围栏之外的解释文字不可被兼容层吞掉，避免协议悄然放宽。"""

        raw_response = "说明如下：\n```json\n{\"rows\": []}\n```"
        with self.assertRaises(TaggingResponseValidationError):
            self.validator.validate(raw_response, self.inputs)

    def test_validator_rejects_invalid_protocol_variants(self):
        """非法 JSON、协议漂移、漏项、重复项与非法标签必须全部失败。"""

        valid_rows = [
            {
                "itemId": "202608::weighted",
                "label": "3属性",
                "reason": "表示加重功能。",
            },
            {
                "itemId": "202608::leopard",
                "label": "2外形",
                "reason": "表示动物造型。",
            },
        ]
        cases = {
            "invalid_json": "not-json",
            "top_level_extra": {"rows": valid_rows, "confidence": 0.9},
            "rows_not_array": {"rows": {}},
            "row_extra": {
                "rows": [
                    {**valid_rows[0], "confidence": 0.9},
                    valid_rows[1],
                ]
            },
            "unknown_item": {
                "rows": [
                    {**valid_rows[0], "itemId": "202608::unknown"},
                    valid_rows[1],
                ]
            },
            "duplicate_item": {
                "rows": [valid_rows[0], valid_rows[0]],
            },
            "missing_item": {"rows": [valid_rows[0]]},
            "invalid_label": {
                "rows": [
                    {**valid_rows[0], "label": "其他"},
                    valid_rows[1],
                ]
            },
            "blank_reason": {
                "rows": [
                    {**valid_rows[0], "reason": "   "},
                    valid_rows[1],
                ]
            },
        }

        for case_name, payload in cases.items():
            with self.subTest(case_name=case_name):
                raw_response = (
                    payload
                    if isinstance(payload, str)
                    else json.dumps(payload, ensure_ascii=False)
                )
                with self.assertRaises(TaggingResponseValidationError):
                    self.validator.validate(raw_response, self.inputs)

    def test_validator_rejects_duplicate_program_input_identity(self):
        """重复 itemId 是调用方输入错误，不能交给 AI 输出掩盖。"""

        duplicated_inputs = [self.inputs[0], self.inputs[0]]
        with self.assertRaises(TaggingResponseValidationError):
            self.validator.validate('{"rows": []}', duplicated_inputs)


if __name__ == "__main__":
    unittest.main()
