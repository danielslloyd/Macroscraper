"""Depth-first crawler.

Builds a nested tree of category/product nodes starting from each adapter
entry URL. Category pages provide structure only; product pages are leaves.
The full tree is persisted to master.json under `retailers.{slug}.tree`.

How each page is fetched is the adapter's business (`RetailerAdapter.fetch`),
which means the same crawl code works against a live browser session, a saved
fixture, or -- for Kroger, whose site will not serve an automated request --
the retailer's own API via `discover_products`.

A bot check stops that branch and is recorded on the node rather than raised,
so one blocked category does not throw away the rest of a long crawl.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from orchestrator.storage import product_id_for, set_retailer_tree

from .base_adapter import CrawlLink, RetailerAdapter
from .fetch.browser import BotWallError, FetchError

HtmlFetcher = Callable[[str], Awaitable[str]]


async def crawl(
    adapter: RetailerAdapter,
    max_depth: int = 4,
    fetcher: HtmlFetcher | None = None,
) -> dict:
    """Crawl the adapter's entry URLs, returning the persisted tree."""
    visited: set[str] = set()

    async def fetch(url: str) -> str | None:
        if fetcher is not None:
            return await fetcher(url)
        try:
            return await adapter.fetch(url, kind="category")
        except FileNotFoundError:
            return None

    # Retailers whose catalogue has to be discovered through an API rather
    # than by following links.
    discover = getattr(adapter, "discover_products", None)
    if discover is not None and adapter.mode == "api" and fetcher is None:
        tree = await _api_tree(adapter, discover)
        await set_retailer_tree(adapter.retailer, tree)
        return tree

    async def walk(link: CrawlLink, depth: int) -> dict:
        node: dict = {"name": link.name, "url": link.url, "kind": link.kind}
        if link.kind == "product":
            node["product_id"] = product_id_for(adapter.retailer, link.url)
            return node
        if depth >= max_depth or link.url in visited:
            node["children"] = []
            return node
        visited.add(link.url)
        try:
            html = await fetch(link.url)
        except BotWallError as exc:
            node["children"] = []
            node["blocked"] = exc.signal
            return node
        except FetchError as exc:
            node["children"] = []
            node["error"] = str(exc)
            return node
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

    roots: list[dict] = []
    for entry in adapter.entry_urls:
        roots.append(await walk(CrawlLink(url=entry, name=entry, kind="category"), 0))
    tree = {"name": adapter.retailer, "kind": "category", "children": roots}
    await set_retailer_tree(adapter.retailer, tree)
    return tree


async def _api_tree(adapter: RetailerAdapter, discover) -> dict:
    """Build the tree from an API product listing.

    `discover_products` returns a flat sequence where each category link is
    followed by its products, which is enough to nest them one level deep --
    the API exposes search terms, not the site's category hierarchy.
    """
    roots: list[dict] = []
    current: dict | None = None
    for link in await discover():
        if link.kind == "category":
            current = {"name": link.name, "url": link.url, "kind": "category",
                       "children": []}
            roots.append(current)
            continue
        node = {
            "name": link.name,
            "url": link.url,
            "kind": "product",
            "product_id": product_id_for(adapter.retailer, link.url),
        }
        if current is None:
            current = {"name": adapter.retailer, "url": "", "kind": "category",
                       "children": []}
            roots.append(current)
        current["children"].append(node)
    return {"name": adapter.retailer, "kind": "category", "children": roots}


async def scrape_product(
    adapter: RetailerAdapter,
    url: str,
    fetcher: HtmlFetcher | None = None,
) -> tuple[str, str]:
    """Fetch a product page and return (raw_html, source_url).

    Kept for callers that only want the raw page; the scrape job itself goes
    through `adapter.fetch_product`, which also covers API-backed retailers.
    """
    if fetcher is not None:
        html = await fetcher(url)
    else:
        html = await adapter.fetch(url, kind="product")
    return html, url
