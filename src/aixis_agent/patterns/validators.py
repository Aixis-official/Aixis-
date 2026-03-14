"""Test pattern validation utilities."""

from pathlib import Path

from ..core.models import TestCase


class ValidationError:
    def __init__(self, test_id: str, message: str):
        self.test_id = test_id
        self.message = message

    def __str__(self) -> str:
        return f"[{self.test_id}] {self.message}"


def validate_test_case(case: TestCase) -> list[ValidationError]:
    """Validate a single test case for common issues."""
    errors = []

    if not case.id:
        errors.append(ValidationError(case.id or "unknown", "IDが空です"))

    if not case.prompt or not case.prompt.strip():
        errors.append(ValidationError(case.id, "プロンプトが空です"))

    if len(case.prompt) > 500000:
        errors.append(ValidationError(case.id, f"プロンプトが長すぎます ({len(case.prompt)}文字)"))

    # Check for unresolved template variables
    if "{{" in case.prompt or "}}" in case.prompt:
        errors.append(ValidationError(case.id, "未展開のテンプレート変数が含まれています"))

    if not case.expected_behaviors:
        errors.append(ValidationError(case.id, "期待される挙動が定義されていません"))

    return errors


def validate_all(cases: list[TestCase]) -> list[ValidationError]:
    """Validate all test cases and return any errors found."""
    all_errors = []
    seen_ids = set()

    for case in cases:
        if case.id in seen_ids:
            all_errors.append(ValidationError(case.id, "IDが重複しています"))
        seen_ids.add(case.id)
        all_errors.extend(validate_test_case(case))

    return all_errors
