"""RetailerAdapter base class.

Each retailer has one subclass that defines:
  * `retailer` — slug used in product IDs and master.json
  * `entry_urls` — seed URLs for the crawler
  * `min_delay`, `max_delay` — polite-delay range
  * `classify(url, html) -> 'category' | 'product'`
  * `extract_links(url, html) -> list[CrawlLink]`
  * `parse_product(url, html) -> ScrapedProduct`

Adapters in this codebase are fixture-driven: when a saved fixture exists at
`scraper/fixtures/{retailer}/{filename}.html`, the adapter reads from disk
instead of fetching live. This keeps the test/integration loop offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import urlparse

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
    name: str = ""
    brand: str = ""
    price: dict = field(default_factory=dict)
    image_urls: list[str] = field(default_factory=list)
    nutrition_label_url: str | None = None
    nutrition: dict = field(default_factory=dict)
    similar_items: list[dict] = field(default_factory=list)


class RetailerAdapter:
    retailer: str = ""
    entry_urls: list[str] = []
    min_delay: float = 1.0
    max_delay: float = 3.0
    # When True, crawler/scraper read from `fixtures/{retailer}/` keyed by URL
    # slug instead of fetching live pages.
    fixture_driven: bool = True

    def __init__(self) -> None:
        if not self.retailer:
            raise ValueError(f"{type(self).__name__} must set `retailer`")
        self.rate_limiter = RateLimiter(self.min_delay, self.max_delay)

    # ----- Fixture support -------------------------------------------------

    def fixture_dir(self) -> Path:
        return FIXTURE_ROOT / self.retailer

    def fixture_for(self, url: str) -> Path | None:
        """Return the fixture path for a URL, or None if no fixture exists.

        The lookup tries, in order:
          * `{retailer}/{last_path_segment}.html`
          * `{retailer}/{md5-ish slug of url}.html`
          * `{retailer}/index.html` (entry pages)
        """
        d = self.fixture_dir()
        if not d.exists():
            return None
        parsed = urlparse(url)
        segs = [s for s in parsed.path.split("/") if s]
        candidates: list[str] = []
        if segs:
            candidates.append(segs[-1])
            candidates.append("_".join(segs))
        candidates.append(re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_"))
        if not segs:
            candidates.append("index")
        for c in candidates:
            p = d / f"{c}.html"
            if p.exists():
                return p
        return None

    # ----- Required overrides ---------------------------------------------

    def classify(self, url: str, html: str) -> PageKind:
        raise NotImplementedError

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        raise NotImplementedError

    def parse_product(self, url: str, html: str) -> ScrapedProduct:
        raise NotImplementedError
