from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..queue import submit
from ..storage import get_product

router = APIRouter()


class CleanRequest(BaseModel):
    product_ids: list[str]


@router.post("/clean")
async def queue_clean(req: CleanRequest) -> dict:
    if not req.product_ids:
        raise HTTPException(status_code=400, detail="product_ids must be non-empty")
    queued = []
    for pid in req.product_ids:
        product = await get_product(pid)
        if product is None:
            queued.append({"product_id": pid, "skipped": "not found"})
            continue
        if product.get("status") not in {"scraped", "cleaned", "flagged"}:
            queued.append({"product_id": pid, "skipped": f"status={product.get('status')}"})
            continue
        job = submit(
            "clean",
            {"product_id": pid},
            product_id=pid,
            retailer=product.get("retailer"),
            activity_label=f"clean:{pid}",
        )
        queued.append({"job_id": job.id, "product_id": pid})
    return {"queued": queued}
