"""Persistent-profile browser fetching.

One Chrome profile is reused for the whole project (under `data/`, gitignored).
That matters for two reasons: cookies and consent state persist, so repeat
visits look like repeat visits rather than a fresh robot each time; and when a
retailer presents a bot check, the person can satisfy it once in a visible
window (`python -m scraper.unblock walmart`) and every later headless run
inherits that session.

Bot walls are detected and surfaced, not circumvented.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .. import config

try:  # Playwright is required for live fetching but not for fixture runs/tests.
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - exercised only without Playwright
    async_playwright = None  # type: ignore[assignment]


class FetchError(RuntimeError):
    """A page could not be fetched."""


class BotWallError(FetchError):
    """The retailer served a bot check instead of the page.

    Carries enough detail for the UI to tell the person what to do next.
    """

    def __init__(self, retailer: str, url: str, signal: str) -> None:
        super().__init__(
            f"{retailer} served a bot check for {url} (matched: {signal}). "
            f"Run: python -m scraper.unblock {retailer}"
        )
        self.retailer = retailer
        self.url = url
        self.signal = signal


# Title/body markers for the common bot walls. Each entry is (name, pattern).
_WALL_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("perimeterx", re.compile(r"px-captcha|_px(?:hd|Captcha)|perimeterx", re.I)),
    ("robot-or-human", re.compile(r"robot or human\?", re.I)),
    ("amazon-captcha", re.compile(
        r"type the characters you see in this image|enter the characters you see below"
        r"|/errors/validateCaptcha", re.I)),
    # Amazon's quieter interstitial: a near-empty page with one "continue"
    # button and a note about automated access. It parses as a product with no
    # fields, which is exactly why it has to be caught here.
    ("amazon-interstitial", re.compile(
        r"click the button below to continue shopping"
        r"|to discuss automated access to amazon data", re.I)),
    ("akamai", re.compile(r"access denied.{0,120}reference\s*#|errors\.edgesuite\.net", re.I | re.S)),
    ("incapsula", re.compile(r"_incapsula_|incident id:\s*\d", re.I)),
    ("pardon-interruption", re.compile(r"pardon our interruption", re.I)),
    ("cloudflare", re.compile(r"cf-browser-verification|checking if the site connection is secure", re.I)),
    ("datadome", re.compile(r"datadome|geo\.captcha-delivery\.com", re.I)),
)

_STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = window.chrome || {runtime: {}};
"""


def detect_bot_wall(html: str, *, status: int | None = None) -> str | None:
    """Return the name of the bot wall the HTML looks like, or None.

    Checked before parsing, because a challenge page parses perfectly well as
    "a product with no fields" and would otherwise be stored as a real record.
    """
    if status in (403, 429, 503) and len(html) < 200_000:
        return f"http-{status}"
    head = html[:200_000]
    for name, pattern in _WALL_MARKERS:
        if pattern.search(head):
            return name
    return None


@dataclass
class _Page:
    html: str
    status: int | None
    final_url: str


class BrowserSession:
    """A lazily started, shared persistent browser context."""

    def __init__(self, *, headless: bool | None = None) -> None:
        cfg = config.fetch_config()
        self._cfg = cfg
        self._headless = cfg["headless"] if headless is None else headless
        self._playwright = None
        self._context = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._context is not None:
            return
        if async_playwright is None:
            raise FetchError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
        async with self._lock:
            if self._context is not None:
                return
            profile = config.browser_profile_dir()
            profile.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            launch: dict = {
                "user_data_dir": str(profile),
                "headless": self._headless,
                "viewport": {"width": 1440, "height": 1000},
                "locale": self._cfg["locale"],
                "timezone_id": self._cfg["timezone"],
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if self._cfg.get("user_agent"):
                launch["user_agent"] = self._cfg["user_agent"]
            channel = self._cfg.get("channel")
            if channel:
                launch["channel"] = channel
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(**launch)
            except Exception:
                # Fall back to bundled Chromium when the requested channel
                # (normally the person's real Chrome) is not installed.
                launch.pop("channel", None)
                self._context = await self._playwright.chromium.launch_persistent_context(**launch)
            await self._context.add_init_script(_STEALTH)

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            finally:
                self._context = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None

    async def goto(
        self,
        url: str,
        *,
        settle_ms: int | None = None,
        wait_for: str | None = None,
        expand: tuple[str, ...] = (),
    ) -> _Page:
        """Load a URL and return its rendered HTML.

        `wait_for` is a selector to wait for before reading the DOM; `expand` is
        a list of selectors to click first (nutrition panels on several
        retailers sit inside a collapsed accordion and are not in the DOM until
        it is opened).
        """
        await self.start()
        assert self._context is not None
        page = await self._context.new_page()
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded",
                timeout=self._cfg["nav_timeout_ms"],
            )
            status = response.status if response else None
            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=15000)
                except Exception:
                    pass  # absent selector is a parsing concern, not a fetch failure
            await page.wait_for_timeout(
                self._cfg["settle_ms"] if settle_ms is None else settle_ms
            )
            for selector in expand:
                try:
                    locator = page.locator(selector).first
                    if await locator.count():
                        await locator.click(timeout=4000)
                        await page.wait_for_timeout(1200)
                except Exception:
                    continue
            html = await page.content()
            return _Page(html=html, status=status, final_url=page.url)
        except Exception as exc:
            if isinstance(exc, FetchError):
                raise
            raise FetchError(f"could not load {url}: {type(exc).__name__}: {exc}") from exc
        finally:
            await page.close()


_session: BrowserSession | None = None


def browser_session() -> BrowserSession:
    """The process-wide browser session."""
    global _session
    if _session is None:
        _session = BrowserSession()
    return _session


async def close_browser_session() -> None:
    global _session
    if _session is not None:
        await _session.close()
        _session = None


async def fetch_rendered(
    retailer: str,
    url: str,
    *,
    wait_for: str | None = None,
    expand: tuple[str, ...] = (),
) -> str:
    """Fetch a page through the shared browser, raising on a bot wall."""
    page = await browser_session().goto(url, wait_for=wait_for, expand=expand)
    wall = detect_bot_wall(page.html, status=page.status)
    if wall:
        raise BotWallError(retailer, url, wall)
    if page.status is not None and page.status >= 400:
        raise FetchError(f"{retailer}: HTTP {page.status} for {url}")
    return page.html


__all__ = [
    "BotWallError",
    "BrowserSession",
    "FetchError",
    "browser_session",
    "close_browser_session",
    "detect_bot_wall",
    "fetch_rendered",
]
