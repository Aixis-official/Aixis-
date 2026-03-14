"""Abstract base classes defining the plugin architecture."""

from abc import ABC, abstractmethod
from pathlib import Path

from .models import (
    AuditReport,
    AxisScore,
    ExecutionResult,
    TargetConfig,
    TestResult,
)


class TestExecutor(ABC):
    """Abstraction that separates HOW to send a prompt from WHAT to send.

    PlaywrightExecutor (v1) and APIExecutor (future) both implement this.
    The orchestrator never knows or cares which executor is active.
    """

    @abstractmethod
    async def initialize(self, target_config: TargetConfig) -> None:
        """Set up connection to the target tool (browser launch, login, etc.)."""

    @abstractmethod
    async def send_prompt(self, prompt: str) -> ExecutionResult:
        """Send a single prompt and return the result."""

    @abstractmethod
    async def cleanup(self) -> None:
        """Release resources (close browser, etc.)."""


class Scorer(ABC):
    """Scores a set of test results on a single axis."""

    @abstractmethod
    def score(self, results: list[TestResult], rules_config: dict) -> AxisScore:
        """Evaluate results and return a score for this axis."""


class ReportRenderer(ABC):
    """Renders an AuditReport into a specific output format."""

    @abstractmethod
    def render(self, report: AuditReport, output_path: Path) -> Path:
        """Render report to file, return the output path."""
