"""Axis 2: Cost Performance (費用対効果) analyzer.

Semi-automated scoring (~30% confidence).
Measures observable cost-efficiency signals from test results:
  - Response speed efficiency (fast responses = better cost/performance ratio)
  - Error rate (fewer errors = less wasted cost on retries)
  - AI API efficiency (calls used vs. tests completed)
  - Output-to-cost ratio (quality of output per API call)

The remaining ~70% requires manual evaluation of:
  - Pricing model and subscription tiers
  - ROI for specific business use cases
  - Comparison with competitor pricing
"""
from ...core.enums import ScoreAxis, ScoreSource, Severity
from ...core.models import AxisScore, ScoreDetail, TestCase, TestResult


def score_cost_performance(
    results: list[TestResult],
    cases_map: dict[str, TestCase],
    rules_config: dict,
) -> AxisScore:
    """Semi-automated cost-performance scoring from observable metrics."""

    if not results:
        return AxisScore(
            axis=ScoreAxis.COST_PERFORMANCE,
            axis_name_jp="費用対効果",
            score=0.0,
            confidence=0.0,
            source=ScoreSource.MANUAL,
            strengths=[],
            risks=["テスト結果なし — 手動評価が必要"],
        )

    details: list[ScoreDetail] = []
    strengths: list[str] = []
    risks: list[str] = []

    successful = [r for r in results if r.response_raw and not r.error]
    error_results = [r for r in results if r.error]

    # ---------------------------------------------------------------
    # Rule 1: Response Speed Efficiency
    # Fast tool = better cost efficiency (user time saved)
    # ---------------------------------------------------------------
    if successful:
        times_ms = [r.response_time_ms for r in successful if r.response_time_ms > 0]
        if times_ms:
            avg_time = sum(times_ms) / len(times_ms)
            median_time = sorted(times_ms)[len(times_ms) // 2]

            # Score: ≤5s → 1.0, ≤15s → 0.8, ≤30s → 0.6, ≤60s → 0.4, >60s → 0.2
            if median_time <= 5000:
                speed_score = 1.0
            elif median_time <= 15000:
                speed_score = 0.8
            elif median_time <= 30000:
                speed_score = 0.6
            elif median_time <= 60000:
                speed_score = 0.4
            else:
                speed_score = 0.2

            label = f"中央値応答時間: {median_time/1000:.1f}秒"
            if speed_score >= 0.7:
                strengths.append(f"高速応答: {label}")
            elif speed_score <= 0.4:
                risks.append(f"応答時間: 要改善 ({label})")

            details.append(ScoreDetail(
                rule_id="COST-RESPONSE-SPEED",
                rule_name_jp="応答速度効率",
                score=speed_score,
                weight=2.5,
                evidence=f"{label}, 平均: {avg_time/1000:.1f}秒 ({len(times_ms)}件)",
                severity=Severity.MEDIUM,
                test_case_ids=[r.test_case_id for r in successful[:5]],
            ))

    # ---------------------------------------------------------------
    # Rule 2: Success Rate (fewer errors = less wasted cost)
    # ---------------------------------------------------------------
    total = len(results)
    success_rate = len(successful) / total if total > 0 else 0.0

    if success_rate >= 0.9:
        rate_score = 1.0
        strengths.append(f"高成功率: {success_rate:.0%} ({len(successful)}/{total}件)")
    elif success_rate >= 0.7:
        rate_score = 0.7
    elif success_rate >= 0.5:
        rate_score = 0.5
        risks.append(f"成功率: {success_rate:.0%} — エラーによるコスト損失あり")
    else:
        rate_score = 0.3
        risks.append(f"低成功率: {success_rate:.0%} — コスト効率が悪い")

    details.append(ScoreDetail(
        rule_id="COST-SUCCESS-RATE",
        rule_name_jp="成功率",
        score=rate_score,
        weight=3.0,
        evidence=f"成功: {len(successful)}/{total}件 ({success_rate:.0%})",
        severity=Severity.HIGH,
        test_case_ids=[r.test_case_id for r in results[:5]],
    ))

    # ---------------------------------------------------------------
    # Rule 3: AI API Efficiency (for AI browser executor)
    # ---------------------------------------------------------------
    total_calls = sum((r.metadata or {}).get("ai_calls_used", 0) for r in results)
    tests_done = len(results)
    if total_calls > 0:
        calls_per_test = total_calls / tests_done

        if calls_per_test <= 1.5:
            api_score = 1.0
            strengths.append(f"高API効率: テストあたり{calls_per_test:.1f}呼出")
        elif calls_per_test <= 3.0:
            api_score = 0.7
        elif calls_per_test <= 5.0:
            api_score = 0.5
        else:
            api_score = 0.3
            risks.append(f"API効率: 低 (テストあたり{calls_per_test:.1f}呼出)")

        details.append(ScoreDetail(
            rule_id="COST-API-EFFICIENCY",
            rule_name_jp="API利用効率",
            score=api_score,
            weight=2.0,
            evidence=f"合計{total_calls}呼出 / {tests_done}テスト = {calls_per_test:.1f}呼出/テスト",
            severity=Severity.MEDIUM,
            test_case_ids=[r.test_case_id for r in results[:5]],
        ))

    # ---------------------------------------------------------------
    # Rule 4: Output Quality per Interaction
    # (meaningful output length relative to input prompt length)
    # ---------------------------------------------------------------
    if successful:
        ratios = []
        for r in successful:
            prompt_len = len(r.prompt_sent or "")
            response_len = len(r.response_raw or "")
            if prompt_len > 0:
                ratios.append(response_len / prompt_len)

        if ratios:
            avg_ratio = sum(ratios) / len(ratios)
            # A good tool produces substantial output from a prompt
            if avg_ratio >= 3.0:
                quality_score = 1.0
                strengths.append(f"高出力効率: プロンプト対比{avg_ratio:.1f}倍の出力")
            elif avg_ratio >= 1.5:
                quality_score = 0.7
            elif avg_ratio >= 0.5:
                quality_score = 0.5
            else:
                quality_score = 0.3
                risks.append(f"出力効率: 低 (プロンプト対比{avg_ratio:.1f}倍)")

            details.append(ScoreDetail(
                rule_id="COST-OUTPUT-QUALITY",
                rule_name_jp="出力品質効率",
                score=quality_score,
                weight=1.5,
                evidence=f"平均出力/入力比: {avg_ratio:.1f}倍 ({len(ratios)}件)",
                severity=Severity.LOW,
                test_case_ids=[r.test_case_id for r in successful[:5]],
            ))

    # ---------------------------------------------------------------
    # Calculate weighted average
    # ---------------------------------------------------------------
    if not details:
        return AxisScore(
            axis=ScoreAxis.COST_PERFORMANCE,
            axis_name_jp="費用対効果",
            score=0.0,
            confidence=0.0,
            source=ScoreSource.MANUAL,
            strengths=[],
            risks=["テスト結果が不十分 — 手動評価が必要"],
        )

    weighted_sum = sum(d.score * d.weight for d in details)
    total_weight = sum(d.weight for d in details)
    raw_score = (weighted_sum / total_weight) * 5.0 if total_weight > 0 else 0.0

    # Confidence: ~0.3 (mostly manual), scaled by coverage
    total_evaluated = len([r for r in results if r.response_raw or r.error])
    coverage = min(1.0, total_evaluated / max(len(results), 1))
    confidence = min(0.3, 0.3 * coverage)

    risks.append("料金プラン・競合比較・ビジネスROIは手動評価が必要")

    return AxisScore(
        axis=ScoreAxis.COST_PERFORMANCE,
        axis_name_jp="費用対効果",
        score=round(min(5.0, max(0.0, raw_score)), 1),
        confidence=round(confidence, 2),
        source=ScoreSource.HYBRID,
        details=details,
        strengths=strengths,
        risks=risks,
    )
