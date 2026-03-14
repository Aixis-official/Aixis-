"""Shared test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def config_dir() -> Path:
    return Path(__file__).parent.parent / "config"


@pytest.fixture
def patterns_dir(config_dir) -> Path:
    return config_dir / "patterns"


@pytest.fixture
def targets_dir(config_dir) -> Path:
    return config_dir / "targets"
