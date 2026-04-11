"""Tests for pattern loading and template expansion."""

from pathlib import Path

from aixis_agent.patterns.loader import (
    expand_parameters,
    load_all_patterns,
    load_pattern_file,
    render_template,
)


def test_render_template_simple():
    result = render_template("Hello {{ name }}", {"name": "World"})
    assert result == "Hello World"


def test_render_template_nested():
    result = render_template(
        "{{ dialect_greeting }}、{{ task_label }}してください",
        {"dialect_greeting": "なんやねん", "task_label": "要約"},
    )
    assert "なんやねん" in result
    assert "要約" in result


def test_expand_parameters_cartesian():
    params = {
        "color": [{"id": "red"}, {"id": "blue"}],
        "size": [{"id": "small"}, {"id": "large"}],
    }
    result = expand_parameters(params)
    assert len(result) == 4  # 2 x 2
    ids = [(r["color"]["id"], r["size"]["id"]) for r in result]
    assert ("red", "small") in ids
    assert ("red", "large") in ids
    assert ("blue", "small") in ids
    assert ("blue", "large") in ids


def test_expand_parameters_empty():
    assert expand_parameters({}) == [{}]


def test_load_pattern_file(patterns_dir):
    dialect_path = patterns_dir / "dialect.yaml"
    if dialect_path.exists():
        data = load_pattern_file(dialect_path)
        assert data["category"] == "dialect"
        assert "parameters" in data
        assert "templates" in data


def test_load_all_patterns(patterns_dir):
    if patterns_dir.exists():
        patterns = load_all_patterns(patterns_dir)
        assert len(patterns) >= 2  # catalog always ships multiple categories
        categories = [p["category"] for p in patterns]
        # Pin two representative canonical categories that currently ship.
        assert "minutes_japanese" in categories
        assert "slide_basic" in categories
