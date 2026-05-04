"""Image, nutrition-label, and HTML-snapshot download helpers.

Filenames are deterministic (product_id-based) per the project spec — never
include user-facing strings like product name. All paths returned to callers
are forward-slash relative to the project root for stable storage in
master.json.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from orchestrator.storage import (
    HTML_DIR,
    IMAGES_DIR,
    LABELS_DIR,
    ensure_dirs,
    html_path,
    image_path,
    label_path,
    relative_to_root,
)


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FoodScraper/0.1"
)


async def _download_to(client: httpx.AsyncClient, url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = await client.get(url, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def save_html_snapshot(product_id: str, html: str) -> str:
    ensure_dirs()
    path = html_path(product_id)
    path.write_text(html, encoding="utf-8")
    return relative_to_root(path)


async def download_images(product_id: str, urls: list[str]) -> list[dict]:
    """Download product images. Returns a list of {public_url, local_path} dicts.

    Failures for individual images are swallowed and dropped from the result —
    one bad CDN URL shouldn't block the whole record.
    """
    ensure_dirs()
    if not urls:
        return []
    results: list[dict] = []
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}) as client:
        tasks = [
            _download_to(client, url, image_path(product_id, i))
            for i, url in enumerate(urls)
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for url, outcome in zip(urls, outcomes):
            if isinstance(outcome, Exception):
                continue
            results.append({"public_url": url, "local_path": relative_to_root(outcome)})
    return results


async def download_label(product_id: str, url: str | None) -> dict | None:
    if not url:
        return None
    ensure_dirs()
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}) as client:
        try:
            path = await _download_to(client, url, label_path(product_id))
        except Exception:
            return None
    return {"public_url": url, "local_path": relative_to_root(path)}


__all__ = [
    "save_html_snapshot",
    "download_images",
    "download_label",
    "IMAGES_DIR",
    "LABELS_DIR",
    "HTML_DIR",
]
