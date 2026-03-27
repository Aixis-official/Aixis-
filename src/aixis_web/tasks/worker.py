"""ARQ background task worker.

After Chrome extension migration, audit execution is handled by the extension.
This worker handles LLM scoring and other background tasks.
"""

import logging
from arq import create_pool
from arq.connections import RedisSettings
from ..config import settings

logger = logging.getLogger(__name__)


async def startup(ctx):
    """Worker startup - initialize DB connection."""
    from ..db.base import async_session
    ctx["db_session_factory"] = async_session


async def shutdown(ctx):
    """Worker shutdown."""
    pass


async def run_llm_scoring(ctx, session_id: str, tool_id: str):
    """Run LLM-based scoring for a completed extension session (background task)."""
    from ..services.llm_scorer import LLMScorer
    from sqlalchemy import text

    db_factory = ctx["db_session_factory"]

    async with db_factory() as db:
        try:
            scorer = LLMScorer()
            await scorer.score_session(session_id, tool_id, db)

            await db.execute(
                text("UPDATE audit_sessions SET status = 'awaiting_manual' WHERE id = :sid"),
                {"sid": session_id},
            )
            await db.commit()

            logger.info("LLM scoring completed for session %s", session_id)
            return {"status": "completed", "session_id": session_id}

        except Exception as e:
            logger.exception("LLM scoring failed for session %s: %s", session_id, e)
            await db.execute(
                text("UPDATE audit_sessions SET status = 'failed', error_message = :err WHERE id = :sid"),
                {"err": str(e)[:2000], "sid": session_id},
            )
            await db.commit()
            return {"error": str(e)}


class WorkerSettings:
    """ARQ worker configuration."""
    functions = [run_llm_scoring]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url) if hasattr(settings, 'redis_url') else RedisSettings()
    max_jobs = 3
    job_timeout = 600  # 10 min max per scoring job
