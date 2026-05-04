# Food Scraper

Local-first food data pipeline. Scrapes packaged food product pages from major retail websites, downloads associated data and images locally, and runs raw scraped data through local Ollama models for cleaning and structured extraction. A React/Vite UI provides a tree-based interface for browsing crawled pages and managing the scrape/clean queue.

## Quick start (Windows)

```
run.bat
```

This creates a Python venv, installs dependencies, installs Playwright Chromium, installs UI npm dependencies, starts FastAPI on `http://localhost:8000`, starts Vite on `http://localhost:5173`, and opens the UI in a browser.

## Quick start (manual)

```bash
python -m venv venv
source venv/bin/activate          # or: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python -m orchestrator.main &
cd ui && npm install && npm run dev
```

## Configuration

- `cleaner_config.json` — Ollama model list (sequential cleaner pass)
- Ollama is expected at `http://localhost:11434` with the configured models pulled.

## Layout

See `CLAUDE.md` for the source-of-truth project specification.

## Adding a new retailer

Adapters are subclasses of `RetailerAdapter` (`scraper/base_adapter.py`).
Most retail sites publish a `Product` JSON-LD block, so the shared
`default_parse_product` / `default_extract_links` helpers in
`scraper/parsing.py` usually do the work — your adapter just declares URL
patterns. Walk through the steps below in order; the whole loop should take
10–20 minutes if the retailer publishes JSON-LD.

Worked example: adding **Sprouts Farmers Market** (`sprouts`).

### 1. Pick a slug

Use lowercase snake_case. The slug is the directory name, the routing key,
and (truncated) the prefix in product IDs. Example: `sprouts`.

### 2. Register the slug for product IDs

Edit `orchestrator/storage.py` and add an entry to `_RETAILER_SLUGS`. The
short prefix is what shows up in product IDs (e.g. `sprouts_a3f9c12b`).

```python
_RETAILER_SLUGS = {
    ...,
    "sprouts": "sprouts",
}
```

If you skip this, IDs will fall back to the full slug — fine, just longer.

### 3. Create the adapter

Copy `scraper/adapters/whole_foods.py` to
`scraper/adapters/sprouts.py` and edit the class:

```python
# scraper/adapters/sprouts.py
import re
from typing import Iterable

from ..base_adapter import CrawlLink, RetailerAdapter, ScrapedProduct
from ..parsing import (
    classify_by_jsonld,
    default_extract_links,
    default_parse_product,
)


class SproutsAdapter(RetailerAdapter):
    retailer = "sprouts"                          # must match the slug
    entry_urls = ["https://shop.sprouts.com/store"]
    min_delay = 1.5                               # polite delay floor (sec)
    max_delay = 3.5                               # polite delay ceiling (sec)
    fixture_driven = True                         # flip to False for live

    # URL-path regexes that classify a link as product vs. category.
    _PRODUCT_PATH = re.compile(r"/product/")
    _CATEGORY_PATH = re.compile(r"/(store|category)/")

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
```

To find the right regexes, open the retailer in a browser, click into a
category and a product page, and note the differing path segments
(`/product/` vs `/category/`, `/p/` vs `/d/`, `/ip/` vs `/cp/`, etc.).

### 4. Register the adapter

Edit `scraper/adapters/__init__.py`:

```python
from .sprouts import SproutsAdapter

ADAPTERS = {
    ...,
    "sprouts": SproutsAdapter,
}
```

Once registered, the slug appears in `GET /api/retailers` and the UI sidebar
automatically.

### 5. (Optional) Add a pretty name in the UI

Edit `ui/src/components/RetailerSidebar.jsx` and add a `PRETTY` entry:

```js
const PRETTY = {
  ...,
  sprouts: "Sprouts",
};
```

Without this the sidebar just shows the slug.

### 6. Capture fixtures (recommended for development)

Save sanitized HTML pages into `scraper/fixtures/sprouts/`. The fixture
loader matches on the URL's last path segment:

```
scraper/fixtures/sprouts/
  store.html                              # entry URL: .../store
  category-name.html                      # category page
  product-name.html                       # one product per file
```

Tip: use your browser's "Save Page As → HTML Only", or `curl -A "Mozilla/..."
URL > file.html`, then trim ad/tracking scripts.

With fixtures in place the crawler runs fully offline, which is what every
test in `tests/` relies on.

### 7. Verify

```bash
# Static check — adapter wiring imports cleanly
python -c "from scraper.adapters import get_adapter; get_adapter('sprouts')"

# Crawl + scrape using fixtures
python -c "
import asyncio
from scraper.adapters import get_adapter
from scraper.crawler import crawl, scrape_product

async def main():
    a = get_adapter('sprouts')
    tree = await crawl(a, max_depth=3)
    print('product nodes:', sum(1 for _ in str(tree).split('\"kind\": \"product\"')) - 1)

asyncio.run(main())
"

# Or via the API
curl -X POST http://localhost:8000/api/crawl/sprouts
curl http://localhost:8000/api/tree/sprouts
```

Add a fixture-driven test in `tests/` mirroring `test_pipeline.py` so
regressions get caught.

### 8. Going live

When you're ready to scrape the real site, set `fixture_driven = False` on
the adapter class (or per instance). The crawler will then fetch through
`httpx` with the configured polite delay. For sites that require JavaScript
rendering, swap the `httpx` fetch in `scraper/crawler.py` for a Playwright
page load — this is left as a per-adapter follow-up because the right
strategy varies by site (login walls, infinite scroll, bot detection).

### Checklist

- [ ] Slug added to `_RETAILER_SLUGS` in `orchestrator/storage.py`
- [ ] `scraper/adapters/{slug}.py` created with correct `retailer`, `entry_urls`, and path regexes
- [ ] Adapter imported and added to `ADAPTERS` in `scraper/adapters/__init__.py`
- [ ] (Optional) Pretty name in `ui/src/components/RetailerSidebar.jsx`
- [ ] At least one product fixture under `scraper/fixtures/{slug}/`
- [ ] `get_adapter('{slug}')` succeeds and `/api/retailers` includes the slug
- [ ] One end-to-end scrape produces a populated record in `master.json`
