"""Database backup service — automatic and on-demand backups.

Supports both SQLite (.backup API) and PostgreSQL (pg_dump).
Backups are stored with timestamps and rotated to prevent disk bloat.
"""

import logging
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..config import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("backups")
MAX_BACKUPS = 30  # Keep last 30 backups


def _ensure_backup_dir() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def get_sqlite_path() -> Path | None:
    """Extract SQLite file path from database URL."""
    url = settings.database_url
    if "sqlite" not in url:
        return None
    path_part = url.split("///")[-1]
    return Path(path_part)


def create_backup(reason: str = "manual") -> dict:
    """Create a timestamped database backup.

    Returns metadata about the backup (path, size, timestamp, reason).
    """
    _ensure_backup_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    db_url = settings.database_url

    if "sqlite" in db_url:
        return _backup_sqlite(timestamp, reason)
    elif "postgresql" in db_url or "postgres" in db_url:
        return _backup_postgresql(timestamp, reason)
    else:
        return {"error": f"Unsupported database type for backup: {db_url}"}


def _backup_sqlite(timestamp: str, reason: str) -> dict:
    """Backup SQLite database using the built-in backup API (WAL-safe)."""
    db_path = get_sqlite_path()
    if db_path is None or not db_path.exists():
        return {"error": f"Database file not found: {db_path}"}

    backup_filename = f"aixis_{timestamp}_{reason}.db"
    backup_path = BACKUP_DIR / backup_filename

    try:
        source = sqlite3.connect(str(db_path))
        dest = sqlite3.connect(str(backup_path))
        source.backup(dest)
        dest.close()
        source.close()

        size_bytes = backup_path.stat().st_size
        size_mb = round(size_bytes / (1024 * 1024), 2)

        logger.info("SQLite backup created: %s (%s MB, reason: %s)", backup_path, size_mb, reason)
        _cleanup_old_backups()

        return {
            "path": str(backup_path),
            "filename": backup_filename,
            "size_mb": size_mb,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "type": "sqlite",
        }
    except Exception as e:
        logger.error("SQLite backup failed: %s", e)
        return {"error": str(e)}


def _backup_postgresql(timestamp: str, reason: str) -> dict:
    """Backup PostgreSQL database using pg_dump."""
    db_url = settings.database_url
    sync_url = db_url.replace("postgresql+asyncpg", "postgresql").replace(
        "postgres+asyncpg", "postgresql"
    )
    parsed = urlparse(sync_url)

    backup_filename = f"aixis_{timestamp}_{reason}.pgdump"
    backup_path = BACKUP_DIR / backup_filename

    env = {
        "PGPASSWORD": parsed.password or "",
        "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
    }

    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", parsed.path.lstrip("/"),
        "--no-owner",
        "--no-acl",
        "-Fc",  # Custom format (compressed)
    ]

    try:
        with open(str(backup_path), "wb") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env, timeout=300)

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            logger.warning("pg_dump failed (rc=%d): %s", result.returncode, stderr)
            backup_path.unlink(missing_ok=True)
            return {"error": f"pg_dump failed: {stderr}", "type": "postgresql"}

        size_bytes = backup_path.stat().st_size
        size_mb = round(size_bytes / (1024 * 1024), 2)

        logger.info("PostgreSQL backup created: %s (%s MB, reason: %s)", backup_path, size_mb, reason)
        _cleanup_old_backups()

        return {
            "path": str(backup_path),
            "filename": backup_filename,
            "size_mb": size_mb,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "type": "postgresql",
        }
    except FileNotFoundError:
        logger.warning("pg_dump not found — PostgreSQL backup skipped")
        backup_path.unlink(missing_ok=True)
        return {"error": "pg_dump not installed", "type": "postgresql"}
    except Exception as e:
        logger.error("PostgreSQL backup failed: %s", e)
        backup_path.unlink(missing_ok=True)
        return {"error": str(e), "type": "postgresql"}


def list_backups() -> list[dict]:
    """List existing backups with metadata, newest first."""
    if not BACKUP_DIR.exists():
        return []

    backups = []
    for f in sorted(BACKUP_DIR.glob("aixis_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return backups


def _cleanup_old_backups():
    """Remove oldest backups beyond MAX_BACKUPS."""
    if not BACKUP_DIR.exists():
        return
    backups = sorted(BACKUP_DIR.glob("aixis_*"), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in backups[MAX_BACKUPS:]:
        old.unlink(missing_ok=True)
        logger.info("Removed old backup: %s", old.name)
