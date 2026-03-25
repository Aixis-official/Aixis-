"""LLM-based rubric scoring service for Chrome extension audit sessions.

Uses Claude API to evaluate slide-creation AI tool observations across
5 axes (practicality, cost_performance, localization, safety, uniqueness),
producing scores compatible with the existing AxisScoreRecord model.

Score composition follows the public audit protocol:
  final_axis_score = (auto_score * 0.6) + (manual_score * 0.4)
When manual evaluation is pending, auto_score is used alone with lower confidence.

Confidence is calculated across 4 dimensions:
  - 再現性 (consistency): score variance across tests in same category
  - 正確性 (correctness): proportion of tests with actual data vs empty
  - 網羅性 (comprehensiveness): test completion rate
  - 解釈性 (intelligibility): richness of evidence data
"""

import json
import logging
import statistics
import uuid
from datetime import datetime, timezone

import anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings

logger = logging.getLogger(__name__)

# Axis definitions with Japanese names and evaluation criteria
# Mapped to the canonical 5 axes used across the platform
AXIS_RUBRICS = {
    "practicality": {
        "name_jp": "実務適性",
        "description": "スライド作成タスクの完了度、指示への忠実性、実務での活用しやすさ",
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
    "cost_performance": {
        "name_jp": "費用対効果",
        "description": "応答速度、タスク成功率、出力の徹底度から見たコストパフォーマンス",
        "criteria": [
            {"rule_id": "response_speed", "name_jp": "応答速度", "weight": 2.5,
             "guide": "プロンプトに対する応答時間が実用的か（5秒以内が理想）"},
            {"rule_id": "task_success_rate", "name_jp": "タスク成功率", "weight": 3.0,
             "guide": "指示されたタスクを正常に完了できた割合"},
            {"rule_id": "output_thoroughness", "name_jp": "出力の徹底度", "weight": 2.5,
             "guide": "出力内容が十分な量と質を備えているか、手直しの必要性"},
        ],
    },
    "localization": {
        "name_jp": "日本語能力",
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
    "safety": {
        "name_jp": "信頼性・安全性",
        "description": "生成された情報の正確性、ハルシネーションの有無、事実性",
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
    "uniqueness": {
        "name_jp": "革新性",
        "description": "プレゼン全体の構成力、論理的つながり、創造的な問題解決",
        "criteria": [
            {"rule_id": "story_flow", "name_jp": "ストーリーフロー", "weight": 3.0,
             "guide": "導入→本論→結論の流れ、スライド間の論理的接続"},
            {"rule_id": "slide_purpose", "name_jp": "各スライドの役割明確性", "weight": 2.5,
             "guide": "各スライドに明確な目的があるか、冗長なスライドがないか"},
            {"rule_id": "data_presentation", "name_jp": "データの提示方法", "weight": 2.0,
             "guide": "数値データの視覚化提案、グラフ種類の適切性"},
            {"rule_id": "contradiction_handling", "name_jp": "矛盾指示への対応力", "weight": 2.5,
             "guide": "矛盾する指示を認識し、代替案や確認質問を提示できたか"},
            {"rule_id": "executive_summary", "name_jp": "要点の明確化", "weight": 2.0,
             "guide": "キーメッセージの明示、テイクアウェイの提示"},
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
        self.model = settings.ai_scoring_model or settings.ai_agent_model or "claude-haiku-4-5-20251001"

    async def score_session(
        self,
        session_id: str,
        tool_id: str,
        db: AsyncSession,
    ) -> list[dict]:
        """Score all observations in a session across all 5 axes."""
        # 1. Fetch observations (test results + screenshot evidence)
        result = await db.execute(text("""
            SELECT tr.test_case_id, tr.category, tr.prompt_sent, tr.response_raw,
                   tr.response_time_ms, tr.error, tr.screenshot_path,
                   tc.expected_behaviors, tc.failure_indicators
            FROM db_test_results tr
            LEFT JOIN db_test_cases tc ON tr.test_case_id = tc.id AND tr.session_id = tc.session_id
            WHERE tr.session_id = :sid
            ORDER BY tr.executed_at
        """), {"sid": session_id})
        rows = result.fetchall()

        if not rows:
            logger.warning("No observations found for session %s", session_id)
            return []

        # Build observations list, grouping screenshots per test
        observations = []
        screenshot_map = {}  # test_case_id -> [screenshot_paths]

        for row in rows:
            test_case_id = row[0]
            category = row[1]
            screenshot_path = row[6]

            if category == "screenshot_evidence":
                # Group screenshots by test_case_id
                if test_case_id not in screenshot_map:
                    screenshot_map[test_case_id] = []
                if screenshot_path:
                    screenshot_map[test_case_id].append(screenshot_path)
                continue

            observations.append({
                "test_case_id": test_case_id,
                "category": category,
                "prompt": row[2],
                "response": row[3] or "",
                "response_time_ms": row[4] or 0,
                "error": row[5],
                "screenshot_path": screenshot_path,
                "expected_behaviors": json.loads(row[7]) if row[7] else [],
                "failure_indicators": json.loads(row[8]) if row[8] else [],
            })

        # Attach grouped screenshots to their test observations
        for obs in observations:
            obs["screenshots"] = screenshot_map.get(obs["test_case_id"], [])
            if obs["screenshot_path"] and obs["screenshot_path"] not in obs["screenshots"]:
                obs["screenshots"].insert(0, obs["screenshot_path"])

        if not observations:
            logger.warning("No test observations found for session %s (only screenshots)", session_id)
            return []

        # 2. Fetch any existing manual checklist scores for 60/40 blending
        try:
            manual_result = await db.execute(text("""
                SELECT axis, AVG(score) as avg_score, COUNT(*) as cnt
                FROM manual_checklist_entries
                WHERE session_id = :sid AND score IS NOT NULL
                GROUP BY axis
            """), {"sid": session_id})
            manual_scores = {row[0]: {"avg": float(row[1]), "count": int(row[2])}
                             for row in manual_result.fetchall()}
        except Exception as e:
            logger.warning("Manual checklist query failed (table may not exist): %s", e)
            manual_scores = {}
            try:
                await db.rollback()
            except Exception:
                pass

        # 3. Calculate confidence dimensions from observations
        confidence_dimensions = self._calculate_confidence_dimensions(observations)

        # 4. Score each axis
        all_scores = []
        for axis, rubric in AXIS_RUBRICS.items():
            try:
                score_data = self._score_axis(axis, rubric, observations)

                # Apply 60/40 auto/manual split
                auto_score = score_data["score"]
                auto_ratio = 0.6
                manual_ratio = 0.4

                if axis in manual_scores:
                    manual_score = manual_scores[axis]["avg"]
                    final_score = (auto_score * auto_ratio) + (manual_score * manual_ratio)
                    source = "hybrid"
                else:
                    # Manual not yet available — use auto only, lower confidence
                    final_score = auto_score
                    manual_score = None
                    source = "llm"
                    # Penalize confidence when manual component is missing
                    score_data["confidence"] = score_data["confidence"] * 0.8

                score_data["score"] = max(0.0, min(5.0, final_score))

                # Merge confidence dimensions into score metadata
                score_data["confidence_dimensions"] = confidence_dimensions
                score_data["auto_ratio"] = auto_ratio
                score_data["manual_ratio"] = manual_ratio
                score_data["auto_score"] = auto_score
                score_data["manual_score"] = manual_score

                all_scores.append(score_data)

                # Build details JSON including confidence dimensions and split info
                details_with_meta = {
                    "rule_results": score_data["details"],
                    "confidence_dimensions": confidence_dimensions,
                    "auto_ratio": auto_ratio,
                    "manual_ratio": manual_ratio,
                    "auto_score": auto_score,
                    "manual_score": manual_score,
                }

                # Store in DB
                score_id = str(uuid.uuid4())
                await db.execute(text("""
                    INSERT INTO axis_scores
                    (id, session_id, tool_id, axis, axis_name_jp, score, confidence,
                     source, details, strengths, risks, scored_at, scored_by)
                    VALUES (:id, :session_id, :tool_id, :axis, :axis_name_jp, :score,
                            :confidence, :source, :details, :strengths, :risks, :scored_at,
                            :scored_by)
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
                    "source": source,
                    "details": json.dumps(details_with_meta, ensure_ascii=False),
                    "strengths": json.dumps(score_data["strengths"], ensure_ascii=False),
                    "risks": json.dumps(score_data["risks"], ensure_ascii=False),
                    "scored_at": datetime.now(timezone.utc),
                    "scored_by": None,  # NULL = automated LLM scoring
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

    def _calculate_confidence_dimensions(
        self,
        observations: list[dict],
    ) -> dict:
        """Calculate 4-dimension confidence metrics from observations.

        Returns dict with:
          consistency (再現性): Based on score variance across tests in same category
          correctness (正確性): Proportion of tests with actual data vs empty
          comprehensiveness (網羅性): Test completion rate
          intelligibility (解釈性): Richness of evidence data
        """
        if not observations:
            return {
                "consistency": 0.0,
                "correctness": 0.0,
                "comprehensiveness": 0.0,
                "intelligibility": 0.0,
            }

        # --- 再現性 (consistency): low variance across categories = high consistency ---
        category_response_times = {}
        for obs in observations:
            cat = obs.get("category", "unknown")
            rt = obs.get("response_time_ms", 0)
            if rt and rt > 0:
                category_response_times.setdefault(cat, []).append(rt)

        if category_response_times:
            cvs = []  # coefficient of variation per category
            for times in category_response_times.values():
                if len(times) >= 2:
                    mean = statistics.mean(times)
                    if mean > 0:
                        cv = statistics.stdev(times) / mean
                        cvs.append(cv)
            if cvs:
                avg_cv = statistics.mean(cvs)
                # CV of 0 = perfect consistency (1.0), CV >= 1.0 = low consistency (0.0)
                consistency = max(0.0, min(1.0, 1.0 - avg_cv))
            else:
                consistency = 0.5  # insufficient data
        else:
            consistency = 0.3  # no timing data

        # --- 正確性 (correctness): proportion of tests with actual response data ---
        tests_with_data = sum(
            1 for obs in observations
            if (obs.get("response") and len(obs["response"].strip()) > 10)
            or obs.get("screenshots")
        )
        correctness = tests_with_data / len(observations) if observations else 0.0

        # --- 網羅性 (comprehensiveness): completion rate ---
        total_planned = len(observations) + max(0, 17 - len(observations))
        comprehensiveness = min(1.0, len(observations) / max(total_planned, 1))

        # --- 解釈性 (intelligibility): richness of evidence ---
        richness_scores = []
        for obs in observations:
            r = 0.0
            if obs.get("response") and len(obs["response"].strip()) > 50:
                r += 0.4
            elif obs.get("response") and len(obs["response"].strip()) > 10:
                r += 0.2
            if obs.get("screenshots"):
                r += 0.3
            if obs.get("expected_behaviors"):
                r += 0.15
            if obs.get("response_time_ms") and obs["response_time_ms"] > 0:
                r += 0.15
            richness_scores.append(min(1.0, r))
        intelligibility = statistics.mean(richness_scores) if richness_scores else 0.0

        return {
            "consistency": round(consistency, 3),
            "correctness": round(correctness, 3),
            "comprehensiveness": round(comprehensiveness, 3),
            "intelligibility": round(intelligibility, 3),
        }

    def _score_axis(
        self,
        axis: str,
        rubric: dict,
        observations: list[dict],
    ) -> dict:
        """Score a single axis using Claude with multimodal (text + screenshots)."""
        prompt_text = self._build_rubric_prompt(axis, rubric, observations)

        # Build multimodal content: text prompt + screenshot images
        content = []

        # Add screenshot images from observations (limit to 10 to control costs)
        image_count = 0
        for obs in observations:
            for ss_path in obs.get("screenshots", [])[:3]:  # Max 3 per test
                if image_count >= 10:
                    break
                image_data = self._load_screenshot(ss_path)
                if image_data:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_data,
                        },
                    })
                    image_count += 1

        # Add text prompt
        content.append({"type": "text", "text": prompt_text})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": content}],
        )

        response_text = response.content[0].text
        return self._parse_score_response(axis, rubric, response_text)

    def _load_screenshot(self, screenshot_path: str) -> str | None:
        """Load a screenshot file and return base64 data."""
        import base64
        from pathlib import Path

        if not screenshot_path:
            return None

        # screenshot_path is like /static/screenshots/extension/session_id/0001.png
        static_dir = Path(__file__).parent.parent / "static"
        # Remove leading /static/ to get relative path
        rel_path = screenshot_path.lstrip("/")
        if rel_path.startswith("static/"):
            rel_path = rel_path[len("static/"):]

        file_path = static_dir / rel_path
        if not file_path.exists():
            logger.debug("Screenshot not found: %s", file_path)
            return None

        try:
            data = file_path.read_bytes()
            # Skip if too large (> 5MB)
            if len(data) > 5 * 1024 * 1024:
                logger.warning("Screenshot too large, skipping: %s", file_path)
                return None
            return base64.b64encode(data).decode("ascii")
        except Exception as e:
            logger.warning("Failed to load screenshot %s: %s", file_path, e)
            return None

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
            response_preview = (obs['response'] or '(テキスト応答なし — スクリーンショットを参照)')[:800]
            entry += f"応答: {response_preview}\n"
            if obs['response_time_ms'] and obs['response_time_ms'] > 0:
                entry += f"応答時間: {obs['response_time_ms']}ms\n"
            else:
                entry += "応答時間: 未計測\n"
            screenshots = obs.get("screenshots", [])
            if screenshots:
                entry += f"添付スクリーンショット: {len(screenshots)}枚（上記の画像を参照）\n"
            if obs['error']:
                entry += f"エラー: {obs['error']}\n"
            if obs['expected_behaviors']:
                entry += f"期待される動作: {', '.join(obs['expected_behaviors'][:5])}\n"
            obs_entries.append(entry)

        observations_text = "\n".join(obs_entries)

        total_planned = len(observations) + max(0, 17 - len(observations))  # approximate
        completion_rate = len(observations) / max(total_planned, 1) * 100

        return f"""あなたはスライド作成・資料作成AIツールの専門的な品質評価者です。
以下の観察データ（テスターが入力したプロンプトとAIの応答）およびスクリーンショット画像に基づいて、
指定された評価軸のルーブリックに従って「{rubric['name_jp']}」（{axis}）軸のスコアを算出してください。

重要な注意事項:
- スクリーンショット画像がある場合は、それを主要な評価根拠としてください。
- テストの完遂率は {completion_rate:.0f}% です（{len(observations)}件/{total_planned}件）。
- 未実施のテストが多い場合、confidenceを低く設定してください（完遂率に比例）。
- 観察データが少ない場合、スコアは控えめに評価してください。
- 応答時間が0msまたは未計測の場合、応答速度の評価はスキップしてください。

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

【重要】スコアリング規則:
- score は必ず 0.0〜5.0 のスケールで記述（5.0が最高）。パーセンテージや100点満点は使わないでください。
- details[].score も必ず 0.0〜5.0 のスケールで記述してください。パーセンテージ（例: 80, 300）は禁止です。
  例: 良い→ "score": 4.2  悪い→ "score": 80（これはパーセンテージなので禁止）
- confidence は 0.0〜1.0（観察データの量と質に基づく信頼度）
- details の各エントリは上記の評価基準に1対1で対応させてください
- severity は critical/high/medium/low/info のいずれか
- 日本語で記述してください
"""

    @staticmethod
    def _normalize_to_5_scale(value: float) -> float:
        """Normalize a score value to the 0.0-5.0 scale.

        Handles cases where the LLM returns percentages (e.g. 80, 300, 450)
        instead of the requested 0.0-5.0 scale.
        """
        if value > 5.0:
            # Likely a percentage (0-100) or scaled percentage (0-500)
            if value <= 100.0:
                value = value / 100.0 * 5.0
            elif value <= 500.0:
                # Could be 0-500 scale (percentage of 5.0)
                value = value / 100.0
            else:
                # Extremely high — treat as percentage
                value = value / 100.0 * 5.0
        return max(0.0, min(5.0, value))

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

        # Validate and normalize — all scores must be on 0.0-5.0 scale
        raw_score = float(data.get("score", 0.0))
        score = self._normalize_to_5_scale(raw_score)
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))

        details = []
        for d in data.get("details", []):
            detail_score = float(d.get("score", 0.0))
            detail_score = self._normalize_to_5_scale(detail_score)
            details.append({
                "rule_id": d.get("rule_id", "unknown"),
                "rule_name_jp": d.get("rule_name_jp", ""),
                "score": detail_score,
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
