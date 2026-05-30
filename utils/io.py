"""Small IO helpers for config and local knowledge file loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file into a dictionary."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in YAML file: {path}")
    return data


def load_json(path: str | Path) -> Any:
    """Load a JSON file from disk."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_dir(path: str | Path) -> Path:
    """Create a directory if it does not exist and return its Path."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target
