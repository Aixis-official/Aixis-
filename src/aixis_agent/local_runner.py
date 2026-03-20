"""Local audit runner — runs browser on your machine, reports to platform.

Usage:
  python -m aixis_agent.local_runner gamma --profile スライド作成AI \\
    --platform https://platform.aixis.jp --api-key axk_...

Flow:
  1. Creates a session on the platform (or runs locally only)
  2. Opens a visible browser on your screen
  3. Waits for you to log in to the target tool
  4. Runs all test cases with AI-assisted browser automation
  5. Scores results and uploads to the platform
  6. Results appear on the platform dashboard

The browser stays visible throughout — you can watch the AI work.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()
app = typer.Typer(name="aixis-local", help="ローカルエージェント: ブラウザを表示して監査実行")


DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_OUTPUT_DIR = Path("output")


def _resolve_target_path(tool_name: str) -> Path:
    for name in [tool_name, tool_name.lower()]:
        p = DEFAULT_CONFIG_DIR / "targets" / f"{name}.yaml"
        if p.exists():
            return p
    return DEFAULT_CONFIG_DIR / "targets" / f"{tool_name}.yaml"


def _resolve_profile(name: str):
    from .profiles.registry import get_profile, search_profiles
    profile = get_profile(name, DEFAULT_CONFIG_DIR / "profiles")
    if profile:
        return profile
    candidates = search_profiles(name, DEFAULT_CONFIG_DIR / "profiles")
    console.print(f"[red]種別 '{name}' が見つかりません。[/red]")
    if candidates:
        console.print("[yellow]もしかして:[/yellow]")
        for c in candidates[:5]:
            console.print(f"  [cyan]{c.get('name_jp', c['id'])}[/cyan]")
    raise typer.Exit(1)


def _upload_results(
    platform_url: str,
    api_key: str,
    session_id: str,
    results: list,
    cases: list,
    axis_scores_data: list[dict],
    total_planned: int,
    total_executed: int,
    ai_volume: dict,
    was_aborted: bool,
    reliability_data: dict | None,
) -> dict:
    """Upload results to the platform API."""
    # Convert agent models to API payload
    test_cases = []
    for case in cases:
        test_cases.append({
            "id": case.id,
            "category": case.category.value if hasattr(case.category, 'value') else str(case.category),
            "prompt": case.prompt,
            "metadata": case.metadata or {},
            "expected_behaviors": case.expected_behaviors or [],
            "failure_indicators": case.failure_indicators or [],
            "tags": case.tags or [],
        })

    test_results = []
    for r in results:
        meta = r.metadata or {}
        test_results.append({
            "test_case_id": r.test_case_id,
            "category": r.category.value if hasattr(r.category, 'value') else str(r.category),
            "prompt_sent": r.prompt_sent,
            "response_raw": r.response_raw,
            "response_time_ms": r.response_time_ms,
            "error": r.error,
            "screenshot_path": r.screenshot_path,
            "executed_at": r.timestamp.isoformat() if r.timestamp else datetime.now(timezone.utc).isoformat(),
            "ai_steps_taken": meta.get("ai_steps_taken", 0),
            "ai_calls_used": meta.get("ai_calls_used", 0),
            "ai_tokens_input": meta.get("ai_tokens_input", 0),
            "ai_tokens_output": meta.get("ai_tokens_output", 0),
        })

    axis_scores = []
    for s in axis_scores_data:
        axis_scores.append({
            "axis": s["axis"],
            "axis_name_jp": s.get("axis_name_jp", ""),
            "score": s.get("score", 0),
            "confidence": s.get("confidence", 0),
            "source": s.get("source", "auto"),
            "details": s.get("details", {}),
            "strengths": s.get("strengths", []),
            "risks": s.get("risks", []),
        })

    payload = {
        "total_planned": total_planned,
        "total_executed": total_executed,
        "was_aborted": was_aborted,
        "test_cases": test_cases,
        "test_results": test_results,
        "axis_scores": axis_scores,
        "volume_metrics": ai_volume or {},
        "reliability_data": reliability_data,
    }

    url = f"{platform_url.rstrip('/')}/api/v1/agent/sessions/{session_id}/results"
    resp = httpx.post(
        url,
        json=payload,
        headers={"X-API-Key": api_key},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _create_remote_session(platform_url: str, api_key: str, tool_id: str, profile_id: str = "") -> dict:
    """Create a session on the platform."""
    url = f"{platform_url.rstrip('/')}/api/v1/agent/sessions"
    resp = httpx.post(
        url,
        json={"tool_id": tool_id, "profile_id": profile_id},
        headers={"X-API-Key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@app.command()
def audit(
    tool: str = typer.Argument(..., help="検証対象ツール名 (例: gamma)"),
    profile_name: str = typer.Option(..., "--profile", "-p", help="ツール種別 (例: スライド作成AI)"),
    platform_url: str = typer.Option("", "--platform", envvar="AIXIS_PLATFORM_URL", help="プラットフォームURL (例: https://platform.aixis.jp)"),
    api_key: str = typer.Option("", "--api-key", envvar="AIXIS_API_KEY", help="プラットフォームAPIキー"),
    tool_id: str = typer.Option("", "--tool-id", envvar="AIXIS_TOOL_ID", help="プラットフォーム上のツールID"),
    output_dir: Path = typer.Option(DEFAULT_OUTPUT_DIR, "--output", "-o"),
):
    """
    ローカルブラウザで監査を実行し、結果をプラットフォームにアップロード。

    使い方:
      # ローカルのみ（プラットフォームなし）
      python -m aixis_agent.local_runner audit gamma -p スライド作成AI

      # プラットフォームに結果をアップロード
      python -m aixis_agent.local_runner audit gamma -p スライド作成AI \\
        --platform https://platform.aixis.jp \\
        --api-key axk_your_key \\
        --tool-id your-tool-uuid
    """
    from .orchestrator.pipeline import Pipeline
    from .profiles.registry import get_categories_for_profile

    profile = _resolve_profile(profile_name)
    categories = get_categories_for_profile(profile)
    target_path = _resolve_target_path(tool)

    if not target_path.exists():
        console.print(f"[red]ターゲット設定が見つかりません: {target_path}[/red]")
        raise typer.Exit(1)

    # Check for Anthropic API key
    if not os.environ.get("AIXIS_ANTHROPIC_API_KEY"):
        console.print("[red]AIXIS_ANTHROPIC_API_KEY 環境変数が設定されていません[/red]")
        raise typer.Exit(1)

    # Create remote session if platform is configured
    remote_session_id = None
    if platform_url and api_key and tool_id:
        console.print(f"\n[blue]プラットフォームに接続中...[/blue] {platform_url}")
        try:
            session_data = _create_remote_session(platform_url, api_key, tool_id)
            remote_session_id = session_data["session_id"]
            console.print(f"[green]セッション作成: {session_data['session_code']}[/green]")
        except Exception as e:
            console.print(f"[yellow]プラットフォーム接続失敗: {e}[/yellow]")
            console.print("[yellow]ローカルのみで実行します[/yellow]")

    # Show welcome
    from .patterns.generator import generate_all
    preview_cases = generate_all(DEFAULT_CONFIG_DIR / "patterns", categories)
    console.print()
    console.print(Panel(
        f"[bold]対象ツール:[/bold] {tool}\n"
        f"[bold]検証種別:[/bold] {profile.get('name_jp', profile['id'])}\n"
        f"[bold]テストケース数:[/bold] {len(preview_cases)}件\n"
        f"[bold]実行モード:[/bold] ローカルブラウザ（画面表示あり）\n"
        f"[bold]プラットフォーム:[/bold] {'✅ ' + platform_url if remote_session_id else '❌ ローカルのみ'}",
        title="[bold blue]Aixis ローカルエージェント[/bold blue]",
        border_style="blue",
    ))
    console.print()
    console.print("[yellow]ブラウザが開きます。ツールにログインしてください。[/yellow]")
    console.print("[yellow]ログイン完了後、プラットフォームUIで「ログイン完了」ボタンを押してください。[/yellow]")
    console.print()

    # Run pipeline
    pipeline = Pipeline(
        target_config_path=target_path,
        patterns_dir=DEFAULT_CONFIG_DIR / "patterns",
        output_dir=output_dir,
        categories=categories,
        dry_run=False,
        profile=profile,
    )

    local_session_id = asyncio.run(pipeline.run())

    console.print()
    console.print("[green]監査完了！[/green]")

    # Upload results if platform is configured
    if remote_session_id and platform_url and api_key:
        console.print(f"\n[blue]結果をプラットフォームにアップロード中...[/blue]")
        try:
            # Load results from local session DB
            from .store.session_store import SessionStore
            from .scoring.engine import ScoringEngine

            store = SessionStore(output_dir / f"{local_session_id}.db")
            results = store.get_results()
            cases = store.get_test_cases()
            total_planned = store.get_total_planned()
            total_executed = len(results)

            # Score
            scoring = ScoringEngine(profile=profile)
            axis_scores_data = scoring.score_all(results, cases)

            # Get AI volume from pipeline
            ai_volume = getattr(pipeline, '_ai_volume', {})

            # Calculate reliability
            from .scoring.reliability import calculate_reliability
            reliability_data = calculate_reliability(results, cases, total_planned)

            upload_result = _upload_results(
                platform_url=platform_url,
                api_key=api_key,
                session_id=remote_session_id,
                results=results,
                cases=cases,
                axis_scores_data=axis_scores_data,
                total_planned=total_planned,
                total_executed=total_executed,
                ai_volume=ai_volume,
                was_aborted=False,
                reliability_data=reliability_data,
            )

            console.print(Panel(
                f"[bold]セッションID:[/bold] {remote_session_id}\n"
                f"[bold]テスト結果:[/bold] {upload_result.get('test_results_count', 0)}件\n"
                f"[bold]スコア:[/bold] {upload_result.get('axis_scores_count', 0)}軸\n"
                f"[bold]ステータス:[/bold] {upload_result.get('final_status', '?')}\n\n"
                f"[bold]ダッシュボード:[/bold] {platform_url}",
                title="[bold green]プラットフォームにアップロード完了[/bold green]",
                border_style="green",
            ))
        except Exception as e:
            console.print(f"[red]アップロード失敗: {e}[/red]")
            console.print(f"[yellow]ローカルの結果は保存されています: {local_session_id}[/yellow]")
            import traceback
            traceback.print_exc()
    else:
        console.print(Panel(
            f"[bold]セッションID:[/bold] {local_session_id}\n\n"
            f"レポート生成:\n"
            f"  [bold green]aixis レポート {local_session_id}[/bold green]",
            title="[bold green]ローカル検証完了[/bold green]",
            border_style="green",
        ))


if __name__ == "__main__":
    app()
