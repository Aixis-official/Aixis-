"""Playwright-based test executor for browser automation of SaaS tools."""

import asyncio
import os
import threading
import time
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext

from ..core.interfaces import TestExecutor
from ..core.models import ExecutionResult, TargetConfig
from ..utils.logging import get_logger

logger = get_logger(__name__)


class PlaywrightExecutor(TestExecutor):
    """Executes test prompts via browser automation using Playwright.

    Supports:
      - Standard <input>/<textarea> and contenteditable (TipTap/ProseMirror) inputs
      - Multi-step submission flows (e.g. Gamma: prompt → outline → generate)
      - Login steps and pre/post-submit actions
      - Environment variable interpolation in action values (${VAR_NAME})
      - Manual login pause: opens browser and waits for user to complete login
    """

    def __init__(self, screenshots_dir: Path | None = None):
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._config: TargetConfig | None = None
        self._screenshots_dir = screenshots_dir or Path("output/screenshots")
        self._prompt_count = 0
        # Event for manual login pause/resume
        self._login_event: threading.Event | None = None

    def set_login_event(self, event: threading.Event) -> None:
        """Set the threading.Event used to pause for manual login."""
        self._login_event = event

    @staticmethod
    def _cleanup_stale_locks(profile_dir: Path) -> None:
        """Remove stale Chromium lock files from a previous crashed session."""
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

        # Kill any zombie Chromium processes using this profile
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
            pass  # pgrep not available or other OS issue

    async def initialize(self, target_config: TargetConfig) -> None:
        self._config = target_config
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()

        # Use a persistent browser context with the real Chrome channel
        # so that target tools (Google, etc.) don't block login as "unsafe browser".
        # The user data dir persists cookies/sessions across audit runs.
        user_data_dir = Path.home() / ".aixis" / "browser-profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        # Clean stale browser locks from previous failed sessions
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
        # launch_persistent_context opens a page automatically
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        # Set generous default timeouts (5 min) so manual login isn't killed
        self._context.set_default_navigation_timeout(300_000)
        self._context.set_default_timeout(60_000)

        # Navigate to target URL — use domcontentloaded (not networkidle)
        # because many SaaS pages never fully stop network activity.
        await self._page.goto(target_config.url, wait_until="domcontentloaded", timeout=60_000)

        # If wait_for_manual_login is True, pause here and wait for user signal
        if target_config.wait_for_manual_login and self._login_event:
            logger.info("Waiting for manual login... Browser is open at %s", target_config.url)
            # Block until the event is set (user clicks "continue" in the UI)
            # Poll every 1s to allow asyncio to remain responsive
            while not self._login_event.is_set():
                await asyncio.sleep(1.0)
            logger.info("Manual login complete, resuming automation")
            # After manual login, user may have navigated to a different page
            # or opened new tabs. Re-acquire the active page.
            pages = self._context.pages
            if pages:
                self._page = pages[-1]  # use the most recently active page
        else:
            # Execute automated login steps if any
            for step in target_config.login_steps:
                await self._execute_action(step)

        # Execute pre-prompt actions if any
        for action in target_config.pre_prompt_actions:
            await self._execute_action(action)

    async def send_prompt(self, prompt: str) -> ExecutionResult:
        if not self._page or not self._config:
            return ExecutionResult(error="Executor not initialized")

        self._prompt_count += 1
        start_time = time.monotonic()

        # Use workflow_steps if defined, otherwise fall back to legacy flow
        if self._config.workflow_steps:
            return await self._send_prompt_workflow(prompt, start_time)
        else:
            return await self._send_prompt_legacy(prompt, start_time)

    async def _send_prompt_workflow(self, prompt: str, start_time: float) -> ExecutionResult:
        """Execute a prompt using the configurable workflow_steps."""
        response_text = None
        screenshot_path = None

        try:
            for i, step in enumerate(self._config.workflow_steps):
                action_type = step.get("action", "")
                logger.info(
                    "Workflow step %d/%d: %s %s",
                    i + 1, len(self._config.workflow_steps),
                    action_type, step.get("selector", step.get("value", ""))[:60],
                )

                if action_type == "extract":
                    # Terminal step: extract response text
                    selector = step.get("selector", "")
                    timeout = int(step.get("timeout", str(self._config.wait_for_response_timeout_ms)))
                    await self._page.wait_for_selector(selector, timeout=timeout, state="visible")
                    await asyncio.sleep(float(step.get("settle_ms", "1000")) / 1000)
                    response_text = await self._page.text_content(selector)
                else:
                    # Substitute {prompt} placeholder in values
                    resolved_step = {
                        k: v.replace("{prompt}", prompt).replace("{url}", self._config.url)
                        if isinstance(v, str) else v
                        for k, v in step.items()
                    }
                    await self._execute_action(resolved_step)

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Take screenshot
            screenshot_path = str(
                self._screenshots_dir / f"prompt_{self._prompt_count:04d}.png"
            )
            await self._page.screenshot(path=screenshot_path)

            # Run reset steps to prepare for next test
            for step in self._config.reset_steps:
                resolved = {
                    k: v.replace("{url}", self._config.url) if isinstance(v, str) else v
                    for k, v in step.items()
                }
                try:
                    await self._execute_action(resolved)
                except Exception as e:
                    logger.warning("Reset step failed (non-fatal): %s", e)

            # Wait between prompts
            if self._config.inter_prompt_delay_ms > 0:
                await asyncio.sleep(self._config.inter_prompt_delay_ms / 1000)

            return ExecutionResult(
                text=response_text,
                response_time_ms=elapsed_ms,
                screenshot_path=screenshot_path,
                page_url=self._page.url,
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            try:
                screenshot_path = str(
                    self._screenshots_dir / f"error_{self._prompt_count:04d}.png"
                )
                await self._page.screenshot(path=screenshot_path)
            except Exception:
                pass
            return ExecutionResult(
                error=str(e),
                response_time_ms=elapsed_ms,
                screenshot_path=screenshot_path,
                page_url=self._page.url if self._page else None,
            )

    async def _send_prompt_legacy(self, prompt: str, start_time: float) -> ExecutionResult:
        """Legacy send_prompt using simple input→submit→response selectors."""
        try:
            # Fill prompt into input
            await self._fill_prompt(prompt)

            # Click submit button
            submit_el = self._page.locator(self._config.submit_selector).first
            await submit_el.wait_for(state="visible", timeout=5000)
            await submit_el.click()

            # Execute post-submit actions (e.g. wait for outline, then click generate)
            for action in self._config.post_submit_actions:
                await self._execute_action(action)

            # Wait for final response
            await self._page.wait_for_selector(
                self._config.response_selector,
                timeout=self._config.wait_for_response_timeout_ms,
                state="visible",
            )

            # Small extra wait for content to stabilize
            await asyncio.sleep(1.0)

            # Extract response text
            response_text = await self._page.text_content(self._config.response_selector)

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Take screenshot
            screenshot_path = str(
                self._screenshots_dir / f"prompt_{self._prompt_count:04d}.png"
            )
            await self._page.screenshot(path=screenshot_path)

            # Wait between prompts
            if self._config.inter_prompt_delay_ms > 0:
                await asyncio.sleep(self._config.inter_prompt_delay_ms / 1000)

            return ExecutionResult(
                text=response_text,
                response_time_ms=elapsed_ms,
                screenshot_path=screenshot_path,
                page_url=self._page.url,
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            # Try to take error screenshot
            screenshot_path = None
            try:
                screenshot_path = str(
                    self._screenshots_dir / f"error_{self._prompt_count:04d}.png"
                )
                await self._page.screenshot(path=screenshot_path)
            except Exception:
                pass

            return ExecutionResult(
                error=str(e),
                response_time_ms=elapsed_ms,
                screenshot_path=screenshot_path,
                page_url=self._page.url if self._page else None,
            )

    async def cleanup(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    # ── Private helpers ──

    async def _fill_prompt(self, prompt: str) -> None:
        """Fill the prompt into the input element, handling both standard and
        contenteditable inputs."""
        input_el = self._page.locator(self._config.input_selector).first
        await input_el.wait_for(state="visible", timeout=10000)

        if self._config.input_method == "type":
            # For contenteditable (TipTap/ProseMirror): click, select all, then type
            await input_el.click()
            await self._page.keyboard.press("Meta+a")  # Select all
            await self._page.keyboard.press("Backspace")  # Clear
            await input_el.type(prompt, delay=10)
        else:
            # Standard fill for <input>/<textarea>
            await input_el.click()
            await input_el.fill("")
            await input_el.fill(prompt)

    async def _execute_action(self, action: dict[str, str]) -> None:
        """Execute a single UI action (click, fill, wait, etc.).

        Supports environment variable interpolation: ${VAR_NAME} in values.
        """
        action_type = action.get("action", "")
        selector = action.get("selector", "")
        value = self._interpolate_env(action.get("value", ""))

        if action_type == "click":
            timeout = int(action.get("timeout", "10000"))
            el = self._page.locator(selector).first
            await el.wait_for(state="visible", timeout=timeout)
            await el.click()
        elif action_type == "click_text":
            # Click a button/link by its visible text content
            timeout = int(action.get("timeout", "10000"))
            el = self._page.get_by_role("button", name=value).or_(
                self._page.locator(f"button:has-text('{value}')"),
            ).or_(
                self._page.locator(f"a:has-text('{value}')"),
            ).first
            await el.wait_for(state="visible", timeout=timeout)
            await el.click()
        elif action_type == "fill":
            await self._page.fill(selector, value)
        elif action_type == "type":
            # Type character-by-character (for contenteditable)
            el = self._page.locator(selector).first
            await el.click()
            await el.type(value, delay=10)
        elif action_type == "clear_type":
            # Click, select all, delete, then type (for contenteditable)
            el = self._page.locator(selector).first
            await el.wait_for(state="visible", timeout=10000)
            await el.click()
            await self._page.keyboard.press("Meta+a")
            await self._page.keyboard.press("Backspace")
            await asyncio.sleep(0.2)
            await el.type(value, delay=10)
        elif action_type == "wait":
            timeout = int(action.get("timeout", "10000"))
            await self._page.wait_for_selector(selector, timeout=timeout, state="visible")
        elif action_type == "wait_hidden":
            # Wait for element to disappear (e.g. loading spinner)
            timeout = int(action.get("timeout", "60000"))
            await self._page.wait_for_selector(selector, timeout=timeout, state="hidden")
        elif action_type == "wait_ms":
            await asyncio.sleep(int(value) / 1000)
        elif action_type == "wait_url":
            # Wait until URL contains a specific substring
            timeout = int(action.get("timeout", "30000"))
            await self._page.wait_for_url(f"**{value}**", timeout=timeout)
        elif action_type == "goto":
            await self._page.goto(value, wait_until="domcontentloaded", timeout=60_000)
        elif action_type == "press":
            await self._page.press(selector, value)
        elif action_type == "scroll_down":
            amount = int(action.get("value", "500"))
            await self._page.mouse.wheel(0, amount)
        elif action_type == "screenshot":
            path = str(self._screenshots_dir / f"action_{self._prompt_count:04d}_{value}.png")
            await self._page.screenshot(path=path)
        else:
            logger.warning(f"Unknown action type: {action_type}")

    @staticmethod
    def _interpolate_env(value: str) -> str:
        """Replace ${VAR_NAME} with environment variable values."""
        if "${" not in value:
            return value
        import re
        def replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        return re.sub(r"\$\{(\w+)\}", replacer, value)
