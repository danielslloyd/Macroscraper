"""Claude-API reading of a nutrition panel.

Tier 5, and the last resort: reached only when the page, the barcode
databases, OCR, and the local vision model have all failed on one product.
Disabled by default (`llm.claude_enabled` in `scraper_config.json`) because
every call costs money and the tiers above it handle the great majority of
products.

Uses the official Anthropic SDK with a JSON schema on the response, so the
output needs no repair before it is normalized.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

from scraper import config

SYSTEM_PROMPT = (
    "You read Nutrition Facts panels from photographs of food packaging. "
    "Report only values printed on the label, as amounts per serving rather "
    "than % Daily Value. Omit any nutrient the panel does not state -- never "
    "estimate, infer, or substitute a typical value."
)

USER_PROMPT = (
    "Read the Nutrition Facts panel in this photograph of a product package. "
    "Give amounts per serving. Omit any field the panel does not state. If the "
    "image shows no Nutrition Facts panel, return an object with found=false."
)

# Numeric nutrients, in the canonical units of the project's schema.
_NUMERIC_FIELDS = {
    "servings_per_container": "Servings per container.",
    "calories": "Calories per serving.",
    "fat_g": "Total fat in grams.",
    "saturated_fat_g": "Saturated fat in grams.",
    "trans_fat_g": "Trans fat in grams.",
    "cholesterol_mg": "Cholesterol in milligrams.",
    "sodium_mg": "Sodium in milligrams.",
    "carbs_g": "Total carbohydrate in grams.",
    "fiber_g": "Dietary fiber in grams.",
    "sugars_g": "Total sugars in grams.",
    "added_sugars_g": "Added sugars in grams.",
    "protein_g": "Protein in grams.",
    "calcium_mg": "Calcium in milligrams.",
    "iron_mg": "Iron in milligrams.",
    "potassium_mg": "Potassium in milligrams.",
    "vitamin_d_mcg": "Vitamin D in micrograms.",
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {
            "type": "boolean",
            "description": "True when the image shows a Nutrition Facts panel.",
        },
        "serving_size": {
            "type": "string",
            "description": 'Serving size exactly as printed, e.g. "2 tbsp (32g)".',
        },
        **{
            name: {"type": "number", "description": description}
            for name, description in _NUMERIC_FIELDS.items()
        },
    },
    "required": ["found"],
    "additionalProperties": False,
}


class ClaudeUnavailable(RuntimeError):
    """The Claude tier is switched off or cannot be used."""


def enabled() -> bool:
    cfg = config.llm_config()
    return bool(cfg.get("claude_enabled")) and bool(config.env("ANTHROPIC_API_KEY"))


def _image_block(path: Path) -> dict:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(path.read_bytes()).decode("ascii"),
        },
    }


async def read_panel(image_path: str | Path, *, model: str | None = None) -> dict:
    """Send one panel photo to Claude and return the extracted fields.

    Returns `{"model", "raw", "parsed"}`, with any failure reported in an
    "error" key rather than raised.
    """
    cfg = config.llm_config()
    model = model or cfg["claude_model"]
    path = Path(image_path)

    if not enabled():
        return {"model": model, "raw": "", "parsed": None,
                "error": "claude tier disabled (set llm.claude_enabled and ANTHROPIC_API_KEY)"}
    if not path.exists():
        return {"model": model, "raw": "", "parsed": None,
                "error": f"image not found: {path}"}

    try:
        import anthropic
    except ImportError:
        return {"model": model, "raw": "", "parsed": None,
                "error": "the anthropic package is not installed (pip install anthropic)"}

    client = anthropic.AsyncAnthropic()
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            output_config={
                "effort": cfg.get("claude_effort", "low"),
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": [_image_block(path), {"type": "text", "text": USER_PROMPT}],
            }],
        )
    except Exception as exc:  # noqa: BLE001 - surfaced in the record
        return {"model": model, "raw": "", "parsed": None,
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        await client.close()

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        return {"model": model, "raw": "", "parsed": None,
                "error": f"refused: {getattr(detail, 'category', None)}"}

    raw = "".join(block.text for block in response.content if block.type == "text")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("found") is False:
        parsed = {}
    if isinstance(parsed, dict):
        parsed.pop("found", None)
    return {"model": model, "raw": raw,
            "parsed": parsed if isinstance(parsed, dict) else None}


__all__ = ["ClaudeUnavailable", "RESPONSE_SCHEMA", "enabled", "read_panel"]
