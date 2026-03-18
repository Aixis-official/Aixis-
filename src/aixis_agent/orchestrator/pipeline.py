"""Main pipeline orchestrating test generation, execution, scoring, and reporting."""

import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from ..core.enums import TestCategory
from ..core.interfaces import TestExecutor
from ..core.models import BudgetTracker, TargetConfig, TestCase, TestResult
from ..executors.playwright_executor import PlaywrightExecutor
from ..patterns.generator import generate_all
from ..utils.logging import get_logger
from .session import SessionStore

logger = get_logger(__name__)
console = Console()


def load_target_config(config_path: Path) -> TargetConfig:
    """Load target tool configuration from YAML."""
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TargetConfig(**data)


class Pipeline:
    """End-to-end test pipeline: generate -> execute -> store."""

    def __init__(
        self,
        target_config_path: Path,
        patterns_dir: Path,
        output_dir: Path,
        categories: list[str] | None = None,
        dry_run: bool = False,
        max_concurrency: int = 1,
        profile: dict | None = None,
        auth_storage_state: dict | None = None,
    ):
        self.target_config_path = target_config_path
        self.patterns_dir = patterns_dir
        self.output_dir = output_dir
        self.categories = categories
        self.dry_run = dry_run
        self.max_concurrency = max_concurrency
        self.profile = profile
        self.auth_storage_state = auth_storage_state

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.target_config = load_target_config(target_config_path)

    async def run(self, session_id: str | None = None, resume: bool = False,
                  login_event=None, abort_event=None,
                  progress_callback=None) -> str:
        """Execute the full pipeline. Returns the session ID."""
        session_id = session_id or f"session-{uuid.uuid4().hex[:8]}"
        db_path = self.output_dir / f"{session_id}.db"
        store = SessionStore(db_path)

        try:
            # Phase 1: Generate test cases
            console.print("\n[bold blue]Phase 1: テストケース生成[/bold blue]")
            test_cases = generate_all(self.patterns_dir, self.categories)
            console.print(f"  生成済み: [green]{len(test_cases)}[/green] テストケース")

            if not test_cases:
                console.print("[yellow]テストケースが生成されませんでした。パターン設定を確認してください。[/yellow]")
                return session_id

            # Create or resume session
            if resume:
                session = store.get_session(session_id)
                if not session:
                    console.print(f"[red]セッション {session_id} が見つかりません[/red]")
                    return session_id
                executed_ids = store.get_executed_case_ids(session_id)
                test_cases = [tc for tc in test_cases if tc.id not in executed_ids]
                console.print(f"  再開: 残り [yellow]{len(test_cases)}[/yellow] テストケース")
            else:
                store.create_session(session_id, self.target_config.name, len(test_cases))
                store.store_test_cases(session_id, test_cases)

            if self.dry_run:
                console.print("\n[bold yellow]ドライラン: テスト実行をスキップ[/bold yellow]")
                self._print_dry_run_summary(test_cases)
                store.complete_session(session_id)
                store.close()
                return session_id

            # Phase 2: Execute tests
            is_ai = self.target_config.executor_type == "ai_browser"
            mode_label = "AI Browser Agent" if is_ai else "Playwright"
            console.print(f"\n[bold blue]Phase 2: テスト実行[/bold blue] (対象: {self.target_config.name}, モード: {mode_label})")

            # Sort test cases by priority when using AI executor (budget may run out)
            if is_ai:
                test_cases = self._sort_by_priority(test_cases)

            screenshots_dir = self.output_dir / session_id / "screenshots"
            executor = self._create_executor(screenshots_dir, login_event, abort_event)

            # Track aggregate AI metrics
            total_ai_steps = 0
            total_ai_calls = 0
            total_ai_tokens_in = 0
            total_ai_tokens_out = 0

            try:
                await executor.initialize(self.target_config)

                # Inject saved auth cookies if available
                if self.auth_storage_state and hasattr(executor, '_context') and executor._context:
                    try:
                        cookies = self.auth_storage_state.get("cookies", [])
                        if cookies:
                            await executor._context.add_cookies(cookies)
                            logger.info("Injected %d auth cookies into browser context", len(cookies))
                            # Reload to apply cookies
                            if hasattr(executor, '_page') and executor._page:
                                await executor._page.reload(wait_until="domcontentloaded")
                    except Exception as e:
                        logger.warning("Failed to inject auth cookies: %s", e)

                # Auth pre-check: detect login page before wasting budget
                if is_ai and hasattr(executor, 'check_auth_status'):
                    auth_ok = await executor.check_auth_status()
                    if not auth_ok:
                        console.print("[bold red]認証失敗: ログインページが検出されました[/bold red]")
                        console.print("[yellow]ツール管理画面で認証Cookieを再設定してください[/yellow]")
                        # Record one AUTH_FAILURE result
                        auth_fail_result = TestResult(
                            test_case_id=test_cases[0].id if test_cases else "auth-check",
                            target_tool=self.target_config.name,
                            category=test_cases[0].category if test_cases else TestCategory.BUSINESS_CONVENTION,
                            prompt_sent="[認証プリチェック]",
                            response_raw="",
                            response_time_ms=0,
                            error="AUTH_FAILURE: ログインページが検出されました。認証Cookieが無効または未設定です。",
                            metadata={"ai_steps_taken": 1, "ai_calls_used": 1},
                        )
                        store.store_result(session_id, auth_fail_result)
                        store.complete_session(session_id)
                        return session_id

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("テスト実行中...", total=len(test_cases))

                    for idx, test_case in enumerate(test_cases):
                        # Check abort signal
                        if abort_event and abort_event.is_set():
                            console.print("[bold red]監査が中止されました[/bold red]")
                            break

                        # Check AI budget before each test case
                        if is_ai and hasattr(executor, '_budget') and executor._budget.is_exhausted:
                            reason = executor._budget.exhaustion_reason
                            console.print(f"[yellow]AI予算停止: {reason}。残りのテストはAPI不使用で続行します。[/yellow]")
                            break

                        result_data = await executor.send_prompt(test_case.prompt)

                        # Auth failure on first test → abort all remaining
                        if idx == 0 and result_data.error and result_data.error.startswith("AUTH_FAILURE:"):
                            console.print(f"[bold red]{result_data.error}[/bold red]")
                            console.print("[yellow]残りのテストを中止します。認証Cookieを再設定してください。[/yellow]")
                            test_result = TestResult(
                                test_case_id=test_case.id,
                                target_tool=self.target_config.name,
                                category=test_case.category,
                                prompt_sent=test_case.prompt,
                                response_raw=result_data.text or "",
                                response_time_ms=result_data.response_time_ms,
                                error=result_data.error,
                                screenshot_path=result_data.screenshot_path,
                                metadata={"ai_steps_taken": result_data.ai_steps_taken, "ai_calls_used": result_data.ai_calls_used},
                            )
                            store.store_result(session_id, test_result)
                            break

                        # Accumulate AI metrics
                        total_ai_steps += result_data.ai_steps_taken
                        total_ai_calls += result_data.ai_calls_used
                        total_ai_tokens_in += result_data.ai_tokens_input
                        total_ai_tokens_out += result_data.ai_tokens_output

                        # Report progress via callback
                        if progress_callback:
                            try:
                                progress_callback(
                                    completed=idx + 1,
                                    total=len(test_cases),
                                    current_case=test_case.id,
                                    current_category=test_case.category.value
                                        if hasattr(test_case.category, 'value')
                                        else str(test_case.category),
                                )
                            except Exception:
                                pass  # Don't let callback errors break the pipeline

                        test_result = TestResult(
                            test_case_id=test_case.id,
                            target_tool=self.target_config.name,
                            category=test_case.category,
                            prompt_sent=test_case.prompt,
                            response_raw=result_data.text,
                            response_time_ms=result_data.response_time_ms,
                            error=result_data.error,
                            screenshot_path=result_data.screenshot_path,
                            timestamp=datetime.now(),
                            metadata={
                                **test_case.metadata,
                                "ai_steps_taken": result_data.ai_steps_taken,
                                "ai_calls_used": result_data.ai_calls_used,
                                "ai_tokens_input": result_data.ai_tokens_input,
                                "ai_tokens_output": result_data.ai_tokens_output,
                            },
                        )
                        store.store_result(session_id, test_result)
                        progress.advance(task)

                        # Inter-prompt delay
                        if self.target_config.inter_prompt_delay_ms > 0:
                            await asyncio.sleep(self.target_config.inter_prompt_delay_ms / 1000)

            finally:
                await executor.cleanup()

            if is_ai:
                budget = getattr(executor, '_budget', None)
                cost_str = f"${budget.estimated_cost_usd:.2f}" if budget else "N/A"
                console.print(f"  AI統計: {total_ai_steps}ステップ / {total_ai_calls}API呼出 / 推定コスト {cost_str}")

            # Complete session even if aborted/budget-exhausted (partial results are valid)
            was_aborted = abort_event and abort_event.is_set()
            was_budget_exhausted = is_ai and hasattr(executor, '_budget') and executor._budget.is_exhausted

            store.complete_session(session_id)

            if was_aborted:
                console.print(f"\n[bold yellow]中止[/bold yellow] セッションID: {session_id} (収集済みの結果でスコアリングを実行します)")
            elif was_budget_exhausted:
                console.print(f"\n[bold yellow]予算到達[/bold yellow] セッションID: {session_id} (収集済みの結果でスコアリングを実行します)")
            else:
                console.print(f"\n[bold green]完了[/bold green] セッションID: {session_id}")
            console.print(f"  データベース: {db_path}")

        except Exception as e:
            store.fail_session(session_id, str(e))
            console.print(f"\n[bold red]エラー: {e}[/bold red]")
            raise
        finally:
            store.close()

        return session_id

    def _create_executor(self, screenshots_dir: Path, login_event=None, abort_event=None) -> TestExecutor:
        """Create the appropriate executor based on target config executor_type."""
        if self.target_config.executor_type == "ai_browser":
            from ..executors.ai_browser_executor import AIBrowserExecutor

            api_key = os.environ.get("AIXIS_ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError(
                    "AIXIS_ANTHROPIC_API_KEY 環境変数が必要です (ai_browser executor)"
                )

            # Convert JPY cost cap to USD (approx 1 USD = 150 JPY)
            max_cost_jpy = self.target_config.ai_budget_max_cost_jpy
            max_cost_usd = max_cost_jpy / 150.0 if max_cost_jpy > 0 else 0.0

            budget = BudgetTracker(
                max_calls_total=self.target_config.ai_budget_max_calls,
                max_calls_per_case=self.target_config.ai_budget_max_calls_per_case,
                max_cost_usd=max_cost_usd,
            )
            executor = AIBrowserExecutor(
                anthropic_api_key=api_key,
                model=os.environ.get("AIXIS_AI_AGENT_MODEL", "claude-haiku-4-5-20251001"),
                screenshots_dir=screenshots_dir,
                budget_tracker=budget,
            )
        else:
            executor = PlaywrightExecutor(screenshots_dir=screenshots_dir)

        if login_event is not None and hasattr(executor, "set_login_event"):
            executor.set_login_event(login_event)
        if abort_event is not None and hasattr(executor, "set_abort_event"):
            executor.set_abort_event(abort_event)

        return executor

    # Priority order for test categories (most important first)
    _CATEGORY_PRIORITY = {
        TestCategory.CONTRADICTORY: 0,
        TestCategory.BUSINESS_JP: 1,
        TestCategory.KEIGO_MIXING: 2,
        TestCategory.DIALECT: 3,
        TestCategory.AMBIGUOUS: 4,
        TestCategory.MULTI_STEP: 5,
        TestCategory.BROKEN_GRAMMAR: 6,
        TestCategory.LONG_INPUT: 7,
        TestCategory.UNICODE_EDGE: 8,
    }

    def _sort_by_priority(self, test_cases: list[TestCase]) -> list[TestCase]:
        """Round-robin interleave test cases across categories.

        When using the AI executor, budget may run out mid-audit.
        Instead of exhausting one category before starting the next,
        we interleave so that partial results cover ALL categories.

        Categories are ordered by priority within each round.
        e.g., Round 1: [contradictory-1, business_jp-1, keigo-1, ...]
              Round 2: [contradictory-2, business_jp-2, keigo-2, ...]
        """
        from collections import defaultdict

        # Group by category, maintaining priority order within groups
        by_category: dict[str, list[TestCase]] = defaultdict(list)
        for tc in test_cases:
            by_category[tc.category].append(tc)

        # Sort categories by priority
        sorted_categories = sorted(
            by_category.keys(),
            key=lambda cat: self._CATEGORY_PRIORITY.get(cat, 99),
        )

        # Round-robin: pick one from each category per round
        result: list[TestCase] = []
        round_idx = 0
        while True:
            added_any = False
            for cat in sorted_categories:
                cases = by_category[cat]
                if round_idx < len(cases):
                    result.append(cases[round_idx])
                    added_any = True
            if not added_any:
                break
            round_idx += 1

        return result

    def _print_dry_run_summary(self, test_cases: list[TestCase]) -> None:
        """Print a summary of what would be executed in a real run."""
        from collections import Counter

        cat_counts = Counter(tc.category.value for tc in test_cases)
        console.print("\n[bold]カテゴリ別テストケース数:[/bold]")
        for cat, count in sorted(cat_counts.items()):
            console.print(f"  {cat}: {count}")

        console.print(f"\n[bold]サンプルプロンプト (最初の3件):[/bold]")
        for tc in test_cases[:3]:
            prompt_preview = tc.prompt[:100] + "..." if len(tc.prompt) > 100 else tc.prompt
            console.print(f"  [{tc.category.value}] {prompt_preview}")
