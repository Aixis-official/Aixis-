"""Database engine and base configuration."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..config import settings

# Configure connection pool — SQLite uses NullPool by default,
# PostgreSQL/MySQL benefit from explicit pool limits.
_engine_kwargs: dict = {"echo": False}
if "sqlite" not in settings.database_url:
    _engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 40,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    })

engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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
                        default = f" DEFAULT {1 if val else 0}"
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
                conn.execute(
                    text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{default}')
                )
