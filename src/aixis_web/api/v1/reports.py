"""Report generation and download endpoints."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.base import get_db
from ...db.models.audit import AuditSession
from ...db.models.user import AuditReportRecord, User
from ..deps import require_analyst

router = APIRouter()

# Safe base directory for report files
REPORT_BASE_DIR = Path(settings.output_dir).resolve()


class ReportGenerateBody(BaseModel):
    session_id: str
    report_type: str = "full"
    format: str = "html"


class ReportMetadataResponse(BaseModel):
    id: str
    session_id: str
    report_type: str
    format: str
    file_size_bytes: int | None = None
    is_public: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("/generate", response_model=ReportMetadataResponse, status_code=status.HTTP_201_CREATED)
async def generate_report(
    body: ReportGenerateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Generate a report for an audit session."""
    # Validate format
    if body.format not in ("html", "pdf", "json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="サポートされるフォーマット: html, pdf, json",
        )
    if body.report_type not in ("full", "summary", "executive"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="サポートされるレポート種別: full, summary, executive",
        )

    session_result = await db.execute(
        select(AuditSession).where(AuditSession.id == body.session_id)
    )
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    # Create report record (actual generation would be async task)
    report = AuditReportRecord(
        session_id=body.session_id,
        report_type=body.report_type,
        format=body.format,
        is_public=False,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


@router.get("/{report_id}", response_model=ReportMetadataResponse)
async def get_report(
    report_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Get report metadata (requires analyst auth)."""
    result = await db.execute(
        select(AuditReportRecord).where(AuditReportRecord.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="レポートが見つかりません",
        )
    return report


def _validate_report_path(file_path_str: str) -> Path:
    """Validate that the report file path is within the allowed base directory.

    Prevents path traversal attacks by resolving symlinks and checking
    that the resolved path is under REPORT_BASE_DIR.
    """
    file_path = Path(file_path_str).resolve()

    # Ensure the file is within the allowed base directory
    try:
        file_path.relative_to(REPORT_BASE_DIR)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="レポートファイルへのアクセスが拒否されました",
        )

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="レポートファイルが見つかりません",
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="無効なファイルパスです",
        )

    return file_path


@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Download report file (requires analyst auth)."""
    result = await db.execute(
        select(AuditReportRecord).where(AuditReportRecord.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="レポートが見つかりません",
        )

    if not report.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="レポートファイルがまだ生成されていません",
        )

    # Validate path is safe (prevents path traversal)
    file_path = _validate_report_path(report.file_path)

    media_type = {
        "pdf": "application/pdf",
        "html": "text/html",
        "json": "application/json",
    }.get(report.format, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=f"report-{report.id}.{report.format}",
    )
