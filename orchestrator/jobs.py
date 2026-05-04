"""Job handlers — bridges the queue to the scraper and cleaner modules.

The queue knows nothing about scraping or cleaning; it just calls registered
handlers. This module owns the bridging logic: turning a payload into the
right module call, then patching master.json with the result.
"""
from __future__ import annotations

from cleaner.runner import clean_product
from scraper.adapters import get_adapter
from scraper.crawler import crawl as crawl_adapter
from scraper.crawler import scrape_product
from scraper.downloader import download_images, download_label, save_html_snapshot

from .queue import Job, register_handler
from .storage import (
    patch_product,
    product_id_for,
    set_status,
    utcnow_iso,
)


async def _handle_crawl(job: Job) -> None:
    retailer = job.payload["retailer"]
    job.retailer = retailer
    adapter = get_adapter(retailer)
    await crawl_adapter(adapter)


async def _handle_scrape(job: Job) -> None:
    retailer = job.payload["retailer"]
    url = job.payload["url"]
    force = bool(job.payload.get("force"))
    pid = product_id_for(retailer, url)
    job.product_id = pid
    job.retailer = retailer

    from .storage import get_product
    existing = await get_product(pid)
    if existing and existing.get("status") in {"scraped", "cleaning", "cleaned"} and not force:
        return

    adapter = get_adapter(retailer)
    html, _ = await scrape_product(adapter, url)
    parsed = adapter.parse_product(url, html)

    html_rel = save_html_snapshot(pid, html)
    images = await download_images(pid, parsed.image_urls)
    label = await download_label(pid, parsed.nutrition_label_url)

    record = {
        "product_id": pid,
        "retailer": retailer,
        "url": url,
        "status": "scraped",
        "scraped_at": utcnow_iso(),
        "name": parsed.name,
        "brand": parsed.brand,
        "price": parsed.price,
        "images": images,
        "nutrition_label_image": label,
        "nutrition": parsed.nutrition,
        "raw_html_snapshot": html_rel,
        "similar_items": parsed.similar_items,
    }
    await patch_product(pid, record)


async def _handle_clean(job: Job) -> None:
    pid = job.payload["product_id"]
    job.product_id = pid
    try:
        await clean_product(pid)
    except Exception:
        await set_status(pid, "scraped")
        raise


def register_all() -> None:
    register_handler("crawl", _handle_crawl)
    register_handler("scrape", _handle_scrape)
    register_handler("clean", _handle_clean)
