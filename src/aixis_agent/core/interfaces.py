"""Abstract base classes defining the plugin architecture."""

from abc import ABC, abstractmethod
from pathlib import Path

from .models import (
    AuditReport,
    AxisScore,
    TestResult,
)


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
