from __future__ import annotations

from fastapi import APIRouter

from scraper.adapters import ADAPTERS

from ..queue import recent_activity, snapshot
from ..storage import load_master

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"ok": True}


@router.get("/status")
async def status() -> dict:
    data = await load_master()
    retailers: dict[str, dict] = {}
    for slug in ADAPTERS:
        retailers[slug] = {"crawled": 0, "scraped": 0, "cleaned": 0, "flagged": 0}
    for prod in data.get("products", {}).values():
        slug = prod.get("retailer")
        if slug not in retailers:
            retailers[slug] = {"crawled": 0, "scraped": 0, "cleaned": 0, "flagged": 0}
        st = prod.get("status")
        if st == "scraped":
            retailers[slug]["scraped"] += 1
        elif st == "cleaned":
            retailers[slug]["cleaned"] += 1
        elif st == "flagged":
            retailers[slug]["flagged"] += 1
    for slug, retailer in data.get("retailers", {}).items():
        if slug not in retailers:
            retailers[slug] = {"crawled": 0, "scraped": 0, "cleaned": 0, "flagged": 0}
        tree = retailer.get("tree") or {}
        retailers[slug]["crawled"] = _count_products_in_tree(tree)
    return {
        "backend_healthy": True,
        "queue": snapshot(),
        "retailers": retailers,
        "activity": recent_activity(limit=10),
    }


def _count_products_in_tree(node: dict) -> int:
    if not node:
        return 0
    if node.get("kind") == "product":
        return 1
    return sum(_count_products_in_tree(c) for c in node.get("children") or [])
