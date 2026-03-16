"""Settings API — manage .env configuration from the dashboard."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...api.deps import require_admin
from ...config import settings
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


def _write_env_key(key_name: str, value: str) -> None:
    """Write or update a key in the .env file (restricted to allowlist)."""
    if key_name not in _ALLOWED_ENV_KEYS:
        raise ValueError(f"Key '{key_name}' is not in the settings allowlist")
    # Sanitize value: strip newlines to prevent env injection
    value = value.replace("\n", "").replace("\r", "").strip()
    if not _ENV_PATH.exists():
        _ENV_PATH.write_text(
            f"# Aixis Platform Configuration\n{key_name}={value}\n",
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
                new_lines.append(f"{key_name}={value}")
                found = True
                continue
        new_lines.append(line)

    if not found:
        new_lines.append(f"\n{key_name}={value}")

    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@router.get("", response_model=SettingsResponse)
async def get_settings(
    user: Annotated[User, Depends(require_admin)],
):
    """Return current settings (API key masked)."""
    raw_key = _read_env_key("AIXIS_ANTHROPIC_API_KEY")
    return SettingsResponse(
        has_api_key=bool(raw_key),
        anthropic_api_key_masked=_mask_key(raw_key),
        model=settings.ai_agent_model,
        cost_limit_jpy=settings.ai_budget_max_cost_jpy,
        call_limit=settings.ai_budget_max_calls,
    )


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    user: Annotated[User, Depends(require_admin)],
):
    """Update settings (writes to .env file)."""
    if body.anthropic_api_key is not None:
        key = body.anthropic_api_key.strip()
        if not key.startswith("sk-ant-") or len(key) < 20:
            raise HTTPException(400, "有効なAnthropicのAPIキーを入力してください（'sk-ant-' で始まる20文字以上）")
        _write_env_key("AIXIS_ANTHROPIC_API_KEY", key)
        # Also update the runtime setting so the next audit picks it up
        import os
        os.environ["AIXIS_ANTHROPIC_API_KEY"] = key
        return {"status": "ok", "message": "APIキーを保存しました"}

    return {"status": "ok", "message": "変更なし"}


@router.post("/backup")
async def create_backup(
    user: Annotated[User, Depends(require_admin)],
):
    """Create a database backup (SQLite only)."""
    from ...services.backup_service import create_backup as do_backup
    result = do_backup()
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


# ── Google Drive Export ──────────────────────────────────────────────


@router.get("/gdrive/status")
async def gdrive_status(
    user: Annotated[User, Depends(require_admin)],
):
    """Get Google Drive export configuration and status."""
    from ...services.gdrive_export_service import get_export_status
    return get_export_status()


@router.post("/gdrive/export")
async def gdrive_export_now(
    user: Annotated[User, Depends(require_admin)],
):
    """Manually trigger a Google Drive export."""
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
    """Update Google Drive export settings (writes to .env file)."""
    import os
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
        _write_env_key("AIXIS_GDRIVE_FOLDER_ID", val)
        os.environ["AIXIS_GDRIVE_FOLDER_ID"] = val
        settings.gdrive_folder_id = val
        changes.append("folder_id")

    if body.interval_hours is not None:
        if body.interval_hours < 1 or body.interval_hours > 168:
            raise HTTPException(400, "エクスポート間隔は1〜168時間で指定してください")
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

    return {"status": "ok", "message": f"Google Drive設定を更新しました ({', '.join(changes)})"}

