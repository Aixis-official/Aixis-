"""Google Drive auto-export service for database backups.

Periodically creates a SQLite backup and uploads it to a specified Google Drive folder
using OAuth2 refresh token (personal Google account). This protects against volume loss
on Railway or similar PaaS.

Authentication approach: OAuth2 refresh token
  - Works with free personal Google accounts (no Workspace required)
  - User authorises once via Google consent screen, obtains a refresh token
  - The refresh token is stored and used to get fresh access tokens automatically

Setup:
  1. Create OAuth2 "Desktop app" credentials in Google Cloud Console
  2. Enable Google Drive API
  3. Run the one-time auth flow (provided via /gdrive/auth endpoint)
  4. Store client_id, client_secret, and refresh_token
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_export_thread: threading.Thread | None = None
_export_stop = threading.Event()
_last_export: dict | None = None

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


# ---------------------------------------------------------------------------
# Google Drive API helpers
# ---------------------------------------------------------------------------

def _get_drive_service():
    """Build a Google Drive API client from OAuth2 refresh token credentials."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    from ..config import settings

    creds_json = settings.gdrive_credentials_json.strip()
    if not creds_json:
        raise ValueError("Google Drive認証情報が設定されていません")

    # Support both inline JSON string and file path
    if creds_json.startswith("{"):
        info = json.loads(creds_json)
    else:
        path = Path(creds_json)
        if not path.exists():
            raise FileNotFoundError(f"認証情報ファイルが見つかりません: {creds_json}")
        info = json.loads(path.read_text(encoding="utf-8"))

    # Detect credential type and build accordingly
    cred_type = info.get("type", "")

    if cred_type == "service_account":
        # Service account — use for shared drives or domain-wide delegation
        from google.oauth2.service_account import Credentials as SACredentials
        creds = SACredentials.from_service_account_info(info, scopes=SCOPES)
    elif "refresh_token" in info:
        # OAuth2 refresh token (personal account — recommended)
        creds = Credentials(
            token=None,
            refresh_token=info["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=info.get("client_id", ""),
            client_secret=info.get("client_secret", ""),
            scopes=SCOPES,
        )
    else:
        raise ValueError(
            "認証情報にrefresh_tokenが含まれていません。"
            "設定画面の手順に従ってOAuth2認証を行ってください。"
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
        body=file_metadata,
        media_body=media,
        fields="id,name,size,createdTime,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return result


# ---------------------------------------------------------------------------
# OAuth2 authorization helper (one-time setup)
# ---------------------------------------------------------------------------

def generate_auth_url(client_id: str, client_secret: str, redirect_uri: str = "") -> str:
    """Generate Google OAuth2 authorization URL for one-time consent.

    ``redirect_uri`` must be set to the app's callback URL, e.g.
    ``https://platform.aixis.jp/api/v1/settings/gdrive/callback``.
    """
    if not redirect_uri:
        raise ValueError("redirect_uri is required")
    from urllib.parse import urlencode
    # Encode client_id & client_secret in the state parameter so the
    # callback can exchange the code without session storage.
    import base64 as _b64
    state_payload = json.dumps({"cid": client_id, "cs": client_secret})
    state = _b64.urlsafe_b64encode(state_payload.encode()).decode()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str,
                              redirect_uri: str = "") -> dict:
    """Exchange authorization code for refresh token."""
    if not redirect_uri:
        raise ValueError("redirect_uri is required")
    import httpx

    resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    data = resp.json()
    if "error" in data:
        raise ValueError(f"トークン取得失敗: {data.get('error_description', data['error'])}")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": data["refresh_token"],
    }


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
            supportsAllDrives=True,
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
