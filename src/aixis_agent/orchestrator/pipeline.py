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
    TestCategory.CONTRADICTORY: 0,
    TestCategory.BUSINESS_JP: 1,
    TestCategory.KEIGO_MIXING: 2,
    TestCategory.DIALECT: 3,
    TestCategory.AMBIGUOUS: 4,
    TestCategory.MULTI_STEP: 5,
    TestCategory.BROKEN_GRAMMAR: 6,
    TestCategory.LONG_INPUT: 7,
    TestCategory.UNICODE_EDGE: 8,
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
