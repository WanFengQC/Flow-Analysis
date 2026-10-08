"""reversing 完整结果到搜索词级 RAW / RESULT / 单词统计的纯数据处理服务。"""

from collections.abc import Mapping
from math import isfinite
import re
from typing import Any

from models.normalization_rule import (
    ApprovedNormalizationRules,
    NormalizationRuleType,
)

# 当前参考数据只能确认基础英文数字片段切分；不引入 NLP 词典或停用词表。
_BASIC_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class AnalysisService:
    """处理内存 reversing 数据，不负责网络、UI、数据库或持久化。"""

    # 同一月份、同一搜索词在多个 ASIN 中重复出现时，这些字段属于市场
    # 公共属性。它们只保留一个 canonical value，绝对不能跨 ASIN 累加。
    _PUBLIC_FIELDS = (
        "keywordCn",
        "keywordJp",
        "searches",
        "products",
        "purchases",
        "purchaseRate",
        "bid",
        "bidMax",
        "bidMin",
        "minPhrasePpc",
        "maxPhrasePpc",
        "phrasePpc",
        "minBroadPpc",
        "maxBroadPpc",
        "broadPpc",
        "minExactPpc",
        "maxExactPpc",
        "exactPpc",
        "searchesRank",
        "searchesRankTimeFrom",
        "searchesRankTimeTo",
        "supplyDemandRatio",
        "searchesTrend",
        "araClickTop3",
        "titleDensityExact",
        "cprExact",
        "avgPrice",
        "avgReviews",
        "avgRating",
        "monopolyClickRate",
        "top3ClickingRate",
        "top3ConversionRate",
        "clicks",
        "impressions",
        "searchesGrowth",
        "yearlyGrowthRate",
        "gkDatas",
        "top10Asin",
    )

    # 来源追溯字段只用于后续选择一个代表产品背景，绝不参与 RESULT 的
    # 市场公共字段合并，也不改变现有的 Word 指标公式。
    _SOURCE_ASIN_SUM_FIELDS = (
        ("calculatedWeeklySearches", "exposure"),
        ("clicks", "clicks"),
        ("impressions", "impressions"),
        ("searches", "searches"),
    )
    _SOURCE_ASIN_RANK_FIELD = ("searchesRank", "abaWeeklyRank")

    def analyze_keyword_results(
        self,
        keyword_results: list[Mapping[str, Any]],
        min_frequency: int | None = None,
        approved_rules: ApprovedNormalizationRules | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """按显式人工批准规则将单月 RESULT 转为单词统计。"""

        if (
            min_frequency is not None
            and (
                isinstance(min_frequency, bool)
                or not isinstance(min_frequency, int)
                or min_frequency < 1
            )
        ):
            raise ValueError("min_frequency 必须为正整数或 None")

        word_buckets: dict[str, dict[str, Any]] = {}
        diagnostics: dict[str, Any] = {
            "invalid_weight_source_count": 0,
            "missing_clicks_source_count": 0,
            "missing_natural_ratio_count": 0,
            "missing_ad_ratio_count": 0,
            "missing_keyword_count": 0,
            "tokenizer_boundary_counts": {
                "apostrophe": 0,
                "hyphen": 0,
                "slash": 0,
                "ampersand": 0,
                "digit": 0,
                "other_punctuation": 0,
            },
        }

        for result_index, keyword_result in enumerate(keyword_results):
            keywords = keyword_result.get("keywords")
            if not isinstance(keywords, str) or not keywords.strip():
                diagnostics["missing_keyword_count"] += 1
                continue

            self._record_tokenizer_boundaries(keywords, diagnostics)
            normalized_tokens = self.tokenize_keyword(
                keywords,
                approved_rules=approved_rules,
            )
            if not normalized_tokens:
                diagnostics["missing_keyword_count"] += 1
                continue

            # 频次必须保留同一搜索词内的重复出现；指标则基于首次出现
            # 顺序去重，避免 `llama llama` 将同一条搜索词的指标算两次。
            unique_tokens = list(dict.fromkeys(normalized_tokens))
            weekly_exposure = self._as_valid_number(
                keyword_result.get("calculatedWeeklySearches")
            )
            clicks = self._as_valid_number(keyword_result.get("clicks"))
            impressions = self._as_valid_number(
                keyword_result.get("impressions")
            )
            natural_ratio = self._as_valid_number(
                keyword_result.get("naturalRatio")
            )
            ad_ratio = self._as_valid_number(keyword_result.get("adRatio"))

            row_weight = self._calculate_search_term_weight(
                weekly_exposure,
                clicks,
                impressions,
            )
            if row_weight is None:
                diagnostics["invalid_weight_source_count"] += 1
            if clicks is None:
                diagnostics["missing_clicks_source_count"] += 1
            if natural_ratio is None:
                diagnostics["missing_natural_ratio_count"] += 1
            if ad_ratio is None:
                diagnostics["missing_ad_ratio_count"] += 1

            for token in normalized_tokens:
                bucket = word_buckets.setdefault(
                    token,
                    self._create_word_bucket(token),
                )
                bucket["frequency"] += 1

            for token in unique_tokens:
                bucket = word_buckets[token]
                bucket["matchingKeywordCount"] += 1
                bucket["_top_phrase_candidates"].append(
                    (weekly_exposure, result_index, keywords)
                )
                # 指标与来源统计都只按唯一 token 累计。这样 ``llama llama``
                # 仍会给 frequency +2，但同一条搜索词不会让来源曝光翻倍。
                self._accumulate_word_source_asin_stats(
                    bucket,
                    keyword_result.get("sourceAsinStats"),
                )

                if clicks is not None:
                    bucket["total"] += clicks

                if row_weight is None:
                    continue

                bucket["weight"] += row_weight
                if natural_ratio is not None:
                    bucket["_natural_weight_sum"] += (
                        row_weight * natural_ratio
                    )
                    bucket["_natural_weight_base"] += row_weight
                if ad_ratio is not None:
                    bucket["_ad_weight_sum"] += row_weight * ad_ratio
                    bucket["_ad_weight_base"] += row_weight

        word_results = [
            self._finalize_word_bucket(bucket)
            for bucket in word_buckets.values()
            if min_frequency is None
            or bucket["frequency"] >= min_frequency
        ]
        diagnostics["unfiltered_word_count"] = len(word_buckets)
        diagnostics["output_word_count"] = len(word_results)
        diagnostics["min_frequency"] = min_frequency

        return word_results, diagnostics

    @staticmethod
    def tokenize_keyword(
        keywords: str,
        approved_rules: ApprovedNormalizationRules | None = None,
    ) -> list[str]:
        """只在显式传入人工批准规则时执行 phrase-first 归一。"""

        if not isinstance(keywords, str):
            return []

        # 默认仅做基础分词。Seed 从不进入本方法，避免未经人工确认的关系
        # 改写预览或正式统计。
        source_tokens = _BASIC_TOKEN_PATTERN.findall(keywords.lower())
        if not source_tokens or approved_rules is None:
            return source_tokens

        phrase_rules = sorted(
            (
                (tuple(variant.split()), rule.canonical)
                for rule in approved_rules.rules
                if rule.rule_type == NormalizationRuleType.PHRASE
                for variant in rule.variants
                if len(_BASIC_TOKEN_PATTERN.findall(variant)) > 1
            ),
            key=lambda rule: len(rule[0]),
            reverse=True,
        )
        word_rules = {
            variant: rule.canonical
            for rule in approved_rules.rules
            if rule.rule_type == NormalizationRuleType.WORD
            for variant in rule.variants
        }
        if not phrase_rules and not word_rules:
            return source_tokens

        # 先保护人工批准短语，再对剩余单词应用人工批准 word rule。长短语
        # 排在前面只是已批准规则的执行顺序，绝不表示系统自动批准。
        phrase_normalized_tokens: list[str] = []
        token_index = 0
        while token_index < len(source_tokens):
            for phrase_tokens, normalized_phrase in phrase_rules:
                phrase_length = len(phrase_tokens)
                if (
                    source_tokens[token_index : token_index + phrase_length]
                    == list(phrase_tokens)
                ):
                    phrase_normalized_tokens.append(normalized_phrase)
                    token_index += phrase_length
                    break
            else:
                phrase_normalized_tokens.append(source_tokens[token_index])
                token_index += 1

        return [
            word_rules.get(token, token)
            for token in phrase_normalized_tokens
        ]

    def build_raw_rows(
        self,
        reversing_results: Mapping[str, Mapping[str, Any]],
        month: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """将完整 reversing items 展开为保留原始字段的 ASIN 级 RAW 行。"""

        diagnostics: dict[str, Any] = {
            "missing_weekly_exposure_count": 0,
            "missing_natural_ratio_count": 0,
            "missing_ad_ratio_count": 0,
            "duplicate_raw_key_count": 0,
            "missing_keyword_count": 0,
        }
        raw_rows: list[dict[str, Any]] = []
        seen_raw_keys: set[tuple[str, str, str]] = set()

        for source_asin, reversing_data in reversing_results.items():
            if not isinstance(reversing_data, Mapping):
                continue

            items = reversing_data.get("items")
            if not isinstance(items, list):
                continue

            normalized_asin = str(source_asin).strip().upper()
            for item in items:
                if not isinstance(item, Mapping):
                    continue

                keywords = item.get("keywords")
                if not isinstance(keywords, str) or not keywords.strip():
                    diagnostics["missing_keyword_count"] += 1
                    continue

                normalized_keywords = keywords.strip()
                raw_key = (month, normalized_asin, normalized_keywords)
                if raw_key in seen_raw_keys:
                    # 同一 ASIN 的同一关键词重复时保留第一条，避免其周曝光
                    # 被重复累加；问题仍通过诊断计数保留给后续排查。
                    diagnostics["duplicate_raw_key_count"] += 1
                    continue
                seen_raw_keys.add(raw_key)

                raw_row = dict(item)
                raw_row["source_asin"] = normalized_asin
                raw_row["month"] = month

                weekly_exposure = self._as_valid_number(
                    item.get("calculatedWeeklySearches")
                )
                natural_ratio = self._as_valid_number(
                    item.get("naturalRatio")
                )
                ad_ratio = self._as_valid_number(item.get("adRatio"))

                if weekly_exposure is None:
                    diagnostics["missing_weekly_exposure_count"] += 1
                if natural_ratio is None:
                    diagnostics["missing_natural_ratio_count"] += 1
                if ad_ratio is None:
                    diagnostics["missing_ad_ratio_count"] += 1

                raw_row["naturalTraffic"] = (
                    weekly_exposure * natural_ratio
                    if weekly_exposure is not None
                    and natural_ratio is not None
                    else None
                )
                raw_row["adTraffic"] = (
                    weekly_exposure * ad_ratio
                    if weekly_exposure is not None and ad_ratio is not None
                    else None
                )
                raw_rows.append(raw_row)

        return raw_rows, diagnostics

    def aggregate_keyword_results(
        self,
        raw_rows: list[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """按 (month, keywords) 聚合 RAW，并保留公共字段的 canonical value。"""

        grouped_results: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}
        public_field_conflicts: dict[str, int] = {}

        for raw_row in raw_rows:
            month = raw_row.get("month")
            keywords = raw_row.get("keywords")
            source_asin = raw_row.get("source_asin")
            if (
                not isinstance(month, str)
                or not isinstance(keywords, str)
                or not keywords
                or not isinstance(source_asin, str)
                or not source_asin
            ):
                continue

            group_key = (month, keywords)
            result = grouped_results.get(group_key)
            if result is None:
                result = self._create_keyword_result(month, keywords)
                grouped_results[group_key] = result

            result["_source_asins"].add(source_asin)
            self._accumulate_result_source_asin_stats(
                result,
                source_asin,
                raw_row,
            )
            self._merge_public_fields(
                result,
                raw_row,
                public_field_conflicts,
            )
            self._accumulate_asin_metrics(result, raw_row)

        results: list[dict[str, Any]] = []
        for result in grouped_results.values():
            self._finalize_keyword_result(result)
            results.append(result)

        return results, public_field_conflicts

    def analyze_reversing_results(
        self,
        reversing_results: Mapping[str, Mapping[str, Any]],
        month: str,
    ) -> dict[str, Any]:
        """构建 RAW、聚合关键词 RESULT，并返回可追溯的诊断信息。"""

        raw_rows, diagnostics = self.build_raw_rows(
            reversing_results,
            month,
        )
        results, public_field_conflicts = self.aggregate_keyword_results(
            raw_rows,
        )
        diagnostics["public_field_conflicts"] = public_field_conflicts
        diagnostics["raw_row_count"] = len(raw_rows)
        diagnostics["result_row_count"] = len(results)

        return {
            "raw": raw_rows,
            "results": results,
            "diagnostics": diagnostics,
        }

    def analyze_monthly_reversing_results(
        self,
        reversing_results_by_month: Mapping[
            str,
            Mapping[str, Mapping[str, Any]],
        ],
        months: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """逐月独立构建 RAW / RESULT，物理隔离不同月份的业务数据。"""

        # 调用方传入的月份顺序代表任务与后续横向展示顺序。未传入时才
        # 使用字典的插入顺序；任何情况下都不将不同月份的 RAW 合并。
        ordered_months = (
            list(months)
            if months is not None
            else list(reversing_results_by_month)
        )
        analysis_raw: dict[str, list[dict[str, Any]]] = {}
        analysis_results: dict[str, list[dict[str, Any]]] = {}
        analysis_diagnostics: dict[str, dict[str, Any]] = {}

        for month in ordered_months:
            month_reversing_results = reversing_results_by_month.get(
                month,
                {},
            )
            month_analysis = self.analyze_reversing_results(
                month_reversing_results,
                month,
            )
            analysis_raw[month] = month_analysis["raw"]
            analysis_results[month] = month_analysis["results"]
            analysis_diagnostics[month] = month_analysis["diagnostics"]

        return {
            "raw": analysis_raw,
            "results": analysis_results,
            "diagnostics": analysis_diagnostics,
        }

    def analyze_monthly_word_results(
        self,
        monthly_results: Mapping[str, list[Mapping[str, Any]]],
        months: list[str],
        approved_rules: ApprovedNormalizationRules | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        """按月份独立生成预览或正式词结果，规则只能显式传入。"""

        word_results: dict[str, list[dict[str, Any]]] = {}
        diagnostics: dict[str, dict[str, Any]] = {}
        for month in months:
            month_results = monthly_results.get(month, [])
            results, month_diagnostics = self.analyze_keyword_results(
                month_results,
                approved_rules=approved_rules,
            )
            word_results[month] = results
            diagnostics[month] = month_diagnostics
        return word_results, diagnostics

    def analyze_monthly_asin_word_results(
        self,
        monthly_raw: Mapping[str, list[Mapping[str, Any]]],
        months: list[str],
        approved_rules: ApprovedNormalizationRules | None = None,
    ) -> tuple[
        dict[str, dict[str, list[dict[str, Any]]]],
        dict[str, dict[str, dict[str, Any]]],
    ]:
        """在分析层生成 ASIN 独立 Word Result，供导出使用而非在 Export 重算。"""

        output: dict[str, dict[str, list[dict[str, Any]]]] = {}
        diagnostics: dict[str, dict[str, dict[str, Any]]] = {}
        for month in months:
            rows_by_asin: dict[str, list[Mapping[str, Any]]] = {}
            for raw_row in monthly_raw.get(month, []):
                source_asin = str(raw_row.get("source_asin") or "").strip().upper()
                if source_asin:
                    rows_by_asin.setdefault(source_asin, []).append(raw_row)

            month_results: dict[str, list[dict[str, Any]]] = {}
            month_diagnostics: dict[str, dict[str, Any]] = {}
            for source_asin, asin_raw_rows in rows_by_asin.items():
                # 这里发生在正式 Analysis 阶段：先构建该 ASIN 的关键词 RESULT，
                # 再按同一套人工批准规则生成 Word Result；Export 只读取成果。
                asin_keyword_results, _ = self.aggregate_keyword_results(
                    asin_raw_rows,
                )
                asin_word_results, asin_diagnostics = self.analyze_keyword_results(
                    asin_keyword_results,
                    approved_rules=approved_rules,
                )
                month_results[source_asin] = asin_word_results
                month_diagnostics[source_asin] = asin_diagnostics

            output[month] = month_results
            diagnostics[month] = month_diagnostics
        return output, diagnostics

    @staticmethod
    def _create_word_bucket(word: str) -> dict[str, Any]:
        """创建按首次 token 出现顺序保存的单词统计桶。"""

        return {
            "word": word,
            "frequency": 0,
            "weight": 0.0,
            "total": 0.0,
            "matchingKeywordCount": 0,
            "_natural_weight_sum": 0.0,
            "_natural_weight_base": 0.0,
            "_ad_weight_sum": 0.0,
            "_ad_weight_base": 0.0,
            "_top_phrase_candidates": [],
            "_source_asin_stats": {},
        }

    @classmethod
    def _create_source_asin_stat_bucket(cls) -> dict[str, float | None]:
        """创建来源 ASIN 元数据桶；未知值必须保留为 None。"""

        return {
            output_field: None
            for _, output_field in (
                *cls._SOURCE_ASIN_SUM_FIELDS,
                cls._SOURCE_ASIN_RANK_FIELD,
            )
        }

    @classmethod
    def _accumulate_source_asin_stat_value(
        cls,
        stats: dict[str, float | None],
        output_field: str,
        source_value: Any,
    ) -> None:
        """累加可求和的来源字段；无有效值时不以零替代。"""

        numeric_value = cls._as_valid_number(source_value)
        if numeric_value is None:
            return

        current_value = stats[output_field]
        stats[output_field] = (
            numeric_value
            if current_value is None
            else current_value + numeric_value
        )

    @classmethod
    def _accumulate_source_asin_rank(
        cls,
        stats: dict[str, float | None],
        source_value: Any,
    ) -> None:
        """ABA 周排名只保留所有有效搜索词中的最小值。"""

        numeric_value = cls._as_valid_number(source_value)
        if numeric_value is None:
            return

        rank_field = cls._SOURCE_ASIN_RANK_FIELD[1]
        current_rank = stats[rank_field]
        stats[rank_field] = (
            numeric_value
            if current_rank is None
            else min(current_rank, numeric_value)
        )

    @classmethod
    def _accumulate_word_source_asin_stats(
        cls,
        word_bucket: dict[str, Any],
        keyword_source_stats: Any,
    ) -> None:
        """将一个 RESULT 搜索词的来源元数据并入最终 word bucket。"""

        if not isinstance(keyword_source_stats, Mapping):
            return

        word_source_stats = word_bucket["_source_asin_stats"]
        for source_asin, source_stats in keyword_source_stats.items():
            normalized_asin = str(source_asin).strip().upper()
            if not normalized_asin or not isinstance(source_stats, Mapping):
                continue

            target_stats = word_source_stats.setdefault(
                normalized_asin,
                cls._create_source_asin_stat_bucket(),
            )
            for _, output_field in cls._SOURCE_ASIN_SUM_FIELDS:
                cls._accumulate_source_asin_stat_value(
                    target_stats,
                    output_field,
                    source_stats.get(output_field),
                )
            cls._accumulate_source_asin_rank(
                target_stats,
                source_stats.get(cls._SOURCE_ASIN_RANK_FIELD[1]),
            )

    @staticmethod
    def _calculate_search_term_weight(
        weekly_exposure: float | None,
        clicks: float | None,
        impressions: float | None,
    ) -> float | None:
        """按参考模型逐搜索词计算 Weight，未知值不伪造为零。"""

        if (
            weekly_exposure is None
            or clicks is None
            or impressions is None
            or impressions <= 0
        ):
            return None

        return weekly_exposure * clicks / impressions * 43

    @staticmethod
    def _finalize_word_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
        """计算词级比例与前十短语，并移除仅供累加的临时字段。"""

        candidates = bucket.pop("_top_phrase_candidates")
        # Python 的稳定排序会在周曝光相同的情况下保留 RESULT 原始顺序。
        candidates.sort(
            key=lambda candidate: (
                candidate[0] is None,
                -(candidate[0] or 0.0),
            )
        )
        bucket["topPhrases"] = [
            keywords for _, _, keywords in candidates[:10]
        ]

        total = bucket["total"]
        # Total 为真实 0 时，占比按用户展示口径写为 0；只有缺失数据才保留
        # None。这样 Excel 不会把确定的零值显示成空白。
        bucket["ratio"] = bucket["weight"] / total if total > 0 else 0.0

        natural_weight_base = bucket.pop("_natural_weight_base")
        natural_weight_sum = bucket.pop("_natural_weight_sum")
        bucket["naturalRatio"] = (
            natural_weight_sum / natural_weight_base
            if natural_weight_base > 0
            else None
        )

        ad_weight_base = bucket.pop("_ad_weight_base")
        ad_weight_sum = bucket.pop("_ad_weight_sum")
        bucket["adRatio"] = (
            ad_weight_sum / ad_weight_base
            if ad_weight_base > 0
            else None
        )
        # 保持 ASIN 字符串排序，使诊断和未来 tagging 输入稳定；该字段只作为
        # 内部来源追溯 metadata，不由主结果表默认展示。
        bucket["sourceAsinStats"] = {
            asin: dict(stats)
            for asin, stats in sorted(
                bucket.pop("_source_asin_stats").items()
            )
        }
        return bucket

    @staticmethod
    def _record_tokenizer_boundaries(
        keywords: str,
        diagnostics: dict[str, Any],
    ) -> None:
        """记录尚未完全确认的边界字符使用量，不阻断词分析。"""

        boundary_counts = diagnostics["tokenizer_boundary_counts"]
        lowered_keywords = keywords.lower()
        if "'" in lowered_keywords or "’" in lowered_keywords:
            boundary_counts["apostrophe"] += 1
        if "-" in lowered_keywords:
            boundary_counts["hyphen"] += 1
        if "/" in lowered_keywords:
            boundary_counts["slash"] += 1
        if "&" in lowered_keywords:
            boundary_counts["ampersand"] += 1
        if any(character.isdigit() for character in lowered_keywords):
            boundary_counts["digit"] += 1
        if re.search(r"[^a-z0-9\s'’\-/&]", lowered_keywords):
            boundary_counts["other_punctuation"] += 1

    @classmethod
    def _create_keyword_result(
        cls,
        month: str,
        keywords: str,
    ) -> dict[str, Any]:
        """初始化一个关键词聚合桶，并为公共字段预留 canonical value。"""

        result: dict[str, Any] = {
            "month": month,
            "keywords": keywords,
            "calculatedWeeklySearches": 0.0,
            "naturalTraffic": None,
            "adTraffic": None,
            "naturalTrafficExposureBase": 0.0,
            "adTrafficExposureBase": 0.0,
            "naturalRatio": None,
            "adRatio": None,
            "naturalRatioComplete": False,
            "adRatioComplete": False,
            "_weekly_exposure_missing": False,
            "_natural_traffic_has_value": False,
            "_ad_traffic_has_value": False,
            "_source_asins": set(),
            "sourceAsinStats": {},
        }
        for field_name in cls._PUBLIC_FIELDS:
            result[field_name] = None

        return result

    @classmethod
    def _merge_public_fields(
        cls,
        result: dict[str, Any],
        raw_row: Mapping[str, Any],
        public_field_conflicts: dict[str, int],
    ) -> None:
        """保留首个非空公共字段，并统计后续 ASIN 的不一致快照。"""

        for field_name in cls._PUBLIC_FIELDS:
            candidate_value = raw_row.get(field_name)
            canonical_value = result[field_name]

            if canonical_value is None:
                if candidate_value is not None:
                    result[field_name] = candidate_value
                continue

            if (
                candidate_value is not None
                and candidate_value != canonical_value
            ):
                public_field_conflicts[field_name] = (
                    public_field_conflicts.get(field_name, 0) + 1
                )

    @classmethod
    def _accumulate_asin_metrics(
        cls,
        result: dict[str, Any],
        raw_row: Mapping[str, Any],
    ) -> None:
        """累加可直接求和的周曝光及已在 RAW 行计算好的流量绝对值。"""

        weekly_exposure = cls._as_valid_number(
            raw_row.get("calculatedWeeklySearches")
        )
        if weekly_exposure is None:
            result["_weekly_exposure_missing"] = True
        else:
            result["calculatedWeeklySearches"] += weekly_exposure

        natural_traffic = cls._as_valid_number(
            raw_row.get("naturalTraffic")
        )
        if natural_traffic is not None:
            result["naturalTraffic"] = (
                (result["naturalTraffic"] or 0.0) + natural_traffic
            )
            result["_natural_traffic_has_value"] = True
            if weekly_exposure is not None:
                result["naturalTrafficExposureBase"] += weekly_exposure

        ad_traffic = cls._as_valid_number(raw_row.get("adTraffic"))
        if ad_traffic is not None:
            result["adTraffic"] = (
                (result["adTraffic"] or 0.0) + ad_traffic
            )
            result["_ad_traffic_has_value"] = True
            if weekly_exposure is not None:
                result["adTrafficExposureBase"] += weekly_exposure

    @classmethod
    def _accumulate_result_source_asin_stats(
        cls,
        result: dict[str, Any],
        source_asin: str,
        raw_row: Mapping[str, Any],
    ) -> None:
        """在搜索词聚合阶段保留每个来源 ASIN 的轻量选择元数据。"""

        source_stats = result["sourceAsinStats"].setdefault(
            source_asin,
            cls._create_source_asin_stat_bucket(),
        )
        for source_field, output_field in cls._SOURCE_ASIN_SUM_FIELDS:
            cls._accumulate_source_asin_stat_value(
                source_stats,
                output_field,
                raw_row.get(source_field),
            )
        cls._accumulate_source_asin_rank(
            source_stats,
            raw_row.get(cls._SOURCE_ASIN_RANK_FIELD[0]),
        )

    @staticmethod
    def _as_valid_number(value: Any) -> float | None:
        """仅接受有限数值；None、布尔和异常值都保留为未知。"""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None

        numeric_value = float(value)
        return numeric_value if isfinite(numeric_value) else None

    @staticmethod
    def _finalize_keyword_result(result: dict[str, Any]) -> None:
        """根据可计算流量的曝光覆盖范围生成不伪造数据的加权比例。"""

        total_weekly_exposure = result["calculatedWeeklySearches"]
        weekly_exposure_complete = not result["_weekly_exposure_missing"]

        result["sourceAsinCount"] = len(result["_source_asins"])
        if (
            weekly_exposure_complete
            and total_weekly_exposure > 0
            and result["naturalTrafficExposureBase"]
            == total_weekly_exposure
            and result["_natural_traffic_has_value"]
        ):
            result["naturalRatioComplete"] = True
            result["naturalRatio"] = (
                result["naturalTraffic"] / total_weekly_exposure
            )

        if (
            weekly_exposure_complete
            and total_weekly_exposure > 0
            and result["adTrafficExposureBase"]
            == total_weekly_exposure
            and result["_ad_traffic_has_value"]
        ):
            result["adRatioComplete"] = True
            result["adRatio"] = (
                result["adTraffic"] / total_weekly_exposure
            )

        # 以下字段只服务于构建过程，不能作为长期 RESULT 数据向上层传播。
        result.pop("_weekly_exposure_missing")
        result.pop("_natural_traffic_has_value")
        result.pop("_ad_traffic_has_value")
        result.pop("_source_asins")
        result["sourceAsinStats"] = {
            asin: dict(stats)
            for asin, stats in sorted(result["sourceAsinStats"].items())
        }
