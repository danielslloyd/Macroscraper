"""Pulling the retailer's own data objects out of a page.

None of these sites render product facts into plain HTML any more. They embed
the data a front-end component will use, and that payload is both richer and
far more stable than any CSS selector:

  Walmart, Publix      a Next.js `__NEXT_DATA__` script, or an inline
                       `digitalData` / `adobeDataLayer` object
  Costco               a Next.js App Router "flight" stream, pushed in pieces
                       through `self.__next_f.push([1, "<chunk>"])`
  Amazon               per-feature inline scripts (handled in its adapter)

The helpers here recover those payloads and then search them *by shape* rather
than by path. A retailer reorganizing `props.pageProps.initialData.data...`
breaks a hardcoded path on its next deploy; "the object that has a `usItemId`"
survives it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

from bs4 import BeautifulSoup

_MAX_DEPTH = 40


# ---------------------------------------------------------------------------
# Payload recovery
# ---------------------------------------------------------------------------


def next_data(soup: BeautifulSoup) -> dict | None:
    """The `__NEXT_DATA__` payload of a Next.js (pages router) page."""
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag is None:
        return None
    text = tag.string or tag.get_text() or ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


_FLIGHT_PUSH = re.compile(r'self\.__next_f\.push\(\[\s*1\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\)')


def flight_payload(html: str) -> str:
    """Reassemble a Next.js App Router flight stream into one string.

    The server streams the page's data as JavaScript string literals; joining
    the decoded chunks gives back the original text, which holds the product
    JSON as an embedded object.
    """
    chunks: list[str] = []
    for match in _FLIGHT_PUSH.finditer(html):
        try:
            chunks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(chunks)


def json_object_at(text: str, start: int) -> dict | None:
    """Parse the JSON object that begins at or just after `start`.

    Walks forward counting braces (respecting strings and escapes) to find the
    matching close, which is what makes it possible to lift one object out of a
    multi-megabyte payload that is not valid JSON as a whole.
    """
    opening = text.find("{", start)
    if opening == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(opening, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[opening:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def object_after_key(text: str, key: str) -> dict | None:
    """Find `"key":` in `text` and parse the object that follows it."""
    for obj in objects_after_key(text, key):
        return obj
    return None


def objects_after_key(text: str, key: str, *, limit: int = 20) -> Iterator[dict]:
    """Every object that follows a `"key":` in `text`.

    A page may mention the same key several times -- an analytics layer, a
    template default, and the real record -- so callers pick the occurrence
    with the fields they need rather than trusting the first.
    """
    marker = f'"{key}"'
    start = 0
    found = 0
    while found < limit:
        index = text.find(marker, start)
        if index == -1:
            return
        start = index + len(marker)
        obj = json_object_at(text, start)
        if obj is not None:
            found += 1
            yield obj


def inline_json_var(html: str, name: str) -> dict | None:
    """Parse an inline `var name = {...};` / `window.name = {...}` object."""
    pattern = re.compile(
        rf"(?:var|let|const)?\s*(?:window\.)?{re.escape(name)}\s*=\s*(?={{)"
    )
    match = pattern.search(html)
    if match is None:
        return None
    return json_object_at(html, match.end())


# ---------------------------------------------------------------------------
# Shape-based search
# ---------------------------------------------------------------------------


def walk(obj: Any, *, depth: int = 0) -> Iterator[Any]:
    """Yield every dict and list node in a nested structure."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value, depth=depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value, depth=depth + 1)


def find_dicts(obj: Any, predicate: Callable[[dict], bool]) -> list[dict]:
    """Every dict in `obj` that satisfies `predicate`, outermost first."""
    return [node for node in walk(obj) if isinstance(node, dict) and predicate(node)]


def find_dict(obj: Any, predicate: Callable[[dict], bool]) -> dict | None:
    for node in walk(obj):
        if isinstance(node, dict) and predicate(node):
            return node
    return None


def has_keys(*keys: str) -> Callable[[dict], bool]:
    """Predicate: the dict has every one of these keys."""
    def check(node: dict) -> bool:
        return all(key in node for key in keys)
    return check


def has_any_key(*keys: str) -> Callable[[dict], bool]:
    def check(node: dict) -> bool:
        return any(key in node for key in keys)
    return check


def find_values(obj: Any, key: str) -> list[Any]:
    """Every value stored under `key` anywhere in `obj`."""
    out: list[Any] = []
    for node in walk(obj):
        if isinstance(node, dict) and key in node:
            out.append(node[key])
    return out


def first_value(obj: Any, *keys: str, cast: Callable[[Any], Any] | None = None):
    """The first non-empty value found under any of `keys`."""
    for key in keys:
        for value in find_values(obj, key):
            if value in (None, "", [], {}):
                continue
            return cast(value) if cast else value
    return None


def collect_urls(obj: Any, *, pattern: re.Pattern[str] | None = None) -> list[str]:
    """Every image-looking URL in a payload, in encounter order, deduplicated."""
    seen: dict[str, None] = {}
    for node in walk(obj):
        if not isinstance(node, dict):
            continue
        for value in node.values():
            if not isinstance(value, str) or not value.startswith("http"):
                continue
            if pattern is not None and not pattern.search(value):
                continue
            seen.setdefault(value, None)
    return list(seen)


__all__ = [
    "collect_urls",
    "find_dict",
    "find_dicts",
    "find_values",
    "first_value",
    "flight_payload",
    "has_any_key",
    "has_keys",
    "inline_json_var",
    "json_object_at",
    "next_data",
    "object_after_key",
    "objects_after_key",
    "walk",
]
