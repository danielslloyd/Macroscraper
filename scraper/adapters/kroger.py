"""Kroger adapter (fixture-driven)."""
from __future__ import annotations

import re
from typing import Iterable

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct
from ..parsing import (
    classify_by_jsonld,
    default_extract_links,
    default_parse_product,
)


class KrogerAdapter(RetailerAdapter):
    retailer = "kroger"
    entry_urls = ["https://www.kroger.com/d/shop"]
    min_delay = 1.5
    max_delay = 3.5

    _PRODUCT_PATH = re.compile(r"/p/")
    _CATEGORY_PATH = re.compile(r"/d/")

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
