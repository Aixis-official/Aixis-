"""CLI entry point for the Aixis destructive testing agent.

コマンド体系:
  aixis 検証 <ツール名> --種別 <プロファイル>    ← メインの検証コマンド
  aixis 検証 gamma --種別 スライド作成AI         ← 例: Gammaの検証
  aixis プレビュー gamma --種別 スライド作成AI   ← 実行前の確認
  aixis レポート <セッションID>                  ← レポート生成
  aixis 一覧                                     ← セッション一覧
  aixis 種別一覧                                 ← 全プロファイル表示
  aixis 種別検索 <キーワード>                    ← プロファイル検索

英語コマンドも併用可能:
  verify / preview / report / list / profiles / search
"""

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="aixis",
    help="Aixis 破壊的テスト自動化エージェント\n\n"
         "使い方:\n"
         "  aixis 検証 gamma --種別 スライド作成AI\n"
         "  aixis レポート <セッションID>\n"
         "  aixis 種別一覧\n"
         "  aixis 種別検索 医療",
    no_args_is_help=True,
)
console = Console()

DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_PROFILES_DIR = DEFAULT_CONFIG_DIR / "profiles"


def _resolve_profile(name: str):
    """名前からプロファイルを検索。見つからなければエラー表示して終了。"""
    from .profiles.registry import get_profile, search_profiles

    profile = get_profile(name, DEFAULT_PROFILES_DIR)
    if profile:
        return profile

    # 見つからない場合: 似た候補を提示
    candidates = search_profiles(name, DEFAULT_PROFILES_DIR)
    console.print(f"[red]種別 '{name}' が見つかりません。[/red]")
    if candidates:
        console.print("[yellow]もしかして:[/yellow]")
        for c in candidates[:5]:
            console.print(f"  [cyan]{c.get('name_jp', c['id'])}[/cyan] ({c['id']})")
    console.print("\n利用可能な種別は [cyan]aixis 種別一覧[/cyan] で確認できます。")
    console.print("キーワード検索: [cyan]aixis 種別検索 <キーワード>[/cyan]")
    raise typer.Exit(1)


def _resolve_target_path(tool_name: str) -> Path:
    """ツール名からターゲット設定ファイルのパスを解決。"""
    for name in [tool_name, tool_name.lower(), tool_name.lower().replace(" ", "_"), tool_name.lower().replace(" ", "-")]:
        for p in [Path(name), DEFAULT_CONFIG_DIR / "targets" / f"{name}.yaml"]:
            if p.exists():
                return p
    return DEFAULT_CONFIG_DIR / "targets" / f"{tool_name}.yaml"


def _print_welcome(tool_name: str, profile: dict, case_count: int, categories: list[str]) -> None:
    """検証開始時のウェルカムメッセージ。"""
    CATEGORY_NAMES = {
        "dialect": "方言", "long_input": "長文", "contradictory": "矛盾",
        "ambiguous": "曖昧", "keigo_mixing": "敬語混合", "unicode_edge": "Unicode",
        "business_jp": "商習慣", "multi_step": "複合指示", "broken_grammar": "文法破壊",
    }
    cat_display = ", ".join(CATEGORY_NAMES.get(c, c) for c in categories)

    console.print()
    console.print(Panel(
        f"[bold]対象ツール:[/bold] {tool_name}\n"
        f"[bold]検証種別:[/bold] {profile.get('name_jp', profile['id'])}\n"
        f"[bold]テストケース数:[/bold] {case_count}件\n"
        f"[bold]検証カテゴリ:[/bold] {cat_display}",
        title="[bold blue]Aixis 破壊的テスト[/bold blue]",
        border_style="blue",
    ))


def _print_preview(cases: list, profile: dict) -> None:
    """プレビュー表示。"""
    from collections import Counter
    from .core.enums import TestCategory

    CATEGORY_NAMES = {
        "dialect": "方言", "long_input": "長文", "contradictory": "矛盾",
        "ambiguous": "曖昧", "keigo_mixing": "敬語混合", "unicode_edge": "Unicode",
        "business_jp": "商習慣", "multi_step": "複合指示", "broken_grammar": "文法破壊",
    }

    cat_counts = Counter(tc.category.value for tc in cases)
    primary_cats = set()
    for c in profile.get("primary_categories", []):
        primary_cats.add(c.value if isinstance(c, TestCategory) else c)

    table = Table(title="テストケース内訳", show_header=True)
    table.add_column("カテゴリ", style="cyan")
    table.add_column("日本語名")
    table.add_column("件数", justify="right", style="green")
    table.add_column("優先度")

    for cat, count in sorted(cat_counts.items()):
        priority = "[bold red]主要[/bold red]" if cat in primary_cats else "[dim]補助[/dim]"
        table.add_row(cat, CATEGORY_NAMES.get(cat, cat), str(count), priority)

    console.print(table)

    # スコアリング重み
    weights = profile.get("scoring_weights", {})
    if weights:
        AXIS_NAMES = {
            "practicality": "実務適性",
            "cost_performance": "費用対効果",
            "localization": "日本語能力",
            "safety": "信頼性・安全性",
            "uniqueness": "革新性",
        }
        console.print("\n[bold]スコアリング重み:[/bold]")
        for axis, w in weights.items():
            bar = "█" * int(w * 5) + "░" * (10 - int(w * 5))
            console.print(f"  {AXIS_NAMES.get(axis, axis):　<8} {bar} {w:.1f}x")

    if profile.get("evaluation_focus"):
        console.print("\n[bold]重点評価項目:[/bold]")
        for focus in profile["evaluation_focus"]:
            console.print(f"  [yellow]→[/yellow] {focus}")

    console.print("\n[bold]サンプルプロンプト:[/bold]")
    for tc in cases[:5]:
        preview = tc.prompt[:100] + "..." if len(tc.prompt) > 100 else tc.prompt
        console.print(f"  [dim][{tc.category.value}][/dim] {preview}")

    console.print(f"\n[dim]実行するには: aixis 検証 <ツール名> --種別 {profile.get('name_jp', profile['id'])}[/dim]")


# ===================================================================
#  メインコマンド: 検証
# ===================================================================

@app.command("検証", help="AIツールの破壊的テストを実行する")
@app.command("verify", hidden=True)
def verify(
    tool: str = typer.Argument(..., help="検証対象ツール名 (例: gamma)"),
    種別: str = typer.Option(..., "--種別", "--profile", "-p", help="ツール種別 (例: スライド作成AI)"),
    dry_run: bool = typer.Option(False, "--プレビュー", "--dry-run", "-d", help="実行せずにテスト内容を確認"),
    output_dir: Path = typer.Option(DEFAULT_OUTPUT_DIR, "--出力先", "--output", "-o"),
    session_id: Optional[str] = typer.Option(None, "--セッション", "--session"),
) -> None:
    """
    AIツールの破壊的テストを実行します。

    使い方:
      aixis 検証 gamma --種別 スライド作成AI
      aixis 検証 gamma --種別 スライド作成AI --プレビュー
    """
    from .orchestrator.pipeline import Pipeline
    from .profiles.registry import get_categories_for_profile

    profile = _resolve_profile(種別)
    categories = get_categories_for_profile(profile)

    target_path = _resolve_target_path(tool)
    if not target_path.exists():
        console.print(f"[red]ターゲット設定が見つかりません: {target_path}[/red]")
        console.print(f"  [dim]config/targets/{tool}.yaml を作成してください[/dim]")
        console.print(f"  [dim]テンプレート: config/targets/example_target.yaml[/dim]")
        raise typer.Exit(1)

    from .patterns.generator import generate_all
    preview_cases = generate_all(DEFAULT_CONFIG_DIR / "patterns", categories)
    _print_welcome(tool, profile, len(preview_cases), categories)

    if dry_run:
        _print_preview(preview_cases, profile)
        return

    pipeline = Pipeline(
        target_config_path=target_path,
        patterns_dir=DEFAULT_CONFIG_DIR / "patterns",
        output_dir=output_dir,
        categories=categories,
        dry_run=False,
        profile=profile,
    )

    result_session_id = asyncio.run(pipeline.run(session_id=session_id))

    console.print()
    console.print(Panel(
        f"セッションID: [cyan]{result_session_id}[/cyan]\n\n"
        f"レポート生成:\n"
        f"  [bold green]aixis レポート {result_session_id}[/bold green]",
        title="[bold green]検証完了[/bold green]",
        border_style="green",
    ))


# ===================================================================
#  プレビュー（ショートカット）
# ===================================================================

@app.command("プレビュー", help="検証内容を実行せずに確認する")
@app.command("preview", hidden=True)
def preview(
    tool: str = typer.Argument(..., help="検証対象ツール名"),
    種別: str = typer.Option(..., "--種別", "--profile", "-p", help="ツール種別"),
) -> None:
    """テスト内容のプレビュー。"""
    from .profiles.registry import get_categories_for_profile

    profile = _resolve_profile(種別)
    categories = get_categories_for_profile(profile)

    from .patterns.generator import generate_all
    cases = generate_all(DEFAULT_CONFIG_DIR / "patterns", categories)
    _print_welcome(tool, profile, len(cases), categories)
    _print_preview(cases, profile)


# ===================================================================
#  レポート生成
# ===================================================================

@app.command("レポート", help="検証結果から監査レポートを生成する")
@app.command("report", hidden=True)
def report_cmd(
    session: str = typer.Argument(..., help="セッションID"),
    format: str = typer.Option("html", "--形式", "--format", "-f", help="html / json / pdf / all"),
    output_dir: Path = typer.Option(DEFAULT_OUTPUT_DIR, "--出力先", "--output", "-o"),
) -> None:
    """
    使い方:
      aixis レポート session-abc123
      aixis レポート session-abc123 --形式 all
    """
    from .orchestrator.session import SessionStore
    from .reporting.builder import build_report
    from .reporting.html_renderer import HTMLRenderer
    from .reporting.json_renderer import JSONRenderer
    from .reporting.pdf_renderer import PDFRenderer

    db_path = output_dir / f"{session}.db"
    if not db_path.exists():
        console.print(f"[red]セッション '{session}' が見つかりません。[/red]")
        console.print("[dim]aixis 一覧 で確認できます。[/dim]")
        raise typer.Exit(1)

    store = SessionStore(db_path)
    scoring_rules = DEFAULT_CONFIG_DIR / "scoring" / "scoring_rules.yaml"

    try:
        console.print(f"\n[bold blue]レポート生成中...[/bold blue]")
        audit_report = build_report(session, store, scoring_rules)

        output_base = output_dir / f"report-{session}"
        formats = format.split(",") if "," in format else [format]
        if "all" in formats:
            formats = ["html", "json", "pdf"]

        generated = []
        for fmt in formats:
            fmt = fmt.strip()
            try:
                if fmt == "html":
                    path = HTMLRenderer().render(audit_report, output_base)
                elif fmt == "json":
                    path = JSONRenderer().render(audit_report, output_base)
                elif fmt == "pdf":
                    path = PDFRenderer().render(audit_report, output_base)
                else:
                    console.print(f"  [yellow]未対応: {fmt}[/yellow]")
                    continue
                generated.append((fmt.upper(), str(path)))
                console.print(f"  [green]{fmt.upper()}:[/green] {path}")
            except RuntimeError as e:
                console.print(f"  [yellow]{fmt.upper()}: {e}[/yellow]")

        g = audit_report.overall_grade.value
        s = audit_report.overall_score
        gc = "green" if g in ("S", "A") else "yellow" if g in ("B", "C") else "red"

        console.print()
        console.print(Panel(
            f"[bold]総合評価:[/bold] [{gc}]{g}ランク ({s:.1f}点)[/{gc}]\n"
            f"[bold]テスト数:[/bold] {audit_report.total_tests}件\n"
            + "\n".join(f"  {fmt}: {path}" for fmt, path in generated),
            title="[bold green]レポート生成完了[/bold green]",
            border_style="green",
        ))
    finally:
        store.close()


# ===================================================================
#  セッション一覧
# ===================================================================

@app.command("一覧", help="過去の検証セッション一覧")
@app.command("list", hidden=True)
def list_sessions(
    output_dir: Path = typer.Option(DEFAULT_OUTPUT_DIR, "--出力先", "--output", "-o"),
) -> None:
    from .core.enums import AuditStatus
    from .orchestrator.session import SessionStore

    db_files = list(output_dir.glob("session-*.db"))
    if not db_files:
        console.print("[yellow]検証セッションがまだありません。[/yellow]")
        console.print("[dim]aixis 検証 <ツール名> --種別 <種別> で開始[/dim]")
        return

    table = Table(title="検証セッション一覧")
    table.add_column("セッションID", style="cyan")
    table.add_column("対象ツール")
    table.add_column("開始日時")
    table.add_column("進捗")
    table.add_column("状態")

    for db_file in sorted(db_files, reverse=True):
        session_id = db_file.stem
        store = SessionStore(db_file)
        try:
            session = store.get_session(session_id)
            if session:
                sc = "green" if session.status == AuditStatus.COMPLETED else "yellow"
                table.add_row(
                    session.session_id, session.target_tool,
                    session.started_at.strftime("%Y-%m-%d %H:%M"),
                    f"{session.total_executed}/{session.total_planned}",
                    f"[{sc}]{session.status}[/{sc}]",
                )
        finally:
            store.close()

    console.print(table)
    console.print(f"\n[dim]レポート生成: aixis レポート <セッションID>[/dim]")


# ===================================================================
#  種別一覧（カテゴリ別グループ表示）
# ===================================================================

@app.command("種別一覧", help="対応AIツール種別の一覧（29種別）")
@app.command("profiles", hidden=True)
def list_profiles_cmd() -> None:
    """対応しているAIツール種別の一覧を表示します。"""
    from .profiles.registry import list_profiles

    profiles = list_profiles(DEFAULT_PROFILES_DIR)

    # カテゴリでグループ化
    groups: dict[str, list] = {}
    for p in profiles:
        cat = p.get("category_jp", "その他")
        groups.setdefault(cat, []).append(p)

    for group_name, items in groups.items():
        table = Table(title=f"[bold]{group_name}[/bold]", show_header=True, title_style="bold blue")
        table.add_column("種別名", style="cyan", min_width=16)
        table.add_column("説明", max_width=40)
        table.add_column("対象ツール例", style="dim", max_width=35)

        for p in items:
            table.add_row(
                p.get("name_jp", p["id"]),
                p.get("description_jp", "")[:40],
                p.get("examples", "")[:35],
            )

        console.print(table)
        console.print()

    console.print(f"[bold]合計 {len(profiles)} 種別[/bold]")
    console.print()
    console.print("[bold]使い方:[/bold]")
    console.print("  [green]aixis 検証 gamma --種別 スライド作成AI[/green]")
    console.print("  [green]aixis プレビュー gamma --種別 スライド作成AI[/green]")
    console.print("  [green]aixis 種別検索 医療[/green]  ← キーワードで検索")


# ===================================================================
#  種別検索
# ===================================================================

@app.command("種別検索", help="キーワードでAIツール種別を検索")
@app.command("search", hidden=True)
def search_profiles_cmd(
    keyword: str = typer.Argument(..., help="検索キーワード (例: 医療, 議事録, 契約)"),
) -> None:
    """
    キーワードでツール種別を検索します。

    使い方:
      aixis 種別検索 医療
      aixis 種別検索 契約
      aixis 種別検索 スライド
    """
    from .profiles.registry import search_profiles

    results = search_profiles(keyword, DEFAULT_PROFILES_DIR)

    if not results:
        console.print(f"[yellow]'{keyword}' に該当する種別が見つかりません。[/yellow]")
        console.print("[dim]aixis 種別一覧 で全種別を確認できます。[/dim]")
        return

    console.print(f"\n[bold]'{keyword}' の検索結果: {len(results)}件[/bold]\n")

    for p in results:
        console.print(Panel(
            f"[bold]種別名:[/bold] {p.get('name_jp', p['id'])}\n"
            f"[bold]ID:[/bold] {p['id']}\n"
            f"[bold]説明:[/bold] {p.get('description_jp', '')}\n"
            f"[bold]対象ツール例:[/bold] {', '.join(p.get('examples', []))}\n"
            f"[bold]重点評価:[/bold] {'、'.join(p.get('evaluation_focus', [])[:3])}",
            border_style="cyan",
            width=80,
        ))

    console.print(f"\n[bold]使い方:[/bold]")
    if results:
        first = results[0]
        name = first.get("name_jp", first["id"])
        console.print(f"  [green]aixis 検証 <ツール名> --種別 {name}[/green]")


if __name__ == "__main__":
    app()
