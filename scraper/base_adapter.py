"""RetailerAdapter base class.

Each retailer has one subclass that defines:
  * `retailer` — slug used in product IDs and master.json
  * `entry_urls` — seed URLs for the crawler
  * `classify(url, html) -> 'category' | 'product'`
  * `extract_links(url, html) -> list[CrawlLink]`
  * `parse_product(url, html) -> ScrapedProduct`

How pages are fetched is configuration, not adapter code: `scraper_config.json`
gives each retailer a `mode` of `browser`, `api`, `http` or `fixture`, and
delays come from the same place. An adapter may still override `fetch_product`
when the retailer needs something special — Kroger goes through its official
API rather than its website.

Fixtures remain first-class: whenever a saved file exists at
`scraper/fixtures/{retailer}/{slug}.html` the adapter reads from disk, which is
what keeps the tests offline and lets a parser change be re-run against pages
captured earlier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import urlparse

from . import config
from .rate_limiter import RateLimiter

PageKind = Literal["category", "product"]

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


@dataclass
class CrawlLink:
    url: str
    name: str
    kind: PageKind  # 'category' or 'product'


@dataclass
class ScrapedProduct:
    """What one product page yielded, before downloads or escalation.

    `nutrition` holds whatever the page itself stated; it may well be empty.
    Filling the gaps is the extraction ladder's job, and `scraper.extract`
    records where each value came from.
    """

    name: str = ""
    brand: str = ""
    price: dict = field(default_factory=dict)
    image_urls: list[str] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)  # {public_url, role, alt}
    nutrition_label_url: str | None = None
    nutrition: dict = field(default_factory=dict)
    similar_items: list[dict] = field(default_factory=list)

    # Identity — what lets the same physical product be matched across
    # retailers and looked up in a nutrition database.
    gtin: str | None = None
    upc: str | None = None
    retailer_sku: str | None = None

    # Who is actually selling it. `first_party` is True when the retailer
    # itself is the seller, which is what the "sold directly by" filter uses.
    sold_by: str | None = None
    ships_from: str | None = None
    first_party: bool | None = None

    description: str = ""
    ingredients: str = ""
    categories: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Adapters populate `images`; `image_urls` stays as the flat list the
        # downloader and existing callers expect.
        if self.images and not self.image_urls:
            self.image_urls = [
                i["public_url"] for i in self.images if i.get("public_url")
            ]
        elif self.image_urls and not self.images:
            self.images = [{"public_url": u, "role": "other"} for u in self.image_urls]


# Seller names that mean "the retailer itself", per retailer.
FIRST_PARTY_SELLERS: dict[str, tuple[str, ...]] = {
    "amazon": (
        "amazon.com", "amazon.com services", "amazon.com services llc",
        "amazonfresh", "amazon fresh", "amazon grocery", "amazon retail",
        "whole foods market", "amazon warehouse",
    ),
    "walmart": ("walmart.com", "walmart", "walmart inc"),
    "costco": ("costco", "costco wholesale", "costco.com"),
    "publix": ("publix", "publix super markets", "publix.com"),
    "kroger": ("kroger", "kroger co", "the kroger co"),
}


def is_first_party(retailer: str, seller: str | None) -> bool | None:
    """Whether `seller` is the retailer itself.

    None means the page did not say — which is not the same as "no". Costco
    and Publix name a seller only for the marketplace items they carry, so an
    absent seller there means the retailer sells it; the adapters decide that,
    not this function.
    """
    if not seller:
        return None
    needle = re.sub(r"[^a-z0-9. ]+", "", seller.lower()).strip()
    for known in FIRST_PARTY_SELLERS.get(retailer, ()):  # noqa: SIM110
        if needle == known or needle.startswith(known):
            return True
    return False


class RetailerAdapter:
    retailer: str = ""
    entry_urls: list[str] = []
    min_delay: float = 1.0
    max_delay: float = 3.0
    # Selector to wait for before reading a product page, and controls to click
    # first (several retailers keep nutrition inside a collapsed accordion).
    product_wait_for: str | None = None
    product_expand: tuple[str, ...] = ()

    def __init__(self) -> None:
        if not self.retailer:
            raise ValueError(f"{type(self).__name__} must set `retailer`")
        cfg = config.retailer_config(self.retailer)
        self.settings = cfg
        self.mode: str = cfg["mode"]
        self.min_delay = cfg["min_delay"]
        self.max_delay = cfg["max_delay"]
        self.first_party_only: bool = cfg["first_party_only"]
        self.rate_limiter = RateLimiter(self.min_delay, self.max_delay)

    @property
    def fixture_driven(self) -> bool:
        return self.mode == "fixture"

    # ----- Fixture support -------------------------------------------------

    def fixture_dir(self) -> Path:
        return FIXTURE_ROOT / self.retailer

    def fixture_for(self, url: str) -> Path | None:
        """Return the fixture path for a URL, or None if no fixture exists.

        The lookup tries, in order:
          * `{retailer}/{last_path_segment}.html`
          * `{retailer}/{joined path segments}.html`
          * `{retailer}/{slugified url}.html`
          * `{retailer}/index.html` (entry pages)
        """
        d = self.fixture_dir()
        if not d.exists():
            return None
        parsed = urlparse(url)
        segs = [s for s in parsed.path.split("/") if s]
        candidates: list[str] = []
        if segs:
            # Costco's product URLs already end in ".html"; without stripping
            # it the lookup would ask for "...html.html".
            candidates.append(re.sub(r"\.html?$", "", segs[-1], flags=re.IGNORECASE))
            candidates.append(segs[-1])
            candidates.append("_".join(segs))
        candidates.append(re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_"))
        if not segs:
            candidates.append("index")
        for c in candidates:
            for suffix in (".html", ".json"):
                p = d / f"{c}{suffix}"
                if p.exists():
                    return p
        return None

    # ----- Fetching --------------------------------------------------------

    async def fetch(self, url: str, *, kind: PageKind = "product") -> str:
        """Fetch one page's HTML according to the configured mode.

        A saved fixture always wins, whatever the mode, so that re-parsing runs
        and tests stay offline.
        """
        fixture = self.fixture_for(url)
        if fixture is not None:
            return fixture.read_text(encoding="utf-8")
        if self.mode == "fixture":
            raise FileNotFoundError(
                f"{self.retailer} is in fixture mode and no fixture exists for {url}"
            )

        await self.rate_limiter.wait()
        if self.mode in ("browser", "api"):
            # 'api' adapters override fetch_product; their category pages and
            # any page-level fallback still go through the browser.
            from .fetch.browser import fetch_rendered

            return await fetch_rendered(
                self.retailer,
                url,
                wait_for=self.product_wait_for if kind == "product" else None,
                expand=self.product_expand if kind == "product" else (),
            )
        if self.mode == "http":
            from .fetch.browser import BotWallError, detect_bot_wall
            from .fetch.http import client

            async with client() as http:
                response = await http.get(url)
                wall = detect_bot_wall(response.text, status=response.status_code)
                if wall:
                    raise BotWallError(self.retailer, url, wall)
                response.raise_for_status()
                return response.text
        raise ValueError(f"unknown fetch mode '{self.mode}' for {self.retailer}")

    async def fetch_product(self, url: str) -> tuple[str, ScrapedProduct]:
        """Fetch and parse one product.

        Adapters backed by a real API (Kroger) override this and never look at
        HTML; the string they return is the snapshot written to
        `data/html/{product_id}.html`.
        """
        html = await self.fetch(url, kind="product")
        return html, self.parse_product(url, html)

    # ----- Required overrides ---------------------------------------------

    def classify(self, url: str, html: str) -> PageKind:
        raise NotImplementedError

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        raise NotImplementedError

    def parse_product(self, url: str, html: str) -> ScrapedProduct:
        raise NotImplementedError
