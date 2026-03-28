"""Database engine and base configuration."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..config import settings


def _ensure_async_url(url: str) -> str:
    """Convert sync DB URLs to async driver URLs at the last moment."""
    if not url or not url.strip():
        import os
        if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_SERVICE_ID"):
            raise RuntimeError(
                "DATABASE_URL is empty on Railway! "
                "Ensure ${{Postgres.DATABASE_URL}} variable reference resolves correctly. "
                "Check that PostgreSQL service is in the SAME project as the app."
            )
        return "sqlite+aiosqlite:///./aixis.db"
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


import logging as _logging
_log = _logging.getLogger(__name__)

_db_url = _ensure_async_url(settings.database_url)

# Log the resolved URL (mask password for safety)
_safe_url = _db_url
if "@" in _safe_url:
    # mask password: scheme://user:****@host/db
    _pre_at = _safe_url.split("@")[0]
    _post_at = _safe_url.split("@", 1)[1]
    if ":" in _pre_at.split("//", 1)[-1]:
        _scheme_user = _pre_at.rsplit(":", 1)[0]
        _safe_url = f"{_scheme_user}:****@{_post_at}"
_log.critical("DATABASE URL resolved to: %s", _safe_url)

# Configure connection pool — SQLite uses NullPool by default,
# PostgreSQL/MySQL benefit from explicit pool limits.
_engine_kwargs: dict = {"echo": False}
if "sqlite" not in _db_url:
    _engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 40,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    })

try:
    engine = create_async_engine(_db_url, **_engine_kwargs)
except Exception as _e:
    _log.critical("Failed to create engine. Raw URL repr: %r", _db_url)
    raise
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Enable SQLite WAL mode for better concurrent read performance and crash resilience
if "sqlite" in _db_url:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        # aiosqlite wraps the real connection; unwrap if needed
        raw = getattr(dbapi_conn, "_connection", dbapi_conn)
        try:
            raw.execute("PRAGMA journal_mode=WAL")
            raw.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass  # WAL requires write access to create -wal/-shm files


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    # Ensure all models are registered with Base.metadata before creating tables
    from .models import __all__ as _models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Auto-migrate: add missing columns for existing tables
        await conn.run_sync(_auto_migrate_columns)


def _auto_migrate_columns(conn):
    """Add any missing columns from SQLAlchemy models to existing DB tables.

    This is a simple forward-only migration that only adds new columns.
    It does NOT drop columns or change types.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # Table doesn't exist yet (create_all will handle it)

        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing_cols:
                # Build ALTER TABLE ADD COLUMN
                col_type = col.type.compile(conn.dialect)
                default = ""
                if col.default is not None:
                    val = col.default.arg
                    if callable(val):
                        default = ""  # Skip callable defaults
                    elif isinstance(val, str):
                        default = f" DEFAULT '{val}'"
                    elif isinstance(val, bool):
                        # PostgreSQL needs TRUE/FALSE, SQLite accepts 1/0
                        if "sqlite" in settings.database_url:
                            default = f" DEFAULT {1 if val else 0}"
                        else:
                            default = f" DEFAULT {'TRUE' if val else 'FALSE'}"
                    elif isinstance(val, (int, float)):
                        default = f" DEFAULT {val}"
                    elif isinstance(val, list):
                        import json
                        default = f" DEFAULT '{json.dumps(val)}'"
                    elif isinstance(val, dict):
                        import json
                        default = f" DEFAULT '{json.dumps(val)}'"
                elif col.nullable:
                    default = " DEFAULT NULL"
                try:
                    conn.execute(
                        text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{default}')
                    )
                except Exception as e:
                    _log.debug("Auto-migrate: skipping column %s.%s: %s", table.name, col.name, e)

        # Add missing indexes
        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table.name) if idx["name"]}
        for idx in table.indexes:
            if idx.name and idx.name not in existing_indexes:
                try:
                    idx.create(conn)
                except Exception as e:
                    _log.debug("Auto-migrate: skipping index %s: %s", idx.name, e)
