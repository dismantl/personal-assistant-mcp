"""Unit tests for the CalDAV calendar client."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
import respx

from personal_assistant_mcp.calendar.client import (
    CalDAVConfig,
    create_event,
    delete_event,
    fetch_events,
    list_calendars,
    update_event,
)

_CONFIG = CalDAVConfig(
    base_url="https://cal.example/dav",
    user="user",
    password="pass",
    timezone_name="America/New_York",
)
# 2026-05-11 12:00 UTC = 08:00 ET; "today" window starts at 00:00 ET = 04:00 UTC.
_FIXED_NOW = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)

_PROPFIND_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
  <d:response>
    <d:href>/dav/user/personal/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Personal</d:displayname>
        <d:resourcetype>
          <d:collection/>
          <cal:calendar/>
        </d:resourcetype>
      </d:prop>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/user/holidays/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>US Holidays</d:displayname>
        <d:resourcetype>
          <d:collection/>
          <cs:subscribed/>
        </d:resourcetype>
      </d:prop>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/user/old/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Old</d:displayname>
        <d:resourcetype>
          <d:collection/>
          <d:deleted-calendar/>
        </d:resourcetype>
      </d:prop>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/user/notes/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Notes</d:displayname>
        <d:resourcetype>
          <d:collection/>
        </d:resourcetype>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_ICAL_PERSONAL = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:event1@test
SUMMARY:Standup
DTSTART:20260511T140000Z
DTEND:20260511T143000Z
LOCATION:Zoom
DESCRIPTION:Daily team sync
END:VEVENT
BEGIN:VEVENT
UID:event2@test
SUMMARY:Out of range
DTSTART:20260601T140000Z
DTEND:20260601T150000Z
END:VEVENT
END:VCALENDAR
"""

_ICAL_HOLIDAYS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:holiday1@test
SUMMARY:Memorial Day Observance
DTSTART:20260511T000000Z
DTEND:20260512T000000Z
END:VEVENT
END:VCALENDAR
"""

_REPORT_EVENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/server-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-123
SUMMARY:Existing event
DTSTART:20260511T140000Z
DTEND:20260511T150000Z
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_REPORT_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" />
"""

_REPORT_COMPLEX_UID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/complex-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:2026/05/11:event_123@example.com
SUMMARY:Existing event
DTSTART:20260511T140000Z
DTEND:20260511T150000Z
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALDAV_BASE_URL", "https://x.example/dav")
    monkeypatch.setenv("CALDAV_USER", "u")
    monkeypatch.setenv("CALDAV_PASSWORD", "p")
    monkeypatch.setenv("CALDAV_TIMEZONE", "Europe/Berlin")
    config = CalDAVConfig.from_env()
    assert config.base_url == "https://x.example/dav"
    assert config.timezone_name == "Europe/Berlin"


def test_config_from_env_defaults_tz_to_ny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALDAV_BASE_URL", "u")
    monkeypatch.setenv("CALDAV_USER", "u")
    monkeypatch.setenv("CALDAV_PASSWORD", "p")
    monkeypatch.delenv("CALDAV_TIMEZONE", raising=False)
    config = CalDAVConfig.from_env()
    assert config.timezone_name == "America/New_York"


# -----------------------------------------------------------------------------
# list_calendars
# -----------------------------------------------------------------------------


@respx.mock
async def test_list_calendars_skips_deleted_and_non_calendar() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    calendars = await list_calendars(_CONFIG)
    slugs = [c["slug"] for c in calendars]
    assert slugs == ["personal", "holidays"]
    by_slug = {c["slug"]: c for c in calendars}
    assert by_slug["personal"]["subscribed"] is False
    assert by_slug["holidays"]["subscribed"] is True


@respx.mock
async def test_list_calendars_sends_basic_auth() -> None:
    route = respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    await list_calendars(_CONFIG)
    sent = route.calls.last.request
    # Basic auth: base64("user:pass") = dXNlcjpwYXNz
    assert sent.headers["Authorization"] == "Basic dXNlcjpwYXNz"


# -----------------------------------------------------------------------------
# fetch_events
# -----------------------------------------------------------------------------


@respx.mock
async def test_fetch_events_today_returns_events_in_window() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=_ICAL_PERSONAL)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text=_ICAL_HOLIDAYS)
    )
    events = await fetch_events(_CONFIG, "today", now=_FIXED_NOW)
    summaries = sorted(e["summary"] for e in events)
    assert summaries == ["Memorial Day Observance", "Standup"]


@respx.mock
async def test_fetch_events_extracts_location_and_description() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=_ICAL_PERSONAL)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text=_ICAL_HOLIDAYS)
    )
    events = await fetch_events(_CONFIG, "today", now=_FIXED_NOW)
    standup = next(e for e in events if e["summary"] == "Standup")
    assert standup["location"] == "Zoom"
    assert standup["description"] == "Daily team sync"
    assert standup["calendar"] == "Personal"


@respx.mock
async def test_fetch_events_skips_calendar_that_fails_to_export() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text=_ICAL_HOLIDAYS)
    )
    events = await fetch_events(_CONFIG, "today", now=_FIXED_NOW)
    summaries = [e["summary"] for e in events]
    assert summaries == ["Memorial Day Observance"]


@respx.mock
async def test_fetch_events_week_window_is_seven_days() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    # Personal calendar has an event outside today's 1-day window but inside the week's 7-day window
    ical_week = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:wk@test
SUMMARY:Mid-week
DTSTART:20260514T140000Z
DTEND:20260514T150000Z
END:VEVENT
END:VCALENDAR
"""
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=ical_week)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text=_ICAL_HOLIDAYS)
    )
    week_events = await fetch_events(_CONFIG, "week", now=_FIXED_NOW)
    summaries = sorted(e["summary"] for e in week_events)
    assert "Mid-week" in summaries


async def test_fetch_events_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        await fetch_events(_CONFIG, "month", now=_FIXED_NOW)


@respx.mock
async def test_fetch_events_sorted_by_start_time() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    ical_multi = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:later@test
SUMMARY:Later
DTSTART:20260511T160000Z
DTEND:20260511T170000Z
END:VEVENT
BEGIN:VEVENT
UID:earlier@test
SUMMARY:Earlier
DTSTART:20260511T140000Z
DTEND:20260511T150000Z
END:VEVENT
END:VCALENDAR
"""
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=ical_multi)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    )
    events = await fetch_events(_CONFIG, "today", now=_FIXED_NOW)
    assert [e["summary"] for e in events] == ["Earlier", "Later"]


# -----------------------------------------------------------------------------
# Event mutation
# -----------------------------------------------------------------------------


@respx.mock
async def test_create_event_puts_ical_to_calendar_resource() -> None:
    route = respx.put("https://cal.example/dav/personal/event-123.ics").mock(
        return_value=httpx.Response(201)
    )

    result = await create_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Dentist",
        start="2026-05-11T14:00:00+00:00",
        end="2026-05-11T15:00:00+00:00",
        description="Cleaning",
        location="Downtown",
    )

    assert result == {
        "uid": "event-123",
        "href": "https://cal.example/dav/personal/event-123.ics",
        "created": True,
    }
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Basic dXNlcjpwYXNz"
    assert request.headers["Content-Type"] == "text/calendar; charset=utf-8"
    assert request.headers["If-None-Match"] == "*"
    body = request.content.decode()
    assert "BEGIN:VEVENT" in body
    assert "UID:event-123" in body
    assert "SUMMARY:Dentist" in body
    assert "DESCRIPTION:Cleaning" in body
    assert "LOCATION:Downtown" in body


@respx.mock
async def test_create_event_serializes_offset_datetimes_as_utc() -> None:
    route = respx.put("https://cal.example/dav/personal/event-123.ics").mock(
        return_value=httpx.Response(201)
    )

    await create_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Dentist",
        start="2026-05-11T10:00:00-04:00",
        end="2026-05-11T11:00:00-04:00",
    )

    body = route.calls.last.request.content.decode()
    assert "DTSTART:20260511T140000Z" in body
    assert "DTEND:20260511T150000Z" in body
    assert "TZID" not in body


@respx.mock
async def test_create_event_accepts_non_path_uid_with_safe_resource_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID("12345678-1234-5678-1234-567812345678"))
    route = respx.put("https://cal.example/dav/personal/12345678123456781234567812345678.ics").mock(
        return_value=httpx.Response(201)
    )

    result = await create_event(
        _CONFIG,
        calendar_slug="personal",
        uid="2026/05/11:event_123@example.com",
        summary="Dentist",
        start="2026-05-11T10:00:00-04:00",
        end="2026-05-11T11:00:00-04:00",
    )

    assert result == {
        "uid": "2026/05/11:event_123@example.com",
        "href": "https://cal.example/dav/personal/12345678123456781234567812345678.ics",
        "created": True,
    }
    body = route.calls.last.request.content.decode()
    assert "UID:2026/05/11:event_123@example.com" in body


@respx.mock
async def test_update_event_replaces_ical_calendar_resource() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EVENT_XML)
    )
    route = respx.put("https://cal.example/dav/personal/server-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Dentist moved",
        start="2026-05-11T16:00:00+00:00",
        end="2026-05-11T17:00:00+00:00",
    )

    assert result == {
        "uid": "event-123",
        "href": "https://cal.example/dav/personal/server-resource.ics",
        "updated": True,
    }
    request = route.calls.last.request
    assert request.headers["If-Match"] == "*"
    body = request.content.decode()
    assert "SUMMARY:Dentist moved" in body
    assert "DTSTART:20260511T160000Z" in body
    assert "DTEND:20260511T170000Z" in body


@respx.mock
async def test_update_event_accepts_non_path_uid() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_COMPLEX_UID_XML)
    )
    route = respx.put("https://cal.example/dav/personal/complex-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="2026/05/11:event_123@example.com",
        summary="Dentist moved",
        start="2026-05-11T16:00:00+00:00",
        end="2026-05-11T17:00:00+00:00",
    )

    assert result == {
        "uid": "2026/05/11:event_123@example.com",
        "href": "https://cal.example/dav/personal/complex-resource.ics",
        "updated": True,
    }
    body = route.calls.last.request.content.decode()
    assert "UID:2026/05/11:event_123@example.com" in body


@respx.mock
async def test_update_event_returns_error_when_uid_missing() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )

    result = await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="missing-event",
        summary="No-op",
        start="2026-05-11T16:00:00+00:00",
        end="2026-05-11T17:00:00+00:00",
    )

    assert result == {"error": "Event not found: missing-event", "uid": "missing-event"}


@respx.mock
async def test_delete_event_deletes_calendar_resource() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EVENT_XML)
    )
    route = respx.delete("https://cal.example/dav/personal/server-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await delete_event(_CONFIG, calendar_slug="personal", uid="event-123")

    assert result == {
        "uid": "event-123",
        "href": "https://cal.example/dav/personal/server-resource.ics",
        "deleted": True,
    }
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Basic dXNlcjpwYXNz"
    assert request.headers["If-Match"] == "*"


@respx.mock
async def test_delete_event_accepts_non_path_uid() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_COMPLEX_UID_XML)
    )
    route = respx.delete("https://cal.example/dav/personal/complex-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await delete_event(
        _CONFIG,
        calendar_slug="personal",
        uid="2026/05/11:event_123@example.com",
    )

    assert result == {
        "uid": "2026/05/11:event_123@example.com",
        "href": "https://cal.example/dav/personal/complex-resource.ics",
        "deleted": True,
    }
    assert route.calls.last.request.headers["If-Match"] == "*"


async def test_create_event_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="end"):
        await create_event(
            _CONFIG,
            calendar_slug="personal",
            uid="event-123",
            summary="Bad event",
            start="2026-05-11T15:00:00+00:00",
            end="2026-05-11T14:00:00+00:00",
        )


async def test_delete_event_rejects_control_chars_in_uid() -> None:
    with pytest.raises(ValueError, match="uid"):
        await delete_event(_CONFIG, calendar_slug="personal", uid="event\r\n123")


async def test_delete_event_rejects_dotdot_calendar_slug() -> None:
    with pytest.raises(ValueError, match="calendar_slug"):
        await delete_event(_CONFIG, calendar_slug="..", uid="event-123")
