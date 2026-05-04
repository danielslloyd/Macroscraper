"""Costco adapter (fixture-driven)."""
from __future__ import annotations

import re
from typing import Iterable

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct
from ..parsing import (
    classify_by_jsonld,
    default_extract_links,
    default_parse_product,
)


class CostcoAdapter(RetailerAdapter):
    retailer = "costco"
    entry_urls = ["https://www.costco.com/grocery-household.html"]
    min_delay = 2.0
    max_delay = 4.0

    _PRODUCT_PATH = re.compile(r"\.product\.")
    _CATEGORY_PATH = re.compile(r"\.html$")

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
