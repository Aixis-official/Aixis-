"""Report data assembly: takes scoring results and builds a complete AuditReport."""

from pathlib import Path

from ..core.models import AuditReport, TestCase, TestResult
from ..orchestrator.session import SessionStore
from ..scoring.engine import ScoringEngine, load_scoring_rules


def build_report(
    session_id: str,
    store: SessionStore,
    scoring_rules_path: Path | None = None,
) -> AuditReport:
    """Build a complete audit report from stored session data."""
    session = store.get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    results = store.get_results(session_id)
    cases = store.get_test_cases(session_id)

    if not results:
        raise ValueError(f"No results found for session {session_id}")

    rules_config = {}
    if scoring_rules_path and scoring_rules_path.exists():
        rules_config = load_scoring_rules(scoring_rules_path)

    engine = ScoringEngine(rules_config)
    report = engine.score_all(results, cases, session.target_tool)
    report.report_id = f"audit-{session_id}"

    return report
