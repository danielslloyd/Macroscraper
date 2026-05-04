"""Depth-first crawler.

Builds a nested tree of category/product nodes starting from each adapter
entry URL. Category pages provide structure only; product pages are leaves.
The full tree is persisted to master.json under `retailers.{slug}.tree`.

Crawling is fixture-driven when the adapter sets `fixture_driven = True`: the
crawler reads HTML from `scraper/fixtures/{retailer}/` rather than going
live. This keeps tests offline; live mode runs through Playwright.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import httpx

from orchestrator.storage import (
    product_id_for,
    set_retailer_tree,
)

from .base_adapter import CrawlLink, RetailerAdapter

HtmlFetcher = Callable[[str], Awaitable[str]]


async def _fixture_fetch(adapter: RetailerAdapter, url: str) -> str | None:
    p = adapter.fixture_for(url)
    if p is None:
        return None
    return p.read_text(encoding="utf-8")


async def _http_fetch(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()
    return resp.text


async def crawl(
    adapter: RetailerAdapter,
    max_depth: int = 4,
    fetcher: HtmlFetcher | None = None,
) -> dict:
    """Crawl the adapter's entry URLs, returning the persisted tree."""
    visited: set[str] = set()
    client: httpx.AsyncClient | None = None
    if fetcher is None and not adapter.fixture_driven:
        client = httpx.AsyncClient()

    async def fetch(url: str) -> str | None:
        if fetcher is not None:
            return await fetcher(url)
        if adapter.fixture_driven:
            return await _fixture_fetch(adapter, url)
        await adapter.rate_limiter.wait()
        assert client is not None
        return await _http_fetch(client, url)

    async def walk(link: CrawlLink, depth: int) -> dict:
        node: dict = {
            "name": link.name,
            "url": link.url,
            "kind": link.kind,
        }
        if link.kind == "product":
            node["product_id"] = product_id_for(adapter.retailer, link.url)
            return node
        if depth >= max_depth or link.url in visited:
            node["children"] = []
            return node
        visited.add(link.url)
        html = await fetch(link.url)
        if html is None:
            node["children"] = []
            return node
        children: list[dict] = []
        for child in adapter.extract_links(link.url, html):
            if child.url in visited and child.kind == "category":
                continue
            children.append(await walk(child, depth + 1))
        node["children"] = children
        return node

    try:
        roots: list[dict] = []
        for entry in adapter.entry_urls:
            roots.append(
                await walk(CrawlLink(url=entry, name=entry, kind="category"), 0)
            )
        tree = {"name": adapter.retailer, "kind": "category", "children": roots}
        await set_retailer_tree(adapter.retailer, tree)
        return tree
    finally:
        if client is not None:
            await client.aclose()


async def scrape_product(
    adapter: RetailerAdapter,
    url: str,
    fetcher: HtmlFetcher | None = None,
) -> tuple[str, str]:
    """Fetch a product page and return (raw_html, source_url)."""
    if fetcher is not None:
        html = await fetcher(url)
    elif adapter.fixture_driven:
        html = await _fixture_fetch(adapter, url)
        if html is None:
            raise FileNotFoundError(f"no fixture for {url}")
    else:
        await adapter.rate_limiter.wait()
        async with httpx.AsyncClient() as client:
            html = await _http_fetch(client, url)
    return html, url
