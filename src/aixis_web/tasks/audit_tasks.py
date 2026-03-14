"""Audit task helpers for enqueueing jobs."""
from arq import create_pool
from arq.connections import RedisSettings
from ..config import settings


async def enqueue_audit(session_id: str):
    """Enqueue an audit session for background execution."""
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await redis.enqueue_job("run_audit", session_id)
