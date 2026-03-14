"""YAML pattern file loader with Jinja2 template expansion."""

from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, BaseLoader


def load_pattern_file(path: Path) -> dict[str, Any]:
    """Load a single pattern YAML file."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_all_patterns(patterns_dir: Path) -> list[dict[str, Any]]:
    """Load all YAML pattern files from a directory."""
    patterns = []
    for yaml_file in sorted(patterns_dir.glob("*.yaml")):
        data = load_pattern_file(yaml_file)
        if data:
            data["_source_file"] = str(yaml_file)
            patterns.append(data)
    return patterns


def render_template(template_str: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string with the given context."""
    env = Environment(loader=BaseLoader(), keep_trailing_newline=True)
    tmpl = env.from_string(template_str)
    return tmpl.render(**context).strip()


def expand_parameters(parameters: dict[str, list[dict]]) -> list[dict[str, dict]]:
    """Compute Cartesian product of all parameter lists.

    Given:
        parameters = {
            "dialect": [{"id": "kansai", ...}, {"id": "tohoku", ...}],
            "task": [{"id": "summarize", ...}, {"id": "qa", ...}]
        }

    Returns:
        [
            {"dialect": {"id": "kansai", ...}, "task": {"id": "summarize", ...}},
            {"dialect": {"id": "kansai", ...}, "task": {"id": "qa", ...}},
            {"dialect": {"id": "tohoku", ...}, "task": {"id": "summarize", ...}},
            {"dialect": {"id": "tohoku", ...}, "task": {"id": "qa", ...}},
        ]
    """
    from itertools import product

    keys = list(parameters.keys())
    value_lists = [parameters[k] for k in keys]

    combos = []
    for values in product(*value_lists):
        combo = {keys[i]: values[i] for i in range(len(keys))}
        combos.append(combo)
    return combos
