"""Flow Analysis 专属 AI Tagging Prompt 与固定 JSON Schema。"""

import json
from collections.abc import Sequence
from typing import Any

from models.tagging import TagLabel, TaggingInput


TAGGING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["itemId", "label", "reason"],
                "properties": {
                    "itemId": {"type": "string", "minLength": 1},
                    "label": {
                        "type": "string",
                        "enum": [label.value for label in TagLabel],
                    },
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}


class TaggingPromptBuilder:
    """只构造统一 Prompt，不执行 Provider 调用或解析网络响应。"""

    @staticmethod
    def build_system_prompt() -> str:
        """返回所有 Provider 共用的固定系统指令。"""

        return """你是 Flow Analysis 的电商搜索词业务角色标注助手。

任务：根据每个 word 的真实 Amazon 美国站搜索短语语境、唯一 ProductContext 与当前商品品类，给每个输入项选择且仅选择一个业务标签。你不是翻译助手，不能只按字典字面含义分类。

判断优先级必须严格遵守：
1. topPhrases 中的真实搜索语境；
2. ProductContext；
3. 当前商品品类 category；
4. Amazon 美国站电商语境；
5. word 的字面含义。

输入结构说明：
- contexts 是本次请求内完整 ProductContext 的字典；每个 rows 项的 contextId
  都引用同一份请求中的 contexts[contextId].productContext。
- 必须根据该引用背景判断每个 row；不得假设此前或其他 Provider 请求保留任何上下文。

唯一允许的标签及定义：
- 1核心词：商品本体、商品品类、核心产品词，直接说明“这是什么”的搜索入口。
- 2外形：颜色、动物或角色造型、图案、形状、外观设计、视觉风格。所有颜色词必须是 2外形；duck、leopard、cat、cow、dinosaur 等具体动物造型通常也是 2外形。
- 3属性：材质、功能、触感、工艺、性能、状态或产品卖点，例如 weighted、soft、fluffy、washable、cooling、memory foam、portable、breathable。
- 4痛点：用户希望解决的问题、需求、身体不适、情绪困扰或缓解目标，例如 anxiety、stress、pain、insomnia、sensory、relief、calming。
- 5规格：尺寸、重量、容量、数量、包装、型号、长宽或商品尺码，例如 inch、lb、pound、5lb、3.3lb、pack、set、large、small、mini。
- 6受众：使用人群、身份、年龄、性别或使用对象，例如 kids、children、toddler、baby、adult、teen、boys、girls、mom、student。
- 7场景：使用地点、时机、活动、节日、送礼或生活场景，例如 gift、birthday、Christmas、travel、office、school、car、home、bedroom、bedtime、camping。
- 8品牌：品牌、竞品品牌、IP、商标、系列或商业专名。必须结合 topPhrases 和 ProductContext 判断，不能仅因拼写特殊而标品牌。
- 无效词：确实无法形成有效业务语义，或明显是噪声、残缺、无意义表达。仅仅不确定时不得使用无效词。

重点边界：
- weighted 表示商品具有加重功能时，通常为 3属性；3.3 lb、5 lb、10lb 等重量表达必须为 5规格。
- animal 在 weighted stuffed animal 等整体商品类别中可以是 1核心词；duck、leopard、cat、cow 等具体造型通常是 2外形。不得把所有动物词硬编码为同一类。
- 非英语 word 也必须正常进行业务分类，不能因语言不同自动标为无效词；reason 可简短说明语言语境。

输出规则：
- 只输出一个合法 JSON 对象，不要 Markdown、代码围栏或额外文字。
- 顶层只能有 rows；每行只能有 itemId、label、reason。
- 必须为每个输入 itemId 恰好输出一行；不得新增、遗漏或重复 itemId。
- label 只能取上述九个标签之一；不得多标签、自定义标签或输出 confidence、思维过程。
- reason 必须是简短中文业务理由。"""

    @staticmethod
    def build_user_prompt(inputs: Sequence[TaggingInput]) -> str:
        """构造单次 Provider 请求完整且去重后的上下文与输入行。"""

        if not inputs:
            raise ValueError("TaggingInput 不能为空")

        item_ids = [tagging_input.item_id for tagging_input in inputs]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("同一请求中的 item_id 不能重复")

        # Context 仅在本次 HTTP 请求内去重。每个 Provider、每个拆分后的
        # child batch 都会重新调用本方法，因此不会错误依赖跨请求会话状态。
        contexts: dict[str, dict[str, Any]] = {}
        context_ids_by_signature: dict[str, str] = {}
        rows: list[dict[str, Any]] = []
        for tagging_input in inputs:
            context = tagging_input.to_dict().pop("productContext")
            context_signature = json.dumps(
                context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            context_id = context_ids_by_signature.get(context_signature)
            if context_id is None:
                context_id = f"ctx_{len(contexts) + 1}"
                context_ids_by_signature[context_signature] = context_id
                contexts[context_id] = {"productContext": context}

            row = tagging_input.to_dict()
            row.pop("productContext")
            row["contextId"] = context_id
            rows.append(row)

        payload = {"contexts": contexts, "rows": rows}
        return "请严格按系统规则为以下输入逐项输出标签结果：\n" + json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
