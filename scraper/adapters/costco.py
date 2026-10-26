"""Costco adapter.

Costco's product page is a Next.js App Router page: the facts arrive in a
streamed "flight" payload rather than in the HTML, and `scraper.embedded`
reassembles it. Inside is `productDetailsData`, with the item's own record --
`productData` (identity, descriptions, attributes, categories, `nutritionInfo`)
and `priceInfo` (the warehouse-specific price).

Two things to know about nutrition here. Costco does model it (`nutritionInfo`,
gated per department by `isShowNutrPanel`), but for a great many grocery items
the field is an empty object -- the flag says a panel belongs on the page and
there is no data behind it. And the gallery carries six photographs, which
normally include the back of the pack. So Costco is the retailer where the OCR
tier does most of the work, and the adapter's job is to hand it good images.

Everything on costco.com is sold by Costco unless the page names another
seller, so `first_party` is True by default here and set to False only when a
seller is named.
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct
from .. import embedded
from ..extract import nutrients
from ..parsing import find_product_jsonld, soup_of

# .../{productId}-{warehouse}__{n}.jpg — n is the gallery position.
_GALLERY = re.compile(r"/([0-9]{5,})-[0-9]+__([0-9]+)\.(jpg|jpeg|png)$", re.IGNORECASE)
_PRODUCT_ID = re.compile(r"\.product\.(\d+)\.html|/p/[^/]*/[^/]*/(\d+)")
_SELLER_TEXT = re.compile(r"sold\s+by\s*:?\s*([^.|\n<]{2,60})", re.IGNORECASE)


class CostcoAdapter(RetailerAdapter):
    retailer = "costco"
    entry_urls = ["https://www.costco.com/grocery-household.html"]
    product_wait_for = "h1"
    product_expand = (
        "button:has-text('Nutritional Facts')",
        "button:has-text('Nutrition')",
        "#nutritional_facts",
    )

    _PRODUCT_PATH = re.compile(r"\.product\.\d+\.html|/p/[^/]+/[^/]+/\d+")
    _CATEGORY_PATH = re.compile(r"\.html$")

    # ----- crawl -----------------------------------------------------------

    def classify(self, url: str, html: str) -> str:
        if self._PRODUCT_PATH.search(url):
            return "product"
        return "product" if find_product_jsonld(soup_of(html)) else "category"

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        soup = soup_of(html)
        seen: set[str] = set()
        for a in soup.select("a[href]"):
            href = urljoin(url, a["href"]).split("#", 1)[0].split("?", 1)[0]
            parsed = urlparse(href)
            if parsed.netloc and "costco.com" not in parsed.netloc:
                continue
            if href in seen or href == url:
                continue
            name = a.get_text(" ", strip=True) or (a.find("img") or {}).get("alt", "")
            if not name:
                continue
            seen.add(href)
            if self._PRODUCT_PATH.search(href):
                yield CrawlLink(url=href, name=name, kind="product")
            elif self._CATEGORY_PATH.search(parsed.path) and "/p/" not in parsed.path:
                yield CrawlLink(url=href, name=name, kind="category")

    # ----- parse -----------------------------------------------------------

    def parse_product(self, url: str, html: str) -> ScrapedProduct:
        soup = soup_of(html)
        flight = embedded.flight_payload(html)
        details = embedded.object_after_key(flight, "productDetailsData") or {}
        data = details.get("productData") or {}
        jsonld = find_product_jsonld(soup) or {}

        product = ScrapedProduct()
        description = self._description(data)
        attributes = self._attributes(data)
        product.attributes = attributes

        product.retailer_sku = str(data.get("id") or self._product_id(url) or "") or None
        product.name = (description.get("shortDescription")
                        or jsonld.get("name")
                        or self._heading(soup))
        product.brand = attributes.get("Brand") or _brand_from_jsonld(jsonld)
        product.description = _strip_html(description.get("longDescription") or "")
        product.upc = _barcode(data.get("upc"))
        product.gtin = _barcode(data.get("gtin")) or product.upc
        product.categories = self._categories(data)
        product.price = self._price(details, data, jsonld, attributes)
        product.images = self._images(flight, product.retailer_sku, jsonld)
        product.nutrition = self._nutrition(data, soup)

        seller = self._seller(soup)
        product.sold_by = seller or "Costco Wholesale"
        # costco.com is Costco's own catalogue; a named third-party seller is
        # the exception, so absence of one means first party.
        product.first_party = seller is None
        product.similar_items = self._similar(url, soup)
        return product

    # ----- field helpers ---------------------------------------------------

    @staticmethod
    def _description(data: dict) -> dict:
        for entry in data.get("descriptions") or []:
            obj = entry.get("object")
            if isinstance(obj, dict):
                return obj
        return {}

    @staticmethod
    def _attributes(data: dict) -> dict:
        """Flatten `productAttributes` into {key: value}, joining repeats."""
        out: dict = {}
        for entry in data.get("productAttributes") or []:
            for attribute in entry.get("object") or []:
                key = attribute.get("key")
                value = attribute.get("value")
                if not key or value in (None, ""):
                    continue
                if key in out:
                    out[key] = f"{out[key]}, {value}"
                else:
                    out[key] = value
        return out

    @staticmethod
    def _categories(data: dict) -> list[str]:
        out: list[str] = []
        for entry in data.get("categories") or []:
            for category in entry.get("object") or []:
                name = category.get("name")
                if name and name not in out:
                    out.append(name)
        return out

    @staticmethod
    def _heading(soup) -> str:
        h1 = soup.find("h1")
        return h1.get_text(" ", strip=True) if h1 else ""

    @staticmethod
    def _product_id(url: str) -> str | None:
        match = _PRODUCT_ID.search(url)
        if not match:
            return None
        return match.group(1) or match.group(2)

    @staticmethod
    def _price(details: dict, data: dict, jsonld: dict, attributes: dict) -> dict:
        price: dict = {}
        display = (details.get("priceInfo") or {}).get("displayPrice") or {}
        amount = nutrients.to_number(
            display.get("onlinePrice")
            or display.get("deliveredPrice")
            or (jsonld.get("offers") or {}).get("price")
            if isinstance(jsonld.get("offers"), dict)
            else display.get("onlinePrice")
        )
        if amount is not None:
            price["package_price"] = amount
        size = attributes.get("Package Net Weight") or attributes.get("Net Weight")
        if size:
            price["package_size"] = size
        # Costco gives the pack's total measure as uom + factor, which is
        # enough to state a unit price the page itself does not print.
        factor = nutrients.to_number(data.get("factor"))
        uom = (data.get("uom") or "").lower()
        if amount and factor and factor > 0 and uom:
            unit = {"ounce": "oz", "pound": "lb", "gram": "g",
                    "fluid ounce": "fl oz"}.get(uom, uom)
            price["unit_price"] = round(amount / factor, 4)
            price["unit"] = f"per {unit}"
            price["unit_price_derived"] = True
        return price

    @staticmethod
    def _images(flight: str, product_id: str | None, jsonld: dict) -> list[dict]:
        """The gallery, in printed order, preferring JPEG over AVIF.

        Costco serves each photo as both .avif and .jpg; the JPEG is what Pillow
        and the OCR engine can open, so the AVIF twins are dropped.
        """
        candidates: dict[int, str] = {}
        pattern = re.compile(r"https://[^\"\\\s]+\.(?:jpg|jpeg|png)", re.IGNORECASE)
        for raw in pattern.findall(flight):
            match = _GALLERY.search(urlparse(raw).path)
            if not match:
                continue
            if product_id and match.group(1) != str(product_id):
                continue
            position = int(match.group(2))
            candidates.setdefault(position, raw)
        images = [
            {"public_url": candidates[pos],
             "role": "front" if pos == 1 else "other",
             "alt": f"gallery {pos}"}
            for pos in sorted(candidates)
        ]
        if images:
            return images
        hero = jsonld.get("image")
        if isinstance(hero, list):
            hero = hero[0] if hero else None
        if isinstance(hero, str):
            return [{"public_url": hero, "role": "front"}]
        return []

    @staticmethod
    def _nutrition(data: dict, soup) -> dict:
        """Costco's own nutrition record, if the item has one.

        `nutritionInfo` is frequently `{}` even when the page reserves space
        for a panel, which is the normal reason this retailer falls through to
        the OCR tier. The shape varies by department, so the nutrient entries
        are found by shape rather than by a fixed path.
        """
        info = data.get("nutritionInfo")
        out: dict = {}
        if isinstance(info, dict) and info:
            serving = embedded.first_value(
                info, "servingSize", "serving_size", "servingSizeText"
            )
            if serving:
                out["serving_size"] = str(serving).strip()
            entries = embedded.find_dicts(
                info,
                lambda node: (
                    any(k in node for k in ("name", "nutrientName", "description", "label"))
                    and any(k in node for k in ("value", "amount", "quantity"))
                ),
            )
            mapping: dict = {}
            for entry in entries:
                label = (entry.get("name") or entry.get("nutrientName")
                         or entry.get("description") or entry.get("label"))
                value = entry.get("value", entry.get("amount", entry.get("quantity")))
                unit = entry.get("uom") or entry.get("unit") or entry.get("unitOfMeasure")
                if label is None or value in (None, ""):
                    continue
                mapping[str(label)] = {"value": value, "unit": unit} if unit else value
            out.update(nutrients.normalize_mapping(mapping))
            if not entries:
                # A flat {"Total Fat": "15g"} style object.
                out.update(nutrients.normalize_mapping(info))

        if not out:
            # The rendered accordion, when the browser managed to open it.
            for node in soup.select(
                "#nutritional_facts, [id*='nutrition'], [class*='nutrition']"
            ):
                text = node.get_text(" | ", strip=True)
                if nutrients.looks_like_panel(text) >= 0.3:
                    out = nutrients.parse_panel_text(text)
                    break
        return out

    @staticmethod
    def _seller(soup) -> str | None:
        for node in soup.select("[class*='seller'], [id*='seller'], [class*='marketplace']"):
            match = _SELLER_TEXT.search(node.get_text(" ", strip=True))
            if match:
                seller = match.group(1).strip()
                if seller and "costco" not in seller.lower():
                    return seller
        return None

    def _similar(self, url: str, soup) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for section in soup.select(
            "[class*='related'], [class*='recommend'], [class*='similar'], "
            "[data-testid*='recommend'], [class*='also-bought']"
        ):
            for a in section.select("a[href]"):
                href = urljoin(url, a["href"]).split("#", 1)[0].split("?", 1)[0]
                if not self._PRODUCT_PATH.search(href) or href in seen or href == url:
                    continue
                name = (a.get_text(" ", strip=True)
                        or (a.find("img") or {}).get("alt", "")
                        or href)
                seen.add(href)
                out.append({"name": name[:200], "public_url": href, "product_id": None})
        return out[:20]


def _brand_from_jsonld(jsonld: dict) -> str:
    brand = jsonld.get("brand")
    if isinstance(brand, dict):
        return (brand.get("name") or "").strip()
    return (brand or "").strip() if isinstance(brand, str) else ""


def _barcode(value) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) >= 8 else None


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
