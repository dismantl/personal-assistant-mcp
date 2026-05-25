"""CalDAV client for calendar reads and event mutation.

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
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import defusedxml.ElementTree as ET  # noqa: N817 - defused stdlib-compatible alias
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
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._~@-]+$")


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


def _parse_iso_datetime(value: str, field_name: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 datetime string") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


def _validate_path_segment(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped or stripped in {".", ".."} or not _SAFE_PATH_SEGMENT.fullmatch(stripped):
        raise ValueError(f"{field_name} must be a safe CalDAV path segment")
    return stripped


def _validate_uid_text(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped or any(char in stripped for char in "\x00\r\n"):
        raise ValueError(f"{field_name} must be a non-empty iCalendar UID")
    return stripped


def _calendar_collection_href(config: CalDAVConfig, calendar_slug: str) -> str:
    safe_calendar = _validate_path_segment(calendar_slug, "calendar_slug")
    return f"{config.base_url.rstrip('/')}/{safe_calendar}/"


def _event_href(config: CalDAVConfig, calendar_slug: str, uid: str) -> str:
    safe_uid = _validate_path_segment(uid, "uid")
    return urljoin(_calendar_collection_href(config, calendar_slug), f"{safe_uid}.ics")


def _resource_id_for_uid(uid: str) -> str:
    try:
        return _validate_path_segment(uid, "uid")
    except ValueError:
        return uuid.uuid4().hex


def _event_uid_report_body(uid: str) -> str:
    safe_uid = xml_escape(uid)
    return (
        '<?xml version="1.0"?>\n'
        '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">\n'
        "  <d:prop><d:getetag/><c:calendar-data/></d:prop>\n"
        "  <c:filter>\n"
        '    <c:comp-filter name="VCALENDAR">\n'
        '      <c:comp-filter name="VEVENT">\n'
        '        <c:prop-filter name="UID">\n'
        f'          <c:text-match collation="i;octet">{safe_uid}</c:text-match>\n'
        "        </c:prop-filter>\n"
        "      </c:comp-filter>\n"
        "    </c:comp-filter>\n"
        "  </c:filter>\n"
        "</c:calendar-query>"
    )


def _calendar_data_has_uid(calendar_data: str, uid: str) -> bool:
    try:
        calendar = icalendar.Calendar.from_ical(calendar_data)
    except ValueError:
        return False
    return any(str(component.get("UID", "")) == uid for component in calendar.walk("VEVENT"))


async def _find_event_href_by_uid(
    config: CalDAVConfig,
    calendar_slug: str,
    uid: str,
    *,
    client: httpx.AsyncClient,
) -> str | None:
    safe_uid = _validate_uid_text(uid, "uid")
    collection_href = _calendar_collection_href(config, calendar_slug)
    response = await client.request(
        "REPORT",
        collection_href,
        content=_event_uid_report_body(safe_uid),
        headers={
            "Authorization": _auth_header(config),
            "Content-Type": "application/xml",
            "Depth": "1",
        },
    )
    response.raise_for_status()

    for elem in ET.fromstring(response.text).findall(".//d:response", _XML_NS):
        href = elem.findtext("d:href", "", _XML_NS) or ""
        calendar_data = elem.findtext(".//c:calendar-data", "", _XML_NS) or ""
        if not href or not _calendar_data_has_uid(calendar_data, safe_uid):
            continue
        absolute_href = urljoin(collection_href, href)
        if absolute_href.startswith(collection_href):
            return absolute_href
    return None


def _build_event_ical(
    *,
    uid: str,
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
) -> bytes:
    safe_uid = _validate_uid_text(uid, "uid")
    clean_summary = summary.strip()
    if not clean_summary:
        raise ValueError("summary must not be empty")

    dtstart = _parse_iso_datetime(start, "start").astimezone(timezone.utc)
    dtend = _parse_iso_datetime(end, "end").astimezone(timezone.utc)
    if dtend <= dtstart:
        raise ValueError("end must be after start")

    cal = icalendar.Calendar()
    cal.add("prodid", "-//personal-assistant-mcp//calendar//EN")
    cal.add("version", "2.0")

    event = icalendar.Event()
    event.add("uid", safe_uid)
    event.add("summary", clean_summary)
    event.add("dtstart", dtstart)
    event.add("dtend", dtend)
    event.add("dtstamp", datetime.now(timezone.utc))
    if description is not None:
        event.add("description", description)
    if location is not None:
        event.add("location", location)

    cal.add_component(event)
    return cal.to_ical()


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


async def create_event(
    config: CalDAVConfig,
    *,
    calendar_slug: str,
    summary: str,
    start: str,
    end: str,
    uid: str | None = None,
    description: str | None = None,
    location: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Create a CalDAV event resource without overwriting an existing UID."""
    has_supplied_uid = uid is not None
    event_uid = _validate_uid_text(uid, "uid") if has_supplied_uid else uuid.uuid4().hex
    resource_id = _resource_id_for_uid(event_uid)
    body = _build_event_ical(
        uid=event_uid,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location,
    )

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        if has_supplied_uid:
            existing_href = await _find_event_href_by_uid(
                config, calendar_slug, event_uid, client=client
            )
            if existing_href is not None:
                return {
                    "error": f"Event already exists: {event_uid}",
                    "uid": event_uid,
                    "href": existing_href,
                }
        href = _event_href(config, calendar_slug, resource_id)
        response = await client.put(
            href,
            content=body,
            headers={
                "Authorization": _auth_header(config),
                "Content-Type": "text/calendar; charset=utf-8",
                "If-None-Match": "*",
            },
        )
        response.raise_for_status()
    finally:
        if own_client:
            await client.aclose()

    return {"uid": event_uid, "href": href, "created": True}


async def update_event(
    config: CalDAVConfig,
    *,
    calendar_slug: str,
    uid: str,
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Replace a CalDAV event resource by UID."""
    event_uid = _validate_uid_text(uid, "uid")
    body = _build_event_ical(
        uid=event_uid,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location,
    )

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        href = await _find_event_href_by_uid(config, calendar_slug, event_uid, client=client)
        if href is None:
            return {"error": f"Event not found: {event_uid}", "uid": event_uid}
        response = await client.put(
            href,
            content=body,
            headers={
                "Authorization": _auth_header(config),
                "Content-Type": "text/calendar; charset=utf-8",
                "If-Match": "*",
            },
        )
        response.raise_for_status()
    finally:
        if own_client:
            await client.aclose()

    return {"uid": event_uid, "href": href, "updated": True}


async def delete_event(
    config: CalDAVConfig,
    *,
    calendar_slug: str,
    uid: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Delete a CalDAV event resource by UID."""
    event_uid = _validate_uid_text(uid, "uid")

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        href = await _find_event_href_by_uid(config, calendar_slug, event_uid, client=client)
        if href is None:
            return {"error": f"Event not found: {event_uid}", "uid": event_uid}
        response = await client.delete(
            href,
            headers={
                "Authorization": _auth_header(config),
                "If-Match": "*",
            },
        )
        response.raise_for_status()
    finally:
        if own_client:
            await client.aclose()

    return {"uid": event_uid, "href": href, "deleted": True}


__all__ = [
    "CalDAVConfig",
    "create_event",
    "delete_event",
    "fetch_events",
    "list_calendars",
    "update_event",
]
