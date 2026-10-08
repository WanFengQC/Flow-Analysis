"""严格处理三个独立 AI Provider 的标签共识。"""

from collections.abc import Mapping, Sequence

from models.tagging import TaggingDecision, TaggingInput
from models.tagging_provider import (
    ProviderTaggingFailure,
    ProviderTaggingOutcome,
    ProviderTaggingResult,
    TaggingConsensusResult,
    TaggingConsensusStatus,
)


class TaggingConsensusService:
    """只比较合法 label；绝不执行多数投票或模型优先级裁决。"""

    _PRIMARY_REASON_PROVIDER = "openai"

    def build_results(
        self,
        inputs: Sequence[TaggingInput],
        provider_outcomes: Sequence[ProviderTaggingOutcome],
    ) -> list[TaggingConsensusResult]:
        """按输入顺序生成每项结果，并保留三方独立判断。"""

        outcomes_by_provider = self._index_provider_outcomes(
            provider_outcomes
        )
        results: list[TaggingConsensusResult] = []

        for tagging_input in inputs:
            item_outcomes = self._build_item_outcomes(
                tagging_input.item_id,
                outcomes_by_provider,
            )
            results.append(
                self._build_item_result(
                    tagging_input.item_id,
                    item_outcomes,
                )
            )

        return results

    @staticmethod
    def _index_provider_outcomes(
        provider_outcomes: Sequence[ProviderTaggingOutcome],
    ) -> dict[str, ProviderTaggingOutcome]:
        """保证每个稳定 Provider identity 只出现一次。"""

        indexed: dict[str, ProviderTaggingOutcome] = {}
        for outcome in provider_outcomes:
            if outcome.provider_id in indexed:
                raise ValueError("同一个 Provider 不允许重复返回")
            indexed[outcome.provider_id] = outcome
        return indexed

    @staticmethod
    def _build_item_outcomes(
        item_id: str,
        outcomes_by_provider: Mapping[str, ProviderTaggingOutcome],
    ) -> dict[str, ProviderTaggingOutcome]:
        """将批级 Provider 结果按 item 保留，失败仍以原失败对象记录。"""

        item_outcomes: dict[str, ProviderTaggingOutcome] = {}
        for provider_id, outcome in outcomes_by_provider.items():
            if isinstance(outcome, ProviderTaggingFailure):
                item_outcomes[provider_id] = outcome
                continue

            decisions = tuple(
                decision
                for decision in outcome.decisions
                if decision.item_id == item_id
            )
            if len(decisions) != 1:
                raise ValueError("成功 Provider 的决策与输入项不完整对应")
            item_outcomes[provider_id] = ProviderTaggingResult(
                provider_id=outcome.provider_id,
                model_id=outcome.model_id,
                decisions=decisions,
                latency_ms=outcome.latency_ms,
                attempt_count=outcome.attempt_count,
            )
        return item_outcomes

    def _build_item_result(
        self,
        item_id: str,
        item_outcomes: Mapping[str, ProviderTaggingOutcome],
    ) -> TaggingConsensusResult:
        """只在三家全部合法且 label 完全一致时给出正式共识。"""

        if len(item_outcomes) != 3 or any(
            isinstance(outcome, ProviderTaggingFailure)
            for outcome in item_outcomes.values()
        ):
            return TaggingConsensusResult(
                item_id=item_id,
                status=TaggingConsensusStatus.INCOMPLETE,
                provider_results=dict(item_outcomes),
                consensus_label=None,
                consensus_reason=None,
            )

        decisions = {
            provider_id: self._only_decision(outcome)
            for provider_id, outcome in item_outcomes.items()
            if isinstance(outcome, ProviderTaggingResult)
        }
        labels = {decision.label for decision in decisions.values()}
        if len(labels) != 1:
            return TaggingConsensusResult(
                item_id=item_id,
                status=TaggingConsensusStatus.DISAGREEMENT,
                provider_results=dict(item_outcomes),
                consensus_label=None,
                consensus_reason=None,
            )

        # 一致时仅为稳定展示选择 OpenAI Provider 的理由；理由不参与共识。
        primary_decision = decisions.get(self._PRIMARY_REASON_PROVIDER)
        if primary_decision is None:
            primary_decision = next(iter(decisions.values()))

        return TaggingConsensusResult(
            item_id=item_id,
            status=TaggingConsensusStatus.CONSENSUS,
            provider_results=dict(item_outcomes),
            consensus_label=primary_decision.label,
            consensus_reason=primary_decision.reason,
        )

    @staticmethod
    def _only_decision(outcome: ProviderTaggingResult) -> TaggingDecision:
        """当前 item 映射必须恰好保留一条决策。"""

        if len(outcome.decisions) != 1:
            raise ValueError("单项 Provider 结果必须恰好包含一个决策")
        return outcome.decisions[0]
