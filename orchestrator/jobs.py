"""Job handlers — bridges the queue to the scraper and cleaner modules.

The queue knows nothing about scraping or cleaning; it just calls registered
handlers. This module owns the bridging logic: turning a payload into the
right module call, then patching master.json with the result.

The scrape handler is where the pipeline comes together:

  1. fetch and parse the product page (or API record) through the adapter
  2. drop or flag it if the retailer is not the seller and the run asked for
     first-party items only
  3. download the gallery, keeping each photo's position and role
  4. run the extraction ladder over the record, which fills in nutrition from
     whichever tier can -- page, barcode database, OCR of the panel photo, a
     local vision model, or Claude
  5. patch one merged record into master.json, with provenance

A bot check is recorded, not retried: the product is flagged with the reason
so the UI can show what is blocked and which retailer needs an interactive
unblock, and the next scrape of it starts clean.
"""
from __future__ import annotations

from cleaner.runner import clean_product
from scraper import config
from scraper.adapters import get_adapter
from scraper.crawler import crawl as crawl_adapter
from scraper.downloader import (
    adopt_label_image,
    download_images,
    download_label,
    save_api_snapshot,
    save_html_snapshot,
)
from scraper.extract.ladder import extract_nutrition
from scraper.fetch.browser import BotWallError

from .queue import Job, register_handler
from .storage import (
    get_product,
    patch_product,
    product_id_for,
    set_status,
    utcnow_iso,
)


async def _handle_crawl(job: Job) -> None:
    retailer = job.payload["retailer"]
    job.retailer = retailer
    adapter = get_adapter(retailer)
    await crawl_adapter(adapter, max_depth=int(job.payload.get("max_depth", 4)))


async def _handle_scrape(job: Job) -> None:
    retailer = job.payload["retailer"]
    url = job.payload["url"]
    force = bool(job.payload.get("force"))
    pid = product_id_for(retailer, url)
    job.product_id = pid
    job.retailer = retailer

    existing = await get_product(pid)
    if existing and existing.get("status") in {"scraped", "cleaning", "cleaned"} and not force:
        return

    adapter = get_adapter(retailer)

    try:
        raw, parsed = await adapter.fetch_product(url)
    except BotWallError as exc:
        await patch_product(pid, {
            "product_id": pid,
            "retailer": retailer,
            "url": url,
            "status": "flagged",
            "flag_reason": "bot_wall",
            "flag_detail": str(exc),
            "flagged_at": utcnow_iso(),
        })
        raise

    # Defence in depth behind the bot-wall check: an interstitial or an error
    # page parses cleanly as "a product with nothing in it", and storing that
    # as a successful scrape would hide the failure and block a later retry.
    if not parsed.name and not parsed.images:
        await patch_product(pid, {
            "product_id": pid,
            "retailer": retailer,
            "url": url,
            "status": "flagged",
            "flag_reason": "empty_parse",
            "flag_detail": "the fetched page yielded neither a name nor any images",
            "flagged_at": utcnow_iso(),
        })
        raise RuntimeError(
            f"{retailer}: {url} yielded no product fields. The page was probably "
            f"an interstitial or error page; try: python -m scraper.unblock {retailer}"
        )

    first_party_only = config.retailer_config(retailer)["first_party_only"]
    if first_party_only and parsed.first_party is False:
        await patch_product(pid, {
            "product_id": pid,
            "retailer": retailer,
            "url": url,
            "status": "flagged",
            "flag_reason": "third_party_seller",
            "flag_detail": f"sold by {parsed.sold_by!r}, not {retailer}",
            "flagged_at": utcnow_iso(),
            "name": parsed.name,
            "sold_by": parsed.sold_by,
            "first_party": False,
        })
        return

    if getattr(adapter, "snapshot_kind", "html") == "json":
        snapshot_key, snapshot_path = "raw_api_snapshot", save_api_snapshot(pid, raw)
    else:
        snapshot_key, snapshot_path = "raw_html_snapshot", save_html_snapshot(pid, raw)

    images = await download_images(pid, parsed.images or parsed.image_urls)
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
        snapshot_key: snapshot_path,
        "similar_items": parsed.similar_items,
        "retailer_sku": parsed.retailer_sku,
        "gtin": parsed.gtin,
        "upc": parsed.upc,
        "sold_by": parsed.sold_by,
        "ships_from": parsed.ships_from,
        "first_party": parsed.first_party,
        "description": parsed.description,
        "ingredients": parsed.ingredients,
        "categories": parsed.categories,
        "attributes": parsed.attributes,
    }

    # The ladder reads the images that were just downloaded, so it runs against
    # the record as it will be stored rather than against the parse alone.
    result = await extract_nutrition(
        {**record, "images": images},
        page_nutrition=parsed.nutrition,
        root=config.ROOT,
    )
    record["nutrition"] = result.nutrition
    record["nutrition_sources"] = result.sources
    record["nutrition_confidence"] = result.confidence
    record["nutrition_score"] = result.score
    if result.conflicts:
        record["nutrition_conflicts"] = result.conflicts
    if result.image_ocr:
        record["image_ocr_text"] = result.image_ocr

    # Give every product its label image in the one place the spec names, even
    # when the retailer never called it that.
    if label is None and result.label_image and result.label_image.get("local_path"):
        adopted = adopt_label_image(
            pid,
            result.label_image["local_path"],
            result.label_image.get("public_url") or "",
        )
        if adopted:
            adopted["panel_likeness"] = result.label_image.get("panel_likeness")
            adopted["image_index"] = result.label_image.get("image_index")
            record["nutrition_label_image"] = adopted

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
