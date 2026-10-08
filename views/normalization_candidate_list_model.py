"""归一候选审核列表的轻量数据模型。"""

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class NormalizationCandidateListModel(QAbstractListModel):
    """只负责将已筛选的候选映射为 QListView 文本，不修改候选状态。"""

    CandidateIdRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        """初始化空模型，候选数据始终由 MainWindow 通过公开方法提供。"""

        super().__init__(parent)
        self._candidates: list[Mapping[str, Any]] = []
        self._history_references: dict[str, Mapping[str, Any]] = {}

    def set_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> None:
        """替换当前可见候选，不排序、不修改任何原始候选对象。"""

        self.beginResetModel()
        self._candidates = list(candidates)
        self.endResetModel()

    def set_history_references(
        self,
        references: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """保存只读历史摘要并刷新列表文本，不向候选对象写入字段。"""

        self.beginResetModel()
        self._history_references = {
            candidate_id: reference
            for candidate_id, reference in references.items()
            if isinstance(candidate_id, str) and isinstance(reference, Mapping)
        }
        self.endResetModel()

    def rowCount(
        self,
        parent: QModelIndex = QModelIndex(),
    ) -> int:
        """返回当前筛选结果数量。"""

        return 0 if parent.isValid() else len(self._candidates)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """提供候选摘要文本及稳定 ID，供 View 选择与跳转使用。"""

        if not index.isValid() or not 0 <= index.row() < len(self._candidates):
            return None

        candidate = self._candidates[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_text(candidate)
        if role == self.CandidateIdRole:
            return candidate.get("id")
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._display_text(candidate)
        return None

    def _display_text(self, candidate: Mapping[str, Any]) -> str:
        """生成固定四行摘要，避免为每个候选创建复杂自定义列表控件。"""

        canonical = str(candidate.get("suggestedCanonical") or "")
        variants = candidate.get("variants")
        variant_text = " ↔ ".join(
            str(variant)
            for variant in variants
            if isinstance(variant, str)
        ) if isinstance(variants, list) else ""
        reason_types = candidate.get("reasonTypes")
        reason_text = ", ".join(
            self._reason_short_name(reason)
            for reason in reason_types
            if isinstance(reason, str)
        ) if isinstance(reason_types, list) else ""
        impact = candidate.get("impactWeeklyExposure")
        impact_text = (
            f"{float(impact):,.0f}"
            if isinstance(impact, (int, float)) and not isinstance(impact, bool)
            else "—"
        )
        decision = self._decision_text(
            candidate.get("decision")
        )
        candidate_id = candidate.get("id")
        reference = (
            self._history_references.get(candidate_id)
            if isinstance(candidate_id, str)
            else None
        )
        history_text = ""
        if isinstance(reference, Mapping) and reference.get("totalDecisionCount"):
            history_text = (
                " · 历史结论有变化"
                if reference.get("hasDecisionConflict")
                or reference.get("hasCanonicalConflict")
                else f" · 历史 {reference.get('totalDecisionCount')}"
            )
        return (
            f"{canonical}\n{variant_text}\n{reason_text} · {impact_text}\n"
            f"{decision}{history_text}"
        )

    @staticmethod
    def _reason_short_name(reason_type: str) -> str:
        """将内部 reasonType 映射为列表中紧凑的中文名称。"""

        return {
            "PHRASE_TOKEN_VARIANT": "短语",
            # 旧审计记录兼容显示；当前候选发现不会再生成该类型。
            "PHRASE_CONCEPT_SEED": "旧版短语概念",
            "COMPACT_SIGNATURE_MATCH": "格式",
            "KNOWN_WORD_ALIAS_VARIANT": "已有规则",
            "CHARACTER_SIMILARITY": "字符",
        }.get(reason_type, reason_type)

    @staticmethod
    def _decision_text(decision: object) -> str:
        """以中性中文文本展示当前人工审核状态。"""

        return {
            "APPROVED": "批准",
            "REJECTED": "拒绝",
            "SKIPPED": "跳过",
        }.get(str(decision), "待审核")
