"""CalDAV client for calendar reads.

Ported from ``calendar_fetch.py``. Lists calendars via ``PROPFIND``, fetches
each calendar's ICS export via ``GET``, expands recurring events via
``recurring_ical_events``, and returns a flat list of events.

Env vars used by ``CalDAVConfig.from_env``:

- ``CALDAV_BASE_URL``
- ``CALDAV_USER``
- ``CALDAV_PASSWORD``
- ``CALDAV_TIMEZONE`` (defaults to ``America/New_York``)
"""

from __future__ import annotations

import base64
import os
import xml.etree.ElementTree as ET  # noqa: N817 - canonical alias for stdlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events

_DEFAULT_TZ = "America/New_York"
_XML_NS = {
    "d": "DAV:",
    "c": "urn:ietf:params:xml:ns:caldav",
    "cs": "http://calendarserver.org/ns/",
}
_PROPFIND_BODY = (
    '<?xml version="1.0"?>\n'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">\n'
    "  <d:prop><d:displayname/><d:resourcetype/></d:prop>\n"
    "</d:propfind>"
)


@dataclass(frozen=True)
class CalDAVConfig:
    base_url: str
    user: str
    password: str
    timezone_name: str = _DEFAULT_TZ

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> CalDAVConfig:
        return cls(
            base_url=_required("CALDAV_BASE_URL"),
            user=_required("CALDAV_USER"),
            password=_required("CALDAV_PASSWORD"),
            timezone_name=os.environ.get("CALDAV_TIMEZONE", _DEFAULT_TZ) or _DEFAULT_TZ,
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name!r} is not set or empty")
    return value


def _auth_header(config: CalDAVConfig) -> str:
    raw = f"{config.user}:{config.password}".encode("utf-8")
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def _format_ical_value(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo:
            tz_name = str(value.tzinfo)
            suffix = f" ({tz_name})" if tz_name != "UTC" else ""
            return value.isoformat() + suffix
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _window(
    config: CalDAVConfig, kind: str, now: datetime | None = None
) -> tuple[datetime, datetime]:
    now_local = (now or datetime.now(timezone.utc)).astimezone(config.tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    days = 1 if kind == "today" else 7
    end_local = start_local + timedelta(days=days)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def list_calendars(
    config: CalDAVConfig, *, http_client: httpx.AsyncClient | None = None
) -> list[dict[str, Any]]:
    """Return active calendars (skips deleted, includes subscribed)."""
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.request(
            "PROPFIND",
            f"{config.base_url}/",
            content=_PROPFIND_BODY,
            headers={
                "Content-Type": "application/xml",
                "Depth": "1",
                "Authorization": _auth_header(config),
            },
        )
        response.raise_for_status()
    finally:
        if own_client:
            await client.aclose()

    calendars: list[dict[str, Any]] = []
    for elem in ET.fromstring(response.text).findall(".//d:response", _XML_NS):
        resource_type = elem.find(".//d:resourcetype", _XML_NS)
        if resource_type is None:
            continue
        tags = {child.tag.split("}")[-1] for child in resource_type}
        if "deleted-calendar" in tags:
            continue
        if "calendar" not in tags and "subscribed" not in tags:
            continue
        href = elem.findtext("d:href", "", _XML_NS) or ""
        calendars.append(
            {
                "slug": href.rstrip("/").rsplit("/", 1)[-1],
                "name": elem.findtext(".//d:displayname", "", _XML_NS) or "",
                "subscribed": "subscribed" in tags,
            }
        )
    return calendars


async def fetch_events(
    config: CalDAVConfig,
    kind: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return events for ``kind`` ∈ {``today``, ``week``}, sorted by start."""
    if kind not in {"today", "week"}:
        raise ValueError(f"Unknown kind {kind!r}: expected 'today' or 'week'")

    start, end = _window(config, kind, now=now)
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        calendars = await list_calendars(config, http_client=client)
        events: list[dict[str, Any]] = []
        for cal in calendars:
            try:
                response = await client.get(
                    f"{config.base_url}/{cal['slug']}?export",
                    headers={
                        "Content-Type": "text/calendar",
                        "Authorization": _auth_header(config),
                    },
                )
                response.raise_for_status()
            except httpx.HTTPStatusError:
                continue

            ical_cal = icalendar.Calendar.from_ical(response.text)
            expanded = recurring_ical_events.of(ical_cal).between(start, end)
            for event in expanded:
                row: dict[str, Any] = {
                    "summary": str(event.get("SUMMARY", "")),
                    "calendar": cal["name"],
                }
                if (dtstart := event.get("DTSTART")) is not None:
                    row["start"] = _format_ical_value(dtstart.dt)
                if (dtend := event.get("DTEND")) is not None:
                    row["end"] = _format_ical_value(dtend.dt)
                if (location := event.get("LOCATION")) is not None:
                    row["location"] = str(location)
                if (description := event.get("DESCRIPTION")) is not None:
                    text = str(description)
                    row["description"] = text[:200] + ("..." if len(text) > 200 else "")
                events.append(row)
    finally:
        if own_client:
            await client.aclose()

    events.sort(key=lambda item: item.get("start", ""))
    return events


__all__ = ["CalDAVConfig", "fetch_events", "list_calendars"]
