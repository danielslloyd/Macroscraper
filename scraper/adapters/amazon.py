"""Amazon adapter.

Amazon is the retailer the project flagged as the likely problem, and it is:
no JSON-LD, no single data payload, bot checks on fresh sessions, and a
different buy-box layout for Amazon Fresh items than for the rest of the
catalogue. What it does have, reliably, is a fully rendered nutrition panel in
the page HTML, which makes it the one retailer where tier 1 almost always
succeeds.

Where each field comes from (verified against a live product page):
  name          `#productTitle`
  brand         `#bylineInfo`
  price         the first `.a-offscreen` inside the price feature blocks
  images        the `"hiRes"` URLs in the ImageBlock bootstrap script, which
                are the full-resolution gallery -- `data-a-dynamic-image`
                carries only thumbnail variants
  nutrition     the `cse-nutritional-info-and-ingredient-card` widget
  upc / gtin    the product-details table rows
  sold by       `#almShipsFromSoldBy_feature_div` (Fresh/grocery layout),
                `#merchant-info`, or the tabular buy box

`first_party` comes from that seller name, so `first_party_only` in
`scraper_config.json` keeps items Amazon sells itself and drops marketplace
listings.
"""
from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct, is_first_party
from ..parsing import soup_of
from ..extract import nutrients

_ASIN_IN_URL = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
_HIRES = re.compile(r'"hiRes"\s*:\s*"(https://[^"]+)"')
_LARGE = re.compile(r'"large"\s*:\s*"(https://[^"]+)"')
_PRICE_TEXT = re.compile(r"\$\s*([0-9][0-9,]*\.?[0-9]*)")
_UNIT_PRICE = re.compile(
    r"\$\s*([0-9]+\.?[0-9]*)\s*/\s*(fl\s*oz|oz|ounce|lb|pound|count|ct|each|g|kg|l|ml)",
    re.IGNORECASE,
)


class AmazonAdapter(RetailerAdapter):
    retailer = "amazon"
    entry_urls = [
        "https://www.amazon.com/s?i=amazonfresh",
        "https://www.amazon.com/grocery",
    ]
    product_wait_for = "#productTitle"
    # The nutrition card is rendered server-side, but ingredients sometimes sit
    # behind a "See more" control.
    product_expand = ("#nic-expander-heading", "a:has-text('Nutrition')")

    _PRODUCT_PATH = re.compile(r"/dp/[A-Z0-9]{10}|/gp/product/[A-Z0-9]{10}")
    _CATEGORY_PATH = re.compile(r"/s\?|/b\?|/b/|/gp/browse|/grocery")

    # ----- crawl -----------------------------------------------------------

    def classify(self, url: str, html: str) -> str:
        if self._PRODUCT_PATH.search(url):
            return "product"
        soup = soup_of(html)
        return "product" if soup.select_one("#productTitle") else "category"

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        soup = soup_of(html)
        seen: set[str] = set()
        for a in soup.select("a[href]"):
            href = urljoin(url, a["href"]).split("#", 1)[0]
            parsed = urlparse(href)
            if parsed.netloc and "amazon.com" not in parsed.netloc:
                continue
            asin = _ASIN_IN_URL.search(parsed.path)
            if asin:
                canonical = f"https://www.amazon.com/dp/{asin.group(1)}"
                if canonical in seen:
                    continue
                seen.add(canonical)
                name = a.get_text(" ", strip=True) or a.get("title") or ""
                yield CrawlLink(url=canonical, name=name or canonical, kind="product")
            elif self._CATEGORY_PATH.search(href) and href not in seen:
                name = a.get_text(" ", strip=True)
                if not name:
                    continue
                seen.add(href)
                yield CrawlLink(url=href, name=name, kind="category")

    # ----- parse -----------------------------------------------------------

    def parse_product(self, url: str, html: str) -> ScrapedProduct:
        soup = soup_of(html)
        product = ScrapedProduct()

        asin = _ASIN_IN_URL.search(url)
        product.retailer_sku = asin.group(1) if asin else None

        title = soup.select_one("#productTitle")
        if title:
            product.name = title.get_text(" ", strip=True)

        product.brand = self._brand(soup)
        product.price = self._price(soup)
        product.images = self._images(html, soup)

        details = self._detail_pairs(soup)
        product.attributes = details
        product.upc = _digits(details.get("upc"))
        product.gtin = _digits(
            details.get("global trade identification number") or details.get("gtin")
        )
        if not product.retailer_sku:
            product.retailer_sku = details.get("asin")

        product.sold_by, product.ships_from = self._seller(soup)
        product.first_party = is_first_party(self.retailer, product.sold_by)

        panel_text, ingredients = self._nutrition_card(soup)
        product.nutrition = nutrients.parse_panel_text(panel_text)
        product.ingredients = ingredients
        if not product.ingredients:
            product.ingredients = details.get("ingredients", "")

        description = soup.select_one("#feature-bullets")
        if description:
            product.description = description.get_text(" ", strip=True)

        product.categories = [
            crumb.get_text(" ", strip=True)
            for crumb in soup.select("#wayfinding-breadcrumbs_feature_div a")
            if crumb.get_text(strip=True)
        ]
        product.similar_items = self._similar(url, soup)
        return product

    # ----- field helpers ---------------------------------------------------

    @staticmethod
    def _brand(soup: BeautifulSoup) -> str:
        byline = soup.select_one("#bylineInfo")
        if byline:
            text = byline.get_text(" ", strip=True)
            text = re.sub(r"^(?:Brand|Visit the)\s*:?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*Store$", "", text, flags=re.I)
            if text:
                return text.strip()
        for row in soup.select("tr"):
            header = row.find("th")
            if header and header.get_text(strip=True).lower() == "brand":
                cell = row.find("td")
                if cell:
                    return cell.get_text(" ", strip=True)
        return ""

    @staticmethod
    def _price(soup: BeautifulSoup) -> dict:
        price: dict = {}
        for selector in (
            "#corePriceDisplay_desktop_feature_div .a-offscreen",
            "#corePrice_feature_div .a-offscreen",
            "#price_inside_buybox",
            ".a-price .a-offscreen",
        ):
            node = soup.select_one(selector)
            if node:
                match = _PRICE_TEXT.search(node.get_text(" ", strip=True))
                if match:
                    price["package_price"] = float(match.group(1).replace(",", ""))
                    break
        for selector in (
            "#corePriceDisplay_desktop_feature_div .aok-align-center",
            ".a-price + .a-size-small",
            "#per-unit-price",
            "#corePrice_feature_div",
        ):
            node = soup.select_one(selector)
            if not node:
                continue
            match = _UNIT_PRICE.search(node.get_text(" ", strip=True))
            if match:
                price["unit_price"] = float(match.group(1))
                price["unit"] = f"per {match.group(2).lower()}"
                break
        size = soup.select_one("#variation_size_name .selection")
        if size:
            price["package_size"] = size.get_text(" ", strip=True)
        return price

    @staticmethod
    def _images(html: str, soup: BeautifulSoup) -> list[dict]:
        urls: list[str] = []
        for pattern in (_HIRES, _LARGE):
            for match in pattern.finditer(html):
                candidate = match.group(1)
                if candidate not in urls:
                    urls.append(candidate)
            if urls:
                break
        if not urls:
            # Fall back to the thumbnail map, taking the widest variant of each.
            for img in soup.select("img[data-a-dynamic-image]"):
                try:
                    variants = json.loads(img["data-a-dynamic-image"])
                except (json.JSONDecodeError, KeyError):
                    continue
                best = max(
                    variants.items(),
                    key=lambda kv: (kv[1] or [0, 0])[0] if isinstance(kv[1], list) else 0,
                    default=(None, None),
                )[0]
                if best and best not in urls:
                    urls.append(best)
        return [{"public_url": u, "role": "front" if i == 0 else "other"}
                for i, u in enumerate(urls)]

    @staticmethod
    def _detail_pairs(soup: BeautifulSoup) -> dict:
        """Flatten the several shapes of Amazon's product-details tables."""
        out: dict = {}
        for row in soup.select(
            "#productDetails_detailBullets_sections1 tr, "
            "#productDetails_techSpec_section_1 tr, "
            "#productDetails_db_sections tr, "
            ".prodDetTable tr, "
            "table tr:has(th)"
        ):
            header = row.find("th")
            cell = row.find("td")
            if not header or not cell:
                continue
            key = re.sub(r"\s+", " ", header.get_text(" ", strip=True)).strip(" :").lower()
            value = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            if key and value and key not in out:
                out[key] = value
        for item in soup.select("#detailBullets_feature_div li"):
            text = item.get_text(" ", strip=True).replace("‏", "").replace("‎", "")
            if ":" not in text:
                continue
            key, _, value = text.partition(":")
            key = re.sub(r"\s+", " ", key).strip().lower()
            value = re.sub(r"\s+", " ", value).strip()
            if key and value and key not in out:
                out[key] = value
        return out

    @staticmethod
    def _seller(soup: BeautifulSoup) -> tuple[str | None, str | None]:
        """Read 'Sold by' and 'Ships from' out of whichever buy box is present."""
        sold_by: str | None = None
        ships_from: str | None = None

        # Fresh / grocery layout: a two-column table of label and value. Take
        # the cells themselves, not every descendant -- each cell wraps its
        # text in a span, so a flat descendant list repeats the label.
        for container in soup.select(
            "#almShipsFromSoldBy_feature_div, #fresh-merchant-info, "
            "#tabular-buybox, .tabular-buybox-container"
        ):
            rows: list[list[str]] = []
            for row in container.select("tr"):
                rows.append([c.get_text(" ", strip=True) for c in row.find_all("td")])
            for row in container.select(".tabular-buybox-container > div"):
                rows.append([
                    c.get_text(" ", strip=True)
                    for c in row.find_all(recursive=False)
                ])
            for cells in rows:
                cells = [c for c in cells if c]
                if len(cells) < 2:
                    continue
                label = cells[0].rstrip(":").lower()
                value = cells[1].strip()
                if label == "sold by" and not sold_by:
                    sold_by = value
                elif label == "ships from" and not ships_from:
                    ships_from = value

        if not sold_by:
            node = soup.select_one("#sellerProfileTriggerId")
            if node:
                sold_by = node.get_text(" ", strip=True)
        if not sold_by:
            node = soup.select_one("#merchant-info")
            if node:
                text = node.get_text(" ", strip=True)
                match = re.search(r"[Ss]old by\s+([^.]+?)(?:\s+and|\.|$)", text)
                if match:
                    sold_by = match.group(1).strip()
                elif re.search(r"[Ss]hips from and sold by Amazon", text):
                    sold_by = ships_from = "Amazon.com"
        if not ships_from:
            node = soup.select_one("#fulfillerInfoFeature_feature_div")
            if node:
                ships_from = node.get_text(" ", strip=True) or None
        return sold_by, ships_from

    @staticmethod
    def _nutrition_card(soup: BeautifulSoup) -> tuple[str, str]:
        """Panel text and ingredients from the nutritional-info widget."""
        for card in soup.select(
            '[cel_widget_id*="nutritional"], [data-cel-widget*="nutritional"], '
            '[data-feature-name*="nutrition"], #nic-container, '
            '#nutrition-facts, .nutrition-facts'
        ):
            text = card.get_text(" | ", strip=True)
            if nutrients.looks_like_panel(text) < 0.3:
                continue
            ingredients = ""
            match = re.search(
                r"Ingredients\s*\|\s*(.+?)(?:\s*\|\s*(?:Product details|Nutrition|Allergen|$))",
                text,
                re.IGNORECASE | re.DOTALL,
            )
            if match:
                ingredients = re.sub(r"\s+", " ", match.group(1)).strip(" |")
            return text, ingredients
        return "", ""

    @staticmethod
    def _similar(url: str, soup: BeautifulSoup) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for section in soup.select(
            '#similarities_feature_div, [data-cel-widget*="similarities"], '
            '[cel_widget_id*="sims"], [data-a-carousel-options], '
            '#sp_detail, #anonCarousel1'
        ):
            for a in section.select("a[href]"):
                asin = _ASIN_IN_URL.search(urlparse(urljoin(url, a["href"])).path)
                if not asin:
                    continue
                canonical = f"https://www.amazon.com/dp/{asin.group(1)}"
                if canonical in seen or canonical == url:
                    continue
                name = (a.get_text(" ", strip=True)
                        or (a.find("img") or {}).get("alt", "")
                        or canonical)
                seen.add(canonical)
                out.append({"name": name[:200], "public_url": canonical,
                            "product_id": None})
        return out[:20]


def _digits(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None
