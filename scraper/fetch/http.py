"""Plain HTTP fetching for JSON APIs and image downloads."""
from __future__ import annotations

import asyncio

import httpx

from .. import config

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def user_agent() -> str:
    return config.fetch_config().get("user_agent") or _DEFAULT_UA


def client(**kwargs) -> httpx.AsyncClient:
    """An httpx client with the project's default headers and timeouts."""
    headers = {"User-Agent": user_agent(), "Accept-Language": "en-US,en;q=0.9"}
    headers.update(kwargs.pop("headers", {}) or {})
    kwargs.setdefault("timeout", 30.0)
    kwargs.setdefault("follow_redirects", True)
    return httpx.AsyncClient(headers=headers, **kwargs)


async def get_json(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    attempts: int = 3,
) -> dict | list | None:
    """GET a JSON document, retrying transient failures with backoff.

    Returns None rather than raising on a 404, since a missing barcode in a
    nutrition database is an ordinary outcome, not an error.
    """
    last: Exception | None = None
    async with client(headers=headers) as http:
        for attempt in range(attempts):
            try:
                response = await http.get(url, params=params)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                last = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.5 * (attempt + 1))
    raise httpx.HTTPError(f"GET {url} failed after {attempts} attempts: {last}")


async def post_form(
    url: str,
    data: dict,
    *,
    headers: dict | None = None,
    auth: tuple[str, str] | None = None,
) -> dict:
    async with client(headers=headers) as http:
        response = await http.post(url, data=data, auth=auth)
        response.raise_for_status()
        return response.json()


__all__ = ["client", "get_json", "post_form", "user_agent"]
