from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..storage import get_product, load_master, patch_product

router = APIRouter()


@router.get("/product/{product_id}")
async def fetch_product(product_id: str) -> dict:
    product = await get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"product {product_id} not found")
    return product


@router.get("/products")
async def list_products(retailer: str | None = None, status: str | None = None) -> dict:
    data = await load_master()
    out = []
    for pid, prod in data.get("products", {}).items():
        if retailer and prod.get("retailer") != retailer:
            continue
        if status and prod.get("status") != status:
            continue
        out.append(
            {
                "product_id": pid,
                "retailer": prod.get("retailer"),
                "name": prod.get("name"),
                "status": prod.get("status"),
                "url": prod.get("url"),
            }
        )
    return {"products": out}


class FlagRequest(BaseModel):
    flagged: bool = True


@router.post("/product/{product_id}/flag")
async def flag_product(product_id: str, req: FlagRequest) -> dict:
    product = await get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"product {product_id} not found")
    new_status = "flagged" if req.flagged else (product.get("prior_status") or "scraped")
    await patch_product(product_id, {"status": new_status})
    return {"product_id": product_id, "status": new_status}
