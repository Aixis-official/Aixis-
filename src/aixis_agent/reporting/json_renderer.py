"""JSON report renderer."""

from pathlib import Path

from ..core.interfaces import ReportRenderer
from ..core.models import AuditReport


class JSONRenderer(ReportRenderer):
    """Renders audit report as JSON."""

    def render(self, report: AuditReport, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        json_path = output_path.with_suffix(".json")

        # Exclude raw_results from the summary JSON to keep it manageable
        report_data = report.model_dump(mode="json")

        # Write full report
        import json
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        return json_path
