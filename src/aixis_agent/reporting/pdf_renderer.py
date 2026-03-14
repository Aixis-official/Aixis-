"""PDF report renderer using WeasyPrint."""

from pathlib import Path

from ..core.interfaces import ReportRenderer
from ..core.models import AuditReport
from .html_renderer import HTMLRenderer


class PDFRenderer(ReportRenderer):
    """Renders audit report as PDF via HTML intermediate."""

    def render(self, report: AuditReport, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path = output_path.with_suffix(".pdf")

        # First render as HTML
        html_renderer = HTMLRenderer()
        html_path = html_renderer.render(report, output_path)

        # Convert HTML to PDF using WeasyPrint
        try:
            from weasyprint import HTML
            HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        except ImportError:
            raise RuntimeError(
                "weasyprint is required for PDF generation. "
                "Install it with: pip install weasyprint"
            )
        except OSError as e:
            raise RuntimeError(
                f"PDF generation failed (system library missing): {e}\n"
                "WeasyPrintにはシステムライブラリが必要です。\n"
                "macOS: brew install pango gdk-pixbuf libffi\n"
                "Ubuntu: apt install libpango-1.0-0 libgdk-pixbuf2.0-0"
            )
        except Exception as e:
            raise RuntimeError(f"PDF generation failed: {e}")

        return pdf_path
