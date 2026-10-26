"""Tests for the extraction ladder: escalation, rejection and provenance.

Every tier is stubbed, so these run offline and assert on the ladder's own
decisions rather than on any retailer's or database's current data.
"""
from __future__ import annotations

import pytest

from scraper.extract import barcode_db, label_finder, ladder, ocr

COMPLETE = {
    "serving_size": "2 tbsp (32g)",
    "calories": 180,
    "fat_g": 15,
    "carbs_g": 7,
    "protein_g": 7,
    "sodium_mg": 130,
}
# Macros that come to ~98 kcal against a stated 180: complete, but wrong.
INCONSISTENT = {
    "serving_size": "2 tbsp",
    "calories": 180,
    "fat_g": 7.5,
    "carbs_g": 3.5,
    "protein_g": 4,
    "sodium_mg": 65,
}

ALL_TIERS = ["page_html", "barcode_db", "name_db", "ocr", "local_vision", "claude"]


def source_for(result, name: str) -> dict:
    """The provenance entry a tier wrote, with a readable failure if absent."""
    for entry in result.sources:
        if entry["source"] == name:
            return entry
    raise AssertionError(
        f"no {name!r} entry; tiers that ran: {[s['source'] for s in result.sources]}"
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Nothing in these tests may reach the network, a model, or an OCR engine."""
    async def no_barcode(*args, **kwargs):
        return None

    async def no_name(*args, **kwargs):
        return None

    async def no_label(*args, **kwargs):
        return label_finder.LabelResult()

    monkeypatch.setattr(barcode_db, "lookup", no_barcode)
    monkeypatch.setattr(barcode_db, "search_by_name", no_name)
    monkeypatch.setattr(label_finder, "find_label", no_label)
    monkeypatch.setattr(ocr, "available", lambda: False)


class TestEscalation:
    @pytest.mark.asyncio
    async def test_a_good_page_record_stops_at_tier_one(self):
        result = await ladder.extract_nutrition(
            {"name": "x", "images": []}, page_nutrition=COMPLETE, tiers=ALL_TIERS
        )
        assert result.confidence == 1.0
        assert result.tiers_used == ["page_html"]
        # Later tiers were never reached, so they left no entry.
        assert [s["source"] for s in result.sources] == ["page_html"]

    @pytest.mark.asyncio
    async def test_an_empty_page_walks_every_tier(self):
        result = await ladder.extract_nutrition(
            {"name": "x", "images": []}, page_nutrition={}, tiers=ALL_TIERS
        )
        assert result.nutrition == {}
        assert result.confidence == 0.0
        assert [s["source"] for s in result.sources] == ALL_TIERS
        assert all(not s["accepted"] for s in result.sources)

    @pytest.mark.asyncio
    async def test_a_thin_page_record_escalates_and_is_completed(self, monkeypatch):
        async def hit(*args, **kwargs):
            return barcode_db.BarcodeHit(
                source="open_food_facts", code="012345678905",
                nutrition=COMPLETE, name="Peanut Butter", brand="Acme",
            )

        monkeypatch.setattr(barcode_db, "lookup", hit)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            page_nutrition={"calories": 180},
            tiers=ALL_TIERS,
        )
        assert result.tiers_used == ["page_html", "barcode_db"]
        assert result.nutrition["protein_g"] == 7
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_an_inconsistent_record_does_not_satisfy_the_threshold(self, monkeypatch):
        async def hit(*args, **kwargs):
            return barcode_db.BarcodeHit(
                source="open_food_facts", code="012345678905",
                nutrition=INCONSISTENT, name="Peanut Butter", brand="Acme",
            )

        monkeypatch.setattr(barcode_db, "lookup", hit)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            page_nutrition={}, tiers=ALL_TIERS,
        )
        # Accepted (it is the only data) but not trusted, so the ladder ran on.
        assert "barcode_db" in result.tiers_used
        assert result.confidence <= 0.5
        assert len(result.sources) == len(ALL_TIERS), "every later tier was tried"


class TestTrustOrder:
    @pytest.mark.asyncio
    async def test_the_page_outranks_a_database(self, monkeypatch):
        async def hit(*args, **kwargs):
            return barcode_db.BarcodeHit(
                source="open_food_facts", code="1",
                nutrition={**COMPLETE, "calories": 250, "fiber_g": 2},
            )

        monkeypatch.setattr(barcode_db, "lookup", hit)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            page_nutrition={"calories": 180, "fat_g": 15},
            tiers=ALL_TIERS,
        )
        assert result.nutrition["calories"] == 180, "the page's value is kept"
        assert result.nutrition["fiber_g"] == 2, "gaps are still filled"

    @pytest.mark.asyncio
    async def test_a_disagreement_is_recorded(self, monkeypatch):
        async def hit(*args, **kwargs):
            return barcode_db.BarcodeHit(
                source="open_food_facts", code="1",
                nutrition={**COMPLETE, "calories": 250},
            )

        monkeypatch.setattr(barcode_db, "lookup", hit)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            # Too thin to accept, so the database tier runs and overlaps it.
            page_nutrition={"calories": 180, "fat_g": 15},
            tiers=ALL_TIERS,
        )
        conflicts = {c["field"]: c for c in result.conflicts}
        assert conflicts["calories"]["kept"] == 180
        assert conflicts["calories"]["rejected"] == 250
        assert conflicts["calories"]["from"] == "barcode_db"
        assert result.nutrition["calories"] == 180

    @pytest.mark.asyncio
    async def test_a_tier_that_adds_nothing_is_marked_as_such(self, monkeypatch):
        async def hit(*args, **kwargs):
            # Same fields, different values: corroboration at best.
            return barcode_db.BarcodeHit(
                source="open_food_facts", code="1",
                nutrition={"fat_g": 90, "carbs_g": 90, "protein_g": 90},
            )

        monkeypatch.setattr(barcode_db, "lookup", hit)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            page_nutrition=COMPLETE,
            tiers=["page_html", "barcode_db"],
            # Never satisfied, so the later tier runs even though tier 1 was good.
            accept_confidence=1.1,
        )
        entry = source_for(result, "barcode_db")
        assert entry["accepted"] is False
        assert "already set" in entry["rejected_reason"]
        assert result.nutrition["fat_g"] == 15
        # The disagreement is still on the record.
        assert {c["field"] for c in result.conflicts} == {"fat_g", "carbs_g", "protein_g"}

    @pytest.mark.asyncio
    async def test_a_tier_that_would_degrade_the_record_is_rejected(self, monkeypatch):
        async def hit(*args, **kwargs):
            # A new field, but one that cannot be true beside fat_g 15.
            return barcode_db.BarcodeHit(
                source="open_food_facts", code="1",
                nutrition={"saturated_fat_g": 90},
            )

        monkeypatch.setattr(barcode_db, "lookup", hit)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            page_nutrition=COMPLETE,
            tiers=["page_html", "barcode_db"],
            accept_confidence=1.1,
        )
        entry = source_for(result, "barcode_db")
        assert entry["accepted"] is False
        assert "lower record confidence" in entry["rejected_reason"]
        assert "saturated_fat_g" not in result.nutrition
        assert result.confidence == 1.0, "the good record is untouched"


class TestProvenance:
    @pytest.mark.asyncio
    async def test_every_tier_records_what_it_found_and_when(self, monkeypatch):
        async def hit(*args, **kwargs):
            return barcode_db.BarcodeHit(
                source="usda_fdc", code="012345678905", nutrition=COMPLETE,
                name="Creamy Peanut Butter", brand="Acme",
            )

        monkeypatch.setattr(barcode_db, "lookup", hit)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            page_nutrition={}, tiers=ALL_TIERS,
        )
        entry = source_for(result, "barcode_db")
        assert entry["tier"] == 2
        assert entry["database"] == "usda_fdc"
        assert entry["code"] == "012345678905"
        assert entry["matched_name"] == "Creamy Peanut Butter"
        assert entry["fields"] == COMPLETE
        assert entry["extracted_at"].endswith("Z")
        assert entry["field_score"]["completeness"] == 1.0

    @pytest.mark.asyncio
    async def test_a_skipped_tier_says_why(self):
        result = await ladder.extract_nutrition(
            {"name": "x", "images": []}, page_nutrition={}, tiers=ALL_TIERS
        )
        reasons = {s["source"]: s.get("skipped") for s in result.sources}
        assert reasons["ocr"] == "no OCR engine installed"
        assert reasons["claude"] == "claude tier disabled"

    @pytest.mark.asyncio
    async def test_a_failing_tier_is_recorded_not_raised(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("database unreachable")

        monkeypatch.setattr(barcode_db, "lookup", boom)
        result = await ladder.extract_nutrition(
            {"name": "x", "upc": "012345678905", "images": []},
            page_nutrition=COMPLETE, tiers=["barcode_db", "page_html"],
        )
        entry = source_for(result, "barcode_db")
        assert "database unreachable" in entry["error"]
        assert result.confidence == 1.0, "the remaining tiers still ran"


class TestOcrTier:
    @pytest.mark.asyncio
    async def test_the_panel_photo_is_read_and_identified(self, monkeypatch):
        panel = (
            "Nutrition Facts Serving size 2 Tbsp (32g) Calories 180 "
            "Total Fat15g 19% Sodium130mg 6% Total Carbohydrate7g 3% Protein 7g"
        )

        async def found(images, root, **kwargs):
            return label_finder.LabelResult(
                index=1,
                local_path="data/images/x_1.jpg",
                public_url="https://cdn.example/x_1.jpg",
                text=panel,
                likeness=1.0,
                ocr_text_by_index={0: "front of pack", 1: panel},
                likeness_by_index={0: 0.0, 1: 1.0},
            )

        monkeypatch.setattr(label_finder, "find_label", found)
        monkeypatch.setattr(ocr, "available", lambda: True)

        result = await ladder.extract_nutrition(
            {"name": "x", "images": [{"local_path": "a"}, {"local_path": "b"}]},
            page_nutrition={}, tiers=["page_html", "ocr"],
        )
        assert result.tiers_used == ["ocr"]
        assert result.nutrition["calories"] == 180
        assert result.nutrition["fat_g"] == 15
        assert result.confidence == 1.0
        assert result.label_image["image_index"] == 1
        assert result.label_image["panel_likeness"] == 1.0
        assert result.image_ocr["1"] == panel

        entry = source_for(result, "ocr")
        assert entry["image"] == "data/images/x_1.jpg"
        assert entry["raw_text"] == panel

    @pytest.mark.asyncio
    async def test_no_panel_found_is_reported(self, monkeypatch):
        async def nothing(images, root, **kwargs):
            return label_finder.LabelResult(
                ocr_text_by_index={0: "front of pack"},
                likeness_by_index={0: 0.0},
            )

        monkeypatch.setattr(label_finder, "find_label", nothing)
        monkeypatch.setattr(ocr, "available", lambda: True)
        result = await ladder.extract_nutrition(
            {"name": "x", "images": [{"local_path": "a"}]},
            page_nutrition={}, tiers=["ocr"],
        )
        entry = result.sources[0]
        assert entry["skipped"] == "no image looked like a nutrition panel"
        assert result.label_image is None
