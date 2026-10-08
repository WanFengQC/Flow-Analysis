"""仅供候选发现使用的归一关系 Seed。"""

# Seed 只能帮助 CandidateDiscovery 识别可能关系和提出 canonical 建议。
# 它们从未经过当前用户的人工审核，绝不可参与正式 token、频次、
# Weight、Total 或任何最终分析结果的计算。
# 单词 Seed 同样不代表正式规则；只有人工 APPROVED candidate 才能生成
# 可执行的 NormalizationRule。
WORD_ALIAS_SEEDS: dict[str, str] = {
    "stuff": "stuffed",
    "animals": "animal",
    "gifts": "gift",
    "toys": "toy",
    "weight": "weighted",
    "weighting": "weighted",
    "plushies": "plush",
}
