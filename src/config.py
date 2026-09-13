"""Configuration loading.

A single JSON file (config/config.json) is the source of truth. Any scalar can be
overridden from the environment so CI can change behaviour without a commit:

    YUHANA_SITE_BASE_URL=https://example.org
    YUHANA_VALIDATION_CONCURRENCY=16

The env var name is YUHANA_ + the dotted path upper-cased with dots as underscores.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "config.json"

_ENV_PREFIX = "YUHANA_"


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return raw


def _apply_env(node: dict, prefix: str) -> None:
    for key, value in list(node.items()):
        if key.startswith("$"):
            continue
        env_name = f"{prefix}{key.upper()}"
        if isinstance(value, dict):
            _apply_env(value, env_name + "_")
        elif env_name in os.environ:
            node[key] = _coerce(os.environ[env_name], value)


class Config(dict):
    """dict with dotted lookup: cfg.get_path('site.base_url')."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # --- derived URL helpers -------------------------------------------------
    @property
    def base_url(self) -> str:
        return str(self.get_path("site.base_url", "")).rstrip("/")

    def playlist_url(self, relative: str) -> str:
        path = str(self.get_path("site.playlist_path", "/playlists")).strip("/")
        return f"{self.base_url}/{path}/{relative.lstrip('/')}"

    def epg_url(self, relative: str = "epg.xml") -> str:
        path = str(self.get_path("site.epg_path", "/epg")).strip("/")
        return f"{self.base_url}/{path}/{relative.lstrip('/')}"


_cached: Config | None = None


def load(path: Path | None = None, use_cache: bool = True) -> Config:
    global _cached
    if use_cache and _cached is not None and path is None:
        return _cached
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _apply_env(data, _ENV_PREFIX)
    cfg = Config(data)
    if path is None and use_cache:
        _cached = cfg
    return cfg
