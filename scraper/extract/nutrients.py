"""Canonical nutrient vocabulary, panel-text parsing, and validation.

This module is the deterministic heart of nutrition extraction: no network, no
models, no retailer-specific knowledge. It turns a blob of nutrition-panel text
-- from a web page, an OCR pass, or a model -- into a canonical nutrition map,
and it scores how complete and self-consistent that map is.

The nutrition map stays a flexible key-value dict per the project spec. The
keys below are the ones we normalize *to*; unrecognized nutrients are kept
under a slugified key rather than discarded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# canonical_key -> (unit, aliases). Aliases are matched case-insensitively with
# optional trailing periods, so "Total Carb." matches "total carb".
_NUTRIENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "calories": ("kcal", ("calories", "calorie", "energy", "kcal")),
    "fat_g": ("g", ("total fat", "fat", "total lipid")),
    "saturated_fat_g": ("g", ("saturated fat", "sat fat", "sat. fat", "saturates")),
    "trans_fat_g": ("g", ("trans fat", "trans")),
    "polyunsaturated_fat_g": ("g", ("polyunsaturated fat", "polyunsat fat")),
    "monounsaturated_fat_g": ("g", ("monounsaturated fat", "monounsat fat")),
    "cholesterol_mg": ("mg", ("cholesterol", "cholest")),
    "sodium_mg": ("mg", ("sodium", "salt")),
    "carbs_g": (
        "g",
        ("total carbohydrate", "total carbohydrates", "total carb", "carbohydrate",
         "carbohydrates", "carbs", "carb"),
    ),
    "fiber_g": ("g", ("dietary fiber", "fiber", "fibre", "total fiber")),
    "sugars_g": ("g", ("total sugars", "sugars", "sugar")),
    "added_sugars_g": ("g", ("added sugars", "incl added sugars", "includes added sugars")),
    "sugar_alcohol_g": ("g", ("sugar alcohol", "sugar alcohols")),
    "protein_g": ("g", ("protein",)),
    "vitamin_d_mcg": ("mcg", ("vitamin d", "vit d")),
    "calcium_mg": ("mg", ("calcium",)),
    "iron_mg": ("mg", ("iron",)),
    "potassium_mg": ("mg", ("potassium", "potas")),
    "vitamin_a_mcg": ("mcg", ("vitamin a", "vit a")),
    "vitamin_c_mg": ("mg", ("vitamin c", "vit c")),
}

# Nutrients we expect on essentially every US Nutrition Facts panel. Used to
# score completeness and decide whether to escalate to the next tier.
CORE_FIELDS = (
    "serving_size",
    "calories",
    "fat_g",
    "carbs_g",
    "protein_g",
    "sodium_mg",
)

# Unit conversions into each canonical key's unit.
_TO_GRAMS = {"g": 1.0, "gram": 1.0, "grams": 1.0, "mg": 0.001, "mcg": 1e-6, "ug": 1e-6}
_TO_MG = {"mg": 1.0, "g": 1000.0, "gram": 1000.0, "grams": 1000.0, "mcg": 0.001, "ug": 0.001}
_TO_MCG = {"mcg": 1.0, "ug": 1.0, "mg": 1000.0, "g": 1e6}

_UNIT_TABLES = {"g": _TO_GRAMS, "mg": _TO_MG, "mcg": _TO_MCG}

# A panel amount. `O` is accepted as zero: OCR reliably reads the printed "0g"
# of "Trans Fat 0g" as "Og", and only ever next to a unit, so this costs
# nothing in precision.
_NUM = r"(\d+(?:[.,]\d+)?|[Oo])"
_UNIT = r"(g|mg|mcg|ug|gram|grams)"
# Panels reach us as table cells joined by pipes, bullets or wide gaps, so a
# nutrient's label and its amount are rarely adjacent in the plain text.
_SEP = r"[\s:\-|\u00b7\u2022*]{0,8}"

# Generic aliases that would otherwise capture a more specific nutrient's
# value: a bare "fat" matches inside "Saturated Fat 2.5g", and a bare "sugars"
# inside "Added Sugars 2g". Each guard becomes a negative lookbehind, so they
# must be fixed-width strings.
_PREFIX_GUARDS: dict[str, tuple[str, ...]] = {
    "fat": ("saturated ", "sat ", "sat. ", "trans ", "unsaturated ",
            "polyunsaturated ", "monounsaturated ", "total "),
    "sugars": ("added ", "incl. ", "incl ", "includes ", "total "),
    "sugar": ("added ", "incl. ", "incl ", "includes "),
    "fiber": ("insoluble ", "soluble "),
    "carb": ("net ",),
}

# Alias -> canonical. Longest aliases first so "saturated fat" wins over "fat".
_ALIAS_TO_KEY: list[tuple[str, str]] = sorted(
    ((alias, key) for key, (_u, aliases) in _NUTRIENTS.items() for alias in aliases),
    key=lambda pair: -len(pair[0]),
)


def unit_for(key: str) -> str | None:
    entry = _NUTRIENTS.get(key)
    return entry[0] if entry else None


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def to_number(value) -> float | None:
    """Coerce a scalar or a string like '15g' / '1,200 mg' to a float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in ("O", "o"):
        return 0.0
    m = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _tidy(value: float) -> float | int:
    rounded = round(value, 3)
    return int(rounded) if float(rounded).is_integer() else rounded


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert between mass units. Returns None when the units are incompatible."""
    if to_unit is None or to_unit == "kcal":
        return value
    if not from_unit or from_unit.lower() == to_unit:
        return value
    table = _UNIT_TABLES.get(to_unit)
    if not table:
        return value
    factor = table.get(from_unit.lower())
    return value * factor if factor is not None else None


def canonical_key(label: str) -> str | None:
    """Map a free-text nutrient label onto a canonical key."""
    cleaned = re.sub(r"[^a-z ]+", " ", label.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    for alias, key in _ALIAS_TO_KEY:
        if re.search(rf"\b{re.escape(alias)}\b", cleaned):
            return key
    return None


def slug_key(label: str) -> str:
    """Fallback key for a nutrient we have no canonical name for."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug or "unknown"


# ---------------------------------------------------------------------------
# Panel-text parsing
# ---------------------------------------------------------------------------

_SERVING_LABEL = re.compile(r"serv(?:ing)?\.?\s*size\b", re.IGNORECASE)

# Units a serving is actually measured in. Tried before bare grams, because the
# text between "Serv. size" and its value often contains other nutrients'
# amounts -- OCR of a two-column panel interleaves them.
_SERVING_UNITS = (
    r"tbsp|tbs|tablespoons?|tsp|teaspoons?|cups?|fl\.?\s*oz|fluid\s*ounces?|oz|ounces?|"
    r"ml|milliliters?|l\b|liters?|pieces?|slices?|bars?|packets?|pouch(?:es)?|"
    r"cookies?|crackers?|chips?|cans?|bottles?|containers?|scoops?|patt(?:y|ies)|"
    r"links?|eggs?|wraps?|tortillas?|nuggets?|servings?|bags?|boxes|sticks?|"
    r"squares?|muffins?|bagels?|rolls?|cakes?"
)
_SERVING_VALUE = re.compile(
    rf"(\d+(?:[./]\d+)?(?:\s+\d/\d)?\s*(?:{_SERVING_UNITS})\b\.?"
    rf"(?:\s*\(\s*[^)]{{1,30}}\))?)",
    re.IGNORECASE,
)
_SERVING_VALUE_GRAMS = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:g|grams?)\b(?:\s*\(\s*[^)]{1,30}\))?)", re.IGNORECASE
)
# How far past the label to look for the value.
_SERVING_WINDOW = 140

_SERVINGS_PER = re.compile(
    r"(?:about\s*)?(\d+(?:\.\d+)?)" + _SEP + r"servings?\s*per\s*container", re.IGNORECASE
)
_SERVINGS_PER_ALT = re.compile(
    r"servings?\s*per\s*container" + _SEP + r"(?:about\s*)?(\d+(?:\.\d+)?)", re.IGNORECASE
)

# "Calories 180" — but not "Calories from Fat 60", and not a gram amount. The
# trailing `(?![\d.,])` stops the match being truncated to "18" when a later
# guard fails; without it the regex happily backtracks into a wrong number.
_CALORIES = re.compile(
    r"calories(?!\s*from)" + _SEP + r"(\d{1,4})(?![\d.,])(?!\s*(?:g|mg|mcg)\b)",
    re.IGNORECASE,
)
# "180 Calories". The lookbehind keeps the 2,000-calorie footnote from being
# read as a serving's calories.
_CALORIES_TRAILING = re.compile(r"(?<![\d.,])(\d{2,4})\s*calories\b", re.IGNORECASE)

# Panels print "Incl. 2g Added Sugars" with the value *before* the label.
_VALUE_FIRST = re.compile(
    rf"(?:incl\.?|includes)" + _SEP + rf"{_NUM}\s*{_UNIT}" + _SEP + r"added\s*sugars",
    re.IGNORECASE,
)


def _match_nutrient(text: str, aliases: tuple[str, ...]) -> tuple[float, str] | None:
    """Find `alias <number><unit>` anywhere in the text.

    Requiring an explicit mass unit is what keeps % Daily Value columns from
    being mistaken for amounts: panels print "Sodium 140mg 6%", and only the
    first number carries a unit.

    The alias may run straight into its value -- OCR of a tight panel yields
    "Total Fat16g" with no space -- so the label ends on "not another letter"
    rather than on a word boundary, which a following digit would not satisfy.
    """
    for alias in aliases:
        guards = "".join(
            f"(?<!{re.escape(word)})" for word in _PREFIX_GUARDS.get(alias, ())
        )
        pattern = re.compile(
            guards + rf"\b{re.escape(alias)}(?![A-Za-z])\.?" + _SEP
            + rf"(?:less than\s*)?{_NUM}\s*{_UNIT}(?![A-Za-z])",
            re.IGNORECASE,
        )
        m = pattern.search(text)
        if m:
            value = to_number(m.group(1))
            if value is not None:
                return value, m.group(2)
    return None


def _find_serving_size(text: str) -> str | None:
    """Pull the serving size out of panel text.

    Looks in a window after each "Serving size" label for a quantity in a
    serving-sized unit (2 Tbsp, 1 cup, 3 cookies), falling back to a plain gram
    weight. Grams are the last resort because the window frequently contains
    other nutrients' gram amounts when OCR interleaves panel columns.
    """
    for label in _SERVING_LABEL.finditer(text):
        window = text[label.end():label.end() + _SERVING_WINDOW]
        for pattern in (_SERVING_VALUE, _SERVING_VALUE_GRAMS):
            m = pattern.search(window)
            if m:
                value = re.sub(r"\s+", " ", m.group(1)).strip(" :;|-")
                if value and any(ch.isdigit() for ch in value):
                    return value
    return None


def parse_panel_text(text: str) -> dict:
    """Parse Nutrition-Facts-style text into a canonical nutrition map.

    Works on jumbled input. OCR of a two-column panel interleaves the columns,
    so this never relies on line structure -- every nutrient is found by its own
    label, independently of the others.
    """
    if not text:
        return {}
    # Normalize whitespace but keep separators that bound a serving size.
    flat = re.sub(r"[\r\n\t]+", " \n ", text)
    flat = re.sub(r"[ ]{2,}", "  ", flat)
    out: dict = {}

    serving = _find_serving_size(flat)
    if serving:
        out["serving_size"] = serving

    m = _SERVINGS_PER.search(flat) or _SERVINGS_PER_ALT.search(flat)
    if m:
        value = to_number(m.group(1))
        if value is not None:
            out["servings_per_container"] = _tidy(value)

    m = _CALORIES.search(flat) or _CALORIES_TRAILING.search(flat)
    if m:
        value = to_number(m.group(1))
        if value is not None:
            out["calories"] = _tidy(value)

    for key, (unit, aliases) in _NUTRIENTS.items():
        if key in out or key == "calories":
            continue
        found = _match_nutrient(flat, aliases)
        if not found:
            continue
        raw_value, raw_unit = found
        converted = convert(raw_value, raw_unit, unit)
        if converted is not None:
            out[key] = _tidy(converted)

    m = _VALUE_FIRST.search(flat)
    if m and "added_sugars_g" not in out:
        value = to_number(m.group(1))
        if value is not None:
            converted = convert(value, m.group(2), "g")
            if converted is not None:
                out["added_sugars_g"] = _tidy(converted)

    return out


# Fields whose value is text, not a measurement. Coercing these to numbers is
# how a serving size of "2 tbsp (32g)" silently becomes 2.
_STRING_FIELDS = {"serving_size", "basis", "household_serving"}
_SERVING_SIZE_LABEL = re.compile(r"serving[\s_-]*size|household[\s_-]*serving", re.I)


def normalize_mapping(raw: dict) -> dict:
    """Normalize an already-structured nutrient mapping (API or model output).

    Accepts `{"Total Fat": "15g"}`, `{"fat": {"value": 15, "unit": "g"}}`, or
    `{"fat_g": 15}` and returns canonical keys with canonical units.
    """
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for label, value in raw.items():
        if value is None or isinstance(value, (list, bool)):
            continue
        label_str = str(label)

        if label_str in _STRING_FIELDS:
            out[label_str] = value if isinstance(value, str) else str(value)
            continue
        if _SERVING_SIZE_LABEL.search(label_str):
            text = value.get("value", "") if isinstance(value, dict) else value
            if text not in (None, ""):
                out["serving_size"] = str(text).strip()
            continue

        key = canonical_key(label_str) or (
            label_str if label_str in _NUTRIENTS else None
        )

        unit_hint: str | None = None
        if isinstance(value, dict):
            unit_hint = value.get("unit") or value.get("uom")
            value = value.get("value", value.get("amount"))
        elif isinstance(value, str):
            m = re.search(_UNIT, value, re.IGNORECASE)
            unit_hint = m.group(1) if m else None

        if key is None:
            if isinstance(value, (int, float)) or (
                isinstance(value, str) and to_number(value) is not None
            ):
                number = to_number(value)
                if number is not None:
                    out.setdefault(slug_key(label_str), _tidy(number))
            continue

        number = to_number(value)
        if number is None:
            continue
        target = unit_for(key)
        converted = convert(number, unit_hint, target)
        if converted is None:
            continue
        out[key] = _tidy(converted)
    return out


# ---------------------------------------------------------------------------
# Validation and scoring
# ---------------------------------------------------------------------------


@dataclass
class Score:
    """How complete and self-consistent a nutrition map is."""

    completeness: float          # fraction of CORE_FIELDS present, 0..1
    calorie_error: float | None  # relative error of the Atwater cross-check
    plausible: bool              # no value outside a sane range
    value: float                 # overall 0..1 confidence

    def as_dict(self) -> dict:
        return {
            "completeness": round(self.completeness, 3),
            "calorie_error": None if self.calorie_error is None
            else round(self.calorie_error, 3),
            "plausible": self.plausible,
            "value": round(self.value, 3),
        }


# Sanity bounds per 1 serving. Anything outside these is almost certainly a
# misread (a % Daily Value picked up as an amount, a decimal point lost).
_BOUNDS = {
    "calories": (0, 2000),
    "fat_g": (0, 200),
    "saturated_fat_g": (0, 150),
    "trans_fat_g": (0, 50),
    "cholesterol_mg": (0, 3000),
    "sodium_mg": (0, 10000),
    "carbs_g": (0, 300),
    "fiber_g": (0, 100),
    "sugars_g": (0, 300),
    "added_sugars_g": (0, 300),
    "protein_g": (0, 200),
}


def implausible_fields(nutrition: dict) -> list[str]:
    bad: list[str] = []
    for key, (low, high) in _BOUNDS.items():
        value = nutrition.get(key)
        number = to_number(value)
        if number is None:
            continue
        if number < low or number > high:
            bad.append(key)
    # Components cannot exceed their totals by more than a rounding step.
    for part, whole in (("saturated_fat_g", "fat_g"), ("fiber_g", "carbs_g"),
                        ("sugars_g", "carbs_g"), ("added_sugars_g", "sugars_g")):
        p, w = to_number(nutrition.get(part)), to_number(nutrition.get(whole))
        if p is not None and w is not None and p > w + 1.5:
            bad.append(part)
    return sorted(set(bad))


# Above this relative Atwater error, a record is treated as unreliable no
# matter how complete it is.
CALORIE_ERROR_LIMIT = 0.35


def calorie_error(nutrition: dict) -> float | None:
    """Relative error between stated calories and the Atwater estimate.

    4 kcal/g protein, 4 kcal/g carbohydrate, 9 kcal/g fat. Panels round and
    fiber/sugar alcohols are not fully metabolized, so even a correct panel
    lands within roughly 25%; a wildly different number means a bad read.
    """
    stated = to_number(nutrition.get("calories"))
    fat = to_number(nutrition.get("fat_g"))
    carbs = to_number(nutrition.get("carbs_g"))
    protein = to_number(nutrition.get("protein_g"))
    if stated is None or None in (fat, carbs, protein):
        return None
    estimate = 9 * fat + 4 * carbs + 4 * protein
    if stated <= 0:
        return None if estimate <= 0 else 1.0
    return abs(estimate - stated) / stated


def score(nutrition: dict) -> Score:
    """Score a nutrition map on completeness and internal consistency."""
    if not nutrition:
        return Score(0.0, None, True, 0.0)

    present = sum(1 for f in CORE_FIELDS if nutrition.get(f) not in (None, ""))
    completeness = present / len(CORE_FIELDS)

    bad = implausible_fields(nutrition)
    plausible = not bad
    err = calorie_error(nutrition)

    value = completeness
    if err is not None:
        # Full credit under 15% error, tapering to no credit at 60%.
        consistency = 1.0 if err <= 0.15 else max(0.0, 1.0 - (err - 0.15) / 0.45)
        value = 0.7 * completeness + 0.3 * consistency
        if err > CALORIE_ERROR_LIMIT:
            # Macros that cannot add up to the stated calories mean something is
            # wrong, however complete the record looks. Cap it below any sane
            # acceptance threshold so the next tier gets a turn.
            value = min(value, 0.5)
    else:
        # No cross-check available: cap confidence so a thin record still escalates.
        value = min(completeness, 0.8)
    if not plausible:
        value *= 0.4
    return Score(completeness, err, plausible, max(0.0, min(1.0, value)))


def merge(base: dict, incoming: dict, *, overwrite: bool = False) -> dict:
    """Merge `incoming` into `base`. Existing values win unless `overwrite`."""
    out = dict(base)
    for key, value in incoming.items():
        if value in (None, ""):
            continue
        if overwrite or out.get(key) in (None, ""):
            out[key] = value
    return out


def looks_like_panel(text: str) -> float:
    """0..1 score that a blob of text is a Nutrition Facts panel.

    Used to pick which product photo is the label, without a model.
    """
    if not text:
        return 0.0
    low = text.lower()
    strong = ("nutrition facts", "amount per serving", "% daily value",
              "daily value", "servings per container", "serving size",
              "amount/serving")
    hits = sum(1 for marker in strong if marker in low)
    nutrient_hits = sum(
        1 for key, (_u, aliases) in _NUTRIENTS.items()
        if any(alias in low for alias in aliases)
    )
    return min(1.0, 0.25 * hits + 0.06 * nutrient_hits)


__all__ = [
    "CORE_FIELDS",
    "Score",
    "calorie_error",
    "canonical_key",
    "convert",
    "implausible_fields",
    "looks_like_panel",
    "merge",
    "normalize_mapping",
    "parse_panel_text",
    "score",
    "to_number",
    "unit_for",
]
