"""Axis 5: Uniqueness / Innovation (革新性) analyzer.

Semi-automated scoring (~50% confidence).
Measures observable innovation signals from test results:
  - Output diversity (does the tool produce varied, non-template responses?)
  - Creative handling (how well does it handle ambiguous/creative prompts?)
  - Feature richness (does output contain rich formatting, structure, etc.?)
  - Error recovery (does it gracefully handle edge cases vs. generic errors?)

The remaining ~50% requires manual evaluation of:
  - Market positioning vs. competitors
  - Unique feature differentiation
  - Strategic innovation value
"""
import re
from ...core.enums import ScoreAxis, ScoreSource, TestCategory, Severity
from ...core.models import AxisScore, ScoreDetail, TestCase, TestResult


def score_uniqueness(
    results: list[TestResult],
    cases_map: dict[str, TestCase],
    rules_config: dict,
) -> AxisScore:
    """Semi-automated uniqueness scoring from observable test signals."""

    if not results:
        return AxisScore(
            axis=ScoreAxis.UNIQUENESS,
            axis_name_jp="革新性",
            score=0.0,
            confidence=0.0,
            source=ScoreSource.MANUAL,
            strengths=[],
            risks=["テスト結果なし — 手動評価が必要"],
        )

    details: list[ScoreDetail] = []
    strengths: list[str] = []
    risks: list[str] = []

    # ---------------------------------------------------------------
    # Rule 1: Output Diversity — do responses vary or are they templated?
    # ---------------------------------------------------------------
    successful = [r for r in results if r.response_raw and not r.error]
    if len(successful) >= 3:
        responses = [r.response_raw.strip()[:300] for r in successful]

        def _char_ngrams(text: str, n: int = 3) -> set[str]:
            """Character n-gram set — works for Japanese (no whitespace splitting)."""
            return {text[i:i+n] for i in range(max(0, len(text) - n + 1))}

        # Compare pairs for similarity using character n-grams
        total_pairs = 0
        similar_pairs = 0
        sample = responses[:min(len(responses), 15)]
        ngram_cache = [_char_ngrams(r) for r in sample]
        for i in range(len(sample)):
            for j in range(i + 1, len(sample)):
                total_pairs += 1
                grams_a = ngram_cache[i]
                grams_b = ngram_cache[j]
                if grams_a and grams_b:
                    overlap = len(grams_a & grams_b) / max(len(grams_a | grams_b), 1)
                    if overlap > 0.6:
                        similar_pairs += 1

        diversity_ratio = 1.0 - (similar_pairs / max(total_pairs, 1))
        diversity_score = min(1.0, diversity_ratio * 1.2)  # Slight bonus for high diversity

        if diversity_score >= 0.7:
            strengths.append(f"出力多様性: 高 ({diversity_score:.0%}) — テンプレート的でない独自の応答")
        elif diversity_score < 0.4:
            risks.append(f"出力多様性: 低 ({diversity_score:.0%}) — テンプレート的な応答が多い")

        details.append(ScoreDetail(
            rule_id="UNQ-OUTPUT-DIVERSITY",
            rule_name_jp="出力多様性",
            score=diversity_score,
            weight=3.0,
            evidence=f"{len(successful)}件の応答を比較、多様性スコア: {diversity_score:.0%}",
            severity=Severity.MEDIUM,
            test_case_ids=[r.test_case_id for r in successful[:10]],
        ))

    # ---------------------------------------------------------------
    # Rule 2: Creative Handling — how well does it handle ambiguous prompts?
    # ---------------------------------------------------------------
    creative_categories = {TestCategory.AMBIGUOUS, TestCategory.MULTI_STEP, TestCategory.CONTRADICTORY}
    creative_results = [
        r for r in results
        if r.test_case_id in cases_map
        and cases_map[r.test_case_id].category in creative_categories
    ]
    if creative_results:
        good_creative = [
            r for r in creative_results
            if r.response_raw and len(r.response_raw.strip()) > 50 and not r.error
        ]
        creative_rate = len(good_creative) / len(creative_results)

        if creative_rate >= 0.7:
            strengths.append(f"創造的タスク対応力: {creative_rate:.0%} — 曖昧・矛盾・複合タスクに強い")
        elif creative_rate < 0.4:
            risks.append(f"創造的タスク対応力: 低 ({creative_rate:.0%})")

        details.append(ScoreDetail(
            rule_id="UNQ-CREATIVE-HANDLING",
            rule_name_jp="創造的タスク対応力",
            score=min(1.0, creative_rate * 1.1),
            weight=2.5,
            evidence=f"曖昧/矛盾/複合ステップ {len(creative_results)}件中 {len(good_creative)}件で有意な応答",
            severity=Severity.MEDIUM,
            test_case_ids=[r.test_case_id for r in creative_results],
        ))

    # ---------------------------------------------------------------
    # Rule 3: Output Richness — formatting, structure, length
    # ---------------------------------------------------------------
    if successful:
        rich_markers = ['#', '##', '- ', '* ', '1.', '|', '```', '**', '•']
        rich_count = 0
        avg_length = 0
        for r in successful:
            text = r.response_raw or ""
            avg_length += len(text)
            if any(marker in text for marker in rich_markers):
                rich_count += 1

        avg_length = avg_length / len(successful)
        richness_ratio = rich_count / len(successful)

        # Score based on both richness and length
        length_score = min(1.0, avg_length / 500)  # 500+ chars = full score
        richness_score = (richness_ratio * 0.6 + length_score * 0.4)

        if richness_score >= 0.6:
            strengths.append(f"出力リッチネス: 構造化された豊かな応答 (平均{avg_length:.0f}文字)")
        elif richness_score < 0.3:
            risks.append("出力リッチネス: 短く単純な応答が多い")

        details.append(ScoreDetail(
            rule_id="UNQ-OUTPUT-RICHNESS",
            rule_name_jp="出力リッチネス",
            score=richness_score,
            weight=2.0,
            evidence=f"平均応答長: {avg_length:.0f}文字, 構造化率: {richness_ratio:.0%}",
            severity=Severity.LOW,
            test_case_ids=[r.test_case_id for r in successful[:5]],
        ))

    # ---------------------------------------------------------------
    # Rule 4: Error Recovery Grace — generic error vs. helpful fallback?
    # ---------------------------------------------------------------
    error_results = [r for r in results if r.error]
    if error_results:
        # Check if errors have useful info (not just generic timeouts)
        helpful_errors = [
            r for r in error_results
            if r.response_raw and len(r.response_raw.strip()) > 20
        ]
        grace_ratio = len(helpful_errors) / len(error_results) if error_results else 0.5

        details.append(ScoreDetail(
            rule_id="UNQ-ERROR-GRACE",
            rule_name_jp="エラー時の対応品質",
            score=min(1.0, grace_ratio + 0.2),  # Slight bonus
            weight=1.5,
            evidence=f"エラー{len(error_results)}件中{len(helpful_errors)}件で有用な情報を返却",
            severity=Severity.LOW,
            test_case_ids=[r.test_case_id for r in error_results[:5]],
        ))

    # ---------------------------------------------------------------
    # Calculate weighted average
    # ---------------------------------------------------------------
    if not details:
        return AxisScore(
            axis=ScoreAxis.UNIQUENESS,
            axis_name_jp="革新性",
            score=0.0,
            confidence=0.0,
            source=ScoreSource.MANUAL,
            strengths=[],
            risks=["テスト結果が不十分 — 手動評価が必要"],
        )

    weighted_sum = sum(d.score * d.weight for d in details)
    total_weight = sum(d.weight for d in details)
    raw_score = (weighted_sum / total_weight) * 5.0 if total_weight > 0 else 0.0

    # Confidence: ~0.5 (semi-automated), scaled by coverage
    total_evaluated = len([r for r in results if r.response_raw])
    coverage = min(1.0, total_evaluated / max(len(results), 1))
    confidence = min(0.5, 0.5 * coverage)  # Cap at 0.5 — manual review still needed

    # Add note about manual portion
    risks.append("市場差別化・競合比較は手動評価が必要（自動評価は出力品質ベース）")

    return AxisScore(
        axis=ScoreAxis.UNIQUENESS,
        axis_name_jp="革新性",
        score=round(min(5.0, max(0.0, raw_score)), 1),
        confidence=round(confidence, 2),
        source=ScoreSource.HYBRID,
        details=details,
        strengths=strengths,
        risks=risks,
    )
