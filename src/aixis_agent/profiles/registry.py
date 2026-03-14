"""Tool type profile registry — fully YAML-driven.

プロファイルは config/profiles/*.yaml で定義する。
Pythonコードを一切触らずに、新しいAIツール種別を追加できる。

プロファイルの構成要素:
  - 特性タグ (traits): そのツールが持つ性質（例: "要テキスト入力", "構造化出力"）
  - 主要/補助/除外カテゴリ: どのテストを重点的に行うか
  - スコアリング重み: 3軸のどこに重点を置くか
  - 評価の着眼点: レポートで何を重点的に評価するか
"""

from pathlib import Path
from typing import Any

import yaml

from ..core.enums import TestCategory


DEFAULT_PROFILES_DIR = Path("config/profiles")


def load_profile(path: Path) -> dict[str, Any]:
    """単一プロファイルYAMLを読み込む。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # カテゴリ文字列を TestCategory enum に変換
    for key in ("primary_categories", "secondary_categories", "excluded_categories"):
        if key in data:
            converted = []
            for c in data[key]:
                try:
                    converted.append(TestCategory(c))
                except ValueError:
                    pass  # 不明なカテゴリは無視
            data[key] = converted

    return data


def load_all_profiles(profiles_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """ディレクトリ内の全プロファイルを読み込む。"""
    d = profiles_dir or DEFAULT_PROFILES_DIR
    profiles = {}
    if not d.exists():
        return profiles
    for f in sorted(d.glob("*.yaml")):
        try:
            p = load_profile(f)
            pid = p.get("id", f.stem)
            p["id"] = pid
            p["_source"] = str(f)
            profiles[pid] = p
        except Exception:
            pass
    return profiles


# ===== エイリアス自動構築 =====

def build_alias_map(profiles: dict[str, dict]) -> dict[str, str]:
    """プロファイルのname_jp, name_en, aliases, idからエイリアスマップを構築。"""
    aliases: dict[str, str] = {}
    for pid, p in profiles.items():
        # ID自体
        aliases[pid] = pid
        # 日本語名
        if "name_jp" in p:
            aliases[p["name_jp"]] = pid
        # 英語名
        if "name_en" in p:
            aliases[p["name_en"].lower()] = pid
        # 明示的なエイリアス
        for a in p.get("aliases", []):
            aliases[a] = pid
    return aliases


# ===== 公開API =====

_cache: dict[str, Any] = {}


def _ensure_loaded(profiles_dir: Path | None = None) -> None:
    d = profiles_dir or DEFAULT_PROFILES_DIR
    key = str(d)
    if key not in _cache:
        profiles = load_all_profiles(d)
        _cache[key] = {
            "profiles": profiles,
            "aliases": build_alias_map(profiles),
        }


def get_profile(name: str, profiles_dir: Path | None = None) -> dict[str, Any] | None:
    """名前・ID・エイリアスからプロファイルを検索。"""
    _ensure_loaded(profiles_dir)
    d = profiles_dir or DEFAULT_PROFILES_DIR
    data = _cache[str(d)]
    pid = data["aliases"].get(name) or data["aliases"].get(name.lower())
    if pid:
        return data["profiles"].get(pid)
    # 部分一致フォールバック
    for alias, target_pid in data["aliases"].items():
        if name in alias or alias in name:
            return data["profiles"].get(target_pid)
    return None


def list_profiles(profiles_dir: Path | None = None) -> list[dict[str, str]]:
    """全プロファイルの概要リストを返す。"""
    _ensure_loaded(profiles_dir)
    d = profiles_dir or DEFAULT_PROFILES_DIR
    profiles = _cache[str(d)]["profiles"]
    result = []
    for p in profiles.values():
        result.append({
            "id": p["id"],
            "name_jp": p.get("name_jp", p["id"]),
            "category_jp": p.get("category_jp", ""),
            "description_jp": p.get("description_jp", ""),
            "examples": ", ".join(p.get("examples", [])),
        })
    return result


def get_categories_for_profile(profile: dict[str, Any]) -> list[str]:
    """プロファイルに基づく実行カテゴリリスト。"""
    primary = profile.get("primary_categories", [])
    secondary = profile.get("secondary_categories", [])
    excluded = set(profile.get("excluded_categories", []))
    all_cats = primary + secondary
    return [c.value if isinstance(c, TestCategory) else c for c in all_cats if c not in excluded]


def get_scoring_weights(profile: dict[str, Any]) -> dict[str, float]:
    """プロファイルのスコアリング重み。"""
    defaults = {
        "practicality": 1.0,
        "cost_performance": 1.0,
        "localization": 1.0,
        "safety": 1.0,
        "uniqueness": 1.0,
    }
    weights = profile.get("scoring_weights", {})
    # Map legacy names to current 5-axis names
    _legacy_map = {
        "japanese_ability": "localization",
        "reliability": "safety",
        "practical": "practicality",
    }
    normalized = {}
    for k, v in weights.items():
        normalized[_legacy_map.get(k, k)] = v
    return {**defaults, **normalized}


def search_profiles(query: str, profiles_dir: Path | None = None) -> list[dict[str, Any]]:
    """キーワードでプロファイルを検索。"""
    _ensure_loaded(profiles_dir)
    d = profiles_dir or DEFAULT_PROFILES_DIR
    profiles = _cache[str(d)]["profiles"]
    results = []
    q = query.lower()
    for p in profiles.values():
        searchable = " ".join([
            p.get("id", ""),
            p.get("name_jp", ""),
            p.get("name_en", ""),
            p.get("description_jp", ""),
            p.get("category_jp", ""),
            " ".join(p.get("examples", [])),
            " ".join(p.get("aliases", [])),
            " ".join(p.get("traits", [])),
        ]).lower()
        if q in searchable:
            results.append(p)
    return results


def clear_cache() -> None:
    """キャッシュをクリア（テスト用）。"""
    _cache.clear()
