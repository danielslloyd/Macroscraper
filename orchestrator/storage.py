"""master.json read/write/patch helpers.

master.json is the single source of truth for all crawled trees and product
records. Every module reads from and writes to it through this module.

Concurrency: all writes go through a single asyncio.Lock plus an atomic
replace, so writes from concurrent jobs don't interleave.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MASTER_PATH = DATA_DIR / "master.json"
IMAGES_DIR = DATA_DIR / "images"
LABELS_DIR = DATA_DIR / "labels"
HTML_DIR = DATA_DIR / "html"

_RETAILER_SLUGS = {
    "whole_foods": "wholefds",
    "trader_joes": "traderjs",
    "walmart": "walmart",
    "costco": "costco",
    "amazon": "amazon",
    "kroger": "kroger",
    "publix": "publix",
}

_lock = asyncio.Lock()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def retailer_slug(retailer: str) -> str:
    return _RETAILER_SLUGS.get(retailer, retailer)


def product_id_for(retailer: str, url: str) -> str:
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return f"{retailer_slug(retailer)}_{digest}"


def ensure_dirs() -> None:
    for d in (DATA_DIR, IMAGES_DIR, LABELS_DIR, HTML_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _empty_master() -> dict:
    return {"retailers": {}, "products": {}}


def load_master_sync() -> dict:
    ensure_dirs()
    if not MASTER_PATH.exists():
        return _empty_master()
    try:
        with MASTER_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        backup = MASTER_PATH.with_suffix(".corrupt.json")
        MASTER_PATH.rename(backup)
        return _empty_master()


def save_master_sync(data: dict) -> None:
    ensure_dirs()
    fd, tmp_path = tempfile.mkstemp(prefix="master.", suffix=".json", dir=str(DATA_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, MASTER_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


async def load_master() -> dict:
    async with _lock:
        return load_master_sync()


async def save_master(data: dict) -> None:
    async with _lock:
        save_master_sync(data)


def _deep_merge(dst: dict, src: dict) -> dict:
    """Merge src into dst without dropping keys present in dst but missing in src."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


async def patch_product(product_id: str, patch: dict) -> dict:
    """Merge `patch` into the product record. Never deletes existing keys."""
    async with _lock:
        data = load_master_sync()
        existing = data["products"].get(product_id, {})
        _deep_merge(existing, patch)
        existing.setdefault("product_id", product_id)
        data["products"][product_id] = existing
        save_master_sync(data)
        return existing


async def append_cleaning_run(product_id: str, run: dict) -> None:
    async with _lock:
        data = load_master_sync()
        product = data["products"].setdefault(product_id, {"product_id": product_id})
        product.setdefault("cleaning_runs", []).append(run)
        save_master_sync(data)


async def set_status(product_id: str, status: str) -> None:
    await patch_product(product_id, {"status": status})


async def get_product(product_id: str) -> dict | None:
    async with _lock:
        data = load_master_sync()
        return data["products"].get(product_id)


async def set_retailer_tree(retailer: str, tree: dict) -> None:
    async with _lock:
        data = load_master_sync()
        data["retailers"][retailer] = {
            "crawled_at": utcnow_iso(),
            "tree": tree,
        }
        save_master_sync(data)


async def get_retailer_tree(retailer: str) -> dict | None:
    async with _lock:
        data = load_master_sync()
        return data["retailers"].get(retailer)


def html_path(product_id: str) -> Path:
    return HTML_DIR / f"{product_id}.html"


def image_path(product_id: str, n: int) -> Path:
    return IMAGES_DIR / f"{product_id}_{n}.jpg"


def label_path(product_id: str) -> Path:
    return LABELS_DIR / f"{product_id}_label.jpg"


def relative_to_root(path: Path) -> str:
    """Return a forward-slash path relative to the project root, for storage in master.json."""
    return str(path.resolve().relative_to(ROOT)).replace(os.sep, "/")


async def link_related_products(a: str, b: str) -> None:
    """Bidirectional related_products link between two product IDs."""
    if a == b:
        return
    async with _lock:
        data = load_master_sync()
        for x, y in ((a, b), (b, a)):
            prod = data["products"].setdefault(x, {"product_id": x})
            related = prod.setdefault("related_products", [])
            if y not in related:
                related.append(y)
        save_master_sync(data)


async def find_product_by_url(url: str) -> str | None:
    async with _lock:
        data = load_master_sync()
        for pid, prod in data["products"].items():
            if prod.get("url") == url:
                return pid
        return None
