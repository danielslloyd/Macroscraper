"""Walmart adapter.

Walmart's product page is a Next.js page whose `__NEXT_DATA__` script carries
the whole item record: identity, price, seller, the image gallery, and -- for
most packaged groceries -- a structured `nutritionFacts` block. That payload is
the best data surface of any retailer here.

Getting at it is the hard part. Walmart fronts the site with a bot check
("Robot or human?"), and an automated first visit is served the challenge
rather than the page. The project does not try to defeat that. Instead:

  * `fetch.browser` detects the challenge and raises `BotWallError` rather than
    storing an empty record;
  * `python -m scraper.unblock walmart` opens the site in a visible window so
    the check can be satisfied by hand, once, and the cookies that result are
    kept in the persistent profile for later headless runs.

Fields are located by shape, not by path -- "the object that has a `usItemId`"
rather than `props.pageProps.initialData.data.product`. Walmart reorganizes
that tree regularly, and the shape has outlived several of its layouts.

`first_party` comes from the seller name, so with `first_party_only` set,
items shipped and sold by Walmart are kept and marketplace listings are
dropped.
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct, is_first_party
from .. import embedded
from ..extract import nutrients
from ..parsing import soup_of

_ITEM_ID = re.compile(r"/ip/(?:[^/]+/)?(\d+)")
_WALMART_IMAGE = re.compile(r"https://i5\.walmartimages\.com/[^\"\\\s]+", re.IGNORECASE)
# Walmart's own seller id for first-party items.
_WALMART_SELLER_IDS = {"0", "F55CDC31AB754BB68FE0B39041159D63"}


class WalmartAdapter(RetailerAdapter):
    retailer = "walmart"
    entry_urls = ["https://www.walmart.com/cp/food/976759"]
    product_wait_for = "#__NEXT_DATA__, h1"
    product_expand = (
        "button:has-text('Nutrition facts')",
        "button:has-text('Nutrition information')",
        "[data-testid*='nutrition'] button",
    )

    _PRODUCT_PATH = re.compile(r"/ip/")
    _CATEGORY_PATH = re.compile(r"/cp/|/browse/|/shop/")

    # ----- crawl -----------------------------------------------------------

    def classify(self, url: str, html: str) -> str:
        if self._PRODUCT_PATH.search(url):
            return "product"
        data = embedded.next_data(soup_of(html))
        if data and embedded.find_dict(data, embedded.has_keys("usItemId")):
            return "product"
        return "category"

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        soup = soup_of(html)
        seen: set[str] = set()
        for a in soup.select("a[href]"):
            href = urljoin(url, a["href"]).split("#", 1)[0].split("?", 1)[0]
            parsed = urlparse(href)
            if parsed.netloc and "walmart.com" not in parsed.netloc:
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
        payload = embedded.next_data(soup) or {}
        node = self._product_node(payload)

        product = ScrapedProduct()
        product.retailer_sku = str(
            embedded.first_value(node, "usItemId") or self._item_id(url) or ""
        ) or None
        product.name = (
            embedded.first_value(node, "name", "productName")
            or self._heading(soup)
            or ""
        )
        brand = embedded.first_value(node, "brand", "brandName")
        product.brand = brand if isinstance(brand, str) else ""
        product.upc = _digits(embedded.first_value(node, "upc"))
        product.gtin = _digits(
            embedded.first_value(node, "gtin14", "gtin", "globalTradeItemNumber")
        ) or product.upc

        description = embedded.first_value(node, "shortDescription", "description")
        product.description = _strip_html(description) if isinstance(description, str) else ""
        product.categories = self._categories(node, soup)
        product.price = self._price(node, soup)
        product.images = self._images(node, html, soup)
        product.nutrition = self._nutrition(node, soup)
        product.ingredients = self._ingredients(node, soup)

        product.sold_by, product.ships_from = self._seller(node)
        product.first_party = self._first_party(node, product.sold_by)
        product.similar_items = self._similar(url, soup)
        return product

    # ----- field helpers ---------------------------------------------------

    @staticmethod
    def _product_node(payload: dict) -> dict:
        """The item's own record inside `__NEXT_DATA__`.

        Identified by `usItemId`, Walmart's item key; failing that, by the
        combination of a name and a price block, which is what every layout of
        the product object has had.
        """
        node = embedded.find_dict(payload, embedded.has_keys("usItemId"))
        if node:
            return node
        node = embedded.find_dict(payload, embedded.has_keys("name", "priceInfo"))
        return node or payload

    @staticmethod
    def _item_id(url: str) -> str | None:
        match = _ITEM_ID.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _heading(soup) -> str:
        for selector in ("h1[itemprop='name']", "#main-title", "h1"):
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return ""

    @staticmethod
    def _categories(node: dict, soup) -> list[str]:
        out: list[str] = []
        for path in embedded.find_values(node, "categoryPath") + \
                embedded.find_values(node, "categoryPathName"):
            if isinstance(path, str):
                out.extend(p.strip() for p in path.split("/") if p.strip())
        if not out:
            for crumb in soup.select("nav[aria-label*='readcrumb'] a, .breadcrumb a"):
                text = crumb.get_text(" ", strip=True)
                if text:
                    out.append(text)
        seen: list[str] = []
        for item in out:
            if item not in seen:
                seen.append(item)
        return seen

    @staticmethod
    def _price(node: dict, soup) -> dict:
        price: dict = {}
        current = embedded.find_dict(node, embedded.has_keys("currentPrice"))
        if current:
            amount = nutrients.to_number(
                (current["currentPrice"] or {}).get("price")
                if isinstance(current["currentPrice"], dict) else current["currentPrice"]
            )
            if amount is not None:
                price["package_price"] = amount
        if "package_price" not in price:
            amount = nutrients.to_number(embedded.first_value(node, "price", "priceString"))
            if amount is not None:
                price["package_price"] = amount

        unit = embedded.find_dict(node, embedded.has_keys("unitPrice"))
        unit_value = (unit or {}).get("unitPrice")
        if isinstance(unit_value, dict):
            amount = nutrients.to_number(unit_value.get("price") or unit_value.get("priceString"))
            if amount is not None:
                price["unit_price"] = amount
            measure = unit_value.get("unit") or unit_value.get("priceUnitOfMeasure")
            if measure:
                price["unit"] = f"per {str(measure).lower()}"
        elif isinstance(unit_value, str):
            match = re.search(
                r"([0-9]+\.?[0-9]*)\s*(?:/|per\s*)\s*([a-z ]+)", unit_value, re.IGNORECASE
            )
            if match:
                price["unit_price"] = float(match.group(1))
                price["unit"] = f"per {match.group(2).strip().lower()}"

        size = embedded.first_value(node, "size", "packageSize", "weightDisplay")
        if isinstance(size, str) and size:
            price["package_size"] = size
        if "package_price" not in price:
            node_price = soup.select_one("[itemprop='price'], [data-testid*='price']")
            if node_price:
                match = re.search(r"\$\s*([0-9,]+\.?[0-9]*)",
                                  node_price.get_text(" ", strip=True))
                if match:
                    price["package_price"] = float(match.group(1).replace(",", ""))
        return price

    @staticmethod
    def _images(node: dict, html: str, soup) -> list[dict]:
        urls: list[str] = []

        def add(candidate) -> None:
            if isinstance(candidate, str) and candidate.startswith("http"):
                clean = candidate.replace("\\u002F", "/")
                if clean not in urls:
                    urls.append(clean)

        for gallery in embedded.find_values(node, "allImages"):
            if isinstance(gallery, list):
                for image in gallery:
                    if isinstance(image, dict):
                        add(image.get("url") or image.get("src"))
                    else:
                        add(image)
        if not urls:
            for key in ("thumbnailUrl", "largeUrl", "imageUrl", "url"):
                for value in embedded.find_values(node, key):
                    if isinstance(value, str) and "walmartimages" in value:
                        add(value)
        if not urls:
            for match in _WALMART_IMAGE.finditer(html):
                candidate = match.group(0)
                if "/asr/" in candidate or "/seo/" in candidate:
                    add(candidate)
        if not urls:
            for img in soup.select("img[src]"):
                if "walmartimages" in (img.get("src") or ""):
                    add(img["src"])
        return [{"public_url": u, "role": "front" if i == 0 else "other"}
                for i, u in enumerate(urls)]

    @staticmethod
    def _nutrition(node: dict, soup) -> dict:
        """Walmart's structured nutrition block, else the rendered panel."""
        out: dict = {}
        blocks = embedded.find_values(node, "nutritionFacts") + \
            embedded.find_values(node, "nutritionalFacts")
        for block in blocks:
            if not isinstance(block, (dict, list)) or not block:
                continue
            serving = embedded.first_value(block, "servingSize", "servingSizeText")
            if serving and not out.get("serving_size"):
                out["serving_size"] = str(serving).strip()
            per_container = nutrients.to_number(
                embedded.first_value(block, "servingsPerContainer", "servingPerContainer")
            )
            if per_container is not None:
                out.setdefault("servings_per_container", per_container)
            # Walmart states calories outside the nutrient list.
            calories = nutrients.to_number(
                embedded.first_value(block, "calorieContent", "calories", "calorieInfo")
            )
            if calories is not None:
                out.setdefault("calories", calories)

            mapping: dict = {}
            entries = embedded.find_dicts(
                block,
                lambda n: (
                    any(k in n for k in ("name", "nutrientName", "label", "description"))
                    and any(k in n for k in ("value", "amount", "valueInGram", "quantity"))
                ),
            )
            for entry in entries:
                label = (entry.get("name") or entry.get("nutrientName")
                         or entry.get("label") or entry.get("description"))
                value = entry.get("value", entry.get("amount",
                        entry.get("valueInGram", entry.get("quantity"))))
                unit = entry.get("uom") or entry.get("unit") or entry.get("unitOfMeasure")
                if label is None or value in (None, ""):
                    continue
                mapping[str(label)] = {"value": value, "unit": unit} if unit else value
            if mapping:
                out.update(nutrients.normalize_mapping(mapping))
            elif isinstance(block, dict):
                out.update(nutrients.normalize_mapping(block))
            if out:
                break

        if not out:
            for region in soup.select(
                "[data-testid*='nutrition'], [class*='nutrition'], [id*='nutrition']"
            ):
                text = region.get_text(" | ", strip=True)
                if nutrients.looks_like_panel(text) >= 0.3:
                    out = nutrients.parse_panel_text(text)
                    if out:
                        break
        return out

    @staticmethod
    def _ingredients(node: dict, soup) -> str:
        value = embedded.first_value(node, "ingredients", "ingredientList")
        if isinstance(value, str) and len(value) > 10:
            return _strip_html(value)[:2000]
        if isinstance(value, list):
            joined = ", ".join(str(v) for v in value if v)
            if len(joined) > 10:
                return joined[:2000]
        for region in soup.select("[data-testid*='ingredient'], [class*='ingredient']"):
            text = re.sub(r"^ingredients\s*:?\s*", "",
                          region.get_text(" ", strip=True), flags=re.IGNORECASE)
            if len(text) > 15:
                return re.sub(r"\s+", " ", text)[:2000]
        return ""

    @staticmethod
    def _seller(node: dict) -> tuple[str | None, str | None]:
        seller = embedded.first_value(
            node, "sellerName", "sellerDisplayName", "seller"
        )
        if isinstance(seller, dict):
            seller = seller.get("sellerDisplayName") or seller.get("name")
        shipper = embedded.first_value(node, "fulfillerName", "shippedBy")
        return (
            seller.strip() if isinstance(seller, str) else None,
            shipper.strip() if isinstance(shipper, str) else None,
        )

    @staticmethod
    def _first_party(node: dict, sold_by: str | None) -> bool | None:
        """Walmart states this three ways; any one of them is enough."""
        flag = embedded.first_value(node, "isSoldByWalmart", "soldByWalmart")
        if isinstance(flag, bool):
            return flag
        seller_id = embedded.first_value(node, "sellerId", "sellerCatalogSellerId")
        if isinstance(seller_id, str) and seller_id.upper() in _WALMART_SELLER_IDS:
            return True
        return is_first_party("walmart", sold_by)

    def _similar(self, url: str, soup) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for section in soup.select(
            "[data-testid*='carousel'], [class*='related'], [class*='similar'], "
            "[class*='recommend'], [data-testid*='similar']"
        ):
            for a in section.select("a[href]"):
                href = urljoin(url, a["href"]).split("#", 1)[0].split("?", 1)[0]
                if "/ip/" not in href or href in seen or href == url:
                    continue
                name = (a.get_text(" ", strip=True)
                        or (a.find("img") or {}).get("alt", "")
                        or href)
                seen.add(href)
                out.append({"name": name[:200], "public_url": href, "product_id": None})
        return out[:20]


def _digits(value) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) >= 8 else None


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
