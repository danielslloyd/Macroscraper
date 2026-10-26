"""The nutrition extraction ladder.

Walks the configured tiers in order, cheapest and most authoritative first,
and stops as soon as the accumulated record is complete and self-consistent
enough to accept. Most products are finished by tier 1 or 2; a model is
reached only for the ones where the numbers exist nowhere but a photograph.

Two rules keep the result trustworthy:

  * An earlier tier's value is never overwritten by a later one. A retailer's
    own structured data outranks a community database, which outranks OCR,
    which outranks a model's reading.
  * A tier's contribution is rejected if it would make the record *worse* --
    less consistent under the calorie cross-check in `nutrients.score`. Bad
    data in a public database therefore cannot poison a product record; it is
    still written to `nutrition_sources[]`, marked as not accepted, so the
    decision is auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import barcode_db, label_finder, nutrients, ocr

TIER_NUMBERS = {
    "page_html": 1,
    "barcode_db": 2,
    "name_db": 2,
    "ocr": 3,
    "local_vision": 4,
    "claude": 5,
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class NutritionResult:
    nutrition: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    score: dict = field(default_factory=dict)
    label_image: dict | None = None
    image_ocr: dict[str, str] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    gtin: str | None = None

    @property
    def tiers_used(self) -> list[str]:
        return [s["source"] for s in self.sources if s.get("accepted")]


def _record_conflicts(
    accumulated: dict, incoming: dict, source: str, into: list[dict]
) -> None:
    """Note where a later tier disagrees with a value we already trust."""
    for key, value in incoming.items():
        if key in ("serving_size", "basis"):
            continue
        existing = accumulated.get(key)
        if existing in (None, ""):
            continue
        a, b = nutrients.to_number(existing), nutrients.to_number(value)
        if a is None or b is None:
            continue
        tolerance = max(0.5, abs(a) * 0.1)
        if abs(a - b) > tolerance:
            into.append({"field": key, "kept": existing, "rejected": value,
                         "from": source})


class _Tiers:
    """One method per tier. Each returns (fields, extra_metadata)."""

    def __init__(self, product: dict, page_nutrition: dict, root: Path) -> None:
        self.product = product
        self.page_nutrition = page_nutrition
        self.root = root
        self.label: label_finder.LabelResult | None = None

    async def page_html(self) -> tuple[dict, dict]:
        return nutrients.normalize_mapping(self.page_nutrition or {}), {}

    async def barcode_db(self) -> tuple[dict, dict]:
        code = self.product.get("gtin") or self.product.get("upc")
        hit = await barcode_db.lookup(code)
        if hit is None:
            return {}, {"code_tried": barcode_db.code_variants(code)}
        return hit.nutrition, {
            "database": hit.source,
            "code": hit.code,
            "matched_name": hit.name,
            "matched_brand": hit.brand,
            "per_serving": hit.per_serving,
            "label_image_url": hit.label_image_url,
        }

    async def name_db(self) -> tuple[dict, dict]:
        """Open Food Facts by name and brand, for products with no barcode.

        Publix publishes no UPC at all and some Costco records leave it empty,
        so without this tier those products would skip straight to reading a
        photograph. The match is gated on brand agreement and a token-overlap
        threshold, and the score travels with the record.
        """
        name = self.product.get("name") or ""
        brand = self.product.get("brand") or ""
        if not name:
            return {}, {"skipped": "no product name to match on"}
        hit = await barcode_db.search_by_name(name, brand)
        if hit is None:
            return {}, {"database": "open_food_facts_name", "matched": False,
                        "threshold": barcode_db.NAME_MATCH_THRESHOLD}
        return hit.nutrition, {
            "database": hit.source,
            "code": hit.code,
            "matched_name": hit.name,
            "matched_brand": hit.brand,
            "match_score": hit.match_score,
            "per_serving": hit.per_serving,
            "label_image_url": hit.label_image_url,
        }

    async def _ensure_label(self) -> label_finder.LabelResult:
        if self.label is None:
            self.label = await label_finder.find_label(
                self.product.get("images") or [], self.root
            )
        return self.label

    async def ocr(self) -> tuple[dict, dict]:
        if not ocr.available():
            return {}, {"skipped": "no OCR engine installed"}
        label = await self._ensure_label()
        meta = {
            "engine": config.nutrition_config()["ocr_engine"],
            "images_read": len(label.ocr_text_by_index),
            "panel_likeness": label.likeness_by_index,
        }
        if not label.found:
            meta["skipped"] = "no image looked like a nutrition panel"
            return {}, meta
        meta.update({
            "image_index": label.index,
            "image": label.local_path,
            "raw_text": label.text,
        })
        return nutrients.parse_panel_text(label.text), meta

    async def _panel_image(self) -> str | None:
        """The best image to hand a vision model."""
        label = await self._ensure_label()
        if label.local_path:
            return label.local_path
        images = self.product.get("images") or []
        # Without a detected panel, the second photo is the usual back-of-pack.
        for image in images[1:2] or images[:1]:
            if image.get("local_path"):
                return image["local_path"]
        return None

    async def local_vision(self) -> tuple[dict, dict]:
        from cleaner import vision

        image = await self._panel_image()
        if not image:
            return {}, {"skipped": "no local image to read"}
        if not await vision.model_available():
            return {}, {"skipped": "ollama vision model unavailable"}
        out = await vision.read_panel(self.root / image)
        meta = {"model": out.get("model"), "image": image}
        if out.get("error"):
            meta["error"] = out["error"]
        return nutrients.normalize_mapping(out.get("parsed") or {}), meta

    async def claude(self) -> tuple[dict, dict]:
        from cleaner import claude_vision

        if not claude_vision.enabled():
            return {}, {"skipped": "claude tier disabled"}
        image = await self._panel_image()
        if not image:
            return {}, {"skipped": "no local image to read"}
        out = await claude_vision.read_panel(self.root / image)
        meta = {"model": out.get("model"), "image": image}
        if out.get("error"):
            meta["error"] = out["error"]
        return nutrients.normalize_mapping(out.get("parsed") or {}), meta


async def extract_nutrition(
    product: dict,
    *,
    page_nutrition: dict | None = None,
    root: Path | None = None,
    tiers: list[str] | None = None,
    accept_confidence: float | None = None,
) -> NutritionResult:
    """Build the best nutrition record this product's data supports."""
    cfg = config.nutrition_config()
    order = tiers if tiers is not None else cfg["tiers"]
    threshold = cfg["accept_confidence"] if accept_confidence is None else accept_confidence
    root = root or config.ROOT

    runner = _Tiers(product, page_nutrition or {}, root)
    result = NutritionResult()
    accumulated: dict = {}

    for name in order:
        method = getattr(runner, name, None)
        if method is None:
            continue
        try:
            fields, meta = await method()
        except Exception as exc:  # noqa: BLE001 - a broken tier must not stop the rest
            fields, meta = {}, {"error": f"{type(exc).__name__}: {exc}"}

        entry = {
            "source": name,
            "tier": TIER_NUMBERS.get(name),
            "extracted_at": _now(),
            "fields": fields,
            "accepted": False,
            **meta,
        }

        if fields:
            own = nutrients.score(fields)
            entry["field_score"] = own.as_dict()
            candidate = nutrients.merge(accumulated, fields)
            before = nutrients.score(accumulated).value
            after = nutrients.score(candidate)
            if candidate == accumulated:
                # Every field was already set by a higher-trust tier. Worth
                # recording -- it is corroboration, and `conflicts` below says
                # whether the two sources actually agreed.
                _record_conflicts(accumulated, fields, name, result.conflicts)
                entry["rejected_reason"] = "added nothing a higher tier had not already set"
            elif after.value + 1e-9 >= before:
                _record_conflicts(accumulated, fields, name, result.conflicts)
                accumulated = candidate
                entry["accepted"] = True
                entry["record_score"] = after.as_dict()
            else:
                entry["rejected_reason"] = (
                    f"would lower record confidence from {before:.2f} to {after.value:.2f}"
                )
        result.sources.append(entry)

        current = nutrients.score(accumulated)
        if current.value >= threshold:
            break

    final = nutrients.score(accumulated)
    result.nutrition = accumulated
    result.confidence = round(final.value, 3)
    result.score = final.as_dict()
    result.gtin = product.get("gtin") or product.get("upc")

    if runner.label is not None:
        result.image_ocr = {
            str(i): text for i, text in runner.label.ocr_text_by_index.items() if text
        }
        if runner.label.found:
            result.label_image = {
                "public_url": runner.label.public_url,
                "local_path": runner.label.local_path,
                "image_index": runner.label.index,
                "panel_likeness": round(runner.label.likeness, 3),
            }
    return result


__all__ = ["NutritionResult", "TIER_NUMBERS", "extract_nutrition"]
