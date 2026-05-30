"""Logging setup for console plus file output in the MVP runtime."""

from __future__ import annotations

import logging
from pathlib import Path

from utils.io import ensure_dir


def setup_logging(log_dir: str | Path) -> Path:
    """Configure root logging and return the log file path."""
    target_dir = ensure_dir(log_dir)
    log_file = target_dir / "cambot.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    return log_file
