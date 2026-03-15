"""ARQ background task worker for long-running audit jobs."""
import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from ..config import settings


async def startup(ctx):
    """Worker startup - initialize DB connection."""
    from ..db.base import async_session
    ctx["db_session_factory"] = async_session


async def shutdown(ctx):
    """Worker shutdown."""
    pass


async def run_audit(ctx, session_id: str):
    """Execute an audit session (background task).

    1. Load tool target config
    2. Generate test cases
    3. Launch Playwright executor
    4. Execute tests, store results
    5. Run automated scoring
    6. Set status to awaiting_manual
    """
    from ..db.models.audit import AuditSession, DBTestCase, DBTestResult
    from ..db.models.score import AxisScoreRecord
    from ..db.models.tool import Tool, ToolTargetConfig
    from ..services.audit_service import generate_test_cases_for_session
    from aixis_agent.executors.playwright_executor import PlaywrightExecutor
    from aixis_agent.scoring.engine import ScoringEngine
    from aixis_agent.core.models import TargetConfig, TestResult as CoreTestResult
    from sqlalchemy import select
    from datetime import datetime, timezone
    from pathlib import Path
    import yaml

    db_factory = ctx["db_session_factory"]

    async with db_factory() as db:
        # Load session
        result = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return {"error": "Session not found"}

        # Update status
        session.status = "running"
        session.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            # Load tool and target config
            tool = await db.execute(select(Tool).where(Tool.id == session.tool_id))
            tool_obj = tool.scalar_one()

            config_result = await db.execute(
                select(ToolTargetConfig)
                .where(ToolTargetConfig.tool_id == tool_obj.id, ToolTargetConfig.is_active == True)
                .order_by(ToolTargetConfig.version.desc())
            )
            target_config_record = config_result.scalar_one_or_none()

            if not target_config_record:
                raise ValueError(f"No active target config for tool {tool_obj.slug}")

            target_config = TargetConfig(**yaml.safe_load(target_config_record.config_yaml))

            # Generate test cases
            patterns_dir = Path(settings.config_dir) / "patterns"
            test_cases = generate_test_cases_for_session(session.profile_id, patterns_dir)
            session.total_planned = len(test_cases)
            await db.commit()

            # Store test cases
            for tc in test_cases:
                db.add(DBTestCase(
                    id=tc.id,
                    session_id=session_id,
                    category=tc.category.value,
                    prompt=tc.prompt,
                    metadata_json=tc.metadata,
                    expected_behaviors=tc.expected_behaviors,
                    failure_indicators=tc.failure_indicators,
                    tags=tc.tags,
                ))
            await db.commit()

            # Execute tests
            executor = PlaywrightExecutor(
                screenshots_dir=Path(settings.output_dir) / session.session_code / "screenshots"
            )
            core_results = []

            try:
                await executor.initialize(target_config)

                for tc in test_cases:
                    exec_result = await executor.send_prompt(tc.prompt)

                    core_result = CoreTestResult(
                        test_case_id=tc.id,
                        target_tool=tool_obj.slug,
                        category=tc.category,
                        prompt_sent=tc.prompt,
                        response_raw=exec_result.text,
                        response_time_ms=exec_result.response_time_ms,
                        error=exec_result.error,
                        screenshot_path=exec_result.screenshot_path,
                        metadata=tc.metadata,
                    )
                    core_results.append(core_result)

                    # Store to DB
                    db.add(DBTestResult(
                        session_id=session_id,
                        test_case_id=tc.id,
                        category=tc.category.value,
                        prompt_sent=tc.prompt,
                        response_raw=exec_result.text,
                        response_time_ms=int(exec_result.response_time_ms),
                        error=exec_result.error,
                        screenshot_path=exec_result.screenshot_path,
                    ))

                    session.total_executed += 1
                    await db.commit()
            finally:
                await executor.cleanup()

            # Run automated scoring
            engine = ScoringEngine()
            cases_map = {tc.id: tc for tc in test_cases}
            report = engine.score_all(core_results, test_cases, tool_obj.slug)

            # Store axis scores
            for axis_score in report.axis_scores:
                db.add(AxisScoreRecord(
                    session_id=session_id,
                    tool_id=tool_obj.id,
                    axis=axis_score.axis.value,
                    axis_name_jp=axis_score.axis_name_jp,
                    score=axis_score.score,
                    confidence=axis_score.confidence,
                    source=axis_score.source.value,
                    details=[d.model_dump() for d in axis_score.details],
                    strengths=axis_score.strengths,
                    risks=axis_score.risks,
                ))

            session.status = "awaiting_manual"
            await db.commit()

            return {"status": "awaiting_manual", "total_executed": len(core_results)}

        except Exception as e:
            session.status = "failed"
            session.error_message = str(e)
            await db.commit()
            return {"error": str(e)}


class WorkerSettings:
    """ARQ worker configuration."""
    functions = [run_audit]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url) if hasattr(settings, 'redis_url') else RedisSettings()
    max_jobs = settings.max_concurrent_audits
    job_timeout = 3600  # 1 hour max per audit
