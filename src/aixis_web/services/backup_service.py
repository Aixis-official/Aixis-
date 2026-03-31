"""Database backup service — automatic and on-demand backups.

Supports both SQLite (.backup API) and PostgreSQL (pg_dump).
Backups are stored with timestamps, checksums, and tiered retention.

Tiered retention policy:
  - Hourly backups: keep last 48
  - Daily backups: keep last 30
  - Weekly backups: keep last 12
  - Manual/pre_deploy backups: keep last 20
"""

import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..config import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("/data/backups")
METADATA_FILE = BACKUP_DIR / "backup_manifest.json"

# Tiered retention limits
RETENTION = {
    "hourly": 48,
    "daily": 30,
    "weekly": 12,
    "manual": 20,
    "pre_deploy": 20,
    "admin_manual": 20,
}
DEFAULT_RETENTION = 30

# Background scheduler
_backup_thread: threading.Thread | None = None
_backup_stop = threading.Event()
_last_backup_status: dict | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _sha256(filepath: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    """Load backup manifest (checksums + metadata)."""
    if METADATA_FILE.exists():
        try:
            return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"backups": {}}


def _save_manifest(manifest: dict):
    """Persist backup manifest."""
    _ensure_backup_dir()
    METADATA_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _wal_checkpoint(db_path: Path):
    """Force WAL checkpoint before backup for maximum consistency."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        logger.info("WAL checkpoint completed before backup")
    except Exception as e:
        logger.warning("WAL checkpoint failed (non-critical): %s", e)


# ---------------------------------------------------------------------------
# Core backup functions
# ---------------------------------------------------------------------------

def create_backup(reason: str = "manual") -> dict:
    """Create a timestamped database backup with SHA-256 checksum.

    Returns metadata about the backup (path, size, timestamp, reason, checksum).
    """
    _ensure_backup_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    db_url = settings.database_url

    if "sqlite" in db_url:
        result = _backup_sqlite(timestamp, reason)
    elif "postgresql" in db_url or "postgres" in db_url:
        result = _backup_postgresql(timestamp, reason)
    else:
        return {"error": f"Unsupported database type for backup: {db_url}"}

    # Record in manifest
    if "error" not in result:
        manifest = _load_manifest()
        manifest["backups"][result["filename"]] = {
            "checksum": result.get("checksum", ""),
            "size_mb": result["size_mb"],
            "created_at": result["created_at"],
            "reason": reason,
            "type": result["type"],
            "verified": result.get("verified", False),
        }
        _save_manifest(manifest)

    return result


def _backup_sqlite(timestamp: str, reason: str) -> dict:
    """Backup SQLite database using the built-in backup API (WAL-safe)."""
    db_path = get_sqlite_path()
    if db_path is None or not db_path.exists():
        return {"error": f"Database file not found: {db_path}"}

    # Force WAL checkpoint for consistency
    _wal_checkpoint(db_path)

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

        # Compute checksum
        checksum = _sha256(backup_path)

        # Verify backup integrity
        verified = _verify_sqlite_backup(backup_path)

        logger.info(
            "SQLite backup created: %s (%s MB, sha256: %s, verified: %s, reason: %s)",
            backup_path, size_mb, checksum[:12], verified, reason,
        )
        _cleanup_old_backups(reason)

        return {
            "path": str(backup_path),
            "filename": backup_filename,
            "size_mb": size_mb,
            "checksum": checksum,
            "verified": verified,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "type": "sqlite",
        }
    except Exception as e:
        logger.error("SQLite backup failed: %s", e)
        backup_path.unlink(missing_ok=True)
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

        # Compute checksum
        checksum = _sha256(backup_path)

        logger.info(
            "PostgreSQL backup created: %s (%s MB, sha256: %s, reason: %s)",
            backup_path, size_mb, checksum[:12], reason,
        )
        _cleanup_old_backups(reason)

        return {
            "path": str(backup_path),
            "filename": backup_filename,
            "size_mb": size_mb,
            "checksum": checksum,
            "verified": True,  # pg_dump success implies valid dump
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


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify_sqlite_backup(backup_path: Path) -> bool:
    """Open the backup database and verify it's a valid, readable SQLite file."""
    try:
        conn = sqlite3.connect(str(backup_path))
        cursor = conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        # Count tables to ensure it's not empty
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        conn.close()
        if result[0] != "ok":
            logger.warning("Backup integrity check failed: %s", result[0])
            return False
        if table_count == 0:
            logger.warning("Backup is empty (0 tables)")
            return False
        return True
    except Exception as e:
        logger.warning("Backup verification failed: %s", e)
        return False


def verify_backup(filename: str) -> dict:
    """Verify an existing backup's checksum and integrity."""
    filepath = BACKUP_DIR / filename
    if not filepath.exists():
        return {"error": "Backup file not found", "filename": filename}

    manifest = _load_manifest()
    stored_checksum = manifest.get("backups", {}).get(filename, {}).get("checksum", "")

    current_checksum = _sha256(filepath)
    checksum_match = stored_checksum == current_checksum if stored_checksum else None

    if filename.endswith(".db"):
        integrity = _verify_sqlite_backup(filepath)
    else:
        integrity = filepath.stat().st_size > 0

    return {
        "filename": filename,
        "checksum": current_checksum,
        "checksum_match": checksum_match,
        "integrity_ok": integrity,
        "size_mb": round(filepath.stat().st_size / (1024 * 1024), 2),
    }


# ---------------------------------------------------------------------------
# Listing and cleanup
# ---------------------------------------------------------------------------

def list_backups() -> list[dict]:
    """List existing backups with metadata, newest first."""
    if not BACKUP_DIR.exists():
        return []

    manifest = _load_manifest()

    backups = []
    for f in sorted(BACKUP_DIR.glob("aixis_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = manifest.get("backups", {}).get(f.name, {})
        backups.append({
            "filename": f.name,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            "checksum": meta.get("checksum", ""),
            "verified": meta.get("verified", None),
            "reason": meta.get("reason", _infer_reason(f.name)),
        })
    return backups


def _infer_reason(filename: str) -> str:
    """Infer backup reason from filename for legacy backups without manifest."""
    for reason in ("pre_deploy", "admin_manual", "hourly", "daily", "weekly", "manual"):
        if reason in filename:
            return reason
    return "unknown"


def _cleanup_old_backups(current_reason: str = "manual"):
    """Remove oldest backups using tiered retention policy."""
    if not BACKUP_DIR.exists():
        return

    # Group backups by reason
    groups: dict[str, list[Path]] = {}
    for f in BACKUP_DIR.glob("aixis_*"):
        if f.name == "backup_manifest.json":
            continue
        reason = _infer_reason(f.name)
        groups.setdefault(reason, []).append(f)

    manifest = _load_manifest()
    removed = []

    for reason, files in groups.items():
        limit = RETENTION.get(reason, DEFAULT_RETENTION)
        sorted_files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
        for old in sorted_files[limit:]:
            old.unlink(missing_ok=True)
            manifest["backups"].pop(old.name, None)
            removed.append(old.name)
            logger.info("Removed old backup: %s (reason: %s, limit: %d)", old.name, reason, limit)

    if removed:
        _save_manifest(manifest)


# ---------------------------------------------------------------------------
# Scheduled automatic backups
# ---------------------------------------------------------------------------

def start_backup_scheduler():
    """Start the automatic backup scheduler thread. Runs hourly backups
    and promotes daily/weekly snapshots automatically."""
    global _backup_thread
    _backup_stop.clear()
    _backup_thread = threading.Thread(
        target=_backup_scheduler_loop, daemon=True, name="backup-scheduler"
    )
    _backup_thread.start()
    logger.info("Backup scheduler started (hourly + daily + weekly)")


def stop_backup_scheduler():
    """Stop the backup scheduler thread."""
    _backup_stop.set()
    if _backup_thread:
        _backup_thread.join(timeout=10)
    logger.info("Backup scheduler stopped")


def _backup_scheduler_loop():
    """Main loop: create hourly backups, promote to daily/weekly as needed."""
    HOURLY_INTERVAL = 3600  # 1 hour

    # Wait 5 minutes after startup before first scheduled backup
    # (pre_deploy backup already runs on startup)
    if _backup_stop.wait(300):
        return

    last_daily: datetime | None = None
    last_weekly: datetime | None = None

    while not _backup_stop.is_set():
        now = datetime.now(timezone.utc)

        try:
            # Hourly backup
            result = create_backup(reason="hourly")
            if "error" not in result:
                _update_last_status(result, "hourly")

                # Auto-upload to Google Drive if enabled (every hourly backup)
                _sync_to_gdrive_if_enabled(result)

                # Daily promotion: once per day (at the first hourly backup after midnight UTC)
                if last_daily is None or (now - last_daily) >= timedelta(hours=23):
                    _promote_backup(result, "daily")
                    last_daily = now

                    # Weekly promotion: once per week
                    if last_weekly is None or (now - last_weekly) >= timedelta(days=6, hours=23):
                        _promote_backup(result, "weekly")
                        last_weekly = now
            else:
                logger.error("Scheduled hourly backup failed: %s", result.get("error"))
                _update_last_status(result, "hourly")

        except Exception:
            logger.exception("Backup scheduler error")

        # Wait for next interval
        if _backup_stop.wait(HOURLY_INTERVAL):
            break


def _promote_backup(source_result: dict, target_reason: str):
    """Copy a backup file as a daily/weekly snapshot."""
    source_path = Path(source_result["path"])
    if not source_path.exists():
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target_filename = f"aixis_{timestamp}_{target_reason}{source_path.suffix}"
    target_path = BACKUP_DIR / target_filename

    try:
        shutil.copy2(str(source_path), str(target_path))
        checksum = _sha256(target_path)

        manifest = _load_manifest()
        manifest["backups"][target_filename] = {
            "checksum": checksum,
            "size_mb": source_result["size_mb"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": target_reason,
            "type": source_result["type"],
            "verified": source_result.get("verified", False),
            "promoted_from": source_result["filename"],
        }
        _save_manifest(manifest)

        logger.info("Promoted backup to %s: %s", target_reason, target_filename)
        _cleanup_old_backups(target_reason)
    except Exception as e:
        logger.error("Failed to promote backup to %s: %s", target_reason, e)


def _sync_to_gdrive_if_enabled(backup_result: dict):
    """Upload the latest backup to Google Drive if the integration is enabled."""
    try:
        if not settings.gdrive_enabled:
            return
        if not settings.gdrive_credentials_json.strip() or not settings.gdrive_folder_id:
            return

        from .gdrive_export_service import _get_drive_service, _upload_file

        backup_path = Path(backup_result["path"])
        if not backup_path.exists():
            return

        service = _get_drive_service()
        gdrive_file = _upload_file(service, backup_path, settings.gdrive_folder_id)
        logger.info(
            "Backup synced to Google Drive: %s (gdrive_id: %s)",
            backup_result["filename"], gdrive_file.get("id"),
        )
    except Exception as e:
        logger.warning("Google Drive sync failed (non-critical): %s", e)


def _update_last_status(result: dict, reason: str):
    """Update the global last backup status for health monitoring."""
    global _last_backup_status
    _last_backup_status = {
        "reason": reason,
        "success": "error" not in result,
        "filename": result.get("filename", ""),
        "error": result.get("error", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checksum": result.get("checksum", ""),
        "verified": result.get("verified", False),
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def get_backup_health() -> dict:
    """Return backup system health status for monitoring dashboards."""
    backups = list_backups()
    now = datetime.now(timezone.utc)

    # Find latest backup of each type
    latest_by_reason: dict[str, dict] = {}
    for b in backups:
        reason = b.get("reason", "unknown")
        if reason not in latest_by_reason:
            latest_by_reason[reason] = b

    # Check if hourly backup is stale (>2 hours old)
    hourly_ok = False
    hourly_latest = latest_by_reason.get("hourly")
    if hourly_latest:
        try:
            ts = datetime.fromisoformat(hourly_latest["created_at"])
            hourly_ok = (now - ts) < timedelta(hours=2)
        except Exception:
            pass

    # Check if daily backup exists within last 25 hours
    daily_ok = False
    daily_latest = latest_by_reason.get("daily")
    if daily_latest:
        try:
            ts = datetime.fromisoformat(daily_latest["created_at"])
            daily_ok = (now - ts) < timedelta(hours=25)
        except Exception:
            pass

    # Overall health
    total_size_mb = sum(b["size_mb"] for b in backups)
    all_verified = all(b.get("verified", True) for b in backups[:5])  # Check last 5

    # Determine overall status
    if not backups:
        status = "critical"
        message = "バックアップが一つもありません"
    elif not hourly_ok and _backup_thread is not None:
        status = "warning"
        message = "毎時バックアップが2時間以上前です"
    elif not all_verified:
        status = "warning"
        message = "直近のバックアップに整合性エラーがあります"
    else:
        status = "healthy"
        message = "バックアップシステムは正常です"

    return {
        "status": status,
        "message": message,
        "total_backups": len(backups),
        "total_size_mb": round(total_size_mb, 2),
        "scheduler_running": _backup_thread is not None and _backup_thread.is_alive(),
        "last_backup": _last_backup_status,
        "latest_by_type": {
            k: {"filename": v["filename"], "created_at": v["created_at"], "verified": v.get("verified")}
            for k, v in latest_by_reason.items()
        },
        "retention_policy": RETENTION,
    }
