"""LLM-based rubric scoring service for Chrome extension audit sessions.

Uses Claude API to evaluate AI tool observations across 5 axes,
producing scores compatible with the existing AxisScoreRecord model.
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
AXIS_RUBRICS = {
    "practicality": {
        "name_jp": "実務適性",
        "description": "実際の日本のビジネスタスクを処理する能力",
        "criteria": [
            {"rule_id": "contradiction_handling", "name_jp": "矛盾検出・対応", "weight": 2.5,
             "guide": "矛盾する指示に対して、矛盾を指摘したか、適切に対処したか"},
            {"rule_id": "multi_step_completion", "name_jp": "複合指示の完遂", "weight": 3.0,
             "guide": "複数のステップを含む指示を漏れなく実行できたか"},
            {"rule_id": "ambiguity_clarification", "name_jp": "曖昧さへの対応", "weight": 2.0,
             "guide": "曖昧な指示に対して確認を行ったか、合理的に解釈したか"},
            {"rule_id": "error_recovery", "name_jp": "エラー回復力", "weight": 1.5,
             "guide": "文法破壊や不完全な入力に対して、意図を汲み取って対応できたか"},
        ],
    },
    "cost_performance": {
        "name_jp": "費用対効果",
        "description": "価格に対する品質とパフォーマンス",
        "criteria": [
            {"rule_id": "response_speed", "name_jp": "応答速度", "weight": 2.5,
             "guide": "レスポンス時間が実務上許容範囲内か (3秒以内が理想、10秒以上は減点)"},
            {"rule_id": "task_success_rate", "name_jp": "タスク成功率", "weight": 3.0,
             "guide": "要求されたタスクを正しく完了できた割合"},
            {"rule_id": "output_thoroughness", "name_jp": "出力の充実度", "weight": 2.0,
             "guide": "回答の網羅性、詳細さ、付加価値の提供"},
        ],
    },
    "localization": {
        "name_jp": "日本語能力",
        "description": "日本語の理解・生成能力",
        "criteria": [
            {"rule_id": "dialect_comprehension", "name_jp": "方言理解", "weight": 2.0,
             "guide": "関西弁、東北弁などの方言を正しく理解できたか"},
            {"rule_id": "keigo_consistency", "name_jp": "敬語の一貫性", "weight": 2.5,
             "guide": "敬語・丁寧語・タメ口の混在入力に対し、適切な敬語レベルで応答したか"},
            {"rule_id": "encoding_preservation", "name_jp": "文字エンコーディング", "weight": 1.5,
             "guide": "旧字体、絵文字、特殊記号が正しく保持されたか"},
            {"rule_id": "business_terminology", "name_jp": "ビジネス用語", "weight": 3.0,
             "guide": "稟議、根回し、報連相などの日本固有のビジネス概念を正しく理解・使用できたか"},
            {"rule_id": "date_format", "name_jp": "日付・数値形式", "weight": 1.0,
             "guide": "和暦、全角数字、日本式の日付形式を適切に扱えたか"},
        ],
    },
    "safety": {
        "name_jp": "信頼性・安全性",
        "description": "安定性、エラー耐性、一貫した動作",
        "criteria": [
            {"rule_id": "no_crash", "name_jp": "クラッシュなし", "weight": 4.0,
             "guide": "エラー、タイムアウト、空回答がなかったか"},
            {"rule_id": "response_consistency", "name_jp": "応答時間の安定性", "weight": 2.0,
             "guide": "応答時間にばらつきがないか（標準偏差が平均の50%以下が理想）"},
            {"rule_id": "long_input_stability", "name_jp": "長文入力の安定性", "weight": 3.0,
             "guide": "長い入力に対しても打ち切りなく安定して処理できたか"},
            {"rule_id": "unicode_handling", "name_jp": "Unicode処理", "weight": 2.0,
             "guide": "絵文字、サロゲートペア、特殊文字を含む入力を正しく処理できたか"},
        ],
    },
    "uniqueness": {
        "name_jp": "革新性",
        "description": "他ツールにない独自の価値",
        "criteria": [
            {"rule_id": "output_diversity", "name_jp": "出力の多様性", "weight": 3.0,
             "guide": "類似の質問に対してもテンプレ的でない多様な回答を生成したか"},
            {"rule_id": "creative_handling", "name_jp": "創造的問題解決", "weight": 2.5,
             "guide": "想定外の入力に対して創造的・柔軟な対応ができたか"},
            {"rule_id": "output_richness", "name_jp": "出力のリッチさ", "weight": 2.0,
             "guide": "構造化された出力、書式設定、図表の活用があったか"},
            {"rule_id": "error_grace", "name_jp": "エラー時の品格", "weight": 1.5,
             "guide": "対応できない場合でも、丁寧で有益な案内ができたか"},
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

        return f"""あなたはAIツールの専門的な評価者です。
以下の観察データに基づいて、「{rubric['name_jp']}」（{axis}）軸のスコアを算出してください。

## 軸の定義
{rubric['description']}

## 評価基準（ルーブリック）
{criteria_text}

## 観察データ（合計 {len(observations)} 件）
{observations_text}

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
