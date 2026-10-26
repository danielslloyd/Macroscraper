"""Kroger adapter, backed by the official Kroger Developer API.

Kroger's website is the one in this set that would not serve an automated
request at all. It does, however, publish a real API, which is a better deal
in every respect: structured records, no bot wall, documented rate limits, and
an image set labelled by perspective (front, back, left, right, top, bottom).

    developer.kroger.com -> create an app -> Product API
    set KROGER_CLIENT_ID and KROGER_CLIENT_SECRET

What the API does not return is nutrition. What it does return is a photograph
of the back of the pack, which is where the panel is printed -- so Kroger
products come out of tier 1 with identity, price and images, and their
nutrition comes from OCR of that back image (tier 3) or from the barcode
databases (tier 2), for which Kroger's own product id *is* the UPC.

`filter.locationId` decides whose prices you get; set `retailers.kroger.
location_id` in `scraper_config.json` to the store you care about, or leave it
null for catalogue data without prices.
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterable

from .. import config
from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct
from ..extract import nutrients
from ..fetch.http import get_json, post_form
from ..parsing import soup_of

TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
PRODUCT_URL = "https://api.kroger.com/v1/products/{product_id}"
SEARCH_URL = "https://api.kroger.com/v1/products"
# Which photograph is most likely to carry the Nutrition Facts panel.
_PANEL_PERSPECTIVES = ("back", "right", "left", "top")
_SIZE_RANK = {"xlarge": 0, "large": 1, "medium": 2, "small": 3, "thumbnail": 4}
_PRODUCT_ID_IN_URL = re.compile(r"/p/(?:[^/]+/)?(\d{8,14})")


class KrogerCredentialsMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Kroger API credentials are not set. Create an app at "
            "developer.kroger.com and export KROGER_CLIENT_ID and "
            "KROGER_CLIENT_SECRET, or set retailers.kroger.mode to 'browser' "
            "in scraper_config.json to scrape the website instead."
        )


class _Token:
    """A cached client-credentials token, refreshed a minute before expiry."""

    def __init__(self) -> None:
        self.value: str | None = None
        self.expires_at: float = 0.0

    def valid(self) -> bool:
        return bool(self.value) and time.time() < self.expires_at - 60


_token = _Token()


async def access_token(*, scope: str = "product.compact") -> str:
    if _token.valid():
        assert _token.value is not None
        return _token.value
    client_id = config.env("KROGER_CLIENT_ID")
    client_secret = config.env("KROGER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise KrogerCredentialsMissing()
    body = await post_form(
        TOKEN_URL,
        {"grant_type": "client_credentials", "scope": scope},
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    _token.value = body["access_token"]
    _token.expires_at = time.time() + float(body.get("expires_in", 1800))
    return _token.value


class KrogerAdapter(RetailerAdapter):
    retailer = "kroger"
    entry_urls = ["https://www.kroger.com/d/shop"]
    # Terms used to discover products when crawling through the API.
    discovery_terms = (
        "milk", "bread", "yogurt", "cereal", "cheese", "peanut butter",
        "pasta", "rice", "juice", "snack bars", "frozen vegetables", "eggs",
    )
    snapshot_kind = "json"

    _PRODUCT_PATH = re.compile(r"/p/")
    _CATEGORY_PATH = re.compile(r"/d/|/pl/")

    # ----- identity --------------------------------------------------------

    @staticmethod
    def product_id_from_url(url: str) -> str | None:
        """Kroger product URLs end in the item's 13-digit UPC."""
        match = _PRODUCT_ID_IN_URL.search(url)
        return match.group(1) if match else None

    @staticmethod
    def url_for(product_id: str, slug: str = "p") -> str:
        return f"https://www.kroger.com/p/{slug}/{product_id}"

    def location_id(self) -> str | None:
        value = self.settings.get("location_id")
        return str(value) if value else None

    # ----- fetching --------------------------------------------------------

    async def fetch_product(self, url: str) -> tuple[str, ScrapedProduct]:
        """Fetch one product from the API (or a fixture, when one exists)."""
        fixture = self.fixture_for(url)
        if fixture is not None:
            text = fixture.read_text(encoding="utf-8")
            if fixture.suffix == ".json" or text.lstrip().startswith("{"):
                payload = json.loads(text)
                # Fixtures hold the whole API envelope, as the endpoint
                # returns it; the record itself is under "data".
                record = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(record, list):
                    record = record[0] if record else {}
                return text, self.parse_api(record if isinstance(record, dict) else payload, url)
            return text, self.parse_product(url, text)

        if self.mode != "api":
            return await super().fetch_product(url)

        product_id = self.product_id_from_url(url)
        if not product_id:
            raise ValueError(f"no Kroger product id in {url}")
        payload = await self.fetch_api_product(product_id)
        return json.dumps(payload, indent=2), self.parse_api(payload.get("data") or {}, url)

    async def fetch_api_product(self, product_id: str) -> dict:
        await self.rate_limiter.wait()
        token = await access_token()
        params: dict = {}
        location = self.location_id()
        if location:
            params["filter.locationId"] = location
        payload = await get_json(
            PRODUCT_URL.format(product_id=product_id),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params or None,
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected Kroger API response for {product_id}")
        return payload

    async def search(self, term: str, *, limit: int = 50) -> list[dict]:
        """Search the catalogue. Used for discovery, since the site is closed."""
        await self.rate_limiter.wait()
        token = await access_token()
        params: dict = {"filter.term": term, "filter.limit": min(limit, 50)}
        location = self.location_id()
        if location:
            params["filter.locationId"] = location
        payload = await get_json(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
        )
        if not isinstance(payload, dict):
            return []
        return [item for item in payload.get("data") or [] if isinstance(item, dict)]

    async def discover_products(self, *, per_term: int = 50) -> list[CrawlLink]:
        """Build a product list from the API instead of crawling the site.

        Kroger's category pages cannot be fetched, so the tree for this
        retailer is one node per discovery term with its products beneath it.
        """
        links: list[CrawlLink] = []
        seen: set[str] = set()
        for term in self.discovery_terms:
            try:
                results = await self.search(term, limit=per_term)
            except Exception:
                continue
            children: list[CrawlLink] = []
            for item in results:
                product_id = item.get("productId")
                if not product_id or product_id in seen:
                    continue
                seen.add(product_id)
                children.append(CrawlLink(
                    url=self.url_for(product_id, _slug(item.get("description", ""))),
                    name=item.get("description") or product_id,
                    kind="product",
                ))
            if children:
                links.append(CrawlLink(url=f"kroger:term/{term}", name=term,
                                       kind="category"))
                links.extend(children)
        return links

    # ----- crawl (website fallback) ----------------------------------------

    def classify(self, url: str, html: str) -> str:
        return "product" if self._PRODUCT_PATH.search(url) else "category"

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        from urllib.parse import urljoin, urlparse

        soup = soup_of(html)
        seen: set[str] = set()
        for a in soup.select("a[href]"):
            href = urljoin(url, a["href"]).split("#", 1)[0].split("?", 1)[0]
            parsed = urlparse(href)
            if parsed.netloc and "kroger.com" not in parsed.netloc:
                continue
            if href in seen or href == url:
                continue
            name = a.get_text(" ", strip=True)
            if not name:
                continue
            seen.add(href)
            if self._PRODUCT_PATH.search(parsed.path):
                yield CrawlLink(url=href, name=name, kind="product")
            elif self._CATEGORY_PATH.search(parsed.path):
                yield CrawlLink(url=href, name=name, kind="category")

    # ----- parse -----------------------------------------------------------

    def parse_api(self, data: dict, url: str = "") -> ScrapedProduct:
        """Turn one Kroger API product record into a ScrapedProduct."""
        product = ScrapedProduct()
        product.retailer_sku = data.get("productId") or self.product_id_from_url(url)
        product.name = (data.get("description") or "").strip()
        product.brand = (data.get("brand") or "").strip()
        product.upc = _digits(data.get("upc") or data.get("productId"))
        product.gtin = product.upc
        product.categories = [c for c in (data.get("categories") or []) if c]
        product.sold_by = "Kroger"
        # The Product API returns Kroger's own catalogue only.
        product.first_party = True

        item = _primary_item(data.get("items") or [])
        product.price = _price(item)
        product.attributes = {
            key: value for key, value in {
                "country_origin": data.get("countryOrigin"),
                "temperature_indicator": (data.get("temperature") or {}).get("indicator"),
                "size": item.get("size"),
                "sold_by_measure": item.get("soldBy"),
                "aisle": _aisle(data.get("aisleLocations") or []),
            }.items() if value
        }
        product.images = _images(data.get("images") or [])
        for image in product.images:
            if image.get("role") == "back":
                product.nutrition_label_url = image["public_url"]
                break
        # Kroger's Product API carries no nutrition; the ladder reads it from
        # the back-of-pack photograph or from the barcode databases.
        product.nutrition = {}
        return product

    def parse_product(self, url: str, html: str) -> ScrapedProduct:
        """Parse a kroger.com page, for runs configured in browser mode."""
        soup = soup_of(html)
        product = ScrapedProduct()
        product.retailer_sku = self.product_id_from_url(url)
        product.upc = _digits(product.retailer_sku)
        product.gtin = product.upc
        heading = soup.find("h1")
        product.name = heading.get_text(" ", strip=True) if heading else ""
        brand = soup.select_one("[data-testid*='brand'], [class*='brand']")
        product.brand = brand.get_text(" ", strip=True) if brand else ""
        product.sold_by = "Kroger"
        product.first_party = True

        images: list[dict] = []
        for img in soup.select("img[src]"):
            src = img.get("src") or ""
            if "kroger.com/product/images" in src and src not in [
                i["public_url"] for i in images
            ]:
                role = next(
                    (p for p in _PANEL_PERSPECTIVES + ("front",) if f"/{p}/" in src.lower()),
                    "other",
                )
                images.append({"public_url": src, "role": role,
                               "alt": img.get("alt", "")})
        product.images = images

        for region in soup.select("[class*='nutrition'], [id*='nutrition']"):
            text = region.get_text(" | ", strip=True)
            if nutrients.looks_like_panel(text) >= 0.3:
                product.nutrition = nutrients.parse_panel_text(text)
                break
        return product


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------


def _primary_item(items: list) -> dict:
    for item in items:
        if isinstance(item, dict) and item.get("price"):
            return item
    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def _price(item: dict) -> dict:
    price: dict = {}
    values = item.get("price") or {}
    promo = nutrients.to_number(values.get("promo"))
    regular = nutrients.to_number(values.get("regular"))
    amount = promo if promo else regular
    if amount is not None:
        price["package_price"] = amount
    if promo and regular and promo != regular:
        price["regular_price"] = regular
    size = item.get("size")
    if size:
        price["package_size"] = size
    sold_by = item.get("soldBy")
    if sold_by:
        price["sold_by_measure"] = sold_by
    return price


def _aisle(locations: list) -> str | None:
    for location in locations:
        if isinstance(location, dict):
            description = location.get("description")
            if description:
                return str(description)
    return None


def _images(images: list) -> list[dict]:
    """Kroger's image set, largest size per perspective, front first.

    The `perspective` label is what makes Kroger cheap to extract nutrition
    from: the panel is on the back, so the OCR tier can be pointed straight at
    the right photograph instead of reading the whole gallery.
    """
    out: list[dict] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        perspective = (image.get("perspective") or "other").lower()
        sizes = [s for s in image.get("sizes") or [] if isinstance(s, dict) and s.get("url")]
        if not sizes:
            continue
        sizes.sort(key=lambda s: _SIZE_RANK.get((s.get("size") or "").lower(), 9))
        out.append({
            "public_url": sizes[0]["url"],
            "role": perspective,
            "alt": perspective,
            "featured": bool(image.get("featured")),
        })
    order = {"front": 0, "back": 1}
    out.sort(key=lambda i: (order.get(i["role"], 2), not i.get("featured")))
    return out


def _digits(value) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) >= 8 else None


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "p"
