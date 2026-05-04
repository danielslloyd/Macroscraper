"""Sequential Ollama cleaner.

Reads `cleaner_config.json` at runtime to discover the model list, then runs
each model sequentially against a single product record. Each model's raw
output is appended to `cleaning_runs[]` verbatim — no merging or alignment.

Vision-capable models additionally receive the nutrition-label image as a
base64 attachment.

Status flow: `scraped` -> `cleaning` -> `cleaned`.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx

from orchestrator.storage import (
    ROOT,
    append_cleaning_run,
    find_product_by_url,
    get_product,
    link_related_products,
    set_status,
    utcnow_iso,
)

from .prompts import SYSTEM_PROMPT, build_user_prompt

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CONFIG_PATH = ROOT / "cleaner_config.json"


def load_models() -> list[dict]:
    if not CONFIG_PATH.exists():
        return []
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)
    return list(config.get("models", []))


def _read_label_b64(product: dict) -> str | None:
    label = product.get("nutrition_label_image") or {}
    rel = label.get("local_path")
    if not rel:
        return None
    p = ROOT / rel
    if not p.exists():
        return None
    return base64.b64encode(p.read_bytes()).decode("ascii")


async def _call_ollama(
    client: httpx.AsyncClient,
    model: str,
    user_prompt: str,
    image_b64: str | None,
) -> dict:
    payload: dict = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
    }
    if image_b64:
        payload["images"] = [image_b64]
    resp = await client.post(
        f"{OLLAMA_HOST}/api/generate",
        json=payload,
        timeout=600.0,
    )
    resp.raise_for_status()
    body = resp.json()
    raw = body.get("response", "")
    try:
        parsed: dict | list | None = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return {"raw": raw, "parsed": parsed}


async def clean_product(product_id: str) -> dict:
    """Run every configured model over one product, sequentially."""
    product = await get_product(product_id)
    if product is None:
        raise KeyError(f"product {product_id} not found")

    models = load_models()
    if not models:
        raise RuntimeError("no models configured in cleaner_config.json")

    await set_status(product_id, "cleaning")
    label_b64 = _read_label_b64(product)
    user_prompt = build_user_prompt(product, has_vision_image=bool(label_b64))

    async with httpx.AsyncClient() as client:
        for model in models:
            name = model["name"]
            vision = bool(model.get("vision"))
            image = label_b64 if vision else None
            try:
                output = await _call_ollama(client, name, user_prompt, image)
                error = None
            except Exception as exc:  # noqa: BLE001 — surface in master.json
                output = {"raw": "", "parsed": None}
                error = f"{type(exc).__name__}: {exc}"
            run = {
                "model": name,
                "run_at": utcnow_iso(),
                "vision": vision,
                "output": output,
            }
            if error:
                run["error"] = error
            await append_cleaning_run(product_id, run)

    await _link_similar_items(product_id)
    await set_status(product_id, "cleaned")
    return await get_product(product_id) or {}


async def _link_similar_items(product_id: str) -> None:
    """Populate similar_items[].product_id and related_products[] bidirectionally."""
    product = await get_product(product_id)
    if not product:
        return
    similar = product.get("similar_items") or []
    if not similar:
        return
    for item in similar:
        if item.get("product_id"):
            continue
        url = item.get("public_url")
        if not url:
            continue
        match = await find_product_by_url(url)
        if match:
            item["product_id"] = match
            await link_related_products(product_id, match)
    # Persist updated similar_items array.
    await _patch_similar(product_id, similar)


async def _patch_similar(product_id: str, similar: list[dict]) -> None:
    from orchestrator.storage import patch_product
    await patch_product(product_id, {"similar_items": similar})
