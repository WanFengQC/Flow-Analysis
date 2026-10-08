"""最终分析行的展示列定义，不包含任何业务计算。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FinalAnalysisColumn:
    """描述一个最终分析字段在 Data Tab 与 Excel 中的共同展示方式。"""

    field: str
    title: str
    value_kind: str = "text"
    default_visible: bool = True


# 首批正式列严格对应当前 Word Result 已有字段和最终标签字段。后续新增的
# 正式业务字段会在保持原始 field 名的前提下追加，不会被导出层静默丢弃。
FINAL_ANALYSIS_COLUMNS: tuple[FinalAnalysisColumn, ...] = (
    FinalAnalysisColumn("month", "月份"),
    FinalAnalysisColumn("word", "词"),
    FinalAnalysisColumn("frequency", "词频", "integer"),
    FinalAnalysisColumn("weight", "Weight", "number"),
    FinalAnalysisColumn("total", "Total", "number"),
    FinalAnalysisColumn("ratio", "占比", "percentage"),
    FinalAnalysisColumn("naturalRatio", "自然流量占比", "percentage"),
    FinalAnalysisColumn("adRatio", "广告流量占比", "percentage"),
    FinalAnalysisColumn("matchingKeywordCount", "匹配搜索词数", "integer"),
    FinalAnalysisColumn("topPhrases", "Top Phrases", "phrases"),
    FinalAnalysisColumn("label", "标签"),
    FinalAnalysisColumn("labelSource", "标签来源"),
    # 原因保留在唯一最终数据源中；Data Tab 默认隐藏以避免主表过宽。
    FinalAnalysisColumn("labelReason", "标签原因", default_visible=False),
)


# 这些字段仅用于 ProductContext 选择或 AI 输入，不能进入用户最终分析表。
INTERNAL_FINAL_ANALYSIS_FIELDS = frozenset(
    {
        "sourceAsinStats",
        "productContext",
        "productContextSource",
        "representativeAsin",
    }
)
