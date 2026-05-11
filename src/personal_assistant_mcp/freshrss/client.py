"""FreshRSS REST client.

Ported from ``freshrss_fetch.py``. Each call re-authenticates and fetches
inside a short-lived ``httpx.AsyncClient`` — stateless from the server's
perspective, matching the upstream behaviour.

Env vars used by ``FreshRSSConfig.from_env``:

- ``FRESHRSS_URL``
- ``FRESHRSS_USER``
- ``FRESHRSS_PASSWORD``
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

_UNREAD_WINDOW_DAYS = 7
_UNREAD_LIMIT = 100


@dataclass(frozen=True)
class FreshRSSConfig:
    url: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> FreshRSSConfig:
        return cls(
            url=_required("FRESHRSS_URL"),
            user=_required("FRESHRSS_USER"),
            password=_required("FRESHRSS_PASSWORD"),
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name!r} is not set or empty")
    return value


def _since_timestamp(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int((now - timedelta(days=_UNREAD_WINDOW_DAYS)).timestamp())


async def login(config: FreshRSSConfig, *, http_client: httpx.AsyncClient | None = None) -> str:
    """Return a FreshRSS auth token via ClientLogin."""
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        params = {"Email": config.user, "Passwd": config.password}
        response = await client.get(f"{config.url}/accounts/ClientLogin", params=params)
        response.raise_for_status()
        for line in response.text.splitlines():
            if line.startswith("Auth="):
                return line.removeprefix("Auth=").strip()
        raise RuntimeError("FreshRSS login did not return an auth token")
    finally:
        if own_client:
            await client.aclose()


async def unread(
    config: FreshRSSConfig,
    *,
    http_client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return unread item IDs (7-day window, capped at 100)."""
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        token = await login(config, http_client=client)
        params = urlencode(
            {
                "s": "user/-/state/com.google/reading-list",
                "xt": "user/-/state/com.google/read",
                "n": str(_UNREAD_LIMIT),
                "ot": str(_since_timestamp(now)),
                "output": "json",
            }
        )
        response = await client.get(
            f"{config.url}/reader/api/0/stream/items/ids?{params}",
            headers={"Authorization": f"GoogleLogin auth={token}"},
        )
        response.raise_for_status()
        return json.loads(response.text)
    finally:
        if own_client:
            await client.aclose()


async def contents(
    config: FreshRSSConfig,
    *,
    http_client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return full content payloads for currently-unread items."""
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        payload = await unread(config, http_client=client, now=now)
        item_refs = payload.get("itemRefs") or []
        if not item_refs:
            return {"items": []}

        token = await login(config, http_client=client)
        form = urlencode([("i", ref["id"]) for ref in item_refs])
        response = await client.post(
            f"{config.url}/reader/api/0/stream/items/contents",
            content=form,
            headers={
                "Authorization": f"GoogleLogin auth={token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response.raise_for_status()
        return json.loads(response.text)
    finally:
        if own_client:
            await client.aclose()


__all__ = ["FreshRSSConfig", "contents", "login", "unread"]
