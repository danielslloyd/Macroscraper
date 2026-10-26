"""Open a visible browser so you can clear a retailer's bot check yourself.

Some retailers (Walmart most reliably, Amazon and Costco occasionally) answer
automated requests with a challenge page. This tool does not try to defeat
that. It opens the project's own browser profile in a visible window on the
retailer's site; you interact with it as yourself, and whatever cookies the
site then sets are kept in the profile, so later headless scrapes reuse the
session you established.

    python -m scraper.unblock walmart
    python -m scraper.unblock walmart --url https://www.walmart.com/ip/10315273
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import config
from .fetch.browser import BrowserSession, detect_bot_wall

HOMEPAGES = {
    "walmart": "https://www.walmart.com/",
    "amazon": "https://www.amazon.com/",
    "costco": "https://www.costco.com/",
    "publix": "https://www.publix.com/",
    "kroger": "https://www.kroger.com/",
}


async def run(retailer: str, url: str | None) -> int:
    target = url or HOMEPAGES.get(retailer)
    if not target:
        print(f"No homepage known for '{retailer}'. Pass --url explicitly.")
        return 2

    session = BrowserSession(headless=False)
    print(f"Opening {target} in a visible browser window.")
    print(f"Profile: {config.browser_profile_dir()}")
    print()
    print("In that window: clear any 'verify you are human' check, accept or")
    print("decline the cookie banner, and if the retailer asks for a store or")
    print("ZIP code, set the one you want scraped prices for.")
    print()
    page = await session.goto(target, settle_ms=1500)
    wall = detect_bot_wall(page.html, status=page.status)
    print(f"Loaded with status {page.status}."
          + (f" Bot check present ({wall})." if wall else " No bot check detected."))
    print()
    try:
        input("Press Enter here when the page is showing normally... ")
    except EOFError:
        print("No interactive terminal; waiting 90s instead.")
        await asyncio.sleep(90)

    check = await session.goto(target, settle_ms=1500)
    wall = detect_bot_wall(check.html, status=check.status)
    await session.close()
    if wall:
        print(f"Still seeing a bot check ({wall}). The session was not established.")
        return 1
    print("Session looks clear and is saved in the profile. Headless scrapes "
          "will reuse it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("retailer", choices=sorted(HOMEPAGES))
    parser.add_argument("--url", help="specific page to open instead of the homepage")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.retailer, args.url))


if __name__ == "__main__":
    sys.exit(main())
