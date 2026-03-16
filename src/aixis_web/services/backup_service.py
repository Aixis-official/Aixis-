"""Database backup service for SQLite environments."""

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("backups")
MAX_BACKUPS = 30  # Keep last 30 backups


def get_sqlite_path() -> Path | None:
    """Extract SQLite file path from database URL."""
    url = settings.database_url
    if "sqlite" not in url:
        return None
    # sqlite+aiosqlite:///./aixis.db → ./aixis.db
    path_part = url.split("///")[-1]
    return Path(path_part)


def create_backup() -> dict:
    """Create a timestamped backup of the SQLite database.

    Uses SQLite's built-in backup API for safe copying even during writes.
    Returns metadata about the backup.
    """
    db_path = get_sqlite_path()
    if db_path is None:
        return {"error": "Backup is only supported for SQLite databases"}

    if not db_path.exists():
        return {"error": f"Database file not found: {db_path}"}

    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_filename = f"aixis_{timestamp}.db"
    backup_path = BACKUP_DIR / backup_filename

    try:
        # Use SQLite backup API for consistency (safe even during WAL writes)
        source = sqlite3.connect(str(db_path))
        dest = sqlite3.connect(str(backup_path))
        source.backup(dest)
        dest.close()
        source.close()

        size_bytes = backup_path.stat().st_size
        size_mb = round(size_bytes / (1024 * 1024), 2)

        logger.info(f"Backup created: {backup_path} ({size_mb} MB)")

        # Cleanup old backups beyond MAX_BACKUPS
        _cleanup_old_backups()

        return {
            "path": str(backup_path),
            "filename": backup_filename,
            "size_mb": size_mb,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return {"error": str(e)}


def list_backups() -> list[dict]:
    """List existing backups with metadata."""
    if not BACKUP_DIR.exists():
        return []

    backups = []
    for f in sorted(BACKUP_DIR.glob("aixis_*.db"), reverse=True):
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
    backups = sorted(BACKUP_DIR.glob("aixis_*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in backups[MAX_BACKUPS:]:
        old.unlink(missing_ok=True)
        logger.info(f"Removed old backup: {old.name}")
