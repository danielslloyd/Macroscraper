"""Publix adapter.

Publix renders its product page with Vue and keeps the item's identity in an
analytics layer (`digitalData` / `adobeDataLayer`): the RIO-PCI id, the numeric
item code, name, brand, category and variety. That is reliable and cheap to
read.

The nutrition panel is a different story. Publix loads it client-side and only
for a selected store, so a plain fetch gets the panel's markup and none of its
numbers. The adapter does three things about that:

  * waits for and opens the nutrition accordion when running under the browser
    fetcher, and parses the panel if it does appear (`.nfd`, `.nf-legacy-*`);
  * leaves `nutrition` empty rather than guessing, so the ladder's provenance
    shows plainly where the numbers came from.

Publix also publishes exactly one photograph per item -- the front of the pack
(`{itemcode}-600x600-A.jpg`; the other view letters 404) -- so unlike Amazon
and Costco there is no back-of-pack image for OCR to read. For Publix items
the ladder therefore relies on the page panel or, failing that, the
name-matched database tier.

Setting a store once with `python -m scraper.unblock publix` makes the page's
own panel available and keeps tier 1 working for later runs.
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct
from .. import embedded
from ..extract import nutrients
from ..parsing import og_meta, soup_of

# https://images.publixcdn.com/pct/images/products/10000/010229-600x600-A.jpg
_IMAGE_URL = re.compile(
    r"(?P<prefix>https://images\.publixcdn\.com/pct/images/products/[^/]+/"
    r"(?P<item>\d+)-)(?P<size>\d+x\d+)-(?P<view>[A-Z])\.(?P<ext>jpg|jpeg|png)",
    re.IGNORECASE,
)


class PublixAdapter(RetailerAdapter):
    retailer = "publix"
    entry_urls = ["https://www.publix.com/shop-online"]
    product_wait_for = "h1"
    product_expand = (
        "button:has-text('Nutritional information')",
        "button:has-text('Nutrition')",
        ".expand-collapse.nutrition-information .title",
        "[class*='nutrition'] button",
    )

    _PRODUCT_PATH = re.compile(r"/pd/")
    _CATEGORY_PATH = re.compile(r"/shop-online|/browse|/c/")

    # ----- crawl -----------------------------------------------------------

    def classify(self, url: str, html: str) -> str:
        if self._PRODUCT_PATH.search(url):
            return "product"
        return "category"

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        soup = soup_of(html)
        seen: set[str] = set()
        for a in soup.select("a[href]"):
            href = urljoin(url, a["href"]).split("#", 1)[0].split("?", 1)[0]
            parsed = urlparse(href)
            if parsed.netloc and "publix.com" not in parsed.netloc:
                continue
            if href in seen or href == url:
                continue
            name = a.get_text(" ", strip=True) or (a.find("img") or {}).get("alt", "")
            if not name:
                continue
            seen.add(href)
            if self._PRODUCT_PATH.search(parsed.path):
                yield CrawlLink(url=href, name=name, kind="product")
            elif self._CATEGORY_PATH.search(parsed.path):
                yield CrawlLink(url=href, name=name, kind="category")

    # ----- parse -----------------------------------------------------------

    def parse_product(self, url: str, html: str) -> ScrapedProduct:
        soup = soup_of(html)
        record = self._data_layer_product(html)
        product = ScrapedProduct()

        product.retailer_sku = str(record.get("id") or self._rio_id(url) or "") or None
        item_code = record.get("itemCode")
        product.name = (record.get("name")
                        or (og_meta(soup, "og:title") or "").split("|")[0].strip()
                        or self._heading(soup))
        product.brand = record.get("brand") or ""
        product.description = (
            self._meta_description(soup) or record.get("variety") or ""
        )
        category = record.get("category") or ""
        product.categories = [c for c in category.split("/") if c]
        product.attributes = {
            k: v for k, v in record.items()
            if k not in ("id", "itemCode", "name", "brand", "category")
            and v not in (None, "", "false")
        }
        if item_code is not None:
            product.attributes["item_code"] = item_code

        product.images = self._images(html, soup)
        product.price = self._price(soup)
        product.nutrition = self._nutrition(soup)
        product.ingredients = self._ingredients(soup)

        # Publix sells its own catalogue; there is no marketplace.
        product.sold_by = "Publix"
        product.first_party = True
        product.similar_items = self._similar(url, soup)
        return product

    # ----- field helpers ---------------------------------------------------

    @staticmethod
    def _data_layer_product(html: str) -> dict:
        """The product record from whichever analytics object carries it.

        The page emits `digitalData` and then pushes a near-copy into
        `adobeDataLayer`, so this takes the first `"products"` object that
        actually has an item code rather than the first one it finds.
        """
        for candidate in embedded.objects_after_key(html, "products"):
            if candidate.get("itemCode") or str(candidate.get("id", "")).startswith("RIO"):
                return candidate
        for name in ("digitalData", "adobeDataLayer"):
            payload = embedded.inline_json_var(html, name)
            if not payload:
                continue
            found = embedded.find_dict(
                payload, lambda node: "itemCode" in node or "variety" in node
            )
            if found:
                return found
        return {}

    @staticmethod
    def _rio_id(url: str) -> str | None:
        match = re.search(r"(RIO-PCI-\d+)", url, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _heading(soup) -> str:
        h1 = soup.find("h1")
        return h1.get_text(" ", strip=True) if h1 else ""

    @staticmethod
    def _meta_description(soup) -> str:
        tag = soup.find("meta", attrs={"name": "description"})
        return (tag.get("content") or "").strip() if tag else ""

    @staticmethod
    def _images(html: str, soup) -> list[dict]:
        """The product photographs the page publishes.

        In practice that is one front-of-pack shot per item: the other view
        letters in Publix's image naming return 404, so there is nothing to
        enumerate and nothing to guess at.
        """
        images: list[dict] = []
        seen: set[str] = set()

        def add(url: str, role: str, alt: str = "") -> None:
            if url not in seen:
                seen.add(url)
                images.append({"public_url": url, "role": role, "alt": alt})

        hero = og_meta(soup, "og:image")
        candidates = [hero] if hero else []
        candidates += [
            img.get("src") or img.get("data-src") or ""
            for img in soup.select("img[src], img[data-src]")
        ]

        for candidate in candidates:
            match = _IMAGE_URL.search(candidate or "")
            if match:
                add(match.group(0), "front" if not images else "other")

        if not images:
            match = _IMAGE_URL.search(html)
            if match:
                add(match.group(0), "front")
        return images

    @staticmethod
    def _price(soup) -> dict:
        price: dict = {}
        for node in soup.select(
            "[class*='price'], [data-testid*='price'], [itemprop='price']"
        ):
            text = node.get_text(" ", strip=True)
            match = re.search(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)", text)
            if match and "package_price" not in price:
                price["package_price"] = float(match.group(1))
            unit = re.search(
                r"\$\s*([0-9]+\.?[0-9]*)\s*(?:/|per\s*)\s*(oz|lb|each|ct|fl\s*oz|qt|gal)",
                text, re.IGNORECASE,
            )
            if unit and "unit_price" not in price:
                price["unit_price"] = float(unit.group(1))
                price["unit"] = f"per {unit.group(2).lower()}"
        size = soup.select_one("[class*='package-size'], [class*='size-option']")
        if size:
            text = size.get_text(" ", strip=True)
            if text:
                price["package_size"] = text[:60]
        return price

    @staticmethod
    def _nutrition(soup) -> dict:
        """Parse the panel if the page actually rendered one."""
        for node in soup.select(
            ".nfd, [class*='nf-legacy'], [class*='nutrition-label'], "
            ".nutrition-info, [class*='nutrition-information']"
        ):
            text = node.get_text(" | ", strip=True)
            if nutrients.looks_like_panel(text) >= 0.3:
                parsed = nutrients.parse_panel_text(text)
                if parsed:
                    return parsed
        return {}

    @staticmethod
    def _ingredients(soup) -> str:
        for node in soup.select("[class*='ingredient'], [id*='ingredient']"):
            text = node.get_text(" ", strip=True)
            cleaned = re.sub(r"^ingredients\s*:?\s*", "", text, flags=re.IGNORECASE)
            if len(cleaned) > 15:
                return re.sub(r"\s+", " ", cleaned)[:2000]
        return ""

    def _similar(self, url: str, soup) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for section in soup.select(
            "[class*='related'], [class*='recommend'], [class*='similar'], "
            "[class*='you-may'], [class*='carousel']"
        ):
            for a in section.select("a[href]"):
                href = urljoin(url, a["href"]).split("#", 1)[0].split("?", 1)[0]
                if "/pd/" not in href or href in seen or href == url:
                    continue
                name = (a.get_text(" ", strip=True)
                        or (a.find("img") or {}).get("alt", "")
                        or href)
                seen.add(href)
                out.append({"name": name[:200], "public_url": href, "product_id": None})
        return out[:20]
