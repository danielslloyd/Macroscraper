"""Page and asset fetching.

Three ways in, chosen per retailer by `scraper_config.json`:

  browser   a persistent-profile Chrome driven by Playwright. Needed for every
            retailer that renders its product page client-side or gates it
            behind a bot check.
  http      plain httpx, for JSON APIs and image downloads.
  fixture   a saved HTML file under `scraper/fixtures/{retailer}/`, so tests and
            re-parsing runs never touch the network.

Bot walls are reported, never worked around: `BotWallError` tells the caller
which retailer blocked us and what gave it away, and `scraper.unblock` opens a
visible browser so the person can satisfy the check themselves. The result is
stored in the persistent profile and reused.
"""
from .browser import BotWallError, BrowserSession, FetchError, browser_session

__all__ = ["BotWallError", "BrowserSession", "FetchError", "browser_session"]
