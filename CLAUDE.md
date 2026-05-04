# CLAUDE.md — Food Scraper Project

This file is the source of truth for Claude Code working on this project. Read it fully before taking any action.

---

## Project Summary

A local-first food data pipeline. It scrapes packaged food product pages from major retail websites, downloads all associated data and images locally, and passes raw scraped data through a sequence of local Ollama models for cleaning and structured extraction. A React/Vite UI provides a tree-based interface for browsing crawled pages and managing the scrape/clean queue.

This is a **standalone tool** — it is not part of the Macro Ternary platform (yet), but the data schema must remain forward-compatible with a PostgreSQL-backed platform.

---

## Repository Structure

Build the project with this layout from the start. Do not deviate.

```
food-scraper/
  CLAUDE.md                  ← this file
  README.md
  run.bat                    ← single-command launcher (Windows)
  cleaner_config.json        ← Ollama model list (user-editable, not hardcoded)
  /orchestrator              ← FastAPI backend
    main.py                  ← bootstrap + uvicorn startup
    queue.py                 ← job queue + recovery
    /routers
      crawl.py
      scrape.py
      clean.py
      products.py
  /scraper                   ← Scraper module
    base_adapter.py
    crawler.py
    downloader.py            ← image + HTML download logic
    rate_limiter.py
    /adapters
      whole_foods.py
      trader_joes.py
      walmart.py
      costco.py
      amazon.py
      kroger.py
      publix.py
  /cleaner                   ← Cleaner module
    runner.py                ← sequential Ollama model runner
    prompts.py               ← model prompt templates
  /ui                        ← React/Vite frontend
    package.json
    vite.config.js
    /src
      App.jsx
      /components
        RetailerSidebar.jsx
        PageTree.jsx
        ProductPanel.jsx
        StatusDashboard.jsx   ← queue status, job counts, activity
        QueueControls.jsx
  /data                      ← gitignored, all local data
    master.json
    /images
    /labels
    /html
  /venv                      ← gitignored, Python virtual environment
  /node_modules              ← gitignored (if top-level), else ui/node_modules
```

---

## Core Constraints

These are non-negotiable. Follow them exactly.

### Data integrity
- `master.json` is the **single source of truth** for all product records and crawled trees. Every module reads from and writes to it.
- Never delete existing keys when updating a record. Always merge/patch.
- Product IDs are deterministic: `{retailer_slug}_{first_8_chars_of_md5(url)}`. Example: `wholefds_a3f9c12b`. Use this scheme everywhere — never generate random UUIDs.

### File naming
- Images: `data/images/{product_id}_{n}.jpg` (0-indexed)
- Nutrition label: `data/labels/{product_id}_label.jpg`
- HTML snapshot: `data/html/{product_id}.html`
- Never use the product name or any user-facing string in a filename. Always use the product ID.

### Scraper behavior
- Crawlers must distinguish `category` pages from `product` pages. Only product (leaf) pages are scrapeable.
- Category pages exist only to provide tree structure in the UI — they have no scraped fields.
- Always store **both** the public URL and the local path for any downloaded asset.
- Polite delays: default 1–3s random jitter between requests. Make this configurable per adapter. Never hammer a site.
- Do not re-scrape a product that already has `status: scraped` or higher — unless `force=true` is passed.
- **Similar items**: Extract any "similar products", "related items", or "you might also like" links from the product page. Store them in `similar_items[]` with `name`, `public_url`, and `product_id: null` (to be populated later when the item is scraped). Do not scrape similar items automatically — let the user queue them separately.

### Cleaner behavior
- Model list is read from `cleaner_config.json` at runtime — never hardcode model names.
- Models run **sequentially**, not in parallel.
- Each model's output is stored verbatim in `cleaning_runs[]` — do not merge, normalize, or discard any model output in v1.
- Set `status: cleaning` before starting, `status: cleaned` after all models complete.
- **Product linking**: After cleaning, check `similar_items[]` for URLs already in `master.json` (by comparing URLs). If a similar item matches an existing product, populate its `product_id` and add it to both products' `related_products[]` (bidirectional link). This creates a queryable graph of related items across retailers and scrape runs.

### Orchestrator
- FastAPI only. Keep routers thin — business logic lives in the scraper/cleaner modules.
- Job queue must survive process restarts (use `master.json` status fields as the recovery mechanism).
- All endpoints return JSON. No HTML responses from the API.

### UI
- React + Vite. No Next.js, no SSR.
- The tree view must handle deep hierarchies (arbitrarily nested category nodes).
- Status badges must reflect `master.json` in near-real-time (polling is fine, 2–5s interval).
- Do not build a settings UI in v1 — config is file-based.
- **Product detail panel**: Show `similar_items[]` as clickable links. Items with a `product_id` (already scraped) should link to their product detail. Items without a `product_id` should have a "Queue scrape" button.
- **Related products graph**: Show `related_products[]` as a linked list or mini-graph in the product panel. Clicking a link navigates to that product's detail.

---

## Data Schema

### `cleaner_config.json`

```json
{
  "models": [
    { "name": "qwen2.5-vl:7b", "vision": true },
    { "name": "llama3.1:8b", "vision": false }
  ]
}
```

### `master.json` — top level

```json
{
  "retailers": {
    "whole_foods": {
      "crawled_at": "ISO timestamp or null",
      "tree": {}
    }
  },
  "products": {
    "wholefds_a3f9c12b": {}
  }
}
```

### Product record (full)

```json
{
  "product_id": "wholefds_a3f9c12b",
  "retailer": "whole_foods",
  "status": "scraped",
  "url": "https://...",
  "scraped_at": "2025-01-01T00:00:00Z",
  "name": "...",
  "brand": "...",
  "price": {
    "package_price": 6.99,
    "unit_price": 0.87,
    "unit": "per oz",
    "package_size": "8 oz"
  },
  "images": [
    { "public_url": "https://...", "local_path": "data/images/wholefds_a3f9c12b_0.jpg" }
  ],
  "nutrition_label_image": {
    "public_url": "https://...",
    "local_path": "data/labels/wholefds_a3f9c12b_label.jpg"
  },
  "nutrition": {
    "serving_size": "1 cup (240g)",
    "calories": 200,
    "fat_g": 9,
    "saturated_fat_g": 3,
    "carbs_g": 24,
    "fiber_g": 2,
    "sugars_g": 10,
    "protein_g": 5,
    "sodium_mg": 140
  },
  "raw_html_snapshot": "data/html/wholefds_a3f9c12b.html",
  "similar_items": [
    {
      "name": "Organic 2% Reduced Fat Milk",
      "public_url": "https://...",
      "product_id": null
    }
  ],
  "related_products": [
    "wholefds_b1f2c34d",
    "kroger_c5e8d91f"
  ],
  "cleaning_runs": [
    {
      "model": "qwen2.5-vl:7b",
      "run_at": "2025-01-01T01:00:00Z",
      "output": {}
    }
  ]
}
```

### Status values

| Status | Meaning |
|---|---|
| `uncrawled` | URL discovered, not yet scraped |
| `scraped` | Raw data collected, not yet cleaned |
| `cleaning` | Cleaner currently running |
| `cleaned` | All configured models have run |
| `flagged` | Manually flagged for review |

---

## Single-Command Launcher

The user wants to launch everything from a **single `.bat` file** on Windows. No Python version juggling, no manual server startup.

### `run.bat` (Windows launcher)

```batch
@echo off
setlocal enabledelayedexpansion

REM Check for Python 3.11+
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python not found. Install Python 3.11+ and add to PATH.
    exit /b 1
)

REM Create venv if missing
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Install/update dependencies
echo Installing Python dependencies...
pip install -q -U pip setuptools wheel
pip install -q fastapi uvicorn playwright httpx python-dotenv

REM Install Playwright browsers (required for scraper)
echo Installing Playwright browsers...
playwright install chromium

REM Install UI dependencies (npm)
if not exist "ui\node_modules" (
    echo Installing UI dependencies...
    cd ui
    npm install -q
    cd ..
)

REM Start orchestrator in background
echo Starting FastAPI server...
start cmd /k "call venv\Scripts\activate.bat && python orchestrator/main.py"
timeout /t 2 >nul

REM Start UI dev server in new window
echo Starting React dev server...
start cmd /k "cd ui && npm run dev"

REM Brief pause, then open browser
timeout /t 3 >nul
start http://localhost:5173

echo.
echo ====================================
echo FastAPI server: http://localhost:8000
echo React UI: http://localhost:5173
echo ====================================
echo.
pause
```

### Orchestrator bootstrap (`orchestrator/main.py`)

The orchestrator must start cleanly and expose a status endpoint that the UI polls:

- Load `master.json` on startup (create if missing)
- Ensure `data/`, `data/images/`, `data/labels/`, `data/html/` exist
- Start FastAPI uvicorn on `http://localhost:8000`
- Expose a `GET /health` endpoint for the UI to verify backend is alive

### UI entry point (`ui/vite.config.js`)

- Dev server on `http://localhost:5173`
- API proxy to `http://localhost:8000` (so `fetch('/api/...')` hits the right place)
- Reload on any Python file change? Optional — omit for now.

---

## Status Dashboard (Main UI)

The UI must always show **what has been done**. The status dashboard is the hero component.

### Display requirements

- **Queue status** — currently processing count, pending count, completed count
- **Per-retailer breakdown** — crawled count, scraped count, cleaned count, flagged count
- **Live activity feed** — last 10 jobs (most recent first), showing job type, product, timestamp, status
- **Health check** — is the FastAPI backend alive? Show a connected/disconnected indicator at the top
- **Refresh interval** — poll `/status` endpoint every 2–5 seconds

### `/status` endpoint (FastAPI)

```json
{
  "backend_healthy": true,
  "queue": {
    "pending_scrape": 5,
    "pending_clean": 3,
    "processing": 1
  },
  "retailers": {
    "whole_foods": {
      "crawled": 1234,
      "scraped": 456,
      "cleaned": 123,
      "flagged": 2
    }
  },
  "activity": [
    {
      "id": "job_xyz",
      "type": "scrape",
      "product_id": "wholefds_a3f9c12b",
      "status": "completed",
      "started_at": "ISO timestamp",
      "completed_at": "ISO timestamp",
      "error": null
    }
  ]
}
```

The UI component should be responsive and live-updating, so the user can see progress in real time while jobs run in the background.

---

## Build Order

Work in this order. Do not skip ahead.

1. **Scaffold** the full directory structure with empty placeholder files
2. **`master.json` helpers** — read/write/patch utilities used by all modules
3. **`base_adapter.py`** — abstract class with shared crawl/scrape interface
4. **Crawler** — depth-first crawl, category vs. product classification, tree building
5. **One adapter** — implement `whole_foods.py` end-to-end as the reference implementation
6. **Downloader** — image, label, and HTML snapshot saving with deterministic naming
7. **Rate limiter** — per-adapter configurable delay
8. **Remaining adapters** — one at a time, using whole_foods as the pattern
9. **Cleaner runner** — sequential Ollama model execution, raw output storage
10. **FastAPI orchestrator** — thin routers wiring UI to modules, async job queue
11. **React UI** — tree view, queue controls, status dashboard, product panel
12. **Integration test** — end-to-end scrape + clean of one product from one retailer

---

## Tech Stack (exact versions where specified)

| Component | Tech |
|---|---|
| Scraper | Python 3.11+, Playwright (async), httpx |
| Cleaner | Python 3.11+, httpx (Ollama HTTP API) |
| Orchestrator | FastAPI, uvicorn, asyncio |
| UI | React 18, Vite 5 |
| Data | JSON (master.json), local filesystem |
| Package mgr | `uv` for Python, `npm` for UI |

---

## What Is Out of Scope

Do not build these in v1:

- Alignment or consensus mechanism across cleaning runs
- Login-gated scraping (flag Amazon as a potential issue but do not implement auth)
- **Cross-retailer deduplication** (automatically merging the same product from two retailers) — `related_products[]` captures manual links and auto-links from similar items, but does not auto-merge records
- Database export (PostgreSQL, CSV) — the JSON schema is designed to support this later
- Ingredients, allergens, certifications — the `nutrition{}` map is extensible, leave room for these keys but do not define them yet
- Settings UI — all config is file-based

---

## Notes for Claude Code

- If a retailer's page structure requires a fundamentally different crawl strategy (e.g., infinite scroll, login wall, heavy JS rendering), flag it with a comment in the adapter and implement the best available fallback — do not silently skip or fail.
- Amazon is the most likely problem retailer. Implement it last. Note any auth or bot-detection issues encountered.
- The `nutrition{}` field is intentionally a flexible key-value map. Do not enforce a fixed set of keys — different retailers expose different nutrition facts.
- When in doubt about file structure or naming, refer to the constraints above. Consistency matters more than cleverness.
- Write one integration test per adapter (scrape a known stable product URL, assert that expected fields are populated). Keep these runnable offline where possible by caching a fixture HTML file.
