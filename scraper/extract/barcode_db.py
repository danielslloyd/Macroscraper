"""Nutrition lookups by barcode.

Every retailer we scrape publishes a UPC or GTIN on the product page, and a
barcode is the one identifier that means the same thing across all of them. Two
free databases key off it:

  Open Food Facts   open data, no key, worldwide packaged-food coverage
  USDA FoodData     the Branded Foods dataset, whose `labelNutrients` block is
  Central           transcribed straight off the printed panel

This tier runs before any OCR because it is cheaper, exact, and returns the
label values rather than an estimate. It is also how the same physical product
scraped from two retailers ends up with consistent numbers.

`FDC_API_KEY` enables the USDA source; without it only Open Food Facts is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import config
from ..fetch import http
from . import nutrients

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

_OFF_FIELDS = (
    "code,product_name,brands,quantity,serving_size,serving_quantity,nutriments,"
    "image_nutrition_url,image_front_url"
)

# Open Food Facts nutriment key -> our canonical key. Values come in two
# flavours: `_serving` (per serving, what a label states) and `_100g`.
_OFF_MAP = {
    "energy-kcal": "calories",
    "fat": "fat_g",
    "saturated-fat": "saturated_fat_g",
    "trans-fat": "trans_fat_g",
    "cholesterol": "cholesterol_mg",
    "sodium": "sodium_mg",
    "carbohydrates": "carbs_g",
    "fiber": "fiber_g",
    "sugars": "sugars_g",
    "added-sugars": "added_sugars_g",
    "proteins": "protein_g",
    "calcium": "calcium_mg",
    "iron": "iron_mg",
    "potassium": "potassium_mg",
    "vitamin-d": "vitamin_d_mcg",
}

# Open Food Facts stores these in grams; our schema wants milligrams.
_OFF_GRAMS_TO_MG = {"sodium_mg", "cholesterol_mg", "calcium_mg", "iron_mg", "potassium_mg"}
_OFF_GRAMS_TO_MCG = {"vitamin_d_mcg"}

# USDA labelNutrients key -> our canonical key. Already per serving.
_FDC_MAP = {
    "calories": "calories",
    "fat": "fat_g",
    "saturatedFat": "saturated_fat_g",
    "transFat": "trans_fat_g",
    "cholesterol": "cholesterol_mg",
    "sodium": "sodium_mg",
    "carbohydrates": "carbs_g",
    "fiber": "fiber_g",
    "sugars": "sugars_g",
    "addedSugar": "added_sugars_g",
    "protein": "protein_g",
    "calcium": "calcium_mg",
    "iron": "iron_mg",
    "potassium": "potassium_mg",
    "vitaminD": "vitamin_d_mcg",
}


@dataclass
class BarcodeHit:
    source: str
    code: str
    nutrition: dict
    name: str = ""
    brand: str = ""
    label_image_url: str | None = None
    per_serving: bool = True
    raw_url: str = ""
    # Set only for name-matched hits; None means the barcode was exact.
    match_score: float | None = None


def normalize_code(raw: str | None) -> str | None:
    """Strip a barcode down to digits. Returns None if it cannot be one."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not 8 <= len(digits) <= 14:
        return None
    return digits


def code_variants(raw: str | None) -> list[str]:
    """Plausible spellings of one barcode.

    Retailers are inconsistent about leading zeros -- Kroger uses a 13-digit
    form, Amazon lists both a 12-digit UPC and a 14-digit GTIN -- and the
    databases are indexed under only one of them.
    """
    code = normalize_code(raw)
    if not code:
        return []
    bare = code.lstrip("0") or "0"
    out: list[str] = []
    for candidate in (code, bare, bare.zfill(12), bare.zfill(13), bare.zfill(14)):
        if 8 <= len(candidate) <= 14 and candidate not in out:
            out.append(candidate)
    return out


def _off_nutrition(nutriments: dict) -> tuple[dict, bool]:
    """Convert Open Food Facts nutriments into our canonical map.

    Prefers per-serving values; falls back to per-100g, flagged so the caller
    can record that the basis is not the printed serving.
    """
    for suffix, per_serving in (("_serving", True), ("_100g", False)):
        out: dict = {}
        for off_key, key in _OFF_MAP.items():
            value = nutrients.to_number(nutriments.get(f"{off_key}{suffix}"))
            if value is None:
                continue
            if key in _OFF_GRAMS_TO_MG:
                value *= 1000.0
            elif key in _OFF_GRAMS_TO_MCG:
                value *= 1_000_000.0
            out[key] = round(value, 3) if value % 1 else int(value)
        if out.get("calories") is None:
            energy_kj = nutrients.to_number(nutriments.get(f"energy{suffix}"))
            if energy_kj:
                out["calories"] = int(round(energy_kj / 4.184))
        if len(out) >= 4:
            return out, per_serving
    return {}, True


async def lookup_open_food_facts(code: str) -> BarcodeHit | None:
    url = OFF_URL.format(code=code)
    data = await http.get_json(url, params={"fields": _OFF_FIELDS})
    if not isinstance(data, dict) or data.get("status") == 0:
        return None
    product = data.get("product")
    if not isinstance(product, dict):
        return None
    nutrition, per_serving = _off_nutrition(product.get("nutriments") or {})
    if not nutrition:
        return None
    serving = product.get("serving_size")
    if serving and per_serving:
        nutrition["serving_size"] = str(serving).strip()
    elif not per_serving:
        nutrition["serving_size"] = "100 g"
        nutrition["basis"] = "per_100g"
    return BarcodeHit(
        source="open_food_facts",
        code=code,
        nutrition=nutrition,
        name=(product.get("product_name") or "").strip(),
        brand=(product.get("brands") or "").split(",")[0].strip(),
        label_image_url=product.get("image_nutrition_url"),
        per_serving=per_serving,
        raw_url=url,
    )


async def lookup_usda(code: str) -> BarcodeHit | None:
    api_key = config.env("FDC_API_KEY")
    if not api_key:
        return None
    data = await http.get_json(
        FDC_SEARCH_URL,
        params={
            "api_key": api_key,
            "query": code,
            "dataType": "Branded",
            "pageSize": 5,
        },
    )
    if not isinstance(data, dict):
        return None
    wanted = code.lstrip("0")
    for food in data.get("foods") or []:
        gtin = re.sub(r"\D", "", str(food.get("gtinUpc") or ""))
        if gtin.lstrip("0") != wanted:
            continue
        label = food.get("labelNutrients") or {}
        out: dict = {}
        for fdc_key, key in _FDC_MAP.items():
            value = nutrients.to_number((label.get(fdc_key) or {}).get("value"))
            if value is not None:
                out[key] = round(value, 3) if value % 1 else int(value)
        if not out:
            continue
        household = food.get("householdServingFullText")
        size = nutrients.to_number(food.get("servingSize"))
        unit = food.get("servingSizeUnit")
        if household:
            out["serving_size"] = str(household).strip()
        elif size is not None and unit:
            out["serving_size"] = f"{size:g} {unit}".strip()
        return BarcodeHit(
            source="usda_fdc",
            code=code,
            nutrition=out,
            name=(food.get("description") or "").strip(),
            brand=(food.get("brandName") or food.get("brandOwner") or "").strip(),
            per_serving=True,
            raw_url=FDC_SEARCH_URL,
        )
    return None


_LOOKUPS = {
    "open_food_facts": lookup_open_food_facts,
    "usda_fdc": lookup_usda,
}


# ---------------------------------------------------------------------------
# Name matching, for products published without a barcode
# ---------------------------------------------------------------------------

# Words that carry no identifying weight in a product name.
_STOPWORDS = {
    "the", "a", "an", "and", "of", "with", "in", "for", "pack", "count", "ct",
    "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds", "g", "kg", "ml",
    "l", "liter", "litre", "fl", "size", "each", "ea", "value", "pk",
}
# How close a candidate must be before its nutrition is used at all.
NAME_MATCH_THRESHOLD = 0.6

_OFF_SEARCH_FIELDS = (
    "code,product_name,brands,quantity,serving_size,nutriments,image_nutrition_url"
)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and not w.isdigit() and len(w) > 1}


def name_match_score(
    want_name: str, want_brand: str, got_name: str, got_brand: str
) -> float:
    """0..1 similarity between a scraped product and a database candidate.

    Brand agreement is required rather than scored: two different brands of
    creamy peanut butter have near-identical names and quite different labels,
    so a name-only match is not safe to copy numbers from.
    """
    want_tokens, got_tokens = _tokens(want_name), _tokens(got_name)
    if not want_tokens or not got_tokens:
        return 0.0
    if want_brand:
        brand_tokens = _tokens(want_brand)
        candidate_brand = _tokens(got_brand) | got_tokens
        if brand_tokens and not (brand_tokens & candidate_brand):
            return 0.0
    overlap = len(want_tokens & got_tokens)
    return overlap / len(want_tokens | got_tokens)


async def search_by_name(
    name: str, brand: str = "", *, limit: int = 8
) -> BarcodeHit | None:
    """Find a product in Open Food Facts by name and brand.

    For retailers that publish no barcode -- Publix, and Costco items whose UPC
    field is empty -- this is the only structured source left before OCR. It is
    deliberately strict: a candidate must clear `NAME_MATCH_THRESHOLD` and
    agree on brand, and the match score is recorded in the product's
    `nutrition_sources[]` so a questionable one can be reviewed.
    """
    if not name:
        return None
    terms = f"{brand} {name}".strip()
    data = await http.get_json(
        OFF_SEARCH_URL,
        params={
            "search_terms": terms,
            "json": 1,
            "page_size": limit,
            "fields": _OFF_SEARCH_FIELDS,
        },
    )
    if not isinstance(data, dict):
        return None

    best: tuple[float, dict] | None = None
    for candidate in data.get("products") or []:
        if not isinstance(candidate, dict):
            continue
        score = name_match_score(
            name, brand,
            candidate.get("product_name") or "",
            candidate.get("brands") or "",
        )
        if score < NAME_MATCH_THRESHOLD:
            continue
        if best is None or score > best[0]:
            best = (score, candidate)
    if best is None:
        return None

    score, candidate = best
    nutrition, per_serving = _off_nutrition(candidate.get("nutriments") or {})
    if not nutrition:
        return None
    serving = candidate.get("serving_size")
    if serving and per_serving:
        nutrition["serving_size"] = str(serving).strip()
    elif not per_serving:
        nutrition["serving_size"] = "100 g"
        nutrition["basis"] = "per_100g"
    hit = BarcodeHit(
        source="open_food_facts_name",
        code=str(candidate.get("code") or ""),
        nutrition=nutrition,
        name=(candidate.get("product_name") or "").strip(),
        brand=(candidate.get("brands") or "").split(",")[0].strip(),
        label_image_url=candidate.get("image_nutrition_url"),
        per_serving=per_serving,
        raw_url=OFF_SEARCH_URL,
    )
    hit.match_score = round(score, 3)
    return hit


async def lookup(raw_code: str | None, *, sources: list[str] | None = None) -> BarcodeHit | None:
    """Try every configured database across every spelling of the barcode."""
    variants = code_variants(raw_code)
    if not variants:
        return None
    names = sources if sources is not None else config.nutrition_config()["barcode_sources"]
    for name in names:
        fn = _LOOKUPS.get(name)
        if fn is None:
            continue
        for code in variants:
            try:
                hit = await fn(code)
            except Exception:  # noqa: BLE001 - a dead database must not stop the tier
                continue
            if hit is not None:
                return hit
    return None


__all__ = ["BarcodeHit", "NAME_MATCH_THRESHOLD", "code_variants", "lookup",
           "lookup_open_food_facts", "lookup_usda", "name_match_score",
           "normalize_code", "search_by_name"]
