"""Configuration management for gh-hygiene.

Config lives at ~/.config/gh-hygiene/config.yml with restricted permissions.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import yaml


CONFIG_DIR = Path.home() / ".config" / "gh-hygiene"
CONFIG_FILE = CONFIG_DIR / "config.yml"


def _ensure_config_dir() -> None:
    """Create config directory with 0700 permissions if it doesn't exist."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(CONFIG_DIR, stat.S_IRWXU)


def load_config() -> dict[str, Any]:
    """Load configuration from disk. Returns empty dict if file missing."""
    _ensure_config_dir()
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict[str, Any]) -> None:
    """Save configuration to disk with 0600 permissions."""
    _ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(config, f)
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)


def get_config_value(key: str, default: Any = None) -> Any:
    """Get a single config value by key."""
    cfg = load_config()
    return cfg.get(key, default)


def set_config_value(key: str, value: Any) -> None:
    """Set a single config value by key."""
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
