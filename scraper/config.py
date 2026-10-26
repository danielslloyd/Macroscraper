"""Runtime configuration, read from `scraper_config.json`.

Nothing here is hardcoded into the modules that use it: retailer delays, fetch
modes, the extraction tier order, confidence thresholds, and model names all
come from the file so they can be changed without touching code (the project
has no settings UI in v1 by design).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "scraper_config.json"

_DEFAULTS: dict[str, Any] = {
    "fetch": {
        "browser_profile": "data/browser-profile",
        "channel": "chrome",
        "headless": True,
        "nav_timeout_ms": 60000,
        "settle_ms": 3000,
        "user_agent": None,
        "locale": "en-US",
        "timezone": "America/New_York",
    },
    "retailers": {},
    "nutrition": {
        "accept_confidence": 0.75,
        "tiers": ["page_html", "barcode_db", "name_db", "ocr", "local_vision", "claude"],
        "ocr_engine": "rapidocr",
        "max_images_ocr": 6,
        "panel_likeness_threshold": 0.3,
        "barcode_sources": ["open_food_facts", "usda_fdc"],
    },
    "llm": {
        "ollama_host": "http://localhost:11434",
        "vision_model": "qwen2.5-vl:7b",
        "claude_model": "claude-sonnet-5-5",
        "claude_enabled": False,
    },
}

_RETAILER_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "mode": "browser",          # browser | api | http | fixture
    "min_delay": 1.5,
    "max_delay": 3.5,
    "first_party_only": False,
}

_cache: dict | None = None


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(*, refresh: bool = False) -> dict:
    """Load and cache the config, falling back to defaults when absent."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    data: dict = {}
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{CONFIG_PATH.name} is not valid JSON: {exc}") from exc
    _cache = _merge(_DEFAULTS, data)
    return _cache


def fetch_config() -> dict:
    return load()["fetch"]


def nutrition_config() -> dict:
    return load()["nutrition"]


def llm_config() -> dict:
    return load()["llm"]


def retailer_config(retailer: str) -> dict:
    configured = load()["retailers"].get(retailer, {})
    return _merge(_RETAILER_DEFAULTS, configured)


def enabled_retailers() -> list[str]:
    retailers = load()["retailers"]
    return [name for name in retailers if retailer_config(name)["enabled"]]


def browser_profile_dir() -> Path:
    """Absolute path to the persistent browser profile.

    The profile lives under `data/` (gitignored) so cookies and the results of
    any interactive bot check survive between runs.
    """
    raw = fetch_config()["browser_profile"]
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


__all__ = [
    "CONFIG_PATH",
    "ROOT",
    "browser_profile_dir",
    "enabled_retailers",
    "env",
    "fetch_config",
    "llm_config",
    "load",
    "nutrition_config",
    "retailer_config",
]
