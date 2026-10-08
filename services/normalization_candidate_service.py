"""从月度搜索词 RESULT 中发现待人工审核的归一候选。"""

from collections import defaultdict
from collections.abc import Mapping
from difflib import SequenceMatcher
from hashlib import sha256
from math import isfinite
import re
from typing import Any

from services.normalization_seeds import WORD_ALIAS_SEEDS


# 候选发现的阈值只影响人工审核排序，绝不自动改变正式归一规则。
MAX_NGRAM_SIZE = 4
WORD_SIMILARITY_THRESHOLD = 0.90
MAX_EVIDENCE_COUNT = 10

# 这些词只能作为完整表达的内部组成，不能作为候选短语的结尾。
# 例如“stuffed animals for”并不是可审核的完整搜索表达。
_TRAILING_FUNCTION_WORDS = frozenset(
    {"for", "to", "with", "of", "and", "or", "the", "a", "an"}
)
_STRONG_REASON_TYPES = frozenset(
    {
        "COMPACT_SIGNATURE_MATCH",
        "KNOWN_WORD_ALIAS_VARIANT",
        "CHARACTER_SIMILARITY",
    }
)

_BASIC_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_SAFE_JOINED_TOKEN_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[-_][a-z0-9]+)+$"
)


class NormalizationCandidateService:
    """只读观察月度 RESULT，生成 PENDING 归一候选而不执行归一。"""

    def discover_candidates(
        self,
        monthly_results: Mapping[str, list[Mapping[str, Any]]],
        allowed_words_by_month: Mapping[str, set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """从多个自然月实际关键词中发现候选，不跨月计算正式指标。

        ``allowed_words_by_month`` 来自 Word Filter。传入时，单词候选必须
        是已通过筛选的词；短语候选的每个组成词也必须都通过，避免候选发现
        将被过滤词重新带入后续审核与 AI 流程。
        """

        (
            variant_stats,
            phrase_occurrence_index,
            keyword_evidence,
            month_order,
        ) = (
            self._collect_variant_statistics(
                monthly_results,
                allowed_words_by_month=allowed_words_by_month,
            )
        )
        candidate_groups: dict[tuple[str, ...], dict[str, Any]] = {}

        self._discover_compact_signature_groups(
            variant_stats,
            candidate_groups,
        )
        self._discover_known_word_alias_groups(
            variant_stats,
            candidate_groups,
        )
        self._discover_phrase_token_variant_groups(
            phrase_occurrence_index,
            candidate_groups,
        )
        self._discover_character_similarity_groups(
            variant_stats,
            candidate_groups,
        )

        # 短语候选使用独立的完整表达索引；其余候选仍使用既有的 n-gram
        # 统计。两类统计绝不能直接覆盖合并，否则完整 keyword 的单词会
        # 丢失它在较长 keyword 中的原有出现次数。
        candidates = []
        for variants, metadata in candidate_groups.items():
            statistics = variant_stats
            if metadata["uses_complete_phrase_stats"]:
                statistics = phrase_occurrence_index
            candidate = self._build_candidate(
                variants,
                metadata,
                statistics,
                keyword_evidence,
                month_order,
            )
            if self._should_keep_candidate(candidate):
                candidates.append(candidate)

        return sorted(
            candidates,
            key=lambda candidate: (
                -candidate["impactWeeklyExposure"],
                -candidate["totalFrequency"],
                -candidate["confidence"],
                candidate["id"],
            ),
        )

    @staticmethod
    def _collect_variant_statistics(
        monthly_results: Mapping[str, list[Mapping[str, Any]]],
        *,
        allowed_words_by_month: Mapping[str, set[str]] | None = None,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[tuple[str, int], dict[str, Any]],
        list[str],
    ]:
        """分别提取通用 n-gram 与完整搜索表达，且不修改输入 RESULT。"""

        variant_stats: dict[str, dict[str, Any]] = {}
        # 该索引只收录完整 keyword 文本，PHRASE_TOKEN_VARIANT 禁止读取
        # 任意滑动 n-gram，避免把前缀或中间切片伪装为独立表达。
        phrase_occurrence_index: dict[str, dict[str, Any]] = {}
        keyword_evidence: dict[tuple[str, int], dict[str, Any]] = {}
        month_order: list[str] = []

        for month, month_results in monthly_results.items():
            if not isinstance(month, str) or not isinstance(month_results, list):
                continue

            month_order.append(month)
            allowed_words = (
                {
                    word.strip().lower()
                    for word in allowed_words_by_month.get(month, set())
                    if isinstance(word, str) and word.strip()
                }
                if allowed_words_by_month is not None
                else None
            )
            for result_index, result in enumerate(month_results):
                if not isinstance(result, Mapping):
                    continue

                keywords = result.get("keywords")
                if not isinstance(keywords, str) or not keywords.strip():
                    continue

                keyword_key = (month, result_index)
                keyword_evidence[keyword_key] = {
                    "keywords": keywords,
                    "weeklyExposure": NormalizationCandidateService._as_number(
                        result.get("calculatedWeeklySearches")
                    ),
                    "order": len(keyword_evidence),
                }

                complete_phrase = (
                    NormalizationCandidateService._normalize_complete_phrase(
                        keywords
                    )
                )
                if complete_phrase and NormalizationCandidateService._is_allowed_variant(
                    complete_phrase,
                    allowed_words,
                ):
                    NormalizationCandidateService._record_variant_occurrence(
                        phrase_occurrence_index,
                        complete_phrase,
                        occurrence_count=1,
                        keyword_key=keyword_key,
                        month=month,
                    )

                for variant, occurrence_count in (
                    NormalizationCandidateService._extract_variants(keywords)
                    .items()
                ):
                    if not NormalizationCandidateService._is_allowed_variant(
                        variant,
                        allowed_words,
                    ):
                        continue
                    NormalizationCandidateService._record_variant_occurrence(
                        variant_stats,
                        variant,
                        occurrence_count=occurrence_count,
                        keyword_key=keyword_key,
                        month=month,
                    )

        return (
            variant_stats,
            phrase_occurrence_index,
            keyword_evidence,
            month_order,
        )

    @staticmethod
    def _is_allowed_variant(
        variant: str,
        allowed_words: set[str] | None,
    ) -> bool:
        """判断候选表达是否完全由当前月筛选通过的词组成。"""

        if allowed_words is None:
            return True
        tokens = _BASIC_TOKEN_PATTERN.findall(variant.lower())
        return bool(tokens) and all(token in allowed_words for token in tokens)

    @staticmethod
    def _record_variant_occurrence(
        statistics: dict[str, dict[str, Any]],
        variant: str,
        *,
        occurrence_count: int,
        keyword_key: tuple[str, int],
        month: str,
    ) -> None:
        """向指定候选索引追加一次已观察到的真实出现记录。"""

        stats = statistics.setdefault(
            variant,
            {
                "frequency": 0,
                "keyword_keys": set(),
                "months": set(),
                # 审核页需要区分同一搜索词中 variant 的实际出现次数；
                # 该只读索引不参与任何候选生成、阈值或正式归一判断。
                "occurrence_counts_by_key": defaultdict(int),
            },
        )
        stats["frequency"] += occurrence_count
        stats["keyword_keys"].add(keyword_key)
        stats["months"].add(month)
        stats["occurrence_counts_by_key"][keyword_key] += occurrence_count

    @staticmethod
    def _normalize_complete_phrase(keywords: str) -> str:
        """保留 keyword 的完整词序列，绝不从中截取任意窗口作为短语。"""

        return " ".join(_BASIC_TOKEN_PATTERN.findall(keywords.lower()))

    @staticmethod
    def _extract_variants(keywords: str) -> dict[str, int]:
        """保守提取 n-gram 与安全连字符形式，避免删除未确认的分隔符。"""

        lowered = keywords.lower().strip()
        tokens = _BASIC_TOKEN_PATTERN.findall(lowered)
        variants: dict[str, int] = defaultdict(int)
        for ngram_size in range(1, MAX_NGRAM_SIZE + 1):
            for index in range(len(tokens) - ngram_size + 1):
                variants[" ".join(tokens[index : index + ngram_size])] += 1

        # 只额外保留由 - 或 _ 连接的安全片段。撇号、&、/ 等尚未确认的
        # 边界不会被粗暴压缩为 signature，以免制造误导候选。
        for segment in re.split(r"\s+", lowered):
            cleaned_segment = segment.strip(".,;:!?()[]{}\"")
            if _SAFE_JOINED_TOKEN_PATTERN.fullmatch(cleaned_segment):
                variants[cleaned_segment] += 1

        return dict(variants)

    @staticmethod
    def _discover_compact_signature_groups(
        variant_stats: Mapping[str, Mapping[str, Any]],
        candidate_groups: dict[tuple[str, ...], dict[str, Any]],
    ) -> None:
        """通过仅忽略空格、连字符与下划线的 signature 发现强候选。"""

        signatures: dict[str, set[str]] = defaultdict(set)
        for variant in variant_stats:
            signature = NormalizationCandidateService._compact_signature(
                variant
            )
            if signature:
                signatures[signature].add(variant)

        for variants in signatures.values():
            if len(variants) >= 2:
                NormalizationCandidateService._register_candidate_group(
                    candidate_groups,
                    variants,
                    reason_type="COMPACT_SIGNATURE_MATCH",
                    confidence=1.0,
                )

    @staticmethod
    def _discover_known_word_alias_groups(
        variant_stats: Mapping[str, Mapping[str, Any]],
        candidate_groups: dict[tuple[str, ...], dict[str, Any]],
    ) -> None:
        """将单词 Seed 作为候选证据，而非正式规则或自动审批。"""

        aliases: dict[str, set[str]] = defaultdict(set)
        for variant in variant_stats:
            if " " in variant or "-" in variant or "_" in variant:
                continue
            aliases[WORD_ALIAS_SEEDS.get(variant, variant)].add(variant)

        for canonical, variants in aliases.items():
            if canonical in variant_stats and len(variants) >= 2:
                NormalizationCandidateService._register_candidate_group(
                    candidate_groups,
                    variants,
                    reason_type="KNOWN_WORD_ALIAS_VARIANT",
                    confidence=0.95,
                )

    @staticmethod
    def _discover_phrase_token_variant_groups(
        variant_stats: Mapping[str, Mapping[str, Any]],
        candidate_groups: dict[tuple[str, ...], dict[str, Any]],
    ) -> None:
        """仅比较完整关键词表达中的受确认单词变体。"""

        phrases_by_token_count: dict[int, set[str]] = defaultdict(set)
        for variant in variant_stats:
            tokens = variant.split()
            if (
                len(tokens) < 2
                or NormalizationCandidateService._ends_with_function_word(tokens)
            ):
                continue

            # 只按完整 phrase 的 token 数分桶。后续逐对比较每个位置，允许
            # 多个位置同时出现严格单复数差异，但绝不读取滑动 n-gram。
            phrases_by_token_count[len(tokens)].add(variant)

        for variants in phrases_by_token_count.values():
            if len(variants) < 2:
                continue

            for left_variant, right_variant in (
                NormalizationCandidateService._iter_pairs(variants)
            ):
                if not NormalizationCandidateService._is_allowed_phrase_pair(
                    left_variant,
                    right_variant,
                ):
                    continue

                NormalizationCandidateService._register_candidate_group(
                    candidate_groups,
                    {left_variant, right_variant},
                    reason_type="PHRASE_TOKEN_VARIANT",
                    confidence=0.95,
                    uses_complete_phrase_stats=True,
                )

    @staticmethod
    def _ends_with_function_word(tokens: list[str]) -> bool:
        """过滤以功能词收尾的残缺表达，不限制“for adults”等完整短语。"""

        return bool(tokens) and tokens[-1] in _TRAILING_FUNCTION_WORDS

    @staticmethod
    def _is_allowed_phrase_pair(
        left_variant: str,
        right_variant: str,
    ) -> bool:
        """校验完整 phrase 仅含一个或多个严格单复数 token 差异。"""

        left_tokens = left_variant.split()
        right_tokens = right_variant.split()
        if (
            len(left_tokens) != len(right_tokens)
            or NormalizationCandidateService._ends_with_function_word(left_tokens)
            or NormalizationCandidateService._ends_with_function_word(right_tokens)
        ):
            return False

        changed_token_pairs = [
            (left_token, right_token)
            for left_token, right_token in zip(
                left_tokens,
                right_tokens,
                strict=True,
            )
            if left_token != right_token
        ]
        return bool(changed_token_pairs) and all(
            NormalizationCandidateService.is_strict_singular_plural(
                left_token,
                right_token,
            )
            for left_token, right_token in changed_token_pairs
        )

    @staticmethod
    def is_strict_singular_plural(left_token: str, right_token: str) -> bool:
        """仅按正向规则生成复数，拒绝词干、词性和语义推断。"""

        if (
            not isinstance(left_token, str)
            or not isinstance(right_token, str)
            or left_token == right_token
        ):
            return False
        return (
            NormalizationCandidateService._generate_regular_plural(left_token)
            == right_token
            or NormalizationCandidateService._generate_regular_plural(right_token)
            == left_token
        )

    @staticmethod
    def _generate_regular_plural(singular: str) -> str | None:
        """从候选单数正向生成保守英文复数，绝不通过删尾猜测单数。"""

        if not re.fullmatch(r"[a-z]+", singular):
            return None
        if (
            len(singular) >= 2
            and singular.endswith("y")
            and singular[-2] not in "aeiou"
        ):
            return f"{singular[:-1]}ies"
        if singular.endswith(("s", "x", "z", "ch", "sh")):
            return f"{singular}es"
        return f"{singular}s"

    @staticmethod
    def _discover_character_similarity_groups(
        variant_stats: Mapping[str, Mapping[str, Any]],
        candidate_groups: dict[tuple[str, ...], dict[str, Any]],
    ) -> None:
        """以严格阈值比较单个词，避免把无关长词错误聚合。"""

        single_words = [
            variant
            for variant in variant_stats
            if re.fullmatch(r"[a-z0-9]+", variant)
        ]
        indexed_words: dict[str, list[str]] = defaultdict(list)
        for word in single_words:
            indexed_words[word[0]].append(word)

        for words in indexed_words.values():
            for left_word, right_word in (
                NormalizationCandidateService._iter_pairs(words)
            ):
                shortest_length = min(len(left_word), len(right_word))
                longest_length = max(len(left_word), len(right_word))
                if shortest_length / longest_length < WORD_SIMILARITY_THRESHOLD:
                    continue

                similarity = SequenceMatcher(
                    None,
                    left_word,
                    right_word,
                ).ratio()
                if similarity < WORD_SIMILARITY_THRESHOLD:
                    continue

                NormalizationCandidateService._register_candidate_group(
                    candidate_groups,
                    {left_word, right_word},
                    reason_type="CHARACTER_SIMILARITY",
                    confidence=similarity,
                )

    @staticmethod
    def _register_candidate_group(
        candidate_groups: dict[tuple[str, ...], dict[str, Any]],
        variants: set[str],
        *,
        reason_type: str,
        confidence: float,
        uses_complete_phrase_stats: bool = False,
    ) -> None:
        """合并相同变体集合的多个证据来源，不做不安全的传递聚类。"""

        group_key = tuple(sorted(variants))
        metadata = candidate_groups.setdefault(
            group_key,
            {
                "reason_types": set(),
                "confidence": 0.0,
                "uses_complete_phrase_stats": False,
            },
        )
        metadata["reason_types"].add(reason_type)
        metadata["confidence"] = max(metadata["confidence"], confidence)
        metadata["uses_complete_phrase_stats"] = (
            metadata["uses_complete_phrase_stats"]
            or uses_complete_phrase_stats
        )

    @staticmethod
    def _should_keep_candidate(candidate: Mapping[str, Any]) -> bool:
        """过滤无影响且没有强证据的短语候选，保留其他只读诊断价值。"""

        impact = NormalizationCandidateService._as_number(
            candidate.get("impactWeeklyExposure")
        )
        reason_types = candidate.get("reasonTypes")
        has_strong_evidence = (
            isinstance(reason_types, list)
            and bool(set(reason_types) & _STRONG_REASON_TYPES)
        )
        return impact is not None and impact > 0 or has_strong_evidence

    @staticmethod
    def _build_candidate(
        variants: tuple[str, ...],
        metadata: Mapping[str, Any],
        variant_stats: Mapping[str, Mapping[str, Any]],
        keyword_evidence: Mapping[tuple[str, int], Mapping[str, Any]],
        month_order: list[str],
    ) -> dict[str, Any]:
        """将内部统计转换为有限 evidence 的人工审核候选结构。"""

        keyword_keys: set[tuple[str, int]] = set()
        months: set[str] = set()
        total_frequency = 0
        details: list[dict[str, Any]] = []
        for variant in variants:
            stats = variant_stats[variant]
            variant_keyword_keys = stats["keyword_keys"]
            keyword_keys.update(variant_keyword_keys)
            months.update(stats["months"])
            total_frequency += stats["frequency"]
            monthly_statistics = {
                month: {
                    "frequency": 0,
                    "matchingKeywordCount": 0,
                    "impactWeeklyExposure": 0.0,
                }
                for month in month_order
            }
            for keyword_key in variant_keyword_keys:
                month = keyword_key[0]
                if month not in monthly_statistics:
                    continue
                keyword = keyword_evidence[keyword_key]
                monthly_statistics[month]["frequency"] += stats[
                    "occurrence_counts_by_key"
                ][keyword_key]
                monthly_statistics[month]["matchingKeywordCount"] += 1
                monthly_statistics[month]["impactWeeklyExposure"] += (
                    keyword["weeklyExposure"] or 0.0
                )

            ordered_variant_keyword_keys = sorted(
                variant_keyword_keys,
                key=lambda key: (
                    keyword_evidence[key]["weeklyExposure"] is None,
                    -(keyword_evidence[key]["weeklyExposure"] or 0.0),
                    keyword_evidence[key]["order"],
                ),
            )
            details.append(
                {
                    "variant": variant,
                    "frequency": stats["frequency"],
                    "matchingKeywordCount": len(variant_keyword_keys),
                    # Variant 独立曝光按它命中的搜索词去重求和；同一条
                    # 搜索词命中两个 variant 时，两个展示列可各自出现该值，
                    # 不能将其当作 candidate 总曝光再次相加。
                    "impactWeeklyExposure": sum(
                        keyword_evidence[key]["weeklyExposure"] or 0.0
                        for key in variant_keyword_keys
                    ),
                    "months": [
                        month for month in month_order if month in stats["months"]
                    ],
                    "monthlyStats": [
                        {
                            "month": month,
                            **monthly_statistics[month],
                        }
                        for month in month_order
                        if monthly_statistics[month]["matchingKeywordCount"]
                    ],
                    # 审核 UI 默认只展示前 5 条，可展开到这里保留的 20 条；
                    # 不暴露完整 RAW，也不影响候选本身的 evidence 字段。
                    "evidence": [
                        keyword_evidence[key]["keywords"]
                        for key in ordered_variant_keyword_keys[:20]
                    ],
                }
            )

        ordered_keyword_keys = sorted(
            keyword_keys,
            key=lambda key: (
                keyword_evidence[key]["weeklyExposure"] is None,
                -(keyword_evidence[key]["weeklyExposure"] or 0.0),
                keyword_evidence[key]["order"],
            ),
        )
        impact_weekly_exposure = sum(
            keyword_evidence[key]["weeklyExposure"] or 0.0
            for key in keyword_keys
        )
        suggested_canonical = (
            NormalizationCandidateService._suggest_canonical(variants, variant_stats)
        )
        # variants 只包含实际观察到的完整表达；建议值也不得从历史短语
        # Seed 派生，避免把单独 phrase 误提示为需要概念化。
        ordered_variants = (
            [
                suggested_canonical,
                *[
                    variant
                    for variant in variants
                    if variant != suggested_canonical
                ],
            ]
            if suggested_canonical in variants
            else list(variants)
        )
        candidate_id_source = "|".join(variants).encode("utf-8")

        return {
            "id": f"normalization-{sha256(candidate_id_source).hexdigest()[:16]}",
            "suggestedCanonical": suggested_canonical,
            "variants": ordered_variants,
            "variantDetails": details,
            "reasonTypes": sorted(metadata["reason_types"]),
            "confidence": metadata["confidence"],
            "months": [month for month in month_order if month in months],
            "totalFrequency": total_frequency,
            "matchingKeywordCount": len(keyword_keys),
            "impactWeeklyExposure": impact_weekly_exposure,
            "evidence": [
                keyword_evidence[key]["keywords"]
                for key in ordered_keyword_keys[:MAX_EVIDENCE_COUNT]
            ],
            "decision": "PENDING",
        }

    @staticmethod
    def _suggest_canonical(
        variants: tuple[str, ...],
        variant_stats: Mapping[str, Mapping[str, Any]],
    ) -> str:
        """生成审核建议值；该建议不写入任何正式 alias 配置。"""

        for variant in variants:
            word_target = WORD_ALIAS_SEEDS.get(variant)
            if word_target and word_target in variants:
                return word_target

        return sorted(
            variants,
            key=lambda variant: (
                " " in variant or "-" in variant or "_" in variant,
                -variant_stats[variant]["frequency"],
                variant,
            ),
        )[0]

    @staticmethod
    def _compact_signature(variant: str) -> str:
        """仅忽略确定安全的空格、连字符和下划线，保留其余字符边界。"""

        return variant.replace(" ", "").replace("-", "").replace("_", "")

    @staticmethod
    def _iter_pairs(items: set[str] | list[str]):
        """稳定地产出候选对，确保结果与候选 ID 顺序可复现。"""

        ordered_items = sorted(items)
        for left_index, left_item in enumerate(ordered_items):
            for right_item in ordered_items[left_index + 1 :]:
                yield left_item, right_item

    @staticmethod
    def _as_number(value: Any) -> float | None:
        """候选影响值仅接受有限数值，缺失周曝光不伪造为零记录。"""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None

        numeric_value = float(value)
        return numeric_value if isfinite(numeric_value) else None
