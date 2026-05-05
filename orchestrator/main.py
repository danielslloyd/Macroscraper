"""FastAPI bootstrap for the orchestrator.

Exposes the JSON-only API the React UI consumes:
  GET  /api/health
  GET  /api/status
  GET  /api/retailers
  GET  /api/tree/{retailer}
  POST /api/crawl/{retailer}
  POST /api/scrape
  POST /api/clean
  GET  /api/product/{id}
  POST /api/product/{id}/flag
  GET  /api/products

On startup: ensure data/ dirs exist, register job handlers, start the worker,
recover any orphan jobs.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .jobs import register_all
from .queue import recover_orphans, start_worker
from .routers import clean, crawl, products, scrape, status
from .storage import ensure_dirs, load_master_sync, save_master_sync


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    # Touch master.json so the first request doesn't race a missing file.
    save_master_sync(load_master_sync())
    register_all()
    start_worker()
    recover_orphans()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Food Scraper", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(status.router, prefix="/api")
    app.include_router(crawl.router, prefix="/api")
    app.include_router(scrape.router, prefix="/api")
    app.include_router(clean.router, prefix="/api")
    app.include_router(products.router, prefix="/api")
    return app


app = create_app()


def main() -> None:
    import psutil
    import uvicorn

    port = 8000
    # Kill only existing Food Scraper instances using port 8000
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.cmdline()) if proc.cmdline() else ''
            if 'orchestrator.main' in cmdline or 'orchestrator/main' in cmdline:
                if proc.connections():
                    for conn in proc.connections():
                        if conn.laddr.port == port:
                            print(f"Killing existing Food Scraper process {proc.pid} ({proc.name()}) using port {port}")
                            proc.kill()
                            proc.wait()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass

    uvicorn.run("orchestrator.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
