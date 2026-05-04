from __future__ import annotations

from fastapi import APIRouter, HTTPException

from scraper.adapters import ADAPTERS

from ..queue import submit
from ..storage import get_retailer_tree

router = APIRouter()


@router.get("/tree/{retailer}")
async def get_tree(retailer: str) -> dict:
    if retailer not in ADAPTERS:
        raise HTTPException(status_code=404, detail=f"unknown retailer: {retailer}")
    tree = await get_retailer_tree(retailer)
    if tree is None:
        return {"retailer": retailer, "crawled_at": None, "tree": None}
    return {"retailer": retailer, **tree}


@router.post("/crawl/{retailer}")
async def trigger_crawl(retailer: str) -> dict:
    if retailer not in ADAPTERS:
        raise HTTPException(status_code=404, detail=f"unknown retailer: {retailer}")
    job = submit(
        "crawl",
        {"retailer": retailer},
        retailer=retailer,
        activity_label=f"crawl:{retailer}",
    )
    return {"job_id": job.id, "retailer": retailer}


@router.get("/retailers")
async def list_retailers() -> dict:
    return {"retailers": sorted(ADAPTERS.keys())}
