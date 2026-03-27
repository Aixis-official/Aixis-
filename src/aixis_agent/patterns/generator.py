"""Test case generator: expands YAML templates into concrete TestCase instances."""

from pathlib import Path

from ..core.enums import TestCategory
from ..core.models import TestCase
from .loader import expand_parameters, load_all_patterns, render_template


def generate_from_pattern(pattern_data: dict) -> list[TestCase]:
    """Generate TestCase instances from a single pattern definition."""
    category_str = pattern_data.get("category", "")
    try:
        category = TestCategory(category_str)
    except ValueError:
        raise ValueError(f"Unknown test category: {category_str}")

    parameters = pattern_data.get("parameters", {})
    templates = pattern_data.get("templates", [])
    generation = pattern_data.get("generation", {})
    max_variants = generation.get("max_variants", 10000)
    mode = generation.get("mode", "cartesian")

    if mode in ("cartesian", "static"):
        return _generate_cartesian(category, parameters, templates, max_variants, pattern_data)
    elif mode == "scaling":
        return _generate_scaling(category, parameters, templates, max_variants, pattern_data)
    else:
        raise ValueError(f"Unknown generation mode: {mode}")


def _generate_cartesian(
    category: TestCategory,
    parameters: dict,
    templates: list[dict],
    max_variants: int,
    pattern_data: dict,
) -> list[TestCase]:
    """Generate via Cartesian product of parameters x templates."""
    if not parameters:
        # No parameters - just use templates directly
        combos = [{}]
    else:
        combos = expand_parameters(parameters)

    extra_data = pattern_data.get("parameter_data", {})
    test_cases = []

    for combo in combos:
        # Build template context: flatten parameter dicts
        context = {}
        for param_name, param_value in combo.items():
            if isinstance(param_value, dict):
                for k, v in param_value.items():
                    context[f"{param_name}_{k}"] = v
                context[param_name] = param_value
            else:
                context[param_name] = param_value

        # Merge extra parameter data
        for param_name, param_value in combo.items():
            if isinstance(param_value, dict) and param_value.get("id") in extra_data.get(param_name, {}):
                extra = extra_data[param_name][param_value["id"]]
                for k, v in extra.items():
                    context[f"{param_name}_{k}"] = v

        for tmpl in templates:
            test_id = render_template(tmpl.get("id", ""), context)
            prompt = render_template(tmpl.get("prompt", ""), context)

            expected = []
            for eb in tmpl.get("expected_behaviors", []):
                expected.append(render_template(eb, context))

            failure = []
            for fi in tmpl.get("failure_indicators", []):
                failure.append(render_template(fi, context))

            tags = [category.value]
            for param_name, param_value in combo.items():
                if isinstance(param_value, dict) and "id" in param_value:
                    tags.append(param_value["id"])

            test_cases.append(
                TestCase(
                    id=test_id,
                    category=category,
                    prompt=prompt,
                    metadata={
                        "parameters": {k: v for k, v in combo.items()},
                        "template_id": tmpl.get("id", ""),
                    },
                    expected_behaviors=expected,
                    failure_indicators=failure,
                    tags=tags,
                )
            )

            if len(test_cases) >= max_variants:
                return test_cases

    return test_cases


def _generate_scaling(
    category: TestCategory,
    parameters: dict,
    templates: list[dict],
    max_variants: int,
    pattern_data: dict,
) -> list[TestCase]:
    """Generate test cases at increasing scales (e.g., input length)."""
    filler_text = pattern_data.get("filler_text", "これはテスト用のテキストです。")
    test_cases = []

    scale_values = parameters.get("scale_values", [])
    if not scale_values:
        return test_cases

    for tmpl in templates:
        for scale_item in scale_values:
            if isinstance(scale_item, dict):
                scale_val = scale_item.get("value", 0)
                scale_id = scale_item.get("id", str(scale_val))
            else:
                scale_val = scale_item
                scale_id = str(scale_val)

            # Build the prompt at the requested scale
            base_prompt = tmpl.get("base_prompt", "")
            # Repeat filler to approximate target character count
            repeat_count = max(1, int(scale_val) // max(1, len(filler_text)))
            generated_body = (filler_text + "\n") * repeat_count

            context = {
                "scale_value": scale_val,
                "scale_id": scale_id,
                "generated_body": generated_body,
            }

            test_id = render_template(tmpl.get("id", f"{category.value}-{scale_id}"), context)
            prompt = render_template(
                base_prompt + "\n\n{{ generated_body }}" if base_prompt else "{{ generated_body }}",
                context,
            )

            test_cases.append(
                TestCase(
                    id=test_id,
                    category=category,
                    prompt=prompt,
                    metadata={"scale_value": scale_val},
                    expected_behaviors=tmpl.get("expected_behaviors", []),
                    failure_indicators=tmpl.get("failure_indicators", []),
                    tags=[category.value, f"scale-{scale_id}"],
                )
            )

            if len(test_cases) >= max_variants:
                return test_cases

    return test_cases


def generate_all(patterns_dir: Path, categories: list[str] | None = None) -> list[TestCase]:
    """Generate all test cases from all pattern files in a directory.

    Args:
        patterns_dir: Path to the patterns config directory
        categories: Optional list of category names to filter
    """
    all_patterns = load_all_patterns(patterns_dir)
    all_cases: list[TestCase] = []

    for pattern_data in all_patterns:
        cat = pattern_data.get("category", "")
        if categories and cat not in categories:
            continue
        cases = generate_from_pattern(pattern_data)
        all_cases.extend(cases)

    return all_cases
