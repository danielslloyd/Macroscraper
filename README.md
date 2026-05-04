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
