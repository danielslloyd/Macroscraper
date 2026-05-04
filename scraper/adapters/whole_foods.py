"""Whole Foods Market adapter.

Reference implementation. Fixture-driven by default — drop a saved HTML page
into `scraper/fixtures/whole_foods/` and the crawler/scraper will read it
instead of going live. To enable live crawling, set `fixture_driven = False`
on the instance.

Page classification is JSON-LD-driven: any page that publishes a `Product`
schema block is treated as a product (leaf), everything else is a category.
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


class WholeFoodsAdapter(RetailerAdapter):
    retailer = "whole_foods"
    entry_urls = ["https://www.wholefoodsmarket.com/products"]
    min_delay = 1.5
    max_delay = 3.5

    _PRODUCT_PATH = re.compile(r"/product/")
    _CATEGORY_PATH = re.compile(r"/products($|/)")

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
