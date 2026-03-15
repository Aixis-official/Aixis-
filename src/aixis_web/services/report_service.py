"""Report generation service."""
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import AuditReportRecord
from ..db.models.audit import AuditSession
from ..config import settings

# Bridge to core report generators
from aixis_agent.reporting.html_renderer import render_html_report
from aixis_agent.reporting.json_renderer import render_json_report
from aixis_agent.reporting.pdf_renderer import render_pdf_report
from aixis_agent.core.models import AuditReport


async def generate_report(db: AsyncSession, session_id: str, report_type: str = "individual",
                           format: str = "html") -> AuditReportRecord | None:
    """Generate a report from an audit session."""
    session = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
    session_obj = session.scalar_one_or_none()
    if not session_obj:
        return None

    output_dir = Path(settings.output_dir) / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{session_obj.session_code}_{report_type}_{timestamp}"

    # TODO: Build AuditReport from DB data, call appropriate renderer
    # For now, create the record
    file_path = str(output_dir / f"{filename}.{format}")

    record = AuditReportRecord(
        session_id=session_id,
        report_type=report_type,
        format=format,
        file_path=file_path,
        is_public=False,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record
