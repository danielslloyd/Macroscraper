"""One integration test per retailer adapter, run against saved fixtures.

Each test asserts on the fields that product actually publishes, so a change
in an adapter that silently stops finding the price, the gallery, the barcode
or the seller fails here rather than quietly writing thinner records.

Three fixtures are captured pages (Amazon, Costco, Publix); two are synthetic
and modelled on the retailer's payload shape (Walmart, Kroger) because those
two could not be captured -- see `scraper/fixtures/README.md`.
"""
from __future__ import annotations

import pytest

from scraper.adapters import get_adapter
from scraper.extract import nutrients

AMAZON_URL = "https://www.amazon.com/dp/B07KWH27FN"
COSTCO_URL = (
    "https://www.costco.com/kirkland-signature-organic-peanut-butter,"
    "-28-oz,-2-count.product.100334334.html"
)
PUBLIX_URL = "https://www.publix.com/pd/skippy-creamy-peanut-butter/RIO-PCI-103797"
WALMART_URL = "https://www.walmart.com/ip/Great-Value-Creamy-Peanut-Butter-40-oz/10315273"
KROGER_URL = "https://www.kroger.com/p/kroger-vitamin-d-whole-milk/0001111040101"


def parse_fixture(retailer: str, url: str):
    adapter = get_adapter(retailer)
    fixture = adapter.fixture_for(url)
    assert fixture is not None, f"no fixture for {url}"
    return adapter, adapter.parse_product(url, fixture.read_text(encoding="utf-8"))


class TestAmazon:
    """Amazon renders a full nutrition panel into the page, so tier 1 suffices."""

    def test_identity_and_price(self):
        _, product = parse_fixture("amazon", AMAZON_URL)
        assert "Creamy Peanut Butter" in product.name
        assert product.brand
        assert product.retailer_sku == "B07KWH27FN"
        assert product.price["package_price"] > 0
        assert product.price["unit"].startswith("per ")

    def test_barcodes(self):
        _, product = parse_fixture("amazon", AMAZON_URL)
        assert product.upc == "842379152535"
        assert product.gtin == "00842379152535"

    def test_images_are_full_resolution_gallery(self):
        _, product = parse_fixture("amazon", AMAZON_URL)
        assert len(product.images) >= 5
        assert product.images[0]["role"] == "front"
        # hiRes URLs, not the thumbnail variants.
        assert all("media-amazon.com" in i["public_url"] for i in product.images)
        assert any("_SL1500_" in i["public_url"] for i in product.images)

    def test_nutrition_comes_from_the_page(self):
        _, product = parse_fixture("amazon", AMAZON_URL)
        assert product.nutrition["calories"] == 180
        assert product.nutrition["fat_g"] == 15
        assert product.nutrition["protein_g"] == 7
        assert product.nutrition["serving_size"] == "2 tbsp (32g)"
        assert nutrients.score(product.nutrition).value == 1.0

    def test_sold_by_amazon_is_first_party(self):
        _, product = parse_fixture("amazon", AMAZON_URL)
        assert product.sold_by == "AmazonFresh"
        assert product.ships_from == "AmazonFresh"
        assert product.first_party is True

    def test_ingredients_and_similar_items(self):
        _, product = parse_fixture("amazon", AMAZON_URL)
        assert "Peanuts" in product.ingredients
        assert product.similar_items
        assert all(s["public_url"].startswith("https://www.amazon.com/dp/")
                   for s in product.similar_items)
        assert all(s["product_id"] is None for s in product.similar_items)

    def test_classify(self):
        adapter, _ = parse_fixture("amazon", AMAZON_URL)
        assert adapter.classify(AMAZON_URL, "") == "product"
        assert adapter.classify("https://www.amazon.com/s?i=amazonfresh", "") == "category"


class TestCostco:
    """Costco publishes no nutrition for this item -- the photos carry it."""

    def test_identity_from_the_flight_payload(self):
        _, product = parse_fixture("costco", COSTCO_URL)
        assert product.name == "Kirkland Signature Organic Peanut Butter, 28 oz, 2-count"
        assert product.brand == "Kirkland Signature"
        assert product.retailer_sku == "100334334"

    def test_price_and_derived_unit_price(self):
        _, product = parse_fixture("costco", COSTCO_URL)
        assert product.price["package_price"] == 11.69
        assert product.price["package_size"] == "56 oz."
        # Costco prints no unit price; it is derived from uom and factor.
        assert product.price["unit_price"] == pytest.approx(11.69 / 56, rel=1e-3)
        assert product.price["unit_price_derived"] is True

    def test_gallery_is_ordered_and_jpeg(self):
        _, product = parse_fixture("costco", COSTCO_URL)
        assert len(product.images) == 6
        assert product.images[0]["role"] == "front"
        assert all(i["public_url"].endswith(".jpg") for i in product.images), \
            "AVIF twins must be dropped; Pillow and the OCR engine cannot open them"
        positions = [i["public_url"].rsplit("__", 1)[1] for i in product.images]
        assert positions == ["1.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg", "6.jpg"]

    def test_attributes_and_categories(self):
        _, product = parse_fixture("costco", COSTCO_URL)
        assert product.attributes["Brand"] == "Kirkland Signature"
        assert "Organic" in product.attributes["Dietary Features"]
        assert any("Nut Butters" in c for c in product.categories)

    def test_page_has_no_nutrition_so_the_ladder_must_escalate(self):
        _, product = parse_fixture("costco", COSTCO_URL)
        assert product.nutrition == {}
        assert nutrients.score(product.nutrition).value == 0.0

    def test_costco_is_the_seller_unless_the_page_says_otherwise(self):
        _, product = parse_fixture("costco", COSTCO_URL)
        assert product.first_party is True
        assert product.sold_by == "Costco Wholesale"


class TestPublix:
    def test_identity_from_the_data_layer(self):
        _, product = parse_fixture("publix", PUBLIX_URL)
        assert product.name == "Skippy Creamy Peanut Butter"
        assert product.brand == "Skippy"
        assert product.retailer_sku == "RIO-PCI-103797"
        assert product.attributes["item_code"] == 10229

    def test_categories_are_split_from_the_path(self):
        _, product = parse_fixture("publix", PUBLIX_URL)
        assert product.categories == ["Grocery", "Sauces and Condiments",
                                      "Peanut Butter and Jelly"]

    def test_one_front_of_pack_photo(self):
        _, product = parse_fixture("publix", PUBLIX_URL)
        assert len(product.images) == 1
        assert product.images[0]["role"] == "front"
        assert "images.publixcdn.com" in product.images[0]["public_url"]

    def test_publix_sells_its_own_catalogue(self):
        _, product = parse_fixture("publix", PUBLIX_URL)
        assert product.first_party is True
        assert product.sold_by == "Publix"

    def test_no_barcode_published(self):
        # Which is why the name-matching tier exists for this retailer.
        _, product = parse_fixture("publix", PUBLIX_URL)
        assert product.upc is None
        assert product.gtin is None


class TestWalmart:
    """Against the synthetic fixture: exercises the shape-based extraction."""

    def test_identity_and_barcodes(self):
        _, product = parse_fixture("walmart", WALMART_URL)
        assert product.name == "Great Value Creamy Peanut Butter, 40 oz"
        assert product.brand == "Great Value"
        assert product.retailer_sku == "10315273"
        assert product.upc == "078742370507"
        assert product.gtin == "00078742370507"

    def test_price_including_unit_price(self):
        _, product = parse_fixture("walmart", WALMART_URL)
        assert product.price["package_price"] == 4.68
        assert product.price["unit_price"] == 0.117
        assert product.price["unit"] == "per oz"
        assert product.price["package_size"] == "40 oz"

    def test_structured_nutrition_block(self):
        _, product = parse_fixture("walmart", WALMART_URL)
        assert product.nutrition["calories"] == 190
        assert product.nutrition["fat_g"] == 16
        assert product.nutrition["sodium_mg"] == 140
        assert product.nutrition["serving_size"] == "2 tbsp (32g)"
        assert nutrients.score(product.nutrition).value == 1.0

    def test_sold_by_walmart_is_first_party(self):
        _, product = parse_fixture("walmart", WALMART_URL)
        assert product.sold_by == "Walmart.com"
        assert product.first_party is True

    def test_marketplace_seller_is_not_first_party(self):
        adapter = get_adapter("walmart")
        node = {"usItemId": "1", "name": "x", "sellerName": "Acme Trading Co",
                "sellerId": "8A1B2C3D", "isSoldByWalmart": False}
        assert adapter._first_party(node, "Acme Trading Co") is False

    def test_gallery(self):
        _, product = parse_fixture("walmart", WALMART_URL)
        assert len(product.images) == 3
        assert product.images[0]["role"] == "front"


class TestKroger:
    """Kroger comes through the official API, so there is no HTML to parse."""

    @pytest.mark.asyncio
    async def test_api_record_maps_onto_the_schema(self):
        adapter = get_adapter("kroger")
        raw, product = await adapter.fetch_product(KROGER_URL)
        assert raw.lstrip().startswith("{"), "the snapshot is the API envelope"
        assert product.name == "Kroger® Vitamin D Whole Milk"
        assert product.brand == "Kroger"
        assert product.retailer_sku == "0001111040101"
        assert product.upc == "0001111040101"
        assert product.first_party is True

    @pytest.mark.asyncio
    async def test_promo_price_wins_and_regular_is_kept(self):
        adapter = get_adapter("kroger")
        _, product = await adapter.fetch_product(KROGER_URL)
        assert product.price["package_price"] == 3.19
        assert product.price["regular_price"] == 3.49
        assert product.price["package_size"] == "1 gal"

    @pytest.mark.asyncio
    async def test_largest_image_per_perspective_front_first(self):
        adapter = get_adapter("kroger")
        _, product = await adapter.fetch_product(KROGER_URL)
        roles = [i["role"] for i in product.images]
        assert roles[:2] == ["front", "back"]
        assert all("/xlarge/" in i["public_url"] or "/large/" in i["public_url"]
                   for i in product.images)

    @pytest.mark.asyncio
    async def test_back_image_becomes_the_nutrition_label(self):
        # The API returns no nutrition; the panel is printed on the back.
        adapter = get_adapter("kroger")
        _, product = await adapter.fetch_product(KROGER_URL)
        assert product.nutrition == {}
        assert product.nutrition_label_url is not None
        assert "/back/" in product.nutrition_label_url

    def test_product_id_is_read_from_the_url(self):
        adapter = get_adapter("kroger")
        assert adapter.product_id_from_url(KROGER_URL) == "0001111040101"
        assert adapter.product_id_from_url("https://www.kroger.com/d/shop") is None

    @pytest.mark.asyncio
    async def test_missing_credentials_are_reported_clearly(self, monkeypatch):
        from scraper.adapters import kroger

        monkeypatch.delenv("KROGER_CLIENT_ID", raising=False)
        monkeypatch.delenv("KROGER_CLIENT_SECRET", raising=False)
        monkeypatch.setattr(kroger._token, "value", None)
        monkeypatch.setattr(kroger._token, "expires_at", 0.0)
        with pytest.raises(kroger.KrogerCredentialsMissing) as exc:
            await kroger.access_token()
        assert "developer.kroger.com" in str(exc.value)


class TestAdapterConfiguration:
    def test_every_retailer_is_configured(self):
        for retailer in ("amazon", "walmart", "costco", "publix", "kroger"):
            adapter = get_adapter(retailer)
            assert adapter.mode in ("browser", "api", "http", "fixture")
            assert 0 < adapter.min_delay <= adapter.max_delay, \
                "polite delays must be set and sane"

    def test_first_party_only_is_on_for_the_marketplaces(self):
        for retailer in ("amazon", "walmart"):
            assert get_adapter(retailer).first_party_only is True, retailer

    def test_fixture_lookup_strips_a_trailing_html_extension(self):
        adapter = get_adapter("costco")
        fixture = adapter.fixture_for(COSTCO_URL)
        assert fixture is not None and fixture.suffix == ".html"
