"""Nutrition and product-field extraction.

The extraction stack is deliberately tiered so that the overwhelming majority
of products never touch a language model:

  tier 1  page_html       structured data already on the retailer's page
  tier 2  barcode_db      Open Food Facts / USDA FoodData Central, keyed by UPC
  tier 3  ocr             OCR of the nutrition-panel photo + regex panel parser
  tier 4  local_vision    a local Ollama vision model reads the panel photo
  tier 5  claude          the Claude API reads the panel photo

`ladder.extract_nutrition` walks the tiers in order and stops as soon as the
accumulated record passes a confidence check (see `nutrients.score`). Every
tier that contributed is recorded in `nutrition_sources[]`, so a value can
always be traced back to where it came from.
"""
