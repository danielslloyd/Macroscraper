"""Local vision-model reading of a nutrition panel (Ollama).

Tier 4 of the extraction ladder: reached only when the page had no structured
nutrition, the barcode databases did not know the product, and OCR plus the
regex parser could not produce a confident panel. Typically that means a
curved, glared, or low-resolution photo -- cases where a vision model's
tolerance for messy input earns its cost.

Runs against the Ollama HTTP API, so it stays local and offline.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from scraper import config

SYSTEM_PROMPT = (
    "You read Nutrition Facts panels from photographs of food packaging. "
    "Report only what is printed on the label. Never estimate, infer, or fill "
    "in a typical value for a missing nutrient."
)

USER_PROMPT = """Read the Nutrition Facts panel in this image.

Return a single JSON object with these keys, omitting any that the panel does
not state. Use the amount per serving, not per container, and give plain
numbers without units:

  serving_size (string, exactly as printed, e.g. "2 tbsp (32g)")
  servings_per_container (number)
  calories (number)
  fat_g, saturated_fat_g, trans_fat_g (numbers, grams)
  cholesterol_mg, sodium_mg (numbers, milligrams)
  carbs_g, fiber_g, sugars_g, added_sugars_g (numbers, grams)
  protein_g (number, grams)
  calcium_mg, iron_mg, potassium_mg (numbers, milligrams)
  vitamin_d_mcg (number, micrograms)

Report amounts, not % Daily Value. If the image does not show a Nutrition
Facts panel, return {}."""


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


async def read_panel(
    image_path: str | Path,
    *,
    model: str | None = None,
    host: str | None = None,
    timeout: float = 300.0,
) -> dict:
    """Ask the local vision model to transcribe a panel.

    Returns `{"model", "raw", "parsed"}`. A failure is reported in an "error"
    key rather than raised, so one unavailable model cannot fail a scrape.
    """
    cfg = config.llm_config()
    model = model or cfg["vision_model"]
    host = (host or cfg["ollama_host"]).rstrip("/")
    path = Path(image_path)
    if not path.exists():
        return {"model": model, "raw": "", "parsed": None,
                "error": f"image not found: {path}"}

    payload = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "prompt": USER_PROMPT,
        "images": [_b64(path)],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{host}/api/generate", json=payload)
            response.raise_for_status()
            raw = response.json().get("response", "")
    except Exception as exc:  # noqa: BLE001 - surfaced in the record
        return {"model": model, "raw": "", "parsed": None,
                "error": f"{type(exc).__name__}: {exc}"}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return {"model": model, "raw": raw,
            "parsed": parsed if isinstance(parsed, dict) else None}


async def model_available(model: str | None = None, host: str | None = None) -> bool:
    """Whether Ollama is up and has the model pulled."""
    cfg = config.llm_config()
    model = model or cfg["vision_model"]
    host = (host or cfg["ollama_host"]).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{host}/api/tags")
            response.raise_for_status()
            names = {m.get("name", "") for m in response.json().get("models", [])}
    except Exception:
        return False
    base = model.split(":")[0]
    return any(name == model or name.split(":")[0] == base for name in names)


__all__ = ["model_available", "read_panel"]
