from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from scraper.adapters import ADAPTERS

from ..queue import submit
from ..storage import product_id_for

router = APIRouter()


class ScrapeRequest(BaseModel):
    retailer: str
    urls: list[str]
    force: bool = False


@router.post("/scrape")
async def queue_scrape(req: ScrapeRequest) -> dict:
    if req.retailer not in ADAPTERS:
        raise HTTPException(status_code=404, detail=f"unknown retailer: {req.retailer}")
    if not req.urls:
        raise HTTPException(status_code=400, detail="urls must be non-empty")
    queued = []
    for url in req.urls:
        pid = product_id_for(req.retailer, url)
        job = submit(
            "scrape",
            {"retailer": req.retailer, "url": url, "force": req.force},
            product_id=pid,
            retailer=req.retailer,
            activity_label=f"scrape:{pid}",
        )
        queued.append({"job_id": job.id, "product_id": pid, "url": url})
    return {"queued": queued}
