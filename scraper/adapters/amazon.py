"""Amazon adapter (fixture-driven).

NOTE: Amazon aggressively gates with login walls and bot detection. Live
scraping is out of scope for v1 — this adapter is fixture-driven only. If a
fixture is missing for a URL, the crawler/scraper will skip the page rather
than attempting to fetch it live.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct
from ..parsing import (
    classify_by_jsonld,
    default_extract_links,
    default_parse_product,
)


class AmazonAdapter(RetailerAdapter):
    retailer = "amazon"
    entry_urls = ["https://www.amazon.com/grocery"]
    min_delay = 3.0
    max_delay = 6.0
    fixture_driven = True

    _PRODUCT_PATH = re.compile(r"/dp/|/gp/product/")
    _CATEGORY_PATH = re.compile(r"/(b|s)\?|/grocery")

    def classify(self, url: str, html: str) -> str:
        if self._PRODUCT_PATH.search(url):
            return "product"
        return classify_by_jsonld(html)

    def extract_links(self, url: str, html: str) -> Iterable[CrawlLink]:
        return default_extract_links(
            url,
            html,
            product_path_pattern=self._PRODUCT_PATH,
            category_path_pattern=self._CATEGORY_PATH,
        )

    def parse_product(self, url: str, html: str) -> ScrapedProduct:
        return default_parse_product(url, html)
