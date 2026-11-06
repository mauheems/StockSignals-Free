"""Configuration helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "config.yaml"


def load_config(config_path: str | Path | None = None) -> dict:
    """Load the YAML config file."""
    path = Path(config_path) if config_path else CONFIG_PATH
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
