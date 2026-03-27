"""LLM-based rubric scoring service for Chrome extension audit sessions.

Uses Claude API to evaluate slide-creation AI tool observations across
5 axes (practicality, cost_performance, localization, safety, uniqueness),
producing scores compatible with the existing AxisScoreRecord model.

Score composition follows the public audit protocol:
  final_axis_score = (auto_score * auto_ratio) + (manual_score * manual_ratio)  [per-axis ratios from AXIS_MIX]
When manual evaluation is pending, auto_score is used alone with lower confidence.

Confidence is calculated across 4 dimensions:
  - 再現性 (consistency): score variance across tests in same category
  - 正確性 (correctness): proportion of tests with actual data vs empty
  - 網羅性 (comprehensiveness): test completion rate
  - 解釈性 (intelligibility): richness of evidence data
"""

import io
import json
import logging
import statistics
import uuid
from collections import defaultdict
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
             "guide": "スクリーンショットから確認できるスライド内容が、指示されたトピック・要件をすべてカバーしているか"},
            {"rule_id": "slide_count", "name_jp": "スライド数の適切性", "weight": 2.0,
             "guide": "スクリーンショットから確認できるスライド枚数が指定数に従っているか、逸脱の場合は理由が妥当か"},
            {"rule_id": "format_compliance", "name_jp": "形式指定の遵守", "weight": 2.5,
             "guide": "スクリーンショットから、箇条書き/表/図表の指定、レイアウト指定に従っているか確認"},
            {"rule_id": "audience_awareness", "name_jp": "対象読者への配慮", "weight": 2.0,
             "guide": "スクリーンショットに表示されたスライドが、指定された対象者（経営層/技術者/新入社員等）に適した表現・深さか"},
        ],
    },
    "cost_performance": {
        "name_jp": "費用対効果",
        "description": "応答速度、タスク成功率、出力の徹底度から見たコストパフォーマンス",
        "criteria": [
            {"rule_id": "response_speed", "name_jp": "応答速度", "weight": 2.5,
             "guide": "タイマーで計測された応答時間（response_time_ms）に基づいて評価。未計測(0ms)の場合はこの項目をスキップ"},
            {"rule_id": "task_success_rate", "name_jp": "タスク成功率", "weight": 3.0,
             "guide": "スクリーンショットから確認できる範囲で、指示されたタスクを正常に完了できた割合"},
            {"rule_id": "output_thoroughness", "name_jp": "出力の徹底度", "weight": 2.5,
             "guide": "スクリーンショットから確認できるスライドの量と質が十分か、手直しの必要性"},
        ],
    },
    "localization": {
        "name_jp": "日本語能力",
        "description": "ビジネス日本語としての品質、敬語、表現の適切性",
        "criteria": [
            {"rule_id": "keigo_consistency", "name_jp": "敬語の一貫性", "weight": 3.0,
             "guide": "スクリーンショットに表示された日本語テキストで、です/ます調の統一、敬語レベルの適切性を確認"},
            {"rule_id": "business_expression", "name_jp": "ビジネス表現", "weight": 2.5,
             "guide": "スクリーンショットのスライド上の表現がビジネスプレゼンにふさわしいか、カタカナ語の適切な使用"},
            {"rule_id": "readability", "name_jp": "可読性", "weight": 2.0,
             "guide": "スクリーンショットから確認できるスライドの文が簡潔か、箇条書きの並列構造、文字量の適切性"},
            {"rule_id": "terminology", "name_jp": "専門用語の正確性", "weight": 2.0,
             "guide": "スクリーンショットに表示された業界用語の正確な使用、不自然な直訳がないか"},
        ],
    },
    "safety": {
        "name_jp": "信頼性・安全性",
        "description": "生成された情報の正確性、ハルシネーションの有無、事実性",
        "criteria": [
            {"rule_id": "factual_accuracy", "name_jp": "事実の正確性", "weight": 3.5,
             "guide": "スクリーンショットに表示された数値、固有名詞、日付等が正確か"},
            {"rule_id": "source_attribution", "name_jp": "出典・根拠の提示", "weight": 2.0,
             "guide": "スクリーンショットのスライド上で、データや主張に対して出典や根拠を示しているか"},
            {"rule_id": "no_hallucination", "name_jp": "ハルシネーションなし", "weight": 3.0,
             "guide": "スクリーンショットに表示された内容に、存在しない製品名、架空の統計、捏造された引用がないか"},
            {"rule_id": "internal_consistency", "name_jp": "内部一貫性", "weight": 2.0,
             "guide": "スクリーンショット間でスライドの数値や主張が矛盾していないか"},
        ],
    },
    "uniqueness": {
        "name_jp": "革新性",
        "description": "プレゼン全体の構成力、論理的つながり、創造的な問題解決",
        "criteria": [
            {"rule_id": "story_flow", "name_jp": "ストーリーフロー", "weight": 3.0,
             "guide": "スクリーンショットから確認できるスライド全体の導入→本論→結論の流れ、スライド間の論理的接続"},
            {"rule_id": "slide_purpose", "name_jp": "各スライドの役割明確性", "weight": 2.5,
             "guide": "スクリーンショットで確認できる各スライドに明確な目的があるか、冗長なスライドがないか"},
            {"rule_id": "data_presentation", "name_jp": "データの提示方法", "weight": 2.0,
             "guide": "スクリーンショットに表示されたグラフ・図表の適切性、数値データの視覚化の質"},
            {"rule_id": "contradiction_handling", "name_jp": "矛盾指示への対応力", "weight": 2.5,
             "guide": "矛盾する指示に対して、スクリーンショットから確認できる対応（代替案や確認質問の提示）"},
            {"rule_id": "executive_summary", "name_jp": "要点の明確化", "weight": 2.0,
             "guide": "スクリーンショットから確認できるキーメッセージの明示、テイクアウェイの提示"},
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
            # Legacy sessions may only have screenshot_evidence entries
            # Create virtual observations from screenshot groups
            if screenshot_map:
                logger.info("Creating virtual observations from %d screenshot groups for session %s",
                           len(screenshot_map), session_id)
                for test_case_id, paths in screenshot_map.items():
                    if test_case_id and test_case_id != '_none':
                        # Look up test case info
                        tc_result = await db.execute(text(
                            "SELECT category, prompt FROM db_test_cases WHERE id = :tid AND session_id = :sid"
                        ), {"tid": test_case_id, "sid": session_id})
                        tc_row = tc_result.fetchone()
                        observations.append({
                            "test_case_id": test_case_id,
                            "category": tc_row[0] if tc_row else "protocol",
                            "prompt": tc_row[1] if tc_row else "",
                            "response": "",
                            "response_time_ms": 0,
                            "error": None,
                            "screenshot_path": paths[0] if paths else None,
                            "screenshots": paths,
                            "expected_behaviors": [],
                            "failure_indicators": [],
                        })

            if not observations:
                logger.warning("No observations or screenshots for session %s", session_id)
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
                score_data = await self._score_axis(axis, rubric, observations)

                # Apply per-axis auto/manual split (from score_service.AXIS_MIX)
                from .score_service import AXIS_MIX
                auto_score = score_data["score"]
                mix = AXIS_MIX.get(axis, {"auto": 0.6, "manual": 0.4})
                auto_ratio = mix["auto"]
                manual_ratio = mix["manual"]

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
                    ON CONFLICT (session_id, axis) DO UPDATE SET
                        score = EXCLUDED.score, confidence = EXCLUDED.confidence,
                        details = EXCLUDED.details, strengths = EXCLUDED.strengths,
                        risks = EXCLUDED.risks, scored_at = EXCLUDED.scored_at
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
                    "scored_at": datetime.utcnow(),
                    "scored_by": None,  # NULL = automated LLM scoring
                })

            except Exception as e:
                logger.exception("Failed to score axis %s for session %s: %s", axis, session_id, e)
                error_score = {
                    "axis": axis,
                    "score": 0.0,
                    "confidence": 0.0,
                    "details": [],
                    "strengths": [],
                    "risks": [f"スコアリングエラー: {str(e)[:200]}"],
                }
                all_scores.append(error_score)
                # Also write error score to DB so the dashboard shows something
                try:
                    err_id = str(uuid.uuid4())
                    await db.execute(text("""
                        INSERT INTO axis_scores
                        (id, session_id, tool_id, axis, axis_name_jp, score, confidence,
                         source, details, strengths, risks, scored_at, scored_by)
                        VALUES (:id, :sid, :tid, :axis, :name, :score, :conf,
                                :source, :details, :strengths, :risks, :scored_at, NULL)
                        ON CONFLICT (session_id, axis) DO UPDATE SET
                            score = EXCLUDED.score, confidence = EXCLUDED.confidence,
                            details = EXCLUDED.details, risks = EXCLUDED.risks,
                            scored_at = EXCLUDED.scored_at
                    """), {
                        "id": err_id, "sid": session_id, "tid": tool_id,
                        "axis": axis, "name": rubric["name_jp"],
                        "score": 0.0, "conf": 0.0, "source": "error",
                        "details": json.dumps({"error": str(e)[:500]}),
                        "strengths": "[]",
                        "risks": json.dumps([f"スコアリングエラー: {str(e)[:200]}"], ensure_ascii=False),
                        "scored_at": datetime.utcnow(),
                    })
                except Exception:
                    logger.warning("Failed to write error score for %s/%s", session_id, axis)

        # --- Apply completion rate penalty ---
        # Fetch total_planned / total_executed from session to compute real completion rate
        session_result = await db.execute(text("""
            SELECT total_planned, total_executed FROM audit_sessions WHERE id = :sid
        """), {"sid": session_id})
        session_row = session_result.fetchone()
        total_planned = session_row[0] if session_row and session_row[0] else len(observations)
        total_executed = session_row[1] if session_row and session_row[1] else len(observations)
        # Use the more conservative estimate (observations vs session metadata)
        if total_planned > 0:
            completion_rate = min(total_executed, len(observations)) / total_planned
        else:
            completion_rate = 1.0 if observations else 0.0

        if completion_rate < 0.3:
            # Less than 30% completion: cap all scores at 2.0 and mark as "insufficient"
            penalty_factor = completion_rate / 0.3  # 0.0 to 1.0
            await db.execute(text("""
                UPDATE axis_scores
                SET score = LEAST(score * :factor, 2.0),
                    confidence = LEAST(confidence * :factor, 0.30)
                WHERE session_id = :sid
            """), {
                "factor": penalty_factor,
                "sid": session_id,
            })
            # Update in-memory scores to reflect penalty
            for s in all_scores:
                s["score"] = min(s["score"] * penalty_factor, 2.0)
                s["confidence"] = min(s["confidence"] * penalty_factor, 0.3)
                s["completion_penalty"] = True
                s["completion_rate"] = completion_rate
            logger.warning(
                "Completion penalty applied for session %s: rate=%.1f%%, factor=%.2f",
                session_id, completion_rate * 100, penalty_factor,
            )
        elif completion_rate < 0.6:
            # 30-60% completion: reduce confidence significantly
            confidence_factor = 0.5 + (completion_rate - 0.3) / 0.6
            await db.execute(text("""
                UPDATE axis_scores
                SET confidence = LEAST(confidence * :factor, 0.60)
                WHERE session_id = :sid
            """), {
                "factor": confidence_factor,
                "sid": session_id,
            })
            for s in all_scores:
                s["confidence"] = min(s["confidence"] * confidence_factor, 0.6)
            logger.info(
                "Confidence penalty applied for session %s: rate=%.1f%%, factor=%.2f",
                session_id, completion_rate * 100, confidence_factor,
            )

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

        # --- 正確性 (correctness): proportion of tests with at least 1 screenshot ---
        tests_with_screenshots = sum(
            1 for obs in observations
            if obs.get("screenshots")
        )
        correctness = tests_with_screenshots / len(observations) if observations else 0.0

        # --- 網羅性 (comprehensiveness): completion rate ---
        total_planned = max(len(observations), 17)  # approximate; actual value from DB used for penalties
        comprehensiveness = min(1.0, len(observations) / max(total_planned, 1))

        # --- 解釈性 (intelligibility): average screenshots per test (more = more evidence) ---
        screenshot_counts = []
        for obs in observations:
            ss_count = len(obs.get("screenshots", []))
            screenshot_counts.append(ss_count)
        if screenshot_counts:
            avg_ss = statistics.mean(screenshot_counts)
            # 3+ screenshots per test = full intelligibility, 0 = low
            intelligibility = min(1.0, avg_ss / 3.0)
        else:
            intelligibility = 0.0

        return {
            "consistency": round(consistency, 3),
            "correctness": round(correctness, 3),
            "comprehensiveness": round(comprehensiveness, 3),
            "intelligibility": round(intelligibility, 3),
        }

    def _select_representative_screenshots(
        self,
        observations: list[dict],
        max_total: int = 10,
    ) -> list[str]:
        """Select the most representative screenshots across all observations.

        Strategy:
        - 1 screenshot from each test category (ensuring diversity)
        - Fill remaining slots from tests with the most screenshots
        - Deduplicate by path
        """
        seen_paths: set[str] = set()
        selected: list[str] = []

        # Group observations by category
        by_category: dict[str, list[dict]] = defaultdict(list)
        for obs in observations:
            cat = obs.get("category", "unknown")
            by_category[cat].append(obs)

        # Phase 1: Pick 1 screenshot per category for diversity
        for _cat, cat_obs in by_category.items():
            if len(selected) >= max_total:
                break
            for obs in cat_obs:
                found = False
                for ss_path in obs.get("screenshots", []):
                    if ss_path and ss_path not in seen_paths:
                        seen_paths.add(ss_path)
                        selected.append(ss_path)
                        found = True
                        break  # 1 per category
                if found:
                    break

        # Phase 2: Fill remaining slots from tests with the most screenshots
        remaining = max_total - len(selected)
        if remaining > 0:
            obs_by_ss_count = sorted(
                observations,
                key=lambda o: len(o.get("screenshots", [])),
                reverse=True,
            )
            for obs in obs_by_ss_count:
                if remaining <= 0:
                    break
                for ss_path in obs.get("screenshots", []):
                    if remaining <= 0:
                        break
                    if ss_path and ss_path not in seen_paths:
                        seen_paths.add(ss_path)
                        selected.append(ss_path)
                        remaining -= 1

        return selected

    @staticmethod
    def _resize_screenshot(img_data: bytes, max_width: int = 1024) -> bytes:
        """Resize screenshot to reduce API token cost.

        Retina screenshots from Chrome's captureVisibleTab are often 2x resolution.
        Resizing to max_width significantly reduces the image token count.
        """
        try:
            from PIL import Image
        except ImportError:
            logger.debug("Pillow not installed, skipping image resize")
            return img_data

        try:
            img = Image.open(io.BytesIO(img_data))
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception as e:
            logger.warning("Failed to resize screenshot: %s", e)
            return img_data

    async def _score_axis(
        self,
        axis: str,
        rubric: dict,
        observations: list[dict],
    ) -> dict:
        """Score a single axis using Claude with multimodal (text + screenshots).

        Screenshots are selected once (max 10 total, deduplicated, diverse by
        category) and resized to reduce API token cost.
        """
        prompt_text = self._build_rubric_prompt(axis, rubric, observations)

        # Build multimodal content: text prompt + screenshot images
        content = []

        # Select representative screenshots across all observations (max 10, deduplicated)
        selected_paths = self._select_representative_screenshots(observations, max_total=10)
        for ss_path in selected_paths:
            image_data = self._load_screenshot(ss_path)
            if image_data:
                # Detect media type from actual content (magic bytes)
                import base64 as _b64
                media_type = "image/png"  # default
                try:
                    raw_head = _b64.b64decode(image_data[:32])
                    if raw_head[:3] == b'\xff\xd8\xff':
                        media_type = "image/jpeg"
                    elif raw_head[:8] == b'\x89PNG\r\n\x1a\n':
                        media_type = "image/png"
                    elif raw_head[:4] == b'RIFF':
                        media_type = "image/webp"
                except Exception:
                    pass
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                })

        # Add text prompt
        content.append({"type": "text", "text": prompt_text})

        # Run synchronous Anthropic API call in a thread to avoid blocking
        # the asyncio event loop (this method is called from async context)
        import asyncio
        import functools

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            functools.partial(
                self.client.messages.create,
                model=self.model,
                max_tokens=2000,
                temperature=0.0,
                messages=[{"role": "user", "content": content}],
            ),
        )

        response_text = response.content[0].text
        return self._parse_score_response(axis, rubric, response_text)

    def _load_screenshot(self, screenshot_path: str) -> str | None:
        """Load a screenshot file and return base64 data."""
        import base64
        from pathlib import Path

        if not screenshot_path:
            return None

        rel_path = screenshot_path.lstrip("/")

        if rel_path.startswith("screenshots/"):
            # New path: /screenshots/session_id/0001.png -> resolve from screenshots_dir
            rel_path = rel_path[len("screenshots/"):]
            file_path = Path(settings.screenshots_dir) / rel_path
        elif rel_path.startswith("static/"):
            # Legacy path: /static/screenshots/extension/session_id/0001.png
            rel_path = rel_path[len("static/"):]
            static_dir = Path(__file__).parent.parent / "static"
            file_path = static_dir / rel_path
        else:
            static_dir = Path(__file__).parent.parent / "static"
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
            # Resize to reduce API token cost (retina screenshots can be 2x+)
            data = self._resize_screenshot(data, max_width=1024)
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
            prompt_text_preview = obs['prompt'][:500] if obs['prompt'] else '(プロンプトなし)'
            screenshots = obs.get("screenshots", [])
            rt_ms = obs.get('response_time_ms', 0) or 0
            if rt_ms > 0:
                rt_sec = rt_ms / 1000.0
                entry += f"テスト「{prompt_text_preview}」— 応答時間: {rt_sec:.1f}秒, スクリーンショット{len(screenshots)}枚\n"
            else:
                entry += f"テスト「{prompt_text_preview}」— 応答時間: 未計測, スクリーンショット{len(screenshots)}枚\n"
            if screenshots:
                entry += f"（上記の画像を参照してスライド品質を評価してください）\n"
            if obs['error']:
                entry += f"エラー: {obs['error']}\n"
            if obs['expected_behaviors']:
                entry += f"期待される動作: {', '.join(obs['expected_behaviors'][:5])}\n"
            obs_entries.append(entry)

        observations_text = "\n".join(obs_entries)

        total_planned = max(len(observations), 17)  # approximate
        completion_rate = len(observations) / max(total_planned, 1) * 100

        return f"""あなたはスライド作成・資料作成AIツール（Gamma等）の専門的な品質評価者です。

## 評価の前提（重要）
このシステムは以下の手順で監査データを収集しています:
1. 人間のテスターがChrome拡張機能を使い、スライド作成AIツールをテストします
2. 各テストで、テスターはプロンプトをAIツールに入力し、生成されたスライドのスクリーンショットを撮影します
3. タイマーが応答時間を計測します（response_time_ms として記録）
4. スクリーンショットが唯一の出力証拠です — テキスト応答データは存在しません

あなたの役割は、添付されたスクリーンショット画像を視覚的に分析し、
「{rubric['name_jp']}」（{axis}）軸のスコアを算出することです。

## 評価の原則
- スクリーンショット画像を主要な評価根拠としてください（テキスト応答は存在しません）
- スライドのレイアウト、デザイン、コンテンツの質、日本語テキストの品質を画像から直接評価してください
- テキスト応答がないことを減点理由にしないでください（そもそもテキスト応答は収集していません）
- 応答時間データ（response_time_ms）が各テストに記録されています。0ms/未計測の場合は応答速度評価をスキップしてください

## 応答時間の評価基準
応答時間データが提供されている場合、以下の基準で応答速度を評価してください:
- 10秒以内: 優秀 (5.0)
- 30秒以内: 良好 (4.0)
- 60秒以内: 許容範囲 (3.0)
- 120秒以内: やや遅い (2.0)
- 120秒超: 遅い (1.0)
- 未計測(0ms): この項目はスキップし、他の項目のみで評価

## テストの完遂率
- 完遂率: {completion_rate:.0f}%（{len(observations)}件/{total_planned}件）
- 未実施のテストが多い場合、confidenceを低く設定してください（完遂率に比例）
- テスト完遂率が低い場合（30%未満）、確信度を大幅に下げ、スコアには「評価不十分」と注記してください

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
    {{"rule_id": "ルールID", "rule_name_jp": "日本語名", "score": 4.0, "weight": 2.0, "evidence": "スクリーンショットから確認した具体的な根拠（日本語）", "severity": "medium"}}
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
- evidence にはスクリーンショットから確認した具体的な視覚的根拠を記述してください
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
