"""Google Drive auto-export service for database backups.

Periodically creates a SQLite backup and uploads it to a specified Google Drive folder
using a service account. This protects against volume loss on Railway or similar PaaS.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

_export_thread: threading.Thread | None = None
_export_stop = threading.Event()
_last_export: dict | None = None


# ---------------------------------------------------------------------------
# Google Drive API helpers
# ---------------------------------------------------------------------------

def _get_drive_service():
    """Build a Google Drive API client from service account credentials."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    from ..config import settings

    creds_json = settings.gdrive_credentials_json.strip()
    if not creds_json:
        raise ValueError("GDRIVE_CREDENTIALS_JSON is not configured")

    # Support both inline JSON string and file path
    if creds_json.startswith("{"):
        info = json.loads(creds_json)
    else:
        path = Path(creds_json)
        if not path.exists():
            raise FileNotFoundError(f"Credentials file not found: {creds_json}")
        info = json.loads(path.read_text(encoding="utf-8"))

    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _upload_file(service, file_path: Path, folder_id: str) -> dict:
    """Upload a file to Google Drive, returning file metadata."""
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(file_path), mimetype="application/x-sqlite3", resumable=True)
    file_metadata = {
        "name": file_path.name,
        "parents": [folder_id],
    }
    result = service.files().create(
        body=file_metadata, media_body=media, fields="id,name,size,createdTime,webViewLink"
    ).execute()
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_to_gdrive() -> dict:
    """Create a backup and upload it to Google Drive. Returns export metadata."""
    global _last_export
    from ..config import settings
    from .backup_service import create_backup

    if not settings.gdrive_folder_id:
        return {"error": "Google DriveのフォルダIDが設定されていません"}

    # Step 1: Create local backup
    backup_result = create_backup()
    if "error" in backup_result:
        return {"error": f"バックアップ作成失敗: {backup_result['error']}"}

    backup_path = Path(backup_result["path"])

    # Step 2: Upload to Google Drive
    try:
        service = _get_drive_service()
        gdrive_file = _upload_file(service, backup_path, settings.gdrive_folder_id)

        export_info = {
            "backup_filename": backup_result["filename"],
            "backup_size_mb": backup_result["size_mb"],
            "gdrive_file_id": gdrive_file.get("id"),
            "gdrive_file_name": gdrive_file.get("name"),
            "gdrive_link": gdrive_file.get("webViewLink", ""),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        _last_export = export_info
        logger.info("Google Drive export succeeded: %s (%s MB)", gdrive_file.get("name"), backup_result["size_mb"])
        return export_info

    except Exception as e:
        logger.exception("Google Drive export failed")
        return {"error": f"Google Driveアップロード失敗: {e}"}


def get_export_status() -> dict:
    """Return current export configuration status and last export info."""
    from ..config import settings

    status = {
        "enabled": settings.gdrive_enabled,
        "has_credentials": bool(settings.gdrive_credentials_json.strip()),
        "folder_id": settings.gdrive_folder_id or "",
        "interval_hours": settings.gdrive_export_interval_hours,
        "last_export": _last_export,
        "thread_running": _export_thread is not None and _export_thread.is_alive(),
    }
    return status


def list_gdrive_exports(max_results: int = 20) -> list[dict]:
    """List backup files in the Google Drive folder."""
    from ..config import settings

    if not settings.gdrive_folder_id:
        return []

    try:
        service = _get_drive_service()
        query = f"'{settings.gdrive_folder_id}' in parents and trashed = false"
        results = service.files().list(
            q=query,
            pageSize=max_results,
            fields="files(id,name,size,createdTime,webViewLink)",
            orderBy="createdTime desc",
        ).execute()

        files = []
        for f in results.get("files", []):
            size_bytes = int(f.get("size", 0))
            files.append({
                "id": f["id"],
                "name": f["name"],
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "created_at": f.get("createdTime", ""),
                "link": f.get("webViewLink", ""),
            })
        return files

    except Exception as e:
        logger.exception("Failed to list Google Drive exports")
        return []


# ---------------------------------------------------------------------------
# Background export thread
# ---------------------------------------------------------------------------

def start_gdrive_export():
    """Start the periodic Google Drive export thread. Called from app lifespan."""
    from ..config import settings

    if not settings.gdrive_enabled:
        logger.info("Google Drive auto-export is disabled")
        return

    if not settings.gdrive_credentials_json.strip() or not settings.gdrive_folder_id:
        logger.warning("Google Drive export enabled but credentials/folder not configured — skipping")
        return

    global _export_thread
    _export_stop.clear()
    _export_thread = threading.Thread(
        target=_export_loop, daemon=True, name="gdrive-export"
    )
    _export_thread.start()
    logger.info(
        "Google Drive auto-export started (interval=%dh)", settings.gdrive_export_interval_hours
    )


def stop_gdrive_export():
    """Stop the periodic export thread."""
    _export_stop.set()
    if _export_thread:
        _export_thread.join(timeout=10)
    logger.info("Google Drive auto-export stopped")


def _export_loop():
    """Main loop: export backup to Google Drive at configured interval."""
    from ..config import settings

    interval_seconds = settings.gdrive_export_interval_hours * 3600

    # Run first export immediately on startup
    try:
        export_to_gdrive()
    except Exception:
        logger.exception("Initial Google Drive export failed")

    while not _export_stop.wait(interval_seconds):
        try:
            export_to_gdrive()
        except Exception:
            logger.exception("Periodic Google Drive export failed")
