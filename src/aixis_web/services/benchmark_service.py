"""Benchmark and leaderboard business logic."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.benchmark import (
    BenchmarkRun,
    BenchmarkSuite,
    BenchmarkTestCase,
    LeaderboardEntry,
)
from ..db.models.tool import Tool
from ..schemas.benchmark import BenchmarkSuiteCreate, BenchmarkTestCaseCreate


async def create_suite(
    db: AsyncSession, data: BenchmarkSuiteCreate, user_id: str
) -> BenchmarkSuite:
    """Create a new BenchmarkSuite."""
    # Check slug uniqueness
    existing = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.slug == data.slug)
    )
    if existing.scalar_one_or_none():
        raise ValueError("このスラッグは既に使用されています")

    suite = BenchmarkSuite(
        slug=data.slug,
        name=data.name,
        name_jp=data.name_jp,
        description=data.description,
        description_jp=data.description_jp,
        version=data.version,
        category_id=data.category_id,
        created_by=user_id,
    )
    db.add(suite)
    await db.commit()
    await db.refresh(suite)
    return suite


async def add_test_cases(
    db: AsyncSession, suite_id: str, cases: list[BenchmarkTestCaseCreate]
) -> int:
    """Batch add test cases to a suite. Returns count added."""
    # Verify suite exists
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.id == suite_id)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise ValueError("ベンチマークスイートが見つかりません")

    added = 0
    for i, case_data in enumerate(cases):
        tc = BenchmarkTestCase(
            suite_id=suite_id,
            category=case_data.category,
            prompt=case_data.prompt,
            expected_behaviors=case_data.expected_behaviors,
            failure_indicators=case_data.failure_indicators,
            weight=case_data.weight,
            tags=case_data.tags,
            sort_order=suite.test_case_count + i,
        )
        db.add(tc)
        added += 1

    suite.test_case_count += added
    await db.commit()
    return added


async def publish_suite(db: AsyncSession, suite_id: str) -> BenchmarkSuite:
    """Publish a benchmark suite."""
    result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.id == suite_id)
    )
    suite = result.scalar_one_or_none()
    if not suite:
        raise ValueError("ベンチマークスイートが見つかりません")

    suite.is_published = True
    suite.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(suite)
    return suite


async def start_benchmark_run(
    db: AsyncSession, suite_id: str, tool_id: str
) -> BenchmarkRun:
    """Create a BenchmarkRun record. The actual audit is triggered separately."""
    # Verify suite and tool exist
    suite_result = await db.execute(
        select(BenchmarkSuite).where(BenchmarkSuite.id == suite_id)
    )
    suite = suite_result.scalar_one_or_none()
    if not suite:
        raise ValueError("ベンチマークスイートが見つかりません")

    tool_result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise ValueError("ツールが見つかりません")

    run = BenchmarkRun(
        suite_id=suite_id,
        tool_id=tool_id,
        suite_version=suite.version,
        total_cases=suite.test_case_count,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def compute_leaderboard(db: AsyncSession, suite_id: str) -> None:
    """Recalculate all leaderboard ranks for a suite by best_score desc."""
    # Get best run per tool
    subquery = (
        select(
            BenchmarkRun.tool_id,
            func.max(BenchmarkRun.score).label("best_score"),
        )
        .where(
            BenchmarkRun.suite_id == suite_id,
            BenchmarkRun.completed_at.isnot(None),
        )
        .group_by(BenchmarkRun.tool_id)
        .subquery()
    )

    rows = await db.execute(
        select(subquery.c.tool_id, subquery.c.best_score).order_by(
            subquery.c.best_score.desc()
        )
    )
    entries = rows.all()

    # Clear existing leaderboard entries for this suite
    await db.execute(
        delete(LeaderboardEntry).where(LeaderboardEntry.suite_id == suite_id)
    )

    # Create new entries with ranks
    now = datetime.now(timezone.utc)
    for rank, (tool_id, best_score) in enumerate(entries, 1):
        # Find the best run
        run_result = await db.execute(
            select(BenchmarkRun)
            .where(
                BenchmarkRun.suite_id == suite_id,
                BenchmarkRun.tool_id == tool_id,
                BenchmarkRun.score == best_score,
                BenchmarkRun.completed_at.isnot(None),
            )
            .order_by(BenchmarkRun.completed_at.desc())
            .limit(1)
        )
        best_run = run_result.scalar_one_or_none()

        entry = LeaderboardEntry(
            suite_id=suite_id,
            tool_id=tool_id,
            best_run_id=best_run.id if best_run else None,
            best_score=best_score,
            rank=rank,
            updated_at=now,
        )
        db.add(entry)

    await db.commit()


async def get_leaderboard(
    db: AsyncSession, suite_id: str
) -> list[dict]:
    """Return ranked leaderboard results with tool info."""
    result = await db.execute(
        select(LeaderboardEntry, Tool)
        .join(Tool, LeaderboardEntry.tool_id == Tool.id)
        .where(LeaderboardEntry.suite_id == suite_id)
        .order_by(LeaderboardEntry.rank)
    )
    rows = result.all()

    leaderboard = []
    for entry, tool in rows:
        leaderboard.append(
            {
                "rank": entry.rank,
                "tool_id": entry.tool_id,
                "tool_slug": tool.slug,
                "tool_name": tool.name,
                "tool_name_jp": tool.name_jp,
                "best_score": entry.best_score,
                "updated_at": entry.updated_at,
            }
        )
    return leaderboard
