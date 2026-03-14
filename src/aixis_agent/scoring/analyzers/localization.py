"""Axis 3: Localization / Japanese Language Ability (日本語能力) analyzer.

Refactored from japanese_ability.py with 2 additional rules for the 5-axis model.
All scores on 0.0-5.0 scale via aggregate_axis_scores helper.
"""

import re

from ...core.enums import ScoreAxis, ScoreSource, Severity, TestCategory
from ...core.models import AxisScore, RuleResult, TestCase, TestResult
from ...utils.japanese import (
    contains_mojibake,
    count_business_terms,
    count_keigo_markers,
    has_keigo_consistency,
    is_meaningful_japanese,
)
from ..rules import ScoringRule, make_rule
from ._base import aggregate_axis_scores


# ---------------------------------------------------------------------------
# Existing rules (preserved exactly from japanese_ability.py)
# ---------------------------------------------------------------------------


def _eval_dialect_comprehension(result: TestResult, case: TestCase | None) -> RuleResult:
    """Did the tool understand dialect input and respond meaningfully?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="JA-DIALECT-COMPREHENSION",
            rule_name_jp="方言理解",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.HIGH,
        )

    response = result.response_raw
    # Check if the response is meaningful Japanese
    if not is_meaningful_japanese(response):
        return RuleResult(
            rule_id="JA-DIALECT-COMPREHENSION",
            rule_name_jp="方言理解",
            passed=False,
            score=0.1,
            evidence="応答に日本語コンテンツが不足",
            severity=Severity.HIGH,
        )

    # Check expected behaviors if available
    score = 0.7  # Base score for meaningful response
    if case and case.expected_behaviors:
        matched = sum(1 for eb in case.expected_behaviors if any(
            keyword in response for keyword in eb.split("、")
        ))
        if matched > 0:
            score = min(1.0, 0.7 + 0.3 * (matched / len(case.expected_behaviors)))

    # Check failure indicators
    if case and case.failure_indicators:
        for fi in case.failure_indicators:
            if any(keyword in response for keyword in fi.split("、") if len(keyword) > 2):
                score = max(0.0, score - 0.3)

    return RuleResult(
        rule_id="JA-DIALECT-COMPREHENSION",
        rule_name_jp="方言理解",
        passed=score >= 0.5,
        score=score,
        evidence=f"日本語応答あり (スコア: {score:.2f})",
        severity=Severity.HIGH,
    )


def _eval_keigo_consistency(result: TestResult, case: TestCase | None) -> RuleResult:
    """Is keigo used consistently in the response?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="JA-KEIGO-CONSISTENCY",
            rule_name_jp="敬語一貫性",
            passed=False,
            score=0.0,
            evidence="応答なし",
            severity=Severity.MEDIUM,
        )

    consistent, detail = has_keigo_consistency(result.response_raw)
    markers = count_keigo_markers(result.response_raw)
    total_markers = sum(markers.values())

    if total_markers == 0:
        # No clear speech register detected - neutral
        return RuleResult(
            rule_id="JA-KEIGO-CONSISTENCY",
            rule_name_jp="敬語一貫性",
            passed=True,
            score=0.7,
            evidence="敬語マーカー検出なし（中立的文体）",
            severity=Severity.MEDIUM,
        )

    score = 1.0 if consistent else 0.4
    return RuleResult(
        rule_id="JA-KEIGO-CONSISTENCY",
        rule_name_jp="敬語一貫性",
        passed=consistent,
        score=score,
        evidence=f"{detail} (です/ます: {markers['desu_masu']}, カジュアル: {markers['casual']})",
        severity=Severity.MEDIUM,
    )


def _eval_encoding_preservation(result: TestResult, case: TestCase | None) -> RuleResult:
    """Are Japanese characters preserved without mojibake?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="JA-ENCODING-PRESERVATION",
            rule_name_jp="文字エンコーディング保全",
            passed=False,
            score=0.0,
            evidence="応答なし",
            severity=Severity.CRITICAL,
        )

    has_mojibake = contains_mojibake(result.response_raw)
    return RuleResult(
        rule_id="JA-ENCODING-PRESERVATION",
        rule_name_jp="文字エンコーディング保全",
        passed=not has_mojibake,
        score=0.0 if has_mojibake else 1.0,
        evidence="文字化け検出" if has_mojibake else "文字化けなし",
        severity=Severity.CRITICAL,
    )


def _eval_business_terms(result: TestResult, case: TestCase | None) -> RuleResult:
    """Does the response use appropriate business terminology?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="JA-BUSINESS-TERMS",
            rule_name_jp="ビジネス用語",
            passed=False,
            score=0.0,
            evidence="応答なし",
            severity=Severity.LOW,
        )

    term_count = count_business_terms(result.response_raw)
    # Business term usage expected more in business-related prompts
    is_business_prompt = any(
        term in result.prompt_sent
        for term in ["売上", "予算", "会社", "ビジネス", "弊社", "御社", "お見積"]
    )

    if is_business_prompt:
        score = min(1.0, term_count / 3)  # Expect at least 3 terms
    else:
        score = min(1.0, 0.5 + term_count / 5)  # Lower bar for non-business

    return RuleResult(
        rule_id="JA-BUSINESS-TERMS",
        rule_name_jp="ビジネス用語",
        passed=score >= 0.5,
        score=score,
        evidence=f"ビジネス用語 {term_count}個検出",
        severity=Severity.LOW,
    )


# ---------------------------------------------------------------------------
# NEW rules for 5-axis model
# ---------------------------------------------------------------------------


def _eval_wareki_handling(result: TestResult, case: TestCase | None) -> RuleResult:
    """Check if response handles Japanese era dates (和暦).

    Look for wareki patterns (令和, 平成, 昭和) in response when prompt
    contains them.  Score 1.0 if preserved/used, 0.5 if western dates used
    instead, 0.2 if no date reference.
    """
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="JA-WAREKI-HANDLING",
            rule_name_jp="和暦対応",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.MEDIUM,
        )

    prompt = result.prompt_sent
    response = result.response_raw

    wareki_patterns = ["令和", "平成", "昭和", "大正", "明治"]
    prompt_has_wareki = any(w in prompt for w in wareki_patterns)

    if not prompt_has_wareki:
        # Rule not strongly applicable; give neutral score
        return RuleResult(
            rule_id="JA-WAREKI-HANDLING",
            rule_name_jp="和暦対応",
            passed=True,
            score=0.7,
            evidence="プロンプトに和暦なし（中立評価）",
            severity=Severity.MEDIUM,
        )

    response_has_wareki = any(w in response for w in wareki_patterns)
    # Check for western date patterns (e.g. 2024年, 2024/)
    western_date_pattern = re.compile(r"(19|20)\d{2}[年/\-.]")
    response_has_western = bool(western_date_pattern.search(response))

    if response_has_wareki:
        score = 1.0
        evidence = "和暦が応答に保持されている"
    elif response_has_western:
        score = 0.5
        evidence = "西暦に変換されているが日付認識あり"
    else:
        score = 0.2
        evidence = "日付への言及なし"

    return RuleResult(
        rule_id="JA-WAREKI-HANDLING",
        rule_name_jp="和暦対応",
        passed=score >= 0.5,
        score=score,
        evidence=evidence,
        severity=Severity.MEDIUM,
    )


def _eval_invoice_format(result: TestResult, case: TestCase | None) -> RuleResult:
    """Check if response understands Japanese business document formats.

    Look for keywords like 請求書, 見積書, 納品書, インボイス, 消費税, 適格請求書.
    Score based on how many are mentioned when the prompt is business-related.
    """
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="JA-INVOICE-FORMAT",
            rule_name_jp="帳票フォーマット理解",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.LOW,
        )

    prompt = result.prompt_sent
    response = result.response_raw

    business_doc_keywords = [
        "請求書", "見積書", "納品書", "インボイス", "消費税", "適格請求書",
    ]

    # Check if prompt is business-document related
    prompt_is_business = any(kw in prompt for kw in business_doc_keywords) or any(
        term in prompt
        for term in ["売上", "経理", "会計", "税", "弊社", "御社", "お見積"]
    )

    if not prompt_is_business:
        return RuleResult(
            rule_id="JA-INVOICE-FORMAT",
            rule_name_jp="帳票フォーマット理解",
            passed=True,
            score=0.7,
            evidence="ビジネス帳票関連のプロンプトでない（中立評価）",
            severity=Severity.LOW,
        )

    matched = sum(1 for kw in business_doc_keywords if kw in response)
    score = min(1.0, matched / 3)  # Expect at least 3 keywords for full score

    return RuleResult(
        rule_id="JA-INVOICE-FORMAT",
        rule_name_jp="帳票フォーマット理解",
        passed=score >= 0.5,
        score=score,
        evidence=f"帳票関連キーワード {matched}/{len(business_doc_keywords)}個検出",
        severity=Severity.LOW,
    )


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

LOCALIZATION_RULES: list[ScoringRule] = [
    # Existing 4 rules
    make_rule(
        "JA-DIALECT-COMPREHENSION", "方言理解", 3.0, Severity.HIGH,
        [TestCategory.DIALECT], _eval_dialect_comprehension,
    ),
    make_rule(
        "JA-KEIGO-CONSISTENCY", "敬語一貫性", 2.0, Severity.MEDIUM,
        [TestCategory.DIALECT, TestCategory.KEIGO_MIXING, TestCategory.BUSINESS_JP],
        _eval_keigo_consistency,
    ),
    make_rule(
        "JA-ENCODING-PRESERVATION", "文字エンコーディング保全", 3.0, Severity.CRITICAL,
        [TestCategory.UNICODE_EDGE], _eval_encoding_preservation,
    ),
    make_rule(
        "JA-BUSINESS-TERMS", "ビジネス用語", 2.0, Severity.LOW,
        [TestCategory.BUSINESS_JP, TestCategory.DIALECT],
        _eval_business_terms,
    ),
    # New rules
    make_rule(
        "JA-WAREKI-HANDLING", "和暦対応", 2.0, Severity.MEDIUM,
        [TestCategory.BUSINESS_JP], _eval_wareki_handling,
    ),
    make_rule(
        "JA-INVOICE-FORMAT", "帳票フォーマット理解", 1.5, Severity.LOW,
        [TestCategory.BUSINESS_JP], _eval_invoice_format,
    ),
]


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------


def score_localization(
    results: list[TestResult],
    cases_map: dict[str, TestCase],
    rules_config: dict,
) -> AxisScore:
    """Score all results on the Localization (日本語能力) axis (0.0-5.0)."""
    return aggregate_axis_scores(
        axis=ScoreAxis.LOCALIZATION,
        rules=LOCALIZATION_RULES,
        results=results,
        cases_map=cases_map,
        source=ScoreSource.AUTO,
    )
