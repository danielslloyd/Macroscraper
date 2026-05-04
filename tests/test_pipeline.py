"""End-to-end pipeline integration test.

Drives the full crawl -> scrape -> clean pipeline against the bundled
Whole Foods fixtures, redirecting `data/` to a tmp dir and stubbing the
Ollama HTTP client so the test is fully offline.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cleaner import runner as cleaner_runner
from orchestrator import storage
from scraper.adapters.whole_foods import WholeFoodsAdapter
from scraper.crawler import crawl, scrape_product
from scraper.downloader import save_html_snapshot

WHOLE_FOODS_BASE = "https://www.wholefoodsmarket.com"
PRODUCT_URL = f"{WHOLE_FOODS_BASE}/product/organic-whole-milk-half-gallon"
RELATED_URL = f"{WHOLE_FOODS_BASE}/product/organic-2-percent-milk-half-gallon"


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "MASTER_PATH", data_dir / "master.json")
    monkeypatch.setattr(storage, "IMAGES_DIR", data_dir / "images")
    monkeypatch.setattr(storage, "LABELS_DIR", data_dir / "labels")
    monkeypatch.setattr(storage, "HTML_DIR", data_dir / "html")
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    storage.ensure_dirs()
    return tmp_path


@pytest.mark.asyncio
async def test_crawl_builds_tree(tmp_storage):
    adapter = WholeFoodsAdapter()
    adapter.entry_urls = [WHOLE_FOODS_BASE + "/products"]
    tree = await crawl(adapter, max_depth=4)
    assert tree["kind"] == "category"
    # Verify at least one product leaf was found.
    leaves = _collect_products(tree)
    assert any("organic-whole-milk" in u for u in leaves), leaves


@pytest.mark.asyncio
async def test_scrape_populates_record(tmp_storage):
    adapter = WholeFoodsAdapter()
    html, _ = await scrape_product(adapter, PRODUCT_URL)
    parsed = adapter.parse_product(PRODUCT_URL, html)

    assert parsed.name.startswith("Organic Whole Milk")
    assert parsed.brand == "Straus Family Creamery"
    assert parsed.price.get("package_price") == 6.99
    assert parsed.image_urls, "expected at least one image"
    assert parsed.nutrition.get("calories") == 150
    assert parsed.nutrition.get("protein_g") == 8
    assert any("2-percent" in s["public_url"] for s in parsed.similar_items)

    pid = storage.product_id_for("whole_foods", PRODUCT_URL)
    rel = save_html_snapshot(pid, html)
    assert (tmp_storage / rel).exists()


@pytest.mark.asyncio
async def test_clean_runs_models_sequentially(tmp_storage, monkeypatch):
    pid = storage.product_id_for("whole_foods", PRODUCT_URL)
    related_pid = storage.product_id_for("whole_foods", RELATED_URL)

    await storage.patch_product(
        pid,
        {
            "product_id": pid,
            "retailer": "whole_foods",
            "url": PRODUCT_URL,
            "status": "scraped",
            "name": "Organic Whole Milk",
            "brand": "Straus",
            "nutrition": {"calories": 150},
            "similar_items": [
                {"name": "2%", "public_url": RELATED_URL, "product_id": None},
            ],
        },
    )
    await storage.patch_product(
        related_pid,
        {
            "product_id": related_pid,
            "retailer": "whole_foods",
            "url": RELATED_URL,
            "status": "scraped",
        },
    )

    monkeypatch.setattr(
        cleaner_runner,
        "load_models",
        lambda: [
            {"name": "llama3.1:8b", "vision": False},
            {"name": "qwen2.5-vl:7b", "vision": True},
        ],
    )

    call_order: list[str] = []

    async def fake_call(client, model, prompt, image):
        call_order.append(model)
        return {"raw": json.dumps({"name": "Organic Whole Milk"}), "parsed": {"name": "Organic Whole Milk"}}

    monkeypatch.setattr(cleaner_runner, "_call_ollama", fake_call)

    cleaner_runner.CONFIG_PATH = tmp_storage / "cleaner_config.json"
    cleaner_runner.CONFIG_PATH.write_text("{}", encoding="utf-8")

    result = await cleaner_runner.clean_product(pid)

    assert call_order == ["llama3.1:8b", "qwen2.5-vl:7b"], "models must run sequentially in config order"
    assert result["status"] == "cleaned"
    assert len(result["cleaning_runs"]) == 2

    # Bidirectional related link populated from similar_items[].
    assert related_pid in (result.get("related_products") or [])
    other = await storage.get_product(related_pid)
    assert pid in (other.get("related_products") or [])

    # Similar item product_id populated.
    assert result["similar_items"][0]["product_id"] == related_pid


def _collect_products(node: dict) -> list[str]:
    out: list[str] = []
    if node.get("kind") == "product":
        out.append(node.get("url", ""))
    for c in node.get("children") or []:
        out.extend(_collect_products(c))
    return out
