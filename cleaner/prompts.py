"""Prompt templates for the cleaning pass.

The same prompt is shared across models in v1; vision models additionally
receive the nutrition-label image. Each model produces its own structured
output, and all outputs are stored verbatim in `cleaning_runs[]`.
"""
from __future__ import annotations

import json

SYSTEM_PROMPT = (
    "You are a data extraction assistant for a packaged-food product database. "
    "Given the raw scraped fields and HTML snippets for a single retail product, "
    "produce a clean JSON object with the schema described below. "
    "Do not invent values: leave a field null if you cannot determine it from the input. "
    "Do not include commentary outside the JSON."
)

OUTPUT_SCHEMA = {
    "name": "string",
    "brand": "string or null",
    "package_size": "string or null",
    "package_price_usd": "number or null",
    "unit_price_usd": "number or null",
    "unit": "string or null (e.g. 'per oz')",
    "serving_size": "string or null",
    "calories": "number or null",
    "fat_g": "number or null",
    "saturated_fat_g": "number or null",
    "trans_fat_g": "number or null",
    "cholesterol_mg": "number or null",
    "sodium_mg": "number or null",
    "carbs_g": "number or null",
    "fiber_g": "number or null",
    "sugars_g": "number or null",
    "added_sugars_g": "number or null",
    "protein_g": "number or null",
    "vitamins": "object or null (key=name, value=amount)",
    "ingredients": "array of strings or null",
    "allergens": "array of strings or null",
    "certifications": "array of strings or null",
    "notes": "string or null",
}


def build_user_prompt(raw_product: dict, has_vision_image: bool) -> str:
    parts = [
        "Extract a clean structured record from the following scraped product data.",
        "",
        "Raw scraped fields (JSON):",
        "```json",
        json.dumps(_compact_for_prompt(raw_product), indent=2, ensure_ascii=False),
        "```",
        "",
        "Output schema (return JSON matching this shape):",
        "```json",
        json.dumps(OUTPUT_SCHEMA, indent=2),
        "```",
    ]
    if has_vision_image:
        parts.extend(
            [
                "",
                "A nutrition label image is attached. Use OCR on the label to fill",
                "nutrition fields when the scraped data is incomplete. Prefer the",
                "label values over inferred ones.",
            ]
        )
    parts.extend(
        [
            "",
            "Return only the JSON object — no prose, no markdown fences.",
        ]
    )
    return "\n".join(parts)


def _compact_for_prompt(product: dict) -> dict:
    """Drop noisy fields (HTML path, related_products, prior cleaning_runs) before prompting."""
    keep = {
        "product_id",
        "retailer",
        "url",
        "name",
        "brand",
        "price",
        "nutrition",
        "similar_items",
    }
    return {k: v for k, v in product.items() if k in keep}
