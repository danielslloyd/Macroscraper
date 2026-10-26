"""Image, nutrition-label, and snapshot download helpers.

Filenames are deterministic (product_id-based) per the project spec — never
include user-facing strings like product name. All paths returned to callers
are forward-slash relative to the project root for stable storage in
master.json.

An image's position in the filename is its position in the adapter's gallery,
so `{product_id}_2.jpg` is the same photograph on every re-run even if one of
the others failed to download. That stability is what lets the extraction
ladder record "the panel is image 2" and have it still be true later.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from orchestrator.storage import (
    DATA_DIR,
    HTML_DIR,
    IMAGES_DIR,
    LABELS_DIR,
    ensure_dirs,
    html_path,
    image_path,
    label_path,
    relative_to_root,
)

from .fetch.http import client


async def _download_to(http, url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = await http.get(url)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def save_html_snapshot(product_id: str, html: str) -> str:
    ensure_dirs()
    path = html_path(product_id)
    path.write_text(html, encoding="utf-8")
    return relative_to_root(path)


def save_api_snapshot(product_id: str, payload: str) -> str:
    """Store a raw API response, for retailers fetched through an API.

    Kept beside the HTML snapshots and named the same way, with a `.api.json`
    suffix so it is obvious that this record did not come from a web page.
    """
    ensure_dirs()
    path = HTML_DIR / f"{product_id}.api.json"
    path.write_text(payload, encoding="utf-8")
    return relative_to_root(path)


async def download_images(product_id: str, images: list) -> list[dict]:
    """Download product images, preserving each one's gallery position and role.

    Accepts either a list of URLs or the adapters' richer
    `{public_url, role, alt}` dicts. Failures for individual images are
    dropped from the result -- one dead CDN URL should not block a record --
    but the surviving entries keep their original index.
    """
    ensure_dirs()
    if not images:
        return []

    entries: list[dict] = []
    for i, image in enumerate(images):
        if isinstance(image, str):
            entries.append({"index": i, "public_url": image, "role": "other", "alt": ""})
        elif isinstance(image, dict) and image.get("public_url"):
            entries.append({
                "index": i,
                "public_url": image["public_url"],
                "role": image.get("role") or "other",
                "alt": image.get("alt") or "",
            })

    results: list[dict] = []
    async with client() as http:
        outcomes = await asyncio.gather(
            *(_download_to(http, e["public_url"], image_path(product_id, e["index"]))
              for e in entries),
            return_exceptions=True,
        )
    for entry, outcome in zip(entries, outcomes):
        if isinstance(outcome, BaseException):
            continue
        record = {
            "public_url": entry["public_url"],
            "local_path": relative_to_root(outcome),
            "role": entry["role"],
        }
        if entry["alt"]:
            record["alt"] = entry["alt"]
        results.append(record)
    return results


async def download_label(product_id: str, url: str | None) -> dict | None:
    """Download a nutrition-label image the adapter identified directly."""
    if not url:
        return None
    ensure_dirs()
    async with client() as http:
        try:
            path = await _download_to(http, url, label_path(product_id))
        except Exception:
            return None
    return {"public_url": url, "local_path": relative_to_root(path)}


def adopt_label_image(product_id: str, local_path: str, public_url: str = "") -> dict | None:
    """Copy an already-downloaded product photo into `data/labels/`.

    The panel is usually just one of the gallery images, identified by the OCR
    tier rather than labelled as such by the retailer. Copying it to the
    spec's `{product_id}_label.jpg` means every product has its label in the
    same place regardless of which retailer it came from.
    """
    ensure_dirs()
    source = DATA_DIR.parent / local_path
    if not source.exists():
        return None
    destination = label_path(product_id)
    try:
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
    except OSError:
        return None
    return {
        "public_url": public_url,
        "local_path": relative_to_root(destination),
        "copied_from": local_path,
    }


__all__ = [
    "adopt_label_image",
    "download_images",
    "download_label",
    "save_api_snapshot",
    "save_html_snapshot",
    "HTML_DIR",
    "IMAGES_DIR",
    "LABELS_DIR",
]
