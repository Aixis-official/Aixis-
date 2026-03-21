#!/usr/bin/env python3
"""AIXIS ローカルエージェント — ユーザーのPC上でブラウザを開いて監査を実行し、結果をプラットフォームにアップロード。

使い方:
    python scripts/aixis_local_agent.py

環境変数:
    AIXIS_PLATFORM_URL      — プラットフォームURL (デフォルト: https://platform.aixis.jp)
    AIXIS_API_KEY            — APIキー (ダッシュボードのAPIキーページで発行)
    AIXIS_ANTHROPIC_API_KEY  — Anthropic APIキー (AI監査用)
"""
import asyncio
import json
import os
import sys
import time
import threading
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PLATFORM_URL = os.environ.get("AIXIS_PLATFORM_URL", "https://platform.aixis.jp").rstrip("/")
API_KEY = os.environ.get("AIXIS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("AIXIS_ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))


def api_request(method: str, path: str, body: dict | None = None) -> dict:
    import urllib.request
    url = f"{PLATFORM_URL}/api/v1{path}"
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    data = json.dumps(body, default=str).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


async def main():
    print("=" * 60)
    print("  AIXIS ローカルエージェント")
    print("  ブラウザを開いてAI監査を実行します")
    print("=" * 60)

    from aixis_agent.orchestrator.pipeline import Pipeline
    from aixis_agent.scoring.engine import ScoringEngine, load_scoring_rules
    from aixis_agent.orchestrator.session import SessionStore

    if ANTHROPIC_API_KEY:
        os.environ["AIXIS_ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY

    # Find target configs
    config_dir = PROJECT_ROOT / "config" / "targets"
    configs = sorted(config_dir.glob("*.yaml"))
    if not configs:
        print("エラー: 設定ファイルがありません")
        return

    print("\n監査対象を選択:")
    for i, cfg in enumerate(configs, 1):
        print(f"  {i}. {cfg.stem}")

    choice = int(input("\n番号: ")) - 1
    target_config_path = configs[choice]
    target_name = target_config_path.stem
    print(f"\n対象: {target_name}")

    # Setup
    output_dir = PROJECT_ROOT / "output"
    login_event = threading.Event()
    abort_event = threading.Event()

    pipeline = Pipeline(
        target_config_path=target_config_path,
        patterns_dir=PROJECT_ROOT / "config" / "patterns",
        output_dir=output_dir,
        budget_overrides={"max_calls": 400, "max_calls_per_case": 20, "max_cost_jpy": 40},
    )

    # Login prompt
    if pipeline.target_config.wait_for_manual_login:
        print("\n" + "=" * 60)
        print("  ブラウザが開きます。ログインしてください。")
        print("  ログイン完了後、Enter を押してください。")
        print("=" * 60)
        def wait_enter():
            input("\n  [Enter] でログイン完了 → ")
            login_event.set()
            print("  監査開始！\n")
        threading.Thread(target=wait_enter, daemon=True).start()

    # Run
    start_time = time.time()
    def on_progress(done, total, case_id, cat):
        pct = int(done / total * 100) if total > 0 else 0
        print(f"  [{pct:3d}%] {done}/{total} ({cat})", end="\r", flush=True)

    session_id = await pipeline.run(
        login_event=login_event, abort_event=abort_event, progress_callback=on_progress,
    )

    elapsed = time.time() - start_time
    print(f"\n\n監査完了！({elapsed:.1f}秒)")

    # Load & score
    store = SessionStore(output_dir / f"{session_id}.db")
    cases = store.list_test_cases()
    results = store.list_results()
    print(f"  結果: {len(results)}/{len(cases)} 件")

    rules_path = PROJECT_ROOT / "config" / "scoring" / "scoring_rules.yaml"
    axis_scores = []
    if rules_path.exists():
        engine = ScoringEngine(load_scoring_rules(rules_path))
        axis_scores = engine.score_all(results, cases, target_name)
        print(f"  5軸スコア: {len(axis_scores)} 項目")

    # Upload to platform
    if API_KEY:
        print(f"\nプラットフォームにアップロード中...")
        try:
            sess = api_request("POST", "/agent/sessions", {
                "tool_id": target_name,
                "target_config_name": target_name,
            })
            rid = sess["session_id"]
            print(f"  セッション: {sess['session_code']}")

            upload = {
                "total_planned": len(cases),
                "total_executed": len(results),
                "was_aborted": False,
                "test_cases": [{
                    "id": c.id,
                    "category": c.category.value if hasattr(c.category, 'value') else str(c.category),
                    "prompt": c.prompt,
                    "metadata": c.metadata if isinstance(c.metadata, dict) else {},
                    "expected_behaviors": getattr(c, 'expected_behaviors', []),
                    "failure_indicators": getattr(c, 'failure_indicators', []),
                    "tags": getattr(c, 'tags', []),
                } for c in cases],
                "test_results": [{
                    "test_case_id": r.test_case_id,
                    "category": r.category.value if hasattr(r.category, 'value') else str(r.category),
                    "prompt_sent": r.prompt_sent,
                    "response_raw": r.response_raw or "",
                    "response_time_ms": r.response_time_ms,
                    "error": r.error,
                    "ai_steps_taken": r.metadata.get("ai_steps_taken", 0) if isinstance(r.metadata, dict) else 0,
                    "ai_calls_used": r.metadata.get("ai_calls_used", 0) if isinstance(r.metadata, dict) else 0,
                    "ai_tokens_input": r.metadata.get("ai_tokens_input", 0) if isinstance(r.metadata, dict) else 0,
                    "ai_tokens_output": r.metadata.get("ai_tokens_output", 0) if isinstance(r.metadata, dict) else 0,
                } for r in results],
                "axis_scores": [{
                    "axis": s.axis,
                    "axis_name_jp": s.axis_name_jp,
                    "score": float(s.score),
                    "confidence": float(s.confidence),
                    "source": s.source,
                    "details": s.details if isinstance(s.details, dict) else {},
                    "strengths": s.strengths if isinstance(s.strengths, list) else [],
                    "risks": s.risks if isinstance(s.risks, list) else [],
                } for s in axis_scores] if axis_scores else [],
                "volume_metrics": {},
            }

            result = api_request("POST", f"/agent/sessions/{rid}/results", upload)
            print(f"  アップロード完了！")
            print(f"\n  ダッシュボードで確認: {PLATFORM_URL}/dashboard/audits/{rid}")
        except Exception as e:
            print(f"  アップロード失敗: {e}")
            import traceback
            traceback.print_exc()

    print(f"\nローカル結果: output/{session_id}.db")
    print("完了！")


if __name__ == "__main__":
    asyncio.run(main())
