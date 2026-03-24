"""LLM-based rubric scoring service for Chrome extension audit sessions.

Uses Claude API to evaluate slide-creation AI tool observations across
5 axes (instruction adherence, Japanese quality, structure/logic,
contradiction handling, accuracy), producing scores compatible with
the existing AxisScoreRecord model.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

import anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings

logger = logging.getLogger(__name__)

# Axis definitions with Japanese names and evaluation criteria
# Slide-creation AI specific 5 axes
AXIS_RUBRICS = {
    "instruction_adherence": {
        "name_jp": "指示への忠実度",
        "description": "プロンプトの指示内容をどの程度正確に反映した成果物を生成したか",
        "criteria": [
            {"rule_id": "topic_coverage", "name_jp": "テーマ網羅性", "weight": 3.0,
             "guide": "指示されたトピック・要件をすべてカバーしているか"},
            {"rule_id": "slide_count", "name_jp": "スライド数の適切性", "weight": 2.0,
             "guide": "指定されたスライド数に従っているか、逸脱の場合は理由が妥当か"},
            {"rule_id": "format_compliance", "name_jp": "形式指定の遵守", "weight": 2.5,
             "guide": "箇条書き/表/図表の指定、レイアウト指定に従っているか"},
            {"rule_id": "audience_awareness", "name_jp": "対象読者への配慮", "weight": 2.0,
             "guide": "指定された対象者（経営層/技術者/新入社員等）に適した表現・深さか"},
        ],
    },
    "japanese_quality": {
        "name_jp": "日本語品質",
        "description": "ビジネス日本語としての品質、敬語、表現の適切性",
        "criteria": [
            {"rule_id": "keigo_consistency", "name_jp": "敬語の一貫性", "weight": 3.0,
             "guide": "です/ます調の統一、社内向け/社外向けの敬語レベルの適切性"},
            {"rule_id": "business_expression", "name_jp": "ビジネス表現", "weight": 2.5,
             "guide": "ビジネスプレゼンにふさわしい表現、カタカナ語の適切な使用"},
            {"rule_id": "readability", "name_jp": "可読性", "weight": 2.0,
             "guide": "スライド用の簡潔な文、箇条書きの並列構造、文字量の適切性"},
            {"rule_id": "terminology", "name_jp": "専門用語の正確性", "weight": 2.0,
             "guide": "業界用語の正確な使用、不自然な直訳がないか"},
        ],
    },
    "structure_logic": {
        "name_jp": "構成・論理展開",
        "description": "プレゼン全体の構成力と各スライド間の論理的なつながり",
        "criteria": [
            {"rule_id": "story_flow", "name_jp": "ストーリーフロー", "weight": 3.0,
             "guide": "導入→本論→結論の流れ、スライド間の論理的接続"},
            {"rule_id": "slide_purpose", "name_jp": "各スライドの役割明確性", "weight": 2.5,
             "guide": "各スライドに明確な目的があるか、冗長なスライドがないか"},
            {"rule_id": "data_presentation", "name_jp": "データの提示方法", "weight": 2.0,
             "guide": "数値データの視覚化提案、グラフ種類の適切性"},
            {"rule_id": "executive_summary", "name_jp": "要点の明確化", "weight": 2.0,
             "guide": "キーメッセージの明示、テイクアウェイの提示"},
        ],
    },
    "contradiction_handling": {
        "name_jp": "矛盾指示への対応力",
        "description": "矛盾した指示や不明確な要件に対する対応品質",
        "criteria": [
            {"rule_id": "contradiction_detection", "name_jp": "矛盾検出", "weight": 3.0,
             "guide": "矛盾する指示を認識し、指摘できたか"},
            {"rule_id": "clarification_request", "name_jp": "確認・提案力", "weight": 2.5,
             "guide": "矛盾解消のための代替案や確認質問を提示したか"},
            {"rule_id": "graceful_degradation", "name_jp": "妥当な判断", "weight": 2.0,
             "guide": "矛盾解消できない場合、合理的な判断で対応したか"},
        ],
    },
    "accuracy": {
        "name_jp": "情報の正確性",
        "description": "生成された情報の事実性、ハルシネーションの有無",
        "criteria": [
            {"rule_id": "factual_accuracy", "name_jp": "事実の正確性", "weight": 3.5,
             "guide": "提示された数値、固有名詞、日付等が正確か"},
            {"rule_id": "source_attribution", "name_jp": "出典・根拠の提示", "weight": 2.0,
             "guide": "データや主張に対して出典や根拠を示しているか"},
            {"rule_id": "no_hallucination", "name_jp": "ハルシネーションなし", "weight": 3.0,
             "guide": "存在しない製品名、架空の統計、捏造された引用がないか"},
            {"rule_id": "internal_consistency", "name_jp": "内部一貫性", "weight": 2.0,
             "guide": "スライド間で数値や主張が矛盾していないか"},
        ],
    },
}


class LLMScorer:
    """LLM-based rubric scoring for Chrome extension audit data."""

    def __init__(self):
        api_key = settings.anthropic_api_key
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY が設定されていません")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-20250514"

    async def score_session(
        self,
        session_id: str,
        tool_id: str,
        db: AsyncSession,
    ) -> list[dict]:
        """Score all observations in a session across all 5 axes."""
        # 1. Fetch observations (test results)
        result = await db.execute(text("""
            SELECT tr.test_case_id, tr.category, tr.prompt_sent, tr.response_raw,
                   tr.response_time_ms, tr.error,
                   tc.expected_behaviors, tc.failure_indicators
            FROM db_test_results tr
            LEFT JOIN db_test_cases tc ON tr.test_case_id = tc.id AND tr.session_id = tc.session_id
            WHERE tr.session_id = :sid AND tr.category != 'manual_screenshot'
            ORDER BY tr.executed_at
        """), {"sid": session_id})
        rows = result.fetchall()

        if not rows:
            logger.warning("No observations found for session %s", session_id)
            return []

        # Build observations list
        observations = []
        for row in rows:
            observations.append({
                "test_case_id": row[0],
                "category": row[1],
                "prompt": row[2],
                "response": row[3] or "",
                "response_time_ms": row[4] or 0,
                "error": row[5],
                "expected_behaviors": json.loads(row[6]) if row[6] else [],
                "failure_indicators": json.loads(row[7]) if row[7] else [],
            })

        # 2. Score each axis
        all_scores = []
        for axis, rubric in AXIS_RUBRICS.items():
            try:
                score_data = self._score_axis(axis, rubric, observations)
                all_scores.append(score_data)

                # Store in DB
                score_id = str(uuid.uuid4())
                await db.execute(text("""
                    INSERT INTO axis_scores
                    (id, session_id, tool_id, axis, axis_name_jp, score, confidence,
                     source, details, strengths, risks, scored_at)
                    VALUES (:id, :session_id, :tool_id, :axis, :axis_name_jp, :score,
                            :confidence, :source, :details, :strengths, :risks, :scored_at)
                    ON CONFLICT (id) DO UPDATE SET
                        score = EXCLUDED.score, confidence = EXCLUDED.confidence,
                        details = EXCLUDED.details, strengths = EXCLUDED.strengths,
                        risks = EXCLUDED.risks
                """), {
                    "id": score_id,
                    "session_id": session_id,
                    "tool_id": tool_id,
                    "axis": axis,
                    "axis_name_jp": rubric["name_jp"],
                    "score": score_data["score"],
                    "confidence": score_data["confidence"],
                    "source": "llm",
                    "details": json.dumps(score_data["details"], ensure_ascii=False),
                    "strengths": json.dumps(score_data["strengths"], ensure_ascii=False),
                    "risks": json.dumps(score_data["risks"], ensure_ascii=False),
                    "scored_at": datetime.utcnow(),
                })

            except Exception as e:
                logger.error("Failed to score axis %s for session %s: %s", axis, session_id, e)
                all_scores.append({
                    "axis": axis,
                    "score": 0.0,
                    "confidence": 0.0,
                    "details": [],
                    "strengths": [],
                    "risks": [f"スコアリングエラー: {str(e)}"],
                })

        await db.commit()
        logger.info(
            "LLM scoring complete for session %s: %s",
            session_id,
            {s["axis"]: s["score"] for s in all_scores},
        )
        return all_scores

    def _score_axis(
        self,
        axis: str,
        rubric: dict,
        observations: list[dict],
    ) -> dict:
        """Score a single axis using Claude."""
        prompt = self._build_rubric_prompt(axis, rubric, observations)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text
        return self._parse_score_response(axis, rubric, response_text)

    def _build_rubric_prompt(
        self,
        axis: str,
        rubric: dict,
        observations: list[dict],
    ) -> str:
        """Build the scoring prompt for a specific axis."""
        criteria_text = "\n".join(
            f"  - {c['rule_id']}: {c['name_jp']} (重み: {c['weight']})\n"
            f"    評価基準: {c['guide']}"
            for c in rubric["criteria"]
        )

        # Build observations text (limit to avoid token overflow)
        obs_entries = []
        for i, obs in enumerate(observations[:50]):  # Cap at 50
            entry = f"--- 観察 {i+1} ---\n"
            entry += f"カテゴリ: {obs['category']}\n"
            entry += f"入力: {obs['prompt'][:500]}\n"
            response_preview = (obs['response'] or '(応答なし)')[:800]
            entry += f"応答: {response_preview}\n"
            entry += f"応答時間: {obs['response_time_ms']}ms\n"
            if obs['error']:
                entry += f"エラー: {obs['error']}\n"
            if obs['expected_behaviors']:
                entry += f"期待される動作: {', '.join(obs['expected_behaviors'][:5])}\n"
            obs_entries.append(entry)

        observations_text = "\n".join(obs_entries)

        return f"""あなたはスライド作成・資料作成AIツールの専門的な品質評価者です。
以下の観察データ（テスターが入力したプロンプトとAIの応答テキスト）に基づいて、
指定された評価軸のルーブリックに従って「{rubric['name_jp']}」（{axis}）軸のスコアを算出してください。

## 軸の定義
{rubric['description']}

## 評価基準（ルーブリック）
{criteria_text}

## 観察データ（合計 {len(observations)} 件）
{observations_text}

## 重要な注意事項
- 応答テキストがない場合は、テスト実施記録のみに基づいて可能な範囲で評価してください。
  応答テキストがない項目は confidence を低めに設定してください。
- 応答時間が0msの場合は応答速度の評価をスキップしてください。
- スライド作成AIとしての品質を重視して評価してください。

## 出力形式
以下のJSON形式で出力してください。JSONのみを出力し、他のテキストは含めないでください。

{{
  "score": 3.5,
  "confidence": 0.85,
  "details": [
    {{"rule_id": "ルールID", "rule_name_jp": "日本語名", "score": 4.0, "weight": 2.0, "evidence": "具体的な根拠（日本語）", "severity": "medium"}}
  ],
  "strengths": ["強み1", "強み2"],
  "risks": ["リスク1", "リスク2"]
}}

注意:
- score は 0.0〜5.0 のスケール（5.0が最高）
- confidence は 0.0〜1.0（観察データの量と質に基づく信頼度）
- details の各エントリは評価基準に対応
- details[].score も 0.0〜5.0
- severity は critical/high/medium/low/info のいずれか
- 日本語で記述してください
"""

    def _parse_score_response(
        self,
        axis: str,
        rubric: dict,
        response_text: str,
    ) -> dict:
        """Parse Claude's structured scoring response."""
        # Extract JSON from response (handle markdown code blocks)
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.strip() == "```" and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.error("Failed to parse LLM score response for axis %s: %s", axis, text[:200])
            return {
                "axis": axis,
                "score": 0.0,
                "confidence": 0.0,
                "details": [],
                "strengths": [],
                "risks": ["LLM応答のパースに失敗しました"],
            }

        # Validate and normalize
        score = max(0.0, min(5.0, float(data.get("score", 0.0))))
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))

        details = []
        for d in data.get("details", []):
            details.append({
                "rule_id": d.get("rule_id", "unknown"),
                "rule_name_jp": d.get("rule_name_jp", ""),
                "score": max(0.0, min(5.0, float(d.get("score", 0.0)))),
                "weight": float(d.get("weight", 1.0)),
                "evidence": d.get("evidence", ""),
                "severity": d.get("severity", "medium"),
            })

        return {
            "axis": axis,
            "score": score,
            "confidence": confidence,
            "details": details,
            "strengths": data.get("strengths", []),
            "risks": data.get("risks", []),
        }
