# Adapter fixtures

Each adapter (when `fixture_driven = True`, the default) reads HTML from
`scraper/fixtures/{retailer}/`. The crawler/scraper looks up a file using the
URL's last path segment, e.g. requesting
`https://www.wholefoodsmarket.com/product/organic-whole-milk-half-gallon`
opens `whole_foods/organic-whole-milk-half-gallon.html`.

Drop sanitized HTML snapshots here to extend offline coverage. Only Whole Foods
ships with fixtures by default — the other six adapters are wired up but have
no captured pages, so they no-op until you populate them.

To switch an adapter to live mode, set `fixture_driven = False` on the class.
