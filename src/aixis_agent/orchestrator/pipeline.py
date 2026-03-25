"""Test case generation utilities.

After the Chrome extension migration, execution is handled by the extension.
This module retains only test case generation and target config loading.
"""

from pathlib import Path

import yaml
from rich.console import Console

from ..core.enums import TestCategory
from ..core.models import TestCase
from ..patterns.generator import generate_all
from ..utils.logging import get_logger

logger = get_logger(__name__)
console = Console()


def load_target_config(config_path: Path) -> dict:
    """Load target tool configuration from YAML."""
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_test_cases(
    patterns_dir: Path,
    categories: list[str] | None = None,
) -> list[TestCase]:
    """Generate test cases from YAML pattern definitions.

    This is the primary entry point used by the Chrome extension API
    to generate test cases for a protocol-driven audit session.
    """
    return generate_all(patterns_dir, categories)


# Priority order for test categories (most important first)
CATEGORY_PRIORITY = {
    # Slide-creation categories (primary)
    TestCategory.SLIDE_BASIC: 0,
    TestCategory.SLIDE_JAPANESE: 1,
    TestCategory.SLIDE_STRUCTURE: 2,
    TestCategory.SLIDE_ACCURACY: 3,
    TestCategory.SLIDE_ADVANCED: 4,
    # Legacy categories
    TestCategory.CONTRADICTORY: 10,
    TestCategory.BUSINESS_JP: 11,
    TestCategory.KEIGO_MIXING: 12,
    TestCategory.DIALECT: 13,
    TestCategory.AMBIGUOUS: 14,
    TestCategory.MULTI_STEP: 15,
    TestCategory.BROKEN_GRAMMAR: 16,
    TestCategory.LONG_INPUT: 17,
    TestCategory.UNICODE_EDGE: 18,
}


def sort_by_priority(test_cases: list[TestCase]) -> list[TestCase]:
    """Round-robin interleave test cases across categories by priority.

    Ensures partial results cover all categories when budget is limited.
    """
    from collections import defaultdict

    by_category: dict[str, list[TestCase]] = defaultdict(list)
    for tc in test_cases:
        by_category[tc.category].append(tc)

    sorted_categories = sorted(
        by_category.keys(),
        key=lambda cat: CATEGORY_PRIORITY.get(cat, 99),
    )

    result: list[TestCase] = []
    round_idx = 0
    while True:
        added_any = False
        for cat in sorted_categories:
            cases = by_category[cat]
            if round_idx < len(cases):
                result.append(cases[round_idx])
                added_any = True
        if not added_any:
            break
        round_idx += 1

    return result
