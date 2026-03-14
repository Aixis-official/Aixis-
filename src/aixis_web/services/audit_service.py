"""Audit session orchestration service."""
import uuid
from datetime import datetime
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.audit import AuditSession, DBTestCase, DBTestResult
from ..db.models.tool import Tool, ToolTargetConfig
from ..config import settings

# Bridge to core engine
from aixis_agent.patterns.generator import generate_all
from aixis_agent.profiles.registry import get_profile, get_categories_for_profile
from aixis_agent.scoring.engine import ScoringEngine
from aixis_agent.core.models import TestCase, TestResult as CoreTestResult


async def create_audit_session(db: AsyncSession, tool_id: str, profile_id: str, initiated_by: str | None = None) -> AuditSession:
    """Create a new audit session."""
    session_code = f"audit-{uuid.uuid4().hex[:8]}"
    session = AuditSession(
        session_code=session_code,
        tool_id=tool_id,
        profile_id=profile_id,
        status="pending",
        initiated_by=initiated_by,
        created_at=datetime.utcnow(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_audit_sessions(db: AsyncSession, tool_id: str | None = None, status: str | None = None,
                               offset: int = 0, limit: int = 20):
    query = select(AuditSession)
    if tool_id:
        query = query.where(AuditSession.tool_id == tool_id)
    if status:
        query = query.where(AuditSession.status == status)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    results = await db.execute(query.offset(offset).limit(limit).order_by(AuditSession.created_at.desc()))
    return results.scalars().all(), total or 0


async def get_audit_session(db: AsyncSession, session_id: str) -> AuditSession | None:
    result = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
    return result.scalar_one_or_none()


async def get_session_results(db: AsyncSession, session_id: str) -> list[DBTestResult]:
    result = await db.execute(
        select(DBTestResult).where(DBTestResult.session_id == session_id).order_by(DBTestResult.executed_at)
    )
    return result.scalars().all()


def generate_test_cases_for_session(profile_id: str, patterns_dir: Path) -> list[TestCase]:
    """Generate test cases based on the profile."""
    profile = get_profile(profile_id, patterns_dir.parent / "profiles")
    if profile:
        categories = get_categories_for_profile(profile)
    else:
        categories = None
    return generate_all(patterns_dir, categories)
