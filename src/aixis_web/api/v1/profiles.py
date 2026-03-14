"""Profile and target config API endpoints."""
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...db.models.user import User
from ..deps import require_analyst

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[4]


@router.get("/")
async def list_profiles(
    _user: Annotated[User, Depends(require_analyst)],
    q: str | None = Query(None, description="Search query"),
):
    """List all available audit profiles (from config/profiles/*.yaml)."""
    from aixis_agent.profiles.registry import list_profiles, search_profiles, clear_cache

    clear_cache()
    profiles_dir = BASE_DIR / "config" / "profiles"

    if q:
        results = search_profiles(q, profiles_dir)
        return {
            "items": [
                {
                    "id": p.get("id", ""),
                    "name_jp": p.get("name_jp", p.get("id", "")),
                    "category_jp": p.get("category_jp", ""),
                    "description_jp": p.get("description_jp", ""),
                }
                for p in results
            ],
            "total": len(results),
        }

    items = list_profiles(profiles_dir)
    return {"items": items, "total": len(items)}


@router.get("/targets")
async def list_target_configs(
    _user: Annotated[User, Depends(require_analyst)],
):
    """List available target config files (from config/targets/*.yaml)."""
    targets_dir = BASE_DIR / "config" / "targets"
    items = []
    if targets_dir.exists():
        for f in sorted(targets_dir.glob("*.yaml")):
            if f.stem.startswith("example"):
                continue
            import yaml
            try:
                with open(f, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                items.append({
                    "name": f.stem,
                    "display_name": data.get("name", f.stem),
                    "url": data.get("url", ""),
                    "executor_type": data.get("executor_type", "playwright"),
                })
            except Exception:
                items.append({"name": f.stem, "display_name": f.stem, "url": "", "executor_type": ""})

    return {"items": items, "total": len(items)}
