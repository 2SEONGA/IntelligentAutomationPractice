from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def load_settings(path: str | Path = CONFIG_DIR / "settings.yaml") -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(name: str) -> dict[str, Any]:
    """Load `config/<name>.yaml` (agents, tasks, policy, settings)."""
    return load_settings(CONFIG_DIR / f"{name}.yaml")
