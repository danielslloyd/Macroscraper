"""Shared HTML parsing helpers.

Major retail sites publish a Product schema via JSON-LD; that's the most
reliable cross-retailer extraction surface. These helpers provide sensible
default parsers that adapters can use directly or override.
"""
from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base_adapter import CrawlLink, ScrapedProduct


_NUTRIENT_KEYS = {
    "calories": "calories",
    "energy": "calories",
    "total fat": "fat_g",
    "fat": "fat_g",
    "saturated fat": "saturated_fat_g",
    "trans fat": "trans_fat_g",
    "cholesterol": "cholesterol_mg",
    "sodium": "sodium_mg",
    "total carbohydrate": "carbs_g",
    "carbohydrate": "carbs_g",
    "carbohydrates": "carbs_g",
    "dietary fiber": "fiber_g",
    "fiber": "fiber_g",
    "total sugars": "sugars_g",
    "sugars": "sugars_g",
    "added sugars": "added_sugars_g",
    "protein": "protein_g",
    "vitamin d": "vitamin_d_mcg",
    "calcium": "calcium_mg",
    "iron": "iron_mg",
    "potassium": "potassium_mg",
}


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def jsonld_blocks(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text() or ""
        text = text.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                out.extend(d for d in graph if isinstance(d, dict))
            else:
                out.append(data)
    return out


def find_product_jsonld(soup: BeautifulSoup) -> dict | None:
    for block in jsonld_blocks(soup):
        t = block.get("@type")
        if t == "Product" or (isinstance(t, list) and "Product" in t):
            return block
    return None


def og_meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop})
    if tag and tag.get("content"):
        return tag["content"]
    return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(m.group(0)) if m else None


def parse_nutrition_table(soup: BeautifulSoup) -> dict:
    """Best-effort parse of a Nutrition Facts table.

    Looks for any table whose header text mentions "Nutrition" or whose rows
    look like nutrient name -> value pairs. Returns a flexible key-value map.
    """
    nutrition: dict = {}
    for table in soup.find_all(["table", "div", "ul"]):
        text = table.get_text(" ", strip=True).lower()
        if not text:
            continue
        if "nutrition" not in text and "calorie" not in text:
            continue
        for row in table.find_all(["tr", "li", "p", "div"]):
            cells = row.find_all(["td", "span", "strong", "b"])
            if not cells:
                continue
            row_text = row.get_text(" ", strip=True)
            for label, key in _NUTRIENT_KEYS.items():
                m = re.search(
                    rf"\b{re.escape(label)}\b[^0-9]*([0-9]+(?:\.[0-9]+)?)",
                    row_text,
                    re.IGNORECASE,
                )
                if m and key not in nutrition:
                    val = _to_float(m.group(1))
                    if val is not None:
                        nutrition[key] = int(val) if val.is_integer() else val
        if nutrition:
            break
    serving_match = re.search(
        r"serving\s+size[^A-Za-z0-9]*([^\n<]{1,80})",
        soup.get_text(" ", strip=True),
        re.IGNORECASE,
    )
    if serving_match:
        nutrition.setdefault("serving_size", serving_match.group(1).strip(" :."))
    return nutrition


def default_parse_product(url: str, html: str) -> ScrapedProduct:
    soup = soup_of(html)
    product = ScrapedProduct()

    ld = find_product_jsonld(soup) or {}
    product.name = (ld.get("name") or og_meta(soup, "og:title") or "").strip()
    brand = ld.get("brand")
    if isinstance(brand, dict):
        product.brand = (brand.get("name") or "").strip()
    elif isinstance(brand, str):
        product.brand = brand.strip()

    offers = ld.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price_val = _to_float(offers.get("price")) if isinstance(offers, dict) else None
    if price_val is not None:
        product.price["package_price"] = price_val
    package_size = ld.get("size") or ld.get("weight")
    if isinstance(package_size, dict):
        package_size = package_size.get("value")
    if package_size:
        product.price["package_size"] = str(package_size)

    images = ld.get("image")
    if isinstance(images, str):
        product.image_urls.append(images)
    elif isinstance(images, list):
        product.image_urls.extend(str(i) for i in images if i)
    if not product.image_urls:
        og_image = og_meta(soup, "og:image")
        if og_image:
            product.image_urls.append(og_image)

    label_img = soup.find("img", attrs={"alt": re.compile(r"nutrition", re.I)})
    if label_img and label_img.get("src"):
        product.nutrition_label_url = urljoin(url, label_img["src"])

    product.nutrition = parse_nutrition_table(soup)

    similar: list[dict] = []
    seen: set[str] = set()
    for section in soup.find_all(
        attrs={"class": re.compile(r"(similar|related|you[- ]?may)", re.I)}
    ):
        for a in section.find_all("a", href=True):
            href = urljoin(url, a["href"])
            name = a.get_text(" ", strip=True)
            if not name or href in seen or href == url:
                continue
            seen.add(href)
            similar.append({"name": name, "public_url": href, "product_id": None})
    product.similar_items = similar[:20]

    return product


def default_extract_links(
    url: str,
    html: str,
    *,
    product_path_pattern: re.Pattern[str] | None = None,
    category_path_pattern: re.Pattern[str] | None = None,
    same_host_only: bool = True,
) -> Iterable[CrawlLink]:
    soup = soup_of(html)
    base_host = urlparse(url).netloc
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#", 1)[0]
        if href in seen:
            continue
        parsed = urlparse(href)
        if same_host_only and parsed.netloc and parsed.netloc != base_host:
            continue
        name = a.get_text(" ", strip=True)
        if not name:
            continue
        if product_path_pattern and product_path_pattern.search(parsed.path):
            seen.add(href)
            yield CrawlLink(url=href, name=name, kind="product")
        elif category_path_pattern and category_path_pattern.search(parsed.path):
            seen.add(href)
            yield CrawlLink(url=href, name=name, kind="category")


def classify_by_jsonld(html: str) -> str:
    soup = soup_of(html)
    if find_product_jsonld(soup) is not None:
        return "product"
    return "category"
