# Food Scraper — Requirements Spec

## Overview

A modular, local-first tool for scraping packaged food data from major retail websites, then cleaning and structuring that data using local LLMs via Ollama. Three distinct components — **Scraper**, **Cleaner**, and **UI** — are coordinated by a FastAPI orchestrator. All data is stored locally.

---

## System Architecture

```
UI (React/Vite standalone)
        ↓ HTTP
FastAPI Orchestrator
    ↙           ↘
Scraper       Cleaner
module        module
    ↘           ↙
   Shared local storage
   (master.json + /data/)
```

---

## Module 1 — Scraper

**Tech:** Python + Playwright (headless browser for all retailers; static HTML fallback where possible)

**Target retailers (v1):** Whole Foods, Trader Joe's, Walmart, Costco, Amazon, Kroger, Publix

### Adapter Pattern

One base `RetailerAdapter` class; one subclass per retailer. Each adapter defines:
- Entry URL(s)
- Crawl selectors (category links vs. product links)
- Product page field selectors (name, price, images, nutrition facts, label image)
- Polite delay config (default: 1–3s random jitter)

### Crawler Behavior

- Crawls to **arbitrary depth** from the entry URL
- Classifies pages as `category` (has subcategory/product links) or `product` (leaf node — scrapeable)
- Category pages are non-scrapeable parents; they exist only to provide tree structure
- Stores full crawled tree in `master.json`
- Does **not** re-crawl already-crawled pages unless explicitly forced
- Configurable polite delay per retailer
- Extracts "similar items" / "related products" / "you might also like" links from product pages and stores them with product name and URL in `similar_items[]`

### Per-Product Scrape Output

Stored in `master.json`:

| Field | Description |
|---|---|
| `public_url` | Original product URL |
| `name`, `brand` | Product identity |
| `price` | Package price, unit price, unit label, package size |
| `images[]` | Public URLs + local downloaded paths |
| `nutrition_label_image` | Public URL + local path |
| `nutrition{}` | Flexible key-value map (see schema) |
| `raw_html_snapshot` | Local path to saved HTML |
| `similar_items[]` | "You may also like" / related items from the page |
| `related_products[]` | List of product IDs (in your DB) that are related to this one |
| `scraped_at` | ISO 8601 timestamp |

---

## Module 2 — Cleaner

**Tech:** Python + Ollama HTTP API

### Input

A product's raw scraped JSON blob from `master.json` + local image paths.

### Behavior

- Reads model list from `cleaner_config.json` (not hardcoded)
- Runs each model **sequentially**
- Each model receives the full raw blob; vision-capable models also receive the nutrition label image for OCR
- Each model's raw output is stored verbatim in `cleaning_runs[]` — no merging or alignment in v1
- Updates product `status` in `master.json` on completion (`cleaning` → `cleaned`)

### `cleaner_config.json` format

```json
{
  "models": [
    { "name": "qwen2.5-vl:7b", "vision": true },
    { "name": "llama3.1:8b", "vision": false }
  ]
}
```

---

## Module 3 — UI

**Tech:** React + Vite (standalone app, separate from Macro Ternary)

### Retailer Tree View

- Sidebar lists available retailers
- Selecting a retailer shows a collapsible page tree
- Category nodes: expandable only, non-selectable
- Product leaf nodes: show status badge (`uncrawled | scraped | cleaning | cleaned | flagged`)
- Multi-select checkboxes for queuing
- "Scrape selected" / "Clean selected" action buttons

### Status Dashboard

- Queue depth, active jobs, completion counts per retailer
- Live-updating via polling or WebSocket

### Product Detail Panel

- All scraped fields rendered
- Each `cleaning_run` result shown side-by-side
- Manual flagging control

---

## Orchestrator

**Tech:** FastAPI

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/tree/{retailer}` | Returns crawled page tree |
| `POST` | `/crawl/{retailer}` | Triggers crawler for a retailer |
| `POST` | `/scrape` | Queues scrape jobs for a list of product URLs |
| `POST` | `/clean` | Queues clean jobs for a list of product IDs |
| `GET` | `/status` | Returns queue/job status |
| `GET` | `/product/{id}` | Returns full product record |

### Queue

Simple in-process async queue (implementation at Claude Code's discretion — `asyncio` queue or Celery). Jobs are recoverable via `master.json` status flags so progress survives restarts.

---

## Storage Layout

```
/data/
  master.json                        # Single source of truth
  images/
    {product_id}_{n}.jpg
  labels/
    {product_id}_label.jpg
  html/
    {product_id}.html
```

### Naming Convention

`product_id` = `{retailer_slug}_{url_hash_8chars}` — deterministic, derived from the product URL.

Example: `wholefds_a3f9c12b`

---

## `master.json` Schema

### Tree structure (top level)

```json
{
  "retailers": {
    "whole_foods": {
      "crawled_at": "2025-01-01T00:00:00Z",
      "tree": { ... }
    }
  },
  "products": {
    "wholefds_a3f9c12b": { ... }
  }
}
```

### Product record

```json
{
  "product_id": "wholefds_a3f9c12b",
  "retailer": "whole_foods",
  "status": "scraped",
  "url": "https://...",
  "scraped_at": "2025-01-01T00:00:00Z",
  "name": "Organic Whole Milk",
  "brand": "Straus Family Creamery",
  "price": {
    "package_price": 6.99,
    "unit_price": 0.87,
    "unit": "per oz",
    "package_size": "8 oz"
  },
  "images": [
    {
      "public_url": "https://...",
      "local_path": "data/images/wholefds_a3f9c12b_0.jpg"
    }
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
| `uncrawled` | URL discovered but not yet scraped |
| `scraped` | Raw data collected, not yet cleaned |
| `cleaning` | Cleaner currently running |
| `cleaned` | All configured models have run |
| `flagged` | Manually flagged for review |

---

## Out of Scope (v1)

- Alignment/consensus mechanism across cleaning runs (future)
- Login-gated scraping (revisit for Amazon)
- **Cross-retailer deduplication** (automatically merging the same product from two retailers) — `related_products[]` captures manual links and auto-links from similar items, but does not auto-merge records
- Export to PostgreSQL / Macro Ternary (schema is forward-compatible by design)
- Ingredients, allergens, certifications (schema is extensible — add keys to `nutrition{}`)
