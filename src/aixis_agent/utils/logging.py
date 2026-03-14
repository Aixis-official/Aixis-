"""Structured logging setup for the Aixis agent."""

import logging
import sys

from rich.logging import RichHandler


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured logging with rich formatting."""
    logger = logging.getLogger("aixis_agent")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = RichHandler(
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    return logger


def get_logger(name: str = "aixis_agent") -> logging.Logger:
    """Get or create a named logger."""
    return logging.getLogger(name)
