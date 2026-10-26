# Food Scraper

Local-first food data pipeline. Scrapes packaged food product pages from major retail websites, downloads associated data and images locally, and runs raw scraped data through local Ollama models for cleaning and structured extraction. A React/Vite UI provides a tree-based interface for browsing crawled pages and managing the scrape/clean queue.

Live adapters: **Kroger**, **Amazon**, **Walmart**, **Costco**, **Publix** (plus fixture-driven Whole Foods and Trader Joe's). For each product the pipeline collects the photo URLs, the price, the barcode, who is selling it, and a nutrition record — assembled from the page's own data, a barcode database, or OCR of the nutrition panel in the packaging photo, escalating to a model only for the products where none of that works.

- [Nutrition extraction](#nutrition-extraction) — the tier ladder and how to read its provenance
- [Live scraping](#live-scraping) — fetch modes, bot checks, Kroger API credentials

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

All configuration is file-based; there is no settings UI.

- `scraper_config.json` — per-retailer fetch mode, polite delays, the
  `first_party_only` filter, the nutrition tier order and its confidence
  threshold, the OCR engine, and model names.
- `cleaner_config.json` — Ollama model list for the sequential cleaner pass.
- Ollama is expected at `http://localhost:11434` with the configured models pulled.

Environment variables, all optional:

| Variable | What it enables |
|---|---|
| `KROGER_CLIENT_ID`, `KROGER_CLIENT_SECRET` | the Kroger adapter (free app at developer.kroger.com) |
| `FDC_API_KEY` | the USDA FoodData Central tier (free key at fdc.nal.usda.gov) |
| `ANTHROPIC_API_KEY` | the Claude tier, when also enabled in `scraper_config.json` |
| `OLLAMA_HOST` | a non-default Ollama address for the cleaner |

## Nutrition extraction

Nutrition is assembled by a ladder of tiers, cheapest and most authoritative
first. Each tier runs only if the record so far is not yet good enough, so most
products never reach a model:

| Tier | Source | Notes |
|---|---|---|
| 1 | `page_html` | the retailer's own structured data. Amazon and Walmart publish complete panels; Costco and Publix usually publish none |
| 2 | `barcode_db` | Open Food Facts, then USDA FoodData Central, keyed on the UPC/GTIN from the page |
| 2 | `name_db` | Open Food Facts matched on brand + name, for products published without a barcode (all of Publix, some of Costco) |
| 3 | `ocr` | RapidOCR over the packaging photos, then a regex panel parser. Local, offline, about a second per image |
| 4 | `local_vision` | a local Ollama vision model reads the panel photo |
| 5 | `claude` | the Claude API reads the panel photo. Off by default |

Two mechanisms keep the result honest.

**A confidence score decides when to stop.** A record is scored on how many
core fields it has and on whether its macros can account for its stated
calories (4 kcal/g protein and carbohydrate, 9 kcal/g fat). A record whose
Atwater estimate is more than 35% away from its printed calories is capped
below the acceptance threshold however complete it looks — which is what
catches a bad community-database entry or a misread panel and sends it to the
next tier.

**Every value is traceable.** `nutrition_sources[]` records each tier that ran,
what it found, whether its values were used, and why not when they were not:

```json
"nutrition_confidence": 1.0,
"nutrition_sources": [
  { "source": "page_html", "tier": 1, "fields": {}, "accepted": false },
  { "source": "barcode_db", "tier": 2, "accepted": false, "code_tried": [] },
  { "source": "ocr", "tier": 3, "accepted": true, "image_index": 1,
    "image": "data/images/costco_c621ab22_1.jpg",
    "raw_text": "Nutrition Facts About 50 servings per container ...",
    "field_score": { "completeness": 1.0, "calorie_error": 0.133 } }
]
```

Which photo holds the panel is worked out without a model: each candidate is
OCR'd and scored on how much it reads like a Nutrition Facts panel, best
candidates first, stopping at the first confident hit. The winner is copied to
`data/labels/{product_id}_label.jpg` and recorded in
`nutrition_label_image`. Disagreements between tiers are kept in
`nutrition_conflicts[]` rather than silently resolved.

To change the ladder, edit `nutrition.tiers` and `nutrition.accept_confidence`
in `scraper_config.json`. Dropping `local_vision` and `claude` from the list
makes extraction entirely model-free.

## Live scraping

Each retailer has a `mode` in `scraper_config.json`:

- **`browser`** (Amazon, Walmart, Costco, Publix) — a persistent Chrome profile
  under `data/browser-profile`, driven by Playwright. One profile for the whole
  project, so cookies and consent state persist between runs.
- **`api`** (Kroger) — the official Kroger Product API. Kroger's site does not
  serve automated requests at all; the API returns better data anyway, including
  photographs labelled by perspective, so the panel is read from the one marked
  `back`.
- **`http`** — plain httpx, for a site that needs no JavaScript.
- **`fixture`** — read only from `scraper/fixtures/{retailer}/`.

A saved fixture always wins over the network, whatever the mode, which is what
keeps the tests offline.

### When a retailer serves a bot check

Walmart reliably, and Amazon and Costco occasionally, answer an automated
request with a challenge page instead of the product. The scraper detects this
and stops rather than storing an empty record: the product is flagged with
`flag_reason: "bot_wall"` and the crawl marks that branch `blocked`.

Nothing here tries to defeat a bot check. To get past one, clear it yourself:

```bash
python -m scraper.unblock walmart
```

That opens the retailer in a visible browser window using the project's own
profile. Clear the check, settle the cookie banner, and set a store or ZIP code
if the retailer asks for one — then press Enter in the terminal. Whatever
session that establishes is kept in the profile and reused by later headless
runs. It is also how you get Publix to show its nutrition panels, which it
renders only once a store is selected.

### Items sold directly by the retailer

`first_party_only` is on for Amazon and Walmart. Each adapter reads the seller
from the page — Amazon's "Sold by" / "Ships from" block, Walmart's `sellerName`
and `isSoldByWalmart` — and records it as `sold_by` plus a `first_party` flag.
With the filter on, a marketplace listing is not stored as a product: it is
flagged `third_party_seller`, so you can see what was skipped and why.

Costco and Publix sell their own catalogues, so `first_party` is true there
unless the page names another seller. Kroger's Product API only returns
Kroger's own catalogue.

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

Fetch mode and delays are not adapter code — add the retailer to
`scraper_config.json`:

```json
"sprouts": {
  "enabled": true,
  "mode": "fixture",
  "min_delay": 1.5,
  "max_delay": 3.5,
  "first_party_only": false
}
```

Start on `fixture` while developing the parser, then switch to `browser` (or
`http`, if the site renders server-side) to go live. If the retailer embeds its
data in a `__NEXT_DATA__` script or an App Router flight payload rather than in
the HTML, use the helpers in `scraper/embedded.py` and locate the product
record by shape — `find_dict(payload, has_keys("usItemId"))` — rather than by a
path that the retailer's next deploy will move.

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

Change the retailer's `mode` in `scraper_config.json` from `fixture` to
`browser` (or `http`). Nothing else changes: delays, the bot-wall check, and
the nutrition ladder are all mode-independent. If the first live fetch raises
`BotWallError`, clear the check once with `python -m scraper.unblock {slug}`
(add the retailer's homepage to `HOMEPAGES` in `scraper/unblock.py`, or pass
`--url`).

If the site needs JavaScript, `browser` already covers it. For a nutrition
panel hidden inside a collapsed accordion, set `product_expand` on the adapter
to the selectors that open it, and `product_wait_for` to something that proves
the page rendered.

### Checklist

- [ ] Slug added to `_RETAILER_SLUGS` in `orchestrator/storage.py`
- [ ] `scraper/adapters/{slug}.py` created with correct `retailer`, `entry_urls`, and path regexes
- [ ] Adapter imported and added to `ADAPTERS` in `scraper/adapters/__init__.py`
- [ ] Retailer entry added to `scraper_config.json`
- [ ] (Optional) Pretty name in `ui/src/components/RetailerSidebar.jsx`
- [ ] At least one product fixture under `scraper/fixtures/{slug}/`
- [ ] `get_adapter('{slug}')` succeeds and `/api/retailers` includes the slug
- [ ] A test in `tests/test_adapters.py` asserting on that fixture's real fields
- [ ] One end-to-end scrape produces a populated record in `master.json`
