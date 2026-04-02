"""Settings API — manage configuration from the dashboard.

Settings are persisted to PostgreSQL (app_settings table) so they survive
Railway container restarts and re-deploys. Also written to .env for local dev.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from ...api.deps import require_admin
from ...config import settings
from ...db.base import get_db, AsyncSession
from ...db.models.app_setting import AppSetting
from ...db.models.user import User

router = APIRouter()

# .env file is at the project root (two levels up from this file, or CWD)
_ENV_PATH = Path.cwd() / ".env"


class SettingsResponse(BaseModel):
    has_api_key: bool
    anthropic_api_key_masked: str
    model: str
    cost_limit_jpy: int
    call_limit: int


class SettingsUpdate(BaseModel):
    anthropic_api_key: str | None = None
    cost_limit_jpy: int | None = None
    call_limit: int | None = None


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 10 and last 4 chars."""
    if not key or len(key) < 20:
        return ""
    return key[:10] + "*" * (len(key) - 14) + key[-4:]


def _read_env_key(key_name: str) -> str:
    """Read a value from the .env file."""
    if not _ENV_PATH.exists():
        return ""
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key_name:
            return v.strip()
    return ""


# Allowlist of keys that can be written via the settings API
_ALLOWED_ENV_KEYS = frozenset({
    "AIXIS_ANTHROPIC_API_KEY",
    "AIXIS_GDRIVE_ENABLED",
    "AIXIS_GDRIVE_CREDENTIALS_JSON",
    "AIXIS_GDRIVE_FOLDER_ID",
    "AIXIS_GDRIVE_EXPORT_INTERVAL_HOURS",
})


def _quote_env_value(value: str) -> str:
    """Quote a .env value to prevent injection of shell metacharacters."""
    # Strip control characters
    value = value.replace("\n", "").replace("\r", "").strip()
    # If value contains special chars, wrap in single quotes (escaping existing ones)
    if any(c in value for c in ('"', "'", "$", "`", "\\", " ", "#", ";")):
        escaped = value.replace("'", "'\"'\"'")
        return f"'{escaped}'"
    return value


def _write_env_key(key_name: str, value: str) -> None:
    """Write or update a key in the .env file (restricted to allowlist)."""
    if key_name not in _ALLOWED_ENV_KEYS:
        raise ValueError(f"Key '{key_name}' is not in the settings allowlist")
    safe_value = _quote_env_value(value)
    if not _ENV_PATH.exists():
        _ENV_PATH.write_text(
            f"# Aixis Platform Configuration\n{key_name}={safe_value}\n",
            encoding="utf-8",
        )
        return

    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == key_name:
                new_lines.append(f"{key_name}={safe_value}")
                found = True
                continue
        new_lines.append(line)

    if not found:
        new_lines.append(f"\n{key_name}={safe_value}")

    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


async def _db_get(db: AsyncSession, key: str) -> str:
    """Read a setting from PostgreSQL."""
    try:
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        row = result.scalar_one_or_none()
        return row.value if row else ""
    except Exception:
        return ""


async def _db_set(db: AsyncSession, key: str, value: str) -> None:
    """Write a setting to PostgreSQL (upsert)."""
    try:
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        row = result.scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
        await db.commit()
    except Exception:
        await db.rollback()
        raise


@router.get("", response_model=SettingsResponse)
async def get_settings(
    user: Annotated[User, Depends(require_admin)],
    db: AsyncSession = Depends(get_db),
):
    """Return current settings (reads from DB first, falls back to .env)."""
    raw_key = await _db_get(db, "AIXIS_ANTHROPIC_API_KEY")
    if not raw_key:
        raw_key = _read_env_key("AIXIS_ANTHROPIC_API_KEY")
    # Read cost/call limits from DB (overrides defaults)
    db_cost = await _db_get(db, "AIXIS_AI_BUDGET_MAX_COST_JPY")
    db_calls = await _db_get(db, "AIXIS_AI_BUDGET_MAX_CALLS")
    cost_limit = int(db_cost) if db_cost else settings.ai_budget_max_cost_jpy
    call_limit = int(db_calls) if db_calls else settings.ai_budget_max_calls

    return SettingsResponse(
        has_api_key=bool(raw_key),
        anthropic_api_key_masked=_mask_key(raw_key),
        model=settings.ai_agent_model,
        cost_limit_jpy=cost_limit,
        call_limit=call_limit,
    )


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    user: Annotated[User, Depends(require_admin)],
    db: AsyncSession = Depends(get_db),
):
    """Update settings (writes to PostgreSQL + .env + runtime)."""
    if body.anthropic_api_key is not None:
        key = body.anthropic_api_key.strip()
        if not key.startswith("sk-ant-") or len(key) < 20:
            raise HTTPException(400, "無効なAPIキーです")
        # Persist to PostgreSQL (survives redeploy)
        await _db_set(db, "AIXIS_ANTHROPIC_API_KEY", key)
        # Also write to .env (local dev) and update runtime
        try:
            _write_env_key("AIXIS_ANTHROPIC_API_KEY", key)
        except Exception:
            logger.debug(".env write skipped (read-only filesystem)")  # Expected on Railway
        os.environ["AIXIS_ANTHROPIC_API_KEY"] = key
        settings.anthropic_api_key = key
        return {"status": "ok", "message": "APIキーを保存しました"}

    messages = []
    if body.cost_limit_jpy is not None:
        if body.cost_limit_jpy < 1 or body.cost_limit_jpy > 500:
            raise HTTPException(400, "コスト上限は1〜500円の範囲で設定してください")
        await _db_set(db, "AIXIS_AI_BUDGET_MAX_COST_JPY", str(body.cost_limit_jpy))
        settings.ai_budget_max_cost_jpy = body.cost_limit_jpy
        messages.append(f"コスト上限を{body.cost_limit_jpy}円に変更")

    if body.call_limit is not None:
        if body.call_limit < 10 or body.call_limit > 1000:
            raise HTTPException(400, "API呼び出し上限は10〜1000回の範囲で設定してください")
        await _db_set(db, "AIXIS_AI_BUDGET_MAX_CALLS", str(body.call_limit))
        settings.ai_budget_max_calls = body.call_limit
        messages.append(f"API呼び出し上限を{body.call_limit}回に変更")

    if messages:
        return {"status": "ok", "message": "、".join(messages)}

    return {"status": "ok", "message": "変更なし"}


@router.post("/backup")
async def create_backup(
    user: Annotated[User, Depends(require_admin)],
):
    """Create a database backup (SQLite + PostgreSQL)."""
    from ...services.backup_service import create_backup as do_backup
    result = do_backup(reason="admin_manual")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/backups")
async def list_backups(
    user: Annotated[User, Depends(require_admin)],
):
    """List existing database backups."""
    from ...services.backup_service import list_backups as do_list
    return {"backups": do_list()}


@router.get("/backup/health")
async def backup_health(
    user: Annotated[User, Depends(require_admin)],
):
    """Return backup system health status for monitoring."""
    from ...services.backup_service import get_backup_health
    return get_backup_health()


@router.post("/backup/verify/{filename}")
async def verify_backup_endpoint(
    filename: str,
    user: Annotated[User, Depends(require_admin)],
):
    """Verify a specific backup's checksum and integrity."""
    from ...services.backup_service import verify_backup
    result = verify_backup(filename)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.post("/backup/restore")
async def restore_backup(
    user: Annotated[User, Depends(require_admin)],
    file: UploadFile = File(...),
):
    """Restore database from an uploaded .pgdump file.

    This endpoint:
      1. Validates the uploaded file is a valid pg_dump custom-format file
      2. Creates a safety backup before restoring
      3. Runs pg_restore --clean --if-exists --single-transaction
      4. Verifies the database is accessible after restore
    """
    import tempfile
    from ...services.backup_service import restore_from_file

    # Validate filename
    if not file.filename:
        raise HTTPException(400, "ファイル名がありません")

    if not file.filename.endswith((".pgdump", ".dump", ".backup")):
        raise HTTPException(
            400,
            "対応していないファイル形式です。.pgdump, .dump, .backup ファイルをアップロードしてください。"
        )

    # Read uploaded file to a temporary location
    content = await file.read()
    if len(content) < 100:
        raise HTTPException(400, "ファイルが小さすぎます（破損の可能性があります）")

    # Size limit: 500 MB
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(400, "ファイルサイズが大きすぎます（上限: 500 MB）")

    # Save to /data/backups/restore_upload_<filename> for pg_restore
    import os
    import re as _re
    from ...services.backup_service import BACKUP_DIR
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitize filename: strip directory components and dangerous characters
    safe_name = os.path.basename(file.filename or "backup")
    safe_name = _re.sub(r'[^a-zA-Z0-9._-]', '_', safe_name)[:100]
    restore_path = BACKUP_DIR / f"restore_upload_{safe_name}"

    try:
        restore_path.write_bytes(content)

        # Run restore (synchronous — runs pg_restore subprocess)
        result = restore_from_file(restore_path)

        if "error" in result:
            raise HTTPException(400, result)

        return result
    finally:
        # Clean up uploaded restore file
        restore_path.unlink(missing_ok=True)


@router.post("/backup/restore-from-gdrive/{filename}")
async def restore_from_gdrive(
    filename: str,
    user: Annotated[User, Depends(require_admin)],
):
    """Download a backup from Google Drive and restore it.

    This is a convenience endpoint that downloads the file from GDrive
    and then runs the same restore process.
    """
    from ...services.backup_service import BACKUP_DIR, restore_from_file

    try:
        from ...services.gdrive_export_service import _get_drive_service
        service = _get_drive_service()
    except Exception as e:
        raise HTTPException(400, f"Google Drive接続に失敗しました: {str(e)[:200]}")

    # Find the file in GDrive
    try:
        results = service.files().list(
            q=f"name='{filename}' and '{settings.gdrive_folder_id}' in parents and trashed=false",
            pageSize=1,
            fields="files(id, name, size)",
        ).execute()
        files = results.get("files", [])
    except Exception as e:
        raise HTTPException(400, f"Google Driveファイル検索に失敗しました: {str(e)[:200]}")

    if not files:
        raise HTTPException(404, f"Google Driveにファイル '{filename}' が見つかりません")

    gdrive_file = files[0]
    file_id = gdrive_file["id"]

    # Download the file
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    restore_path = BACKUP_DIR / f"restore_gdrive_{filename}"

    try:
        import io
        from googleapiclient.http import MediaIoBaseDownload

        request_dl = service.files().get_media(fileId=file_id)
        with open(restore_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request_dl)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        # Run restore
        result = restore_from_file(restore_path)
        if "error" in result:
            raise HTTPException(400, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"復元に失敗しました: {str(e)[:200]}")
    finally:
        restore_path.unlink(missing_ok=True)


# ── Google Drive Export ──────────────────────────────────────────────


@router.get("/gdrive/status")
async def gdrive_status(
    user: Annotated[User, Depends(require_admin)],
):
    """Get Google Drive export configuration and status."""
    from ...services.gdrive_export_service import get_export_status
    return get_export_status()


@router.post("/gdrive/test-connection")
async def gdrive_test_connection(user: Annotated[User, Depends(require_admin)]):
    """Test Google Drive connection by listing files in the configured folder."""
    try:
        from ...services.gdrive_export_service import list_gdrive_files
        files = list_gdrive_files(max_results=1)
        return {"status": "ok", "message": "Google Driveに正常に接続できました"}
    except Exception as e:
        raise HTTPException(400, f"接続テストに失敗しました: {str(e)[:200]}")


_last_manual_export_time = 0


@router.post("/gdrive/export")
async def gdrive_export_now(
    user: Annotated[User, Depends(require_admin)],
):
    """Manually trigger a Google Drive export."""
    import time
    global _last_manual_export_time
    if time.time() - _last_manual_export_time < 300:  # 5 minutes
        raise HTTPException(429, "手動エクスポートは5分に1回までです")
    _last_manual_export_time = time.time()
    from ...services.gdrive_export_service import export_to_gdrive
    result = export_to_gdrive()
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/gdrive/files")
async def gdrive_list_files(
    user: Annotated[User, Depends(require_admin)],
):
    """List backup files stored in Google Drive."""
    from ...services.gdrive_export_service import list_gdrive_exports
    return {"files": list_gdrive_exports()}


class GDriveAuthRequest(BaseModel):
    client_id: str
    client_secret: str


class GDriveTokenRequest(BaseModel):
    client_id: str
    client_secret: str
    code: str


def _gdrive_callback_uri(request: Request) -> str:
    """Build the OAuth2 callback URI using canonical site origin.

    Never trusts Host/X-Forwarded-* headers to prevent token redirection attacks.
    """
    from ...config import settings
    return f"{settings.site_origin}/api/v1/settings/gdrive/callback"


@router.post("/gdrive/auth-url")
async def gdrive_get_auth_url(
    body: GDriveAuthRequest,
    request: Request,
    user: Annotated[User, Depends(require_admin)],
):
    """Generate Google OAuth2 authorization URL."""
    from ...services.gdrive_export_service import generate_auth_url
    redirect_uri = _gdrive_callback_uri(request)
    url = generate_auth_url(
        body.client_id.strip(), body.client_secret.strip(),
        redirect_uri=redirect_uri,
    )
    return {"auth_url": url, "redirect_uri": redirect_uri}


@router.get("/gdrive/callback")
async def gdrive_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    """OAuth2 callback — Google redirects here after user consent."""
    import base64
    import os
    from urllib.parse import urlencode
    from ...services.gdrive_export_service import exchange_code_for_tokens

    if error:
        return RedirectResponse(f"/dashboard/settings?{urlencode({'gdrive_error': error})}")

    if not code or not state:
        return RedirectResponse("/dashboard/settings?gdrive_error=missing_code")

    # Decode client credentials from state parameter
    try:
        state_json = base64.urlsafe_b64decode(state.encode()).decode()
        state_data = json.loads(state_json)
        client_id = state_data["cid"]
        client_secret = state_data["cs"]
    except Exception:
        return RedirectResponse("/dashboard/settings?gdrive_error=invalid_state")

    redirect_uri = _gdrive_callback_uri(request)

    try:
        tokens = exchange_code_for_tokens(
            client_id, client_secret, code,
            redirect_uri=redirect_uri,
        )
    except ValueError as e:
        return RedirectResponse(f"/dashboard/settings?{urlencode({'gdrive_error': str(e)})}")

    # Save credentials to .env + DB
    creds_json = json.dumps(tokens, ensure_ascii=False)
    _write_env_key("AIXIS_GDRIVE_CREDENTIALS_JSON", creds_json)
    os.environ["AIXIS_GDRIVE_CREDENTIALS_JSON"] = creds_json
    settings.gdrive_credentials_json = creds_json

    # Persist to DB so settings survive container restarts
    from ...db.base import async_session as _sf
    try:
        async with _sf() as db:
            await _db_set(db, "AIXIS_GDRIVE_CREDENTIALS_JSON", creds_json)
    except Exception:
        pass  # DB save is best-effort during redirect flow

    return RedirectResponse("/dashboard/settings?gdrive=ok")


@router.post("/gdrive/exchange-token")
async def gdrive_exchange_token(
    body: GDriveTokenRequest,
    request: Request,
    user: Annotated[User, Depends(require_admin)],
):
    """Exchange authorization code for refresh token and save credentials."""
    import os
    from ...services.gdrive_export_service import exchange_code_for_tokens
    redirect_uri = _gdrive_callback_uri(request)
    try:
        tokens = exchange_code_for_tokens(
            body.client_id.strip(), body.client_secret.strip(), body.code.strip(),
            redirect_uri=redirect_uri,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Save as JSON credentials to .env + DB
    creds_json = json.dumps(tokens, ensure_ascii=False)
    _write_env_key("AIXIS_GDRIVE_CREDENTIALS_JSON", creds_json)
    os.environ["AIXIS_GDRIVE_CREDENTIALS_JSON"] = creds_json
    settings.gdrive_credentials_json = creds_json

    # Persist to DB so settings survive container restarts
    from ...db.base import async_session as _sf
    try:
        async with _sf() as db:
            await _db_set(db, "AIXIS_GDRIVE_CREDENTIALS_JSON", creds_json)
    except Exception:
        pass

    return {"status": "ok", "message": "Google Drive認証が完了しました"}


class GDriveSettingsUpdate(BaseModel):
    credentials_json: str | None = None
    folder_id: str | None = None
    interval_hours: int | None = None
    enabled: bool | None = None


@router.put("/gdrive")
async def update_gdrive_settings(
    body: GDriveSettingsUpdate,
    user: Annotated[User, Depends(require_admin)],
):
    """Update Google Drive export settings (writes to .env + PostgreSQL)."""
    import os
    from ...db.base import async_session as _sf
    changes = []

    if body.credentials_json is not None:
        val = body.credentials_json.strip()
        if val and not (val.startswith("{") or Path(val).exists()):
            raise HTTPException(400, "認証情報はJSON文字列またはファイルパスで指定してください")
        _write_env_key("AIXIS_GDRIVE_CREDENTIALS_JSON", val)
        os.environ["AIXIS_GDRIVE_CREDENTIALS_JSON"] = val
        settings.gdrive_credentials_json = val
        changes.append("credentials")

    if body.folder_id is not None:
        val = body.folder_id.strip()
        if val:
            # Validate folder is accessible (try listing contents)
            try:
                from ...services.gdrive_export_service import _get_drive_service
                service = _get_drive_service()
                # Use files().list with folder query — works with broader scopes
                # and avoids 404 on folders not created by the app
                service.files().list(
                    q=f"'{val}' in parents",
                    pageSize=1,
                    fields="files(id)",
                    supportsAllDrives=True,
                ).execute()
            except Exception as e:
                err_msg = str(e)
                if "404" in err_msg:
                    raise HTTPException(
                        400,
                        f"フォルダID '{val}' にアクセスできません。"
                        f"フォルダが存在し、認証アカウントにアクセス権があることを確認してください。"
                    )
                raise HTTPException(400, f"Google Driveフォルダの検証に失敗: {err_msg[:200]}")
        _write_env_key("AIXIS_GDRIVE_FOLDER_ID", val)
        os.environ["AIXIS_GDRIVE_FOLDER_ID"] = val
        settings.gdrive_folder_id = val
        changes.append("folder_id")

    if body.interval_hours is not None:
        if body.interval_hours < 6 or body.interval_hours > 168:
            raise HTTPException(400, "エクスポート間隔は6〜168時間で指定してください")
        val = str(body.interval_hours)
        _write_env_key("AIXIS_GDRIVE_EXPORT_INTERVAL_HOURS", val)
        os.environ["AIXIS_GDRIVE_EXPORT_INTERVAL_HOURS"] = val
        settings.gdrive_export_interval_hours = body.interval_hours
        changes.append("interval")

    if body.enabled is not None:
        val = "true" if body.enabled else "false"
        _write_env_key("AIXIS_GDRIVE_ENABLED", val)
        os.environ["AIXIS_GDRIVE_ENABLED"] = val
        settings.gdrive_enabled = body.enabled
        changes.append("enabled")

        # Start/stop export thread based on new setting
        from ...services.gdrive_export_service import start_gdrive_export, stop_gdrive_export
        if body.enabled:
            start_gdrive_export()
        else:
            stop_gdrive_export()

    if not changes:
        return {"status": "ok", "message": "変更なし"}

    # Persist all changed GDrive settings to PostgreSQL (survive redeploys)
    try:
        async with _sf() as db:
            if "credentials" in changes:
                await _db_set(db, "AIXIS_GDRIVE_CREDENTIALS_JSON", settings.gdrive_credentials_json)
            if "folder_id" in changes:
                await _db_set(db, "AIXIS_GDRIVE_FOLDER_ID", settings.gdrive_folder_id or "")
            if "interval" in changes:
                await _db_set(db, "AIXIS_GDRIVE_EXPORT_INTERVAL_HOURS", str(settings.gdrive_export_interval_hours))
            if "enabled" in changes:
                await _db_set(db, "AIXIS_GDRIVE_ENABLED", "true" if settings.gdrive_enabled else "false")
    except Exception:
        pass  # DB save is best-effort; .env + os.environ already updated

    return {"status": "ok", "message": f"Google Drive設定を更新しました ({', '.join(changes)})"}

