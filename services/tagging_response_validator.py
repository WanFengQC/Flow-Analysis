"""AI Tagging 固定 JSON 响应的严格校验器。"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from models.tagging import TagLabel, TaggingDecision, TaggingInput


class TaggingResponseValidationError(ValueError):
    """Provider 输出不满足当前固定协议时抛出的可诊断业务异常。"""


class TaggingResponseValidator:
    """将未可信的 AI 原始 JSON 转换为与原输入一一对应的决策列表。"""

    _TOP_LEVEL_FIELDS = frozenset({"rows"})
    _ROW_FIELDS = frozenset({"itemId", "label", "reason"})

    def validate(
        self,
        raw_response: str,
        inputs: Sequence[TaggingInput],
    ) -> list[TaggingDecision]:
        """严格校验所有字段，并按请求输入顺序恢复合法决策。"""

        expected_item_ids = self._expected_item_ids(inputs)
        payload = self._parse_json_object(raw_response)
        self._validate_exact_fields(
            payload,
            self._TOP_LEVEL_FIELDS,
            "顶层",
        )

        rows = payload["rows"]
        if not isinstance(rows, list):
            raise TaggingResponseValidationError("rows 必须是数组")

        decisions_by_id: dict[str, TaggingDecision] = {}
        for row in rows:
            decision = self._parse_row(row, expected_item_ids)
            if decision.item_id in decisions_by_id:
                raise TaggingResponseValidationError("itemId 不允许重复")
            decisions_by_id[decision.item_id] = decision

        if set(decisions_by_id) != set(expected_item_ids):
            raise TaggingResponseValidationError("AI 输出存在遗漏或未知 itemId")
        if len(rows) != len(expected_item_ids):
            raise TaggingResponseValidationError("输出数量必须与输入一致")

        return [decisions_by_id[item_id] for item_id in expected_item_ids]

    @staticmethod
    def _expected_item_ids(inputs: Sequence[TaggingInput]) -> list[str]:
        """确认程序自身输入存在唯一 identity，避免错误归因给 AI。"""

        if not inputs:
            raise TaggingResponseValidationError("TaggingInput 不能为空")

        item_ids = [tagging_input.item_id for tagging_input in inputs]
        if len(item_ids) != len(set(item_ids)):
            raise TaggingResponseValidationError("请求输入存在重复 itemId")
        return item_ids

    @staticmethod
    def _parse_json_object(raw_response: str) -> Mapping[str, Any]:
        """接受纯 JSON，或唯一完整 Markdown JSON 围栏内的 JSON 对象。"""

        if not isinstance(raw_response, str):
            raise TaggingResponseValidationError("AI 响应必须是 JSON 字符串")
        normalized_response = TaggingResponseValidator._unwrap_json_code_fence(
            raw_response
        )
        try:
            payload = json.loads(normalized_response)
        except json.JSONDecodeError as exc:
            raise TaggingResponseValidationError("AI 响应不是合法 JSON") from exc
        if not isinstance(payload, Mapping):
            raise TaggingResponseValidationError("AI 响应顶层必须是对象")
        return payload

    @staticmethod
    def _unwrap_json_code_fence(raw_response: str) -> str:
        """仅移除唯一完整的 json 代码围栏，不接受夹带解释文字的输出。"""

        stripped = raw_response.strip()
        lines = stripped.splitlines()
        if len(lines) < 3:
            return raw_response

        opening_fence = lines[0].strip().lower()
        closing_fence = lines[-1].strip()
        if opening_fence not in {"```", "```json"} or closing_fence != "```":
            return raw_response

        # 只允许围栏内部承载 JSON；围栏前后任何文字都会导致上方条件不成立。
        return "\n".join(lines[1:-1]).strip()

    @classmethod
    def _parse_row(
        cls,
        row: object,
        expected_item_ids: Sequence[str],
    ) -> TaggingDecision:
        """校验单行结构、合法 label 与非空短理由。"""

        if not isinstance(row, Mapping):
            raise TaggingResponseValidationError("rows 中每项必须是对象")
        cls._validate_exact_fields(row, cls._ROW_FIELDS, "rows 项")

        item_id = row["itemId"]
        label = row["label"]
        reason = row["reason"]
        if not isinstance(item_id, str) or item_id not in expected_item_ids:
            raise TaggingResponseValidationError("itemId 不属于当前请求")
        if not isinstance(label, str):
            raise TaggingResponseValidationError("label 必须是字符串")
        if not isinstance(reason, str) or not reason.strip():
            raise TaggingResponseValidationError("reason 必须是非空字符串")

        try:
            tag_label = TagLabel(label)
        except ValueError as exc:
            raise TaggingResponseValidationError("label 不属于 TagLabel") from exc
        return TaggingDecision(
            item_id=item_id,
            label=tag_label,
            reason=reason,
        )

    @staticmethod
    def _validate_exact_fields(
        payload: Mapping[str, Any],
        expected_fields: frozenset[str],
        scope: str,
    ) -> None:
        """拒绝缺字段和额外字段，避免协议悄然漂移。"""

        if set(payload) != expected_fields:
            raise TaggingResponseValidationError(
                f"{scope}字段必须严格为：{', '.join(sorted(expected_fields))}"
            )
