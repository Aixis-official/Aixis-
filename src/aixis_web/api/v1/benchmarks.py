"""Benchmark and leaderboard endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.benchmark import (
    BenchmarkRun,
    BenchmarkSuite,
    BenchmarkTestCase,
    LeaderboardEntry,
)
from ...db.models.tool import Tool
from ...db.models.user import User
from ...schemas.benchmark import (
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    BenchmarkSuiteCreate,
    BenchmarkSuiteResponse,
    BenchmarkTestCaseCreate,
    LeaderboardEntryResponse,
)
from ...services.benchmark_service import (
    add_test_cases,
    compute_leaderboard,
    create_suite,
    get_leaderboard,
    publish_suite,
    start_benchmark_run,
)
from ..deps import get_current_user, require_admin, require_analyst

router = APIRouter()


@router.get("/", response_model=list[BenchmarkSuiteResponse])
async def list_suites(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)] = None,
    include_drafts: bool = Query(False),
):
    """List benchmark suites. Public sees only published; analysts see all."""
    if include_drafts and (not user or user.role not in ("admin", "analyst", "auditor")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ドラフト閲覧にはアナリスト以上の権限が必要です",
        )
    query = select(BenchmarkSuite)
    if not include_drafts:
        query = query.where(BenchmarkSuite.is_published.is_(True))
    query = query.order_by(BenchmarkSuite.created_at.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{slug}", response_model=BenchmarkSuiteResponse)
async def get_suite(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get suite detail by slug."""
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )
    return suite


@router.post(
    "/", response_model=BenchmarkSuiteResponse, status_code=status.HTTP_201_CREATED
)
async def create_suite_endpoint(
    body: BenchmarkSuiteCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    """Create a new benchmark suite (admin only)."""
    try:
        suite = await create_suite(db, body, user.id)
        return suite
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e)
        )


@router.post("/{slug}/test-cases", status_code=status.HTTP_201_CREATED)
async def add_test_cases_endpoint(
    slug: str,
    body: list[BenchmarkTestCaseCreate],
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin)],
):
    """Bulk add test cases to a suite (admin only)."""
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )

    try:
        count = await add_test_cases(db, suite.id, body)
        return {"added": count, "total": suite.test_case_count}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        )


@router.post("/{slug}/publish", response_model=BenchmarkSuiteResponse)
async def publish_suite_endpoint(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin)],
):
    """Publish a benchmark suite (admin only)."""
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )

    try:
        suite = await publish_suite(db, suite.id)
        return suite
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )


@router.post("/{slug}/run", response_model=BenchmarkRunResponse)
async def trigger_benchmark_run(
    slug: str,
    body: BenchmarkRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Trigger a benchmark run for a tool (analyst only)."""
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )

    try:
        run = await start_benchmark_run(db, suite.id, body.tool_id)
        return run
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        )


@router.get("/{slug}/leaderboard", response_model=list[LeaderboardEntryResponse])
async def get_leaderboard_endpoint(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get public leaderboard for a benchmark suite."""
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )

    entries = await get_leaderboard(db, suite.id)
    return entries


@router.post("/{slug}/leaderboard/recompute")
async def recompute_leaderboard(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin)],
):
    """Recompute leaderboard rankings (admin only)."""
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )

    await compute_leaderboard(db, suite.id)
    return {"message": "リーダーボードを再計算しました"}


@router.get("/{slug}/runs", response_model=list[BenchmarkRunResponse])
async def list_runs(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all runs for a benchmark suite."""
    suite_result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = suite_result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )

    result = await db.execute(
        select(BenchmarkRun, Tool)
        .outerjoin(Tool, BenchmarkRun.tool_id == Tool.id)
        .where(BenchmarkRun.suite_id == suite.id)
        .order_by(BenchmarkRun.started_at.desc())
    )
    rows = result.all()

    runs = []
    for run, tool in rows:
        resp = BenchmarkRunResponse.model_validate(run)
        if tool:
            resp.tool_name = tool.name
            resp.tool_slug = tool.slug
        runs.append(resp)
    return runs


@router.get("/{slug}/runs/{tool_slug}", response_model=list[BenchmarkRunResponse])
async def get_tool_runs(
    slug: str,
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a tool's benchmark runs for a suite."""
    suite_result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == slug)
    )
    suite = suite_result.scalar_one_or_none()
    if not suite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンチマークスイートが見つかりません",
        )

    tool_result = await db.execute(select(Tool).where(Tool.slug == tool_slug))
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    result = await db.execute(
        select(BenchmarkRun)
        .where(BenchmarkRun.suite_id == suite.id, BenchmarkRun.tool_id == tool.id)
        .order_by(BenchmarkRun.started_at.desc())
    )
    runs = result.scalars().all()

    responses = []
    for run in runs:
        resp = BenchmarkRunResponse.model_validate(run)
        resp.tool_name = tool.name
        resp.tool_slug = tool.slug
        responses.append(resp)
    return responses
