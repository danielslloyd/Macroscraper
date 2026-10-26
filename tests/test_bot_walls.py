"""Tests for bot-check detection and the empty-parse guard.

Both of these exist because a challenge page is not an error: it arrives as a
perfectly valid HTTP 200 document that parses as "a product with no fields".
Without these two checks, a blocked scrape is stored as a successful one and
never retried.
"""
from __future__ import annotations

import pytest

from scraper.fetch.browser import BotWallError, detect_bot_wall

# Amazon's quiet interstitial, as actually served to a fresh profile.
AMAZON_INTERSTITIAL = """<!doctype html><html><head><title>Amazon.com</title></head>
<body><div>Amazon.com</div>
<div>Click the button below to continue shopping</div>
<a href="/dp/B07KWGSCW2">Continue shopping</a>
<p>To discuss automated access to Amazon data please contact
api-services-support@amazon.com.</p>
</body></html>"""

WALMART_CHALLENGE = """<!doctype html><html><head><title>Robot or human?</title></head>
<body><div id="px-captcha"></div><p>Activate and hold the button to confirm
that you are human.</p></body></html>"""

AMAZON_CAPTCHA = """<!doctype html><html><body>
<form action="/errors/validateCaptcha"><p>Enter the characters you see below</p>
</form></body></html>"""

AKAMAI_DENIED = """<!doctype html><html><body><h1>Access Denied</h1>
<p>You don't have permission to access this resource.
Reference #18.1a2b3c4d.1700000000.5e6f7a8b</p></body></html>"""

REAL_PRODUCT = """<!doctype html><html><head><title>Creamy Peanut Butter</title></head>
<body><h1 id="productTitle">Creamy Peanut Butter, 16 oz</h1>
<div id="merchant-info">Ships from and sold by Amazon.com.</div>
<table><tr><th>UPC</th><td>842379152535</td></tr></table>
</body></html>"""


class TestDetectBotWall:
    @pytest.mark.parametrize("html,expected", [
        (AMAZON_INTERSTITIAL, "amazon-interstitial"),
        (WALMART_CHALLENGE, "perimeterx"),
        (AMAZON_CAPTCHA, "amazon-captcha"),
        (AKAMAI_DENIED, "akamai"),
    ])
    def test_challenge_pages_are_recognised(self, html, expected):
        assert detect_bot_wall(html) == expected

    def test_a_real_product_page_is_not_flagged(self):
        assert detect_bot_wall(REAL_PRODUCT) is None

    def test_captured_fixtures_are_not_flagged(self):
        from scraper.adapters import get_adapter

        for retailer, url in (
            ("amazon", "https://www.amazon.com/dp/B07KWH27FN"),
            ("costco", "https://www.costco.com/kirkland-signature-organic-peanut-"
                       "butter,-28-oz,-2-count.product.100334334.html"),
            ("publix", "https://www.publix.com/pd/skippy-creamy-peanut-butter/"
                       "RIO-PCI-103797"),
            ("walmart", "https://www.walmart.com/ip/x/10315273"),
        ):
            fixture = get_adapter(retailer).fixture_for(url)
            assert fixture is not None, retailer
            assert detect_bot_wall(fixture.read_text(encoding="utf-8")) is None, retailer

    def test_a_blocked_status_code_on_a_short_body_counts(self):
        assert detect_bot_wall("<html>nope</html>", status=403) == "http-403"
        assert detect_bot_wall("<html>nope</html>", status=429) == "http-429"

    def test_a_normal_status_does_not(self):
        assert detect_bot_wall(REAL_PRODUCT, status=200) is None

    def test_the_error_names_the_retailer_and_the_way_out(self):
        error = BotWallError("walmart", "https://www.walmart.com/ip/x/1", "perimeterx")
        assert error.retailer == "walmart"
        assert error.signal == "perimeterx"
        assert "python -m scraper.unblock walmart" in str(error)


class TestEmptyParseGuard:
    @pytest.mark.asyncio
    async def test_a_page_with_no_product_fields_is_flagged_not_stored(
        self, tmp_path, monkeypatch
    ):
        from orchestrator import jobs, storage
        from orchestrator.queue import Job
        from scraper.base_adapter import ScrapedProduct

        data_dir = tmp_path / "data"
        for name, value in (
            ("DATA_DIR", data_dir),
            ("MASTER_PATH", data_dir / "master.json"),
            ("IMAGES_DIR", data_dir / "images"),
            ("LABELS_DIR", data_dir / "labels"),
            ("HTML_DIR", data_dir / "html"),
            ("ROOT", tmp_path),
        ):
            monkeypatch.setattr(storage, name, value)
        storage.ensure_dirs()

        url = "https://www.amazon.com/dp/B07KWGSCW2"
        adapter = jobs.get_adapter("amazon")

        async def interstitial(_url):
            # What an unrecognised challenge page looks like after parsing.
            return AMAZON_INTERSTITIAL, ScrapedProduct()

        monkeypatch.setattr(adapter, "fetch_product", interstitial)
        monkeypatch.setattr(jobs, "get_adapter", lambda _r: adapter)

        job = Job(id="job_test", type="scrape",
                  payload={"retailer": "amazon", "url": url, "force": True})
        with pytest.raises(RuntimeError, match="no product fields"):
            await jobs._handle_scrape(job)

        record = await storage.get_product(storage.product_id_for("amazon", url))
        assert record["status"] == "flagged"
        assert record["flag_reason"] == "empty_parse"
        assert "scraped_at" not in record, "a blocked fetch is not a scrape"
