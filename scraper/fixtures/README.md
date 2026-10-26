# Adapter fixtures

A fixture is a saved product page (or API response) that an adapter reads
instead of going to the network. `RetailerAdapter.fetch` checks for one first,
whatever `mode` the retailer is configured with, so the tests run offline and a
parser change can be re-run against pages captured earlier.

## Lookup

The filename comes from the URL's last path segment, with `.html` or `.json`
appended. A trailing `.html` already in the URL is stripped first, so Costco's

    https://www.costco.com/kirkland-...-2-count.product.100334334.html

opens

    costco/kirkland-...-2-count.product.100334334.html

Other segments are tried as fallbacks (the whole path joined with `_`, then a
slugified full URL, then `index`), which is why Amazon's `/dp/B07KWH27FN` finds
`amazon/dp_B07KWH27FN.html`.

## What is here

| File | Source |
|---|---|
| `whole_foods/*.html` | hand-written reference pages for the original pipeline test |
| `amazon/dp_B07KWH27FN.html` | **captured** from the live product page |
| `costco/kirkland-...100334334.html` | **captured** from the live product page |
| `publix/RIO-PCI-103797.html` | **captured** from the live product page |
| `walmart/10315273.html` | **synthetic** — see below |
| `kroger/0001111040101.json` | **synthetic** — see below |

Captured pages are trimmed, not edited: stylesheets, comments, and scripts
carrying no data the adapters read were removed. Everything the parsers touch
is the retailer's own markup, unaltered.

### The two synthetic fixtures

These two could not be captured, and they say so in a comment at the top of the
file. They exist so the adapters' logic has a regression test; the field names
and nesting follow each retailer's real payload shape, but the values are
invented and should not be treated as that product's actual data.

* **Walmart** answers an automated first request with a bot check, so no page
  was retrievable. Run `python -m scraper.unblock walmart`, clear the check in
  the window that opens, then save the real page here as `{itemId}.html`.
* **Kroger** has no public page scrape at all — the adapter uses the official
  Product API, and this project has no API credentials. With
  `KROGER_CLIENT_ID` / `KROGER_CLIENT_SECRET` set, save a real response as
  `{productId}.json` (the whole envelope, with the record under `data`).

## Adding a fixture

Save the rendered HTML of a product page under the retailer's directory, named
after the URL segment above. For a page behind a bot check, use
`python -m scraper.unblock <retailer>` first — the browser profile it
establishes is reused by later fetches.
