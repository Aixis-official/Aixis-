"""Tests for test case generation."""

from pathlib import Path

from aixis_agent.core.enums import TestCategory
from aixis_agent.patterns.generator import generate_all, generate_from_pattern


def test_generate_from_dialect_pattern(patterns_dir):
    from aixis_agent.patterns.loader import load_pattern_file

    dialect_path = patterns_dir / "dialect.yaml"
    if not dialect_path.exists():
        return

    data = load_pattern_file(dialect_path)
    cases = generate_from_pattern(data)

    assert len(cases) > 0
    assert all(c.category == TestCategory.DIALECT for c in cases)
    assert all(c.prompt.strip() for c in cases)
    assert all(c.id for c in cases)

    # Should have combinations of dialects x tasks x templates
    ids = [c.id for c in cases]
    assert any("kansai" in id_ for id_ in ids)
    assert any("tohoku" in id_ for id_ in ids)


def test_generate_from_unicode_pattern(patterns_dir):
    from aixis_agent.patterns.loader import load_pattern_file

    path = patterns_dir / "unicode_edge.yaml"
    if not path.exists():
        return

    data = load_pattern_file(path)
    cases = generate_from_pattern(data)

    assert len(cases) > 0
    assert all(c.category == TestCategory.UNICODE_EDGE for c in cases)
    # Check that prompts contain actual Unicode characters
    all_prompts = " ".join(c.prompt for c in cases)
    assert any(ord(ch) > 127 for ch in all_prompts)


def test_generate_all(patterns_dir):
    if not patterns_dir.exists():
        return

    cases = generate_all(patterns_dir)
    assert len(cases) > 0

    # At least one of the current canonical categories must be produced.
    # (The catalog evolves; we pin a representative set rather than every one.)
    categories_found = set(c.category for c in cases)
    canonical = {
        TestCategory.MINUTES_JAPANESE,
        TestCategory.MINUTES_COMPLEX,
        TestCategory.MINUTES_TRANSCRIPTION,
        TestCategory.SLIDE_BASIC,
        TestCategory.SLIDE_ACCURACY,
        TestCategory.SLIDE_ADVANCED,
    }
    assert categories_found & canonical, (
        f"No canonical categories found. Got: {categories_found}"
    )


def test_generate_with_category_filter(patterns_dir):
    if not patterns_dir.exists():
        return

    # Pick any category that actually has a pattern file so the filter is
    # exercised against real data rather than a removed placeholder.
    cases = generate_all(patterns_dir, categories=["minutes_japanese"])
    assert len(cases) > 0
    assert all(c.category == TestCategory.MINUTES_JAPANESE for c in cases)


def test_no_unresolved_templates(patterns_dir):
    """Ensure no test case has unresolved Jinja2 template variables."""
    if not patterns_dir.exists():
        return

    cases = generate_all(patterns_dir)
    for case in cases:
        assert "{{" not in case.prompt, f"Unresolved template in {case.id}: {case.prompt[:100]}"
        assert "}}" not in case.prompt, f"Unresolved template in {case.id}: {case.prompt[:100]}"
