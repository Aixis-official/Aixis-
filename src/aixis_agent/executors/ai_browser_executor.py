"""Hybrid AI browser executor: unified agent loop with Haiku Vision.

Strategy:
1. EVERY test case uses a unified agent loop:
   - Take screenshot → Ask Haiku "what should I do next?" → Execute action → Repeat
   - Agent decides when to type, click, scroll, wait, or declare done
   - Handles multi-step workflows (e.g., type → click 概要作成 → wait → click 生成 → wait → done)
2. After first test, learned coordinates are reused to SKIP the initial discovery:
   - Input area and first submit button coords are cached
   - AI is only called for post-submit steps (intermediate buttons, waiting, etc.)
3. Recovery: If replay fails, AI diagnoses and recovers
4. Cost control: Hard stop at JPY budget limit. Abort signal support.

Typical cost: ~10-20 Haiku calls per full audit ≈ 10-15円
"""

import asyncio
import base64
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import anthropic
from playwright.async_api import async_playwright, Page, BrowserContext

from ..core.interfaces import TestExecutor
from ..core.models import BudgetTracker, ExecutionResult, TargetConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Learned workflow cache
# ---------------------------------------------------------------------------

@dataclass
class LearnedStep:
    """A recorded step from the first successful test case."""
    action: str         # "click" | "scroll_down" | "wait"
    x: int = 0
    y: int = 0
    delay_before: float = 3.0   # seconds to wait before this step
    description: str = ""


@dataclass
class LearnedWorkflow:
    """Workflow discovered during first test case. Used for replay."""
    input_x: int = 0
    input_y: int = 0
    submit_x: int = 0
    submit_y: int = 0
    post_submit_steps: list[LearnedStep] = field(default_factory=list)
    total_generation_wait: float = 30.0
    reset_url: str = ""
    valid: bool = False

    def save_to_file(self, path: Path) -> None:
        """Persist learned workflow to JSON for reuse across audits."""
        data = {
            "input_x": self.input_x,
            "input_y": self.input_y,
            "submit_x": self.submit_x,
            "submit_y": self.submit_y,
            "post_submit_steps": [
                {"action": s.action, "x": s.x, "y": s.y,
                 "delay_before": s.delay_before, "description": s.description}
                for s in self.post_submit_steps
            ],
            "total_generation_wait": self.total_generation_wait,
            "reset_url": self.reset_url,
            "valid": self.valid,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Saved workflow to %s", path)

    @classmethod
    def load_from_file(cls, path: Path) -> "LearnedWorkflow":
        """Load a previously saved workflow."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        wf = cls(
            input_x=data.get("input_x", 0),
            input_y=data.get("input_y", 0),
            submit_x=data.get("submit_x", 0),
            submit_y=data.get("submit_y", 0),
            total_generation_wait=data.get("total_generation_wait", 30.0),
            reset_url=data.get("reset_url", ""),
            valid=data.get("valid", False),
        )
        for step_data in data.get("post_submit_steps", []):
            wf.post_submit_steps.append(LearnedStep(
                action=step_data["action"],
                x=step_data.get("x", 0),
                y=step_data.get("y", 0),
                delay_before=step_data.get("delay_before", 3.0),
                description=step_data.get("description", ""),
            ))
        logger.info("Loaded workflow from %s (valid=%s)", path, wf.valid)
        return wf


# ---------------------------------------------------------------------------
# Unified agent prompt
# ---------------------------------------------------------------------------

AGENT_PROMPT = """\
あなたはWebアプリを操作するAIエージェントです。
ツール名: {tool_name}
ツール説明: {tool_description}
開始URL: {reset_url}
画面サイズ: 1280×800px

【現在のタスク】{task_description}

【これまでの操作履歴】
{action_history}

画面のスクリーンショットを分析し、次に実行すべきアクション1つをJSON形式で回答してください。

■ 利用可能なアクション:
{{"action":"click","x":<int>,"y":<int>,"desc":"何をクリックするか"}}
{{"action":"type","text":"入力するテキスト","desc":"どこに入力するか"}}
{{"action":"scroll_down","desc":"スクロールの理由"}}
{{"action":"scroll_up","desc":"スクロールの理由"}}
{{"action":"navigate","url":"移動先URL","desc":"ナビゲーション理由"}}
{{"action":"wait","seconds":<5-30>,"desc":"待機理由"}}
{{"action":"done","desc":"完了の理由"}}
{{"action":"fail","desc":"失敗の理由"}}

■ 重要なルール:
- ★ テキスト入力と送信は必ず別のステップで行うこと。1ステップ目でtype、次のステップでclick（送信ボタン）。同じステップで入力と送信を同時にしない
- ★ typeアクションの直後は、入力欄にテキストが正しく表示されているか確認してから送信ボタンをクリック
- 「生成」「作成」「送信」「Submit」「Go」「Start」などのボタンが見え、かつ入力欄にテキストが入力済みならクリック
- 確認ダイアログやモーダルが出たら「OK」「はい」「続行」をクリック
- ローディング中（スピナー、プログレスバー、「生成中...」等）はwaitを選択
- 結果（スライド、テキスト、画像、回答文等）が表示されていたらdoneを選択
- ボタンが画面外にありそうならscroll_downを選択
- エラーメッセージが表示されていてもdone（エラー自体が監査結果として有用）
- クレジット不足やレート制限のエラーが出た場合のみfailを選択
- 同じ操作を3回以上繰り返さないこと。効果がなければ別のアプローチを試す
- scroll_downで見つからなければscroll_upで戻る。それでもダメならnavigateで開始URLに戻る
- 前回のテスト結果画面が表示されている場合は、navigateで開始URLに戻る
- JSONのみ回答（他のテキスト不要）
"""


class AIBrowserExecutor(TestExecutor):
    """Hybrid executor: unified agent loop with Haiku Vision.

    Cost-optimized for ≤20円/audit target.
    """

    def __init__(
        self,
        anthropic_api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        screenshots_dir: Path | None = None,
        budget_tracker: BudgetTracker | None = None,
    ):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)
        self._model = model
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._config: TargetConfig | None = None
        self._screenshots_dir = screenshots_dir or Path("output/screenshots")
        self._budget = budget_tracker or BudgetTracker()
        self._prompt_count = 0
        self._total_screenshots = 0
        self._login_event: threading.Event | None = None

        # Abort signal: set externally to stop execution mid-audit
        self._abort_event: threading.Event = threading.Event()

        # Hybrid state
        self._workflow: LearnedWorkflow = LearnedWorkflow()
        self._first_test_done: bool = False
        self._consecutive_failures: int = 0

    def set_login_event(self, event: threading.Event) -> None:
        self._login_event = event

    def set_abort_event(self, event: threading.Event) -> None:
        """Set an external abort signal. When set, execution stops ASAP."""
        self._abort_event = event

    @property
    def is_aborted(self) -> bool:
        return self._abort_event.is_set()

    # ------------------------------------------------------------------
    # Interruptible sleep (abort-aware)
    # ------------------------------------------------------------------

    async def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep that can be interrupted by abort signal.

        Returns True if aborted, False if sleep completed normally.
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.is_aborted:
                return True
            remaining = end - time.monotonic()
            await asyncio.sleep(min(1.0, max(0.1, remaining)))
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_stale_locks(profile_dir: Path) -> None:
        """Remove stale Chromium lock files from a previous crashed session."""
        import os
        import signal
        import subprocess

        lock_files = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
        for lock_name in lock_files:
            lock_path = profile_dir / lock_name
            if lock_path.exists():
                try:
                    lock_path.unlink(missing_ok=True)
                    logger.info("Removed stale lock file: %s", lock_path)
                except Exception as e:
                    logger.warning("Could not remove lock file %s: %s", lock_path, e)

        try:
            result = subprocess.run(
                ["pgrep", "-f", str(profile_dir)],
                capture_output=True, text=True, timeout=5,
            )
            pids = [int(p.strip()) for p in result.stdout.strip().split("\n") if p.strip()]
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    logger.info("Killed stale Chromium process: PID %d", pid)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass

    async def initialize(self, target_config: TargetConfig) -> None:
        """Launch browser and navigate to target tool."""
        self._config = target_config
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._workflow.reset_url = target_config.url

        self._playwright = await async_playwright().start()

        user_data_dir = Path.home() / ".aixis" / "browser-profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self._cleanup_stale_locks(user_data_dir)

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=target_config.headless,
            locale=target_config.locale,
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation"],
        )
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )
        self._context.set_default_navigation_timeout(300_000)
        self._context.set_default_timeout(60_000)

        await self._page.goto(
            target_config.url, wait_until="domcontentloaded", timeout=60_000
        )

        # Manual login wait
        if target_config.wait_for_manual_login and self._login_event:
            logger.info("Waiting for manual login at %s", target_config.url)
            while not self._login_event.is_set():
                if self.is_aborted:
                    logger.info("Aborted during login wait")
                    return
                await asyncio.sleep(1.0)
            logger.info("Manual login complete, resuming")
            pages = self._context.pages
            if pages:
                self._page = pages[-1]

    def _workflow_cache_path(self) -> Path | None:
        """Path to cached workflow file for the current target tool."""
        if not self._config:
            return None
        cache_dir = Path(__file__).parent.parent.parent.parent / "data" / "workflow_cache"
        return cache_dir / f"{self._config.name}.json"

    def _try_load_cached_workflow(self) -> bool:
        """Load a previously saved workflow if available. Returns True if loaded."""
        path = self._workflow_cache_path()
        if path and path.exists():
            try:
                self._workflow = LearnedWorkflow.load_from_file(path)
                if self._workflow.valid:
                    self._first_test_done = True
                    logger.info("Loaded cached workflow for %s — skipping Discovery", self._config.name)
                    return True
            except Exception as e:
                logger.warning("Failed to load cached workflow: %s", e)
        return False

    def _save_workflow_cache(self) -> None:
        """Save the current workflow to disk for reuse."""
        path = self._workflow_cache_path()
        if path and self._workflow.valid:
            try:
                self._workflow.save_to_file(path)
            except Exception as e:
                logger.warning("Failed to save workflow cache: %s", e)

    async def send_prompt(self, prompt: str) -> ExecutionResult:
        """Send a test prompt using the unified agent loop."""
        if not self._page or not self._config:
            return ExecutionResult(error="Executor not initialized")

        if self.is_aborted:
            return ExecutionResult(error="監査が中止されました")

        # On first call, try loading a cached workflow
        if self._prompt_count == 0:
            self._try_load_cached_workflow()

        self._prompt_count += 1
        start_time = time.monotonic()

        if self._first_test_done and self._workflow.valid:
            return await self._replay_then_agent(prompt, start_time)
        else:
            result = await self._full_agent_loop(prompt, start_time, is_discovery=True)
            # After successful discovery, save the workflow for future audits
            if self._workflow.valid and self._first_test_done:
                self._save_workflow_cache()
            return result

    async def cleanup(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------
    # Full agent loop (first test + fallback)
    # ------------------------------------------------------------------

    async def _full_agent_loop(
        self, prompt: str, start_time: float, *,
        is_discovery: bool = False,
        task_desc: str | None = None,
        max_steps: int = 15,
    ) -> ExecutionResult:
        """Unified agent loop: screenshot → Haiku → action → repeat."""
        total_calls = 0
        total_ti = 0
        total_to = 0
        action_history: list[str] = []
        post_submit_steps: list[LearnedStep] = []
        submit_clicked = False
        step_start_time = time.monotonic()

        if task_desc is None:
            task_desc = f"テキスト入力欄に「{prompt[:50]}」と入力し、送信して結果を取得してください。"

        original_task_desc = task_desc

        # Navigate to start URL for non-discovery runs (2nd+ test cases)
        if not is_discovery and self._workflow.reset_url:
            try:
                await self._page.goto(
                    self._workflow.reset_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                if await self._interruptible_sleep(3):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)
            except Exception as e:
                logger.warning("Failed to navigate to reset URL: %s", e)

        for step_idx in range(max_steps):
            # Check abort & budget
            if self.is_aborted:
                return self._make_result(start_time, error="監査が中止されました",
                                         calls=total_calls, ti=total_ti, to=total_to)
            if self._budget.is_exhausted:
                reason = self._budget.exhaustion_reason
                logger.warning("Budget exhausted: %s", reason)
                return self._make_result(start_time, error=f"予算上限: {reason}",
                                         calls=total_calls, ti=total_ti, to=total_to)

            # --- Loop detection ---
            # If last 3 actions are identical, inject a warning into task description
            if len(action_history) >= 3:
                last_actions = []
                for h in action_history[-3:]:
                    parts = h.split(": ", 1)
                    if len(parts) > 1:
                        last_actions.append(parts[1].split(" — ")[0])
                if len(last_actions) == 3 and len(set(last_actions)) == 1:
                    repeated = last_actions[0]
                    task_desc = (
                        f"⚠️ 同じ操作「{repeated}」が3回連続しています。"
                        f"別のアプローチを試してください。"
                        f"scroll_upでページ上部に戻るか、navigateで開始URLに戻ることも可能です。\n\n"
                        f"元のタスク: {original_task_desc}"
                    )
                    logger.warning("Loop detected: %s repeated 3 times", repeated)
                else:
                    task_desc = original_task_desc

            # Take screenshot & ask Haiku
            ss_b64 = await self._screenshot_b64()
            agent_text = AGENT_PROMPT.format(
                tool_name=self._config.name,
                tool_description=self._config.tool_description or "",
                reset_url=self._workflow.reset_url or self._config.url,
                task_description=task_desc,
                action_history="\n".join(action_history[-5:]) or "(なし)",
            )

            resp_text, ti, to = await self._ask_haiku_async(agent_text, ss_b64)
            total_calls += 1
            total_ti += ti
            total_to += to
            self._budget.record_call(ti, to)

            # Parse action
            try:
                data = json.loads(resp_text)
            except Exception:
                logger.warning("Step %d: unparseable response: %s", step_idx, resp_text[:100])
                action_history.append(f"Step {step_idx}: parse error, waiting 3s")
                if await self._interruptible_sleep(3):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)
                continue

            action = data.get("action", "fail")
            desc = data.get("desc", "")
            action_history.append(f"Step {step_idx}: {action} — {desc}")
            logger.info("Agent step %d: %s — %s", step_idx, action, desc)

            # Execute action
            if action == "click":
                cx, cy = int(data.get("x", 0)), int(data.get("y", 0))
                await self._page.mouse.click(cx, cy)
                logger.info("  Clicked (%d, %d)", cx, cy)

                # Record for learning
                if is_discovery and submit_clicked:
                    delay = time.monotonic() - step_start_time
                    post_submit_steps.append(
                        LearnedStep(action="click", x=cx, y=cy,
                                    delay_before=delay, description=desc)
                    )
                    step_start_time = time.monotonic()
                elif is_discovery and not submit_clicked:
                    self._workflow.submit_x = cx
                    self._workflow.submit_y = cy
                    submit_clicked = True
                    step_start_time = time.monotonic()

                if await self._interruptible_sleep(2):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)

            elif action == "type":
                text = data.get("text", prompt)
                if "{prompt}" in text:
                    text = text.replace("{prompt}", prompt)
                elif text == prompt or not text:
                    text = prompt

                await self._page.keyboard.press("Meta+a")
                await self._page.keyboard.press("Backspace")
                await asyncio.sleep(0.2)

                if len(text) > 500:
                    await self._paste_text(text)
                else:
                    await self._page.keyboard.type(text, delay=10)
                logger.info("  Typed %d chars", len(text))

                if await self._interruptible_sleep(1):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)

            elif action == "scroll_down":
                await self._page.mouse.wheel(0, 400)
                logger.info("  Scrolled down")

                if is_discovery and submit_clicked:
                    delay = time.monotonic() - step_start_time
                    post_submit_steps.append(
                        LearnedStep(action="scroll_down", delay_before=delay, description=desc)
                    )
                    step_start_time = time.monotonic()

                if await self._interruptible_sleep(2):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)

            elif action == "scroll_up":
                await self._page.mouse.wheel(0, -400)
                logger.info("  Scrolled up")

                if await self._interruptible_sleep(2):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)

            elif action == "navigate":
                nav_url = data.get("url", self._workflow.reset_url or self._config.url)
                # Security: only allow navigation to the same domain
                allowed_domain = urlparse(self._config.url).netloc
                target_domain = urlparse(nav_url).netloc
                if target_domain and target_domain != allowed_domain:
                    logger.warning("  Blocked navigation to different domain: %s", nav_url)
                    action_history[-1] += " (BLOCKED: wrong domain)"
                else:
                    try:
                        await self._page.goto(nav_url, wait_until="domcontentloaded", timeout=60_000)
                        logger.info("  Navigated to %s", nav_url)
                    except Exception as e:
                        logger.warning("  Navigation failed: %s", e)
                        action_history[-1] += f" (FAILED: {e})"

                if await self._interruptible_sleep(3):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)

            elif action == "wait":
                wait_s = min(int(data.get("seconds", 10)), 30)
                logger.info("  Waiting %ds", wait_s)
                if await self._interruptible_sleep(wait_s):
                    return self._make_result(start_time, error="監査が中止されました",
                                             calls=total_calls, ti=total_ti, to=total_to)

            elif action == "done":
                logger.info("  Agent declares done: %s", desc)
                if is_discovery:
                    self._workflow.post_submit_steps = post_submit_steps
                    self._workflow.total_generation_wait = time.monotonic() - start_time
                    self._workflow.valid = True
                    self._first_test_done = True
                    logger.info("Workflow learned: %d post-submit steps",
                                len(post_submit_steps))

                response_text = await self._extract_page_text()
                ss_path = await self._save_screenshot()

                return ExecutionResult(
                    text=response_text,
                    response_time_ms=(time.monotonic() - start_time) * 1000,
                    screenshot_path=ss_path,
                    page_url=self._page.url,
                    ai_steps_taken=step_idx + 1,
                    ai_calls_used=total_calls,
                    ai_tokens_input=total_ti,
                    ai_tokens_output=total_to,
                )

            elif action == "fail":
                logger.warning("  Agent declares failure: %s", desc)
                ss_path = await self._save_screenshot_safe()
                return ExecutionResult(
                    error=f"AI判定: {desc}",
                    response_time_ms=(time.monotonic() - start_time) * 1000,
                    screenshot_path=ss_path,
                    page_url=self._page.url,
                    ai_steps_taken=step_idx + 1,
                    ai_calls_used=total_calls,
                    ai_tokens_input=total_ti,
                    ai_tokens_output=total_to,
                )

        # Max steps reached
        ss_path = await self._save_screenshot_safe()
        return ExecutionResult(
            error=f"最大ステップ数 ({max_steps}) に到達",
            response_time_ms=(time.monotonic() - start_time) * 1000,
            screenshot_path=ss_path,
            page_url=self._page.url,
            ai_steps_taken=max_steps,
            ai_calls_used=total_calls,
            ai_tokens_input=total_ti,
            ai_tokens_output=total_to,
        )

    # ------------------------------------------------------------------
    # Replay with AI fallback (2nd+ test cases)
    # ------------------------------------------------------------------

    async def _replay_then_agent(self, prompt: str, start_time: float) -> ExecutionResult:
        """Replay learned workflow steps, then verify with AI that it actually worked.

        Key principle: NEVER count a test as completed without verifying
        that the tool actually processed the prompt and produced output.
        """
        wf = self._workflow

        try:
            # Navigate back to start
            await self._page.goto(
                wf.reset_url, wait_until="domcontentloaded", timeout=60_000
            )
            if await self._interruptible_sleep(3):
                return self._make_result(start_time, error="監査が中止されました")

            if self.is_aborted:
                return self._make_result(start_time, error="監査が中止されました")

            # Dynamically find the input area via DOM (more reliable than fixed coords)
            input_rect = await self._page.evaluate("""() => {
                // Priority: textarea > contenteditable > text input
                const selectors = [
                    'textarea:not([disabled])',
                    '[contenteditable="true"]',
                    'input[type="text"]:not([disabled])',
                    '[role="textbox"]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 50 && rect.height > 10) {
                            return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, found: true };
                        }
                    }
                }
                return { found: false };
            }""")

            if input_rect and input_rect.get("found"):
                input_x = int(input_rect["x"])
                input_y = int(input_rect["y"])
                logger.info("Replay: found input via DOM at (%d, %d)", input_x, input_y)
            else:
                # Fallback to learned coordinates
                input_x = 640
                input_y = max(wf.submit_y - 100, 100)
                logger.info("Replay: DOM input not found, using learned coords (%d, %d)", input_x, input_y)

            await self._page.mouse.click(input_x, input_y)
            await asyncio.sleep(0.8)
            await self._page.keyboard.press("Meta+a")
            await self._page.keyboard.press("Backspace")
            await asyncio.sleep(0.3)

            # Use paste for all prompts (faster, no timing issues with keyboard.type)
            await self._paste_text(prompt)
            await asyncio.sleep(1.5)  # Wait for UI to process pasted text

            # Verify input was entered by checking if there's text in the focused element
            input_check = await self._page.evaluate("""() => {
                const el = document.activeElement;
                if (!el) return '';
                if (el.isContentEditable) return el.innerText || '';
                return el.value || '';
            }""")
            if not input_check or len(input_check.strip()) < 5:
                # Paste may have failed — try keyboard.type as fallback
                logger.warning("Replay: paste may have failed (got '%s'), trying keyboard.type", input_check[:30] if input_check else "")
                await self._page.mouse.click(input_x, input_y)
                await asyncio.sleep(0.5)
                await self._page.keyboard.press("Meta+a")
                await self._page.keyboard.press("Backspace")
                await asyncio.sleep(0.2)
                await self._page.keyboard.type(prompt[:200], delay=15)
                await asyncio.sleep(2)  # Extra wait for keyboard.type to finish

            # Click submit
            await self._page.mouse.click(wf.submit_x, wf.submit_y)
            logger.info("Replay #%d: clicked submit (%d,%d)", self._prompt_count, wf.submit_x, wf.submit_y)

            # Execute learned post-submit steps
            for step in wf.post_submit_steps:
                if await self._interruptible_sleep(step.delay_before):
                    return self._make_result(start_time, error="監査が中止されました")

                if self.is_aborted:
                    return self._make_result(start_time, error="監査が中止されました")

                if step.action == "click":
                    await self._page.mouse.click(step.x, step.y)
                    logger.info("Replay: click (%d,%d) — %s", step.x, step.y, step.description)
                elif step.action == "scroll_down":
                    await self._page.mouse.wheel(0, 400)
                    logger.info("Replay: scroll down — %s", step.description)
                elif step.action == "wait":
                    if await self._interruptible_sleep(step.delay_before):
                        return ExecutionResult(error="監査が中止されました")

                if await self._interruptible_sleep(2):
                    return self._make_result(start_time, error="監査が中止されました")

            # -----------------------------------------------------------
            # SMART VERIFICATION: Minimize API calls while ensuring quality.
            # 1. First, use fast JS-based checks (URL change, DOM change) — FREE
            # 2. Only call AI verification if JS checks are inconclusive
            # 3. Skip AI verify if consecutive successes > 2 (stable replay)
            # -----------------------------------------------------------
            verify_calls = 0
            verify_ti = 0
            verify_to = 0

            # Smart wait: poll for URL/DOM changes instead of fixed-time sleep.
            # This can save 20-40 seconds per test when generation finishes early.
            max_wait = min(wf.total_generation_wait * 1.0, 120)
            min_wait = 5.0  # Always wait at least 5s for generation to start
            poll_interval = 3.0
            elapsed_wait = 0.0
            url_changed = False
            has_substantial_output = False

            logger.info("Replay: polling for generation (max %.0fs)", max_wait)
            if await self._interruptible_sleep(min_wait):
                return self._make_result(start_time, error="監査が中止されました")
            elapsed_wait += min_wait

            while elapsed_wait < max_wait:
                if self.is_aborted:
                    return self._make_result(start_time, error="監査が中止されました")

                # Check if page changed
                current_url = self._page.url
                url_changed = current_url != wf.reset_url

                # Check DOM for substantial content change
                page_text_len = await self._page.evaluate(
                    "() => document.body ? document.body.innerText.length : 0"
                )
                has_substantial_output = page_text_len > 500

                if url_changed or has_substantial_output:
                    logger.info("Replay: generation detected after %.0fs (url_changed=%s, text_len=%d)",
                               elapsed_wait, url_changed, page_text_len)
                    # Wait a bit more for rendering to complete
                    await asyncio.sleep(2)
                    break

                if await self._interruptible_sleep(poll_interval):
                    return self._make_result(start_time, error="監査が中止されました")
                elapsed_wait += poll_interval

            page_text = await self._extract_page_text()
            has_substantial_output = page_text and len(page_text.strip()) > 100

            # Decide: do we need AI verification?
            need_ai_verify = False
            if not has_substantial_output and not url_changed:
                # Page looks the same — likely stuck on input screen
                need_ai_verify = True
            elif self._consecutive_failures > 0:
                # Had recent failures — verify to be safe
                need_ai_verify = True
            # If stable (consecutive_failures==0, good output) → skip AI verify

            if need_ai_verify and not self._budget.is_exhausted and not self.is_aborted:
                ss_b64 = await self._screenshot_b64()
                verify_prompt = (
                    f"画面を見て状態を判断してください。"
                    f"ツール「{self._config.name}」にプロンプト送信後の画面です。\n"
                    f'{{"state":"loading"}} — 生成中\n'
                    f'{{"state":"result"}} — 結果表示済み\n'
                    f'{{"state":"input"}} — まだ入力画面\n'
                    f'{{"state":"error"}} — エラー表示\n'
                    f"JSONのみ回答"
                )

                resp_text, ti, to = await self._ask_haiku_async(verify_prompt, ss_b64)
                verify_calls += 1
                verify_ti += ti
                verify_to += to
                self._budget.record_call(ti, to)

                try:
                    state_data = json.loads(resp_text)
                    page_state = state_data.get("state", "unknown")
                except Exception:
                    page_state = "unknown"

                logger.info("Replay verify: state=%s (url_changed=%s, output_len=%d)",
                           page_state, url_changed, len(page_text or ""))

                if page_state == "loading":
                    extra_wait = min(wf.total_generation_wait * 0.3, 30)
                    logger.info("Replay: still loading, waiting %.0fs more", extra_wait)
                    if await self._interruptible_sleep(extra_wait):
                        return ExecutionResult(error="監査が中止されました")

                elif page_state == "input":
                    logger.warning("Replay verify: still on input screen, falling back to AI agent")
                    self._consecutive_failures += 1
                    if not self._budget.is_exhausted:
                        return await self._full_agent_loop(
                            prompt, start_time,
                            is_discovery=False,
                            task_desc=(
                                f"リプレイ操作を行いましたが、まだ入力画面のままです。"
                                f"テキスト入力欄に「{prompt[:50]}」を入力し、送信して結果を取得してください。"
                            ),
                            max_steps=8,
                        )
                    else:
                        return ExecutionResult(
                            error="リプレイ失敗: 送信されず入力画面のまま（AI予算切れで回復不可）",
                            response_time_ms=(time.monotonic() - start_time) * 1000,
                            ai_calls_used=verify_calls,
                            ai_tokens_input=verify_ti,
                            ai_tokens_output=verify_to,
                        )
            elif need_ai_verify:
                # Need verify but budget exhausted — mark as unverified
                logger.warning("Replay: needs verification but budget exhausted")
                if not has_substantial_output:
                    return ExecutionResult(
                        error="リプレイ失敗の可能性（検証不可: AI予算切れ）",
                        response_time_ms=(time.monotonic() - start_time) * 1000,
                    )

            # Extract response (re-use page_text if AI verify wasn't needed,
            # otherwise re-extract in case loading completed)
            if need_ai_verify:
                response_text = await self._extract_page_text()
            else:
                response_text = page_text
            ss_path = await self._save_screenshot()
            self._consecutive_failures = 0

            return ExecutionResult(
                text=response_text,
                response_time_ms=(time.monotonic() - start_time) * 1000,
                screenshot_path=ss_path,
                page_url=self._page.url,
                ai_calls_used=verify_calls,
                ai_tokens_input=verify_ti,
                ai_tokens_output=verify_to,
            )

        except Exception as e:
            logger.warning("Replay failed: %s — falling back to AI", e)
            self._consecutive_failures += 1

            if self._consecutive_failures > 3:
                return ExecutionResult(
                    error=f"連続失敗: {e}",
                    response_time_ms=(time.monotonic() - start_time) * 1000,
                )

            # Fallback: navigate back to start URL, then use full agent loop
            if not self._budget.is_exhausted and not self.is_aborted:
                try:
                    await self._page.goto(
                        wf.reset_url, wait_until="domcontentloaded", timeout=60_000
                    )
                    await self._interruptible_sleep(3)
                except Exception:
                    pass  # Agent loop will handle the page state

                return await self._full_agent_loop(
                    prompt, start_time,
                    is_discovery=False,
                    task_desc=f"操作中にエラーが発生しました({e})。画面を見て、テキスト入力→送信→結果取得を完了してください。",
                    max_steps=8,
                )

            return ExecutionResult(
                error=f"Replay failed and no budget for recovery: {e}",
                response_time_ms=(time.monotonic() - start_time) * 1000,
            )

    # ------------------------------------------------------------------
    # Haiku Vision API
    # ------------------------------------------------------------------

    async def _ask_haiku_async(self, prompt_text: str, screenshot_b64: str) -> tuple[str, int, int]:
        """Non-blocking wrapper for _ask_haiku_sync (runs in thread pool)."""
        return await asyncio.to_thread(self._ask_haiku_sync, prompt_text, screenshot_b64)

    def _ask_haiku_sync(self, prompt_text: str, screenshot_b64: str) -> tuple[str, int, int]:
        """Call Haiku Vision (synchronous). Returns (response_json_text, input_tokens, output_tokens)."""
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_b64,
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }],
            )
        except Exception as e:
            logger.error("Haiku API error: %s", e)
            return '{"action":"wait","seconds":5,"desc":"API error, retrying"}', 0, 0

        raw = response.content[0].text.strip()

        # Strip markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:])
            if raw.endswith("```"):
                raw = raw[:-3].strip()

        # Extract JSON from response
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            raw = raw[start:end]
        except ValueError:
            pass

        return raw, response.usage.input_tokens, response.usage.output_tokens

    # ------------------------------------------------------------------
    # Playwright helpers
    # ------------------------------------------------------------------

    async def _paste_text(self, text: str) -> None:
        """Paste text via clipboard — faster and more reliable for long inputs."""
        try:
            await self._page.evaluate(
                """(text) => {
                    const el = document.activeElement;
                    if (el && (el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
                        if (el.isContentEditable) {
                            document.execCommand('insertText', false, text);
                        } else {
                            el.value = text;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    } else {
                        const editable = document.querySelector('[contenteditable="true"]') ||
                                         document.querySelector('textarea') ||
                                         document.querySelector('input[type="text"]');
                        if (editable) {
                            editable.focus();
                            if (editable.isContentEditable) {
                                document.execCommand('insertText', false, text);
                            } else {
                                editable.value = text;
                                editable.dispatchEvent(new Event('input', { bubbles: true }));
                                editable.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }
                    }
                }""",
                text,
            )
            logger.info("  Pasted %d chars via JS", len(text))
        except Exception as e:
            logger.warning("  Paste failed (%s), falling back to keyboard.type", e)
            await self._page.keyboard.type(text, delay=10)

    async def _extract_page_text(self) -> str:
        """Extract visible text from the page."""
        content_selectors = [
            "[class*='doc-content']",
            "[class*='slide']",
            "[class*='canvas-content']",
            "[role='document']",
            "main",
            "article",
        ]
        for sel in content_selectors:
            try:
                el = self._page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    text = await el.inner_text(timeout=5000)
                    if text and len(text.strip()) > 20:
                        return text.strip()[:5000]
            except Exception:
                continue

        try:
            text = await self._page.inner_text("body", timeout=5000)
            return text.strip()[:5000]
        except Exception:
            return ""

    async def _screenshot_b64(self) -> str:
        """Take screenshot and return as base64 (in-memory only, no disk write)."""
        self._total_screenshots += 1
        buf = await self._page.screenshot(type="png")
        return base64.standard_b64encode(buf).decode("utf-8")

    async def _save_screenshot(self) -> str:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"result_{self._prompt_count:04d}.png"
        path = str(self._screenshots_dir / filename)
        await self._page.screenshot(path=path)

        # Copy to web-accessible static directory for browser viewing
        try:
            import shutil
            web_ss_dir = Path(__file__).parent.parent.parent / "aixis_web" / "static" / "screenshots"
            web_ss_dir.mkdir(parents=True, exist_ok=True)
            # Include session info in filename to avoid collisions
            session_prefix = self._screenshots_dir.parent.name if self._screenshots_dir.parent else "unknown"
            web_filename = f"{session_prefix}_{filename}"
            web_path = web_ss_dir / web_filename
            shutil.copy2(path, str(web_path))
            # Return the web-accessible relative path
            return f"/static/screenshots/{web_filename}"
        except Exception as e:
            logger.debug("Could not copy screenshot to web static: %s", e)
            return path

    async def _save_screenshot_safe(self) -> str | None:
        try:
            return await self._save_screenshot()
        except Exception:
            return None

    def _make_result(
        self, start_time: float, *, error: str | None = None,
        text: str | None = None,
        calls: int = 0, ti: int = 0, to: int = 0,
    ) -> ExecutionResult:
        elapsed = (time.monotonic() - start_time) * 1000
        return ExecutionResult(
            text=text, error=error, response_time_ms=elapsed,
            screenshot_path=None,
            page_url=self._page.url if self._page else None,
            ai_steps_taken=calls, ai_calls_used=calls,
            ai_tokens_input=ti, ai_tokens_output=to,
        )
