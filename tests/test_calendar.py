"""Unit tests for the CalDAV calendar client."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import cast

import httpx
import icalendar
import pytest
import respx

from personal_assistant_mcp.calendar import client as calendar_client
from personal_assistant_mcp.calendar.client import (
    CalDAVConfig,
    create_event,
    delete_event,
    fetch_events,
    fetch_events_range,
    import_ics,
    list_calendars,
    rsvp_event,
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

_REPORT_EVENT_WITH_ALARM_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Existing event
TRIGGER:-PT15M
END:VALARM
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_REPORT_INVITE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/invite-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:invite-123
SUMMARY:Emily appt
DTSTART:20260609T130000Z
DTEND:20260609T140000Z
ORGANIZER;CN=Emily:mailto:emily@example.test
ATTENDEE;CN=Dan;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:dan@example.test
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_REPORT_RECURRING_INVITE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/recurring-invite-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:recurring-invite-123
SUMMARY:Emily appt
DTSTART:20260609T130000Z
DTEND:20260609T140000Z
RRULE:FREQ=WEEKLY;COUNT=4
ORGANIZER;CN=Emily:mailto:emily@example.test
ATTENDEE;CN=Dan;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:dan@example.test
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

_REPORT_RECURRING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/recurring-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:recurring-123
SUMMARY:Daily standup
DTSTART:20260511T140000Z
DTEND:20260511T150000Z
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_REPORT_RECURRING_OVERRIDE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/recurring-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:recurring-123
SUMMARY:Daily standup
DTSTART:20260511T140000Z
DTEND:20260511T150000Z
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:recurring-123
RECURRENCE-ID:20260512T140000Z
SUMMARY:Moved standup
DTSTART:20260512T160000Z
DTEND:20260512T170000Z
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_REPORT_ALL_DAY_RECURRING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/all-day-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:all-day-123
SUMMARY:Daily all-day
DTSTART;VALUE=DATE:20260511
DTEND;VALUE=DATE:20260512
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_REPORT_ALL_DAY_RECURRING_OVERRIDE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/all-day-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:all-day-123
SUMMARY:Daily all-day
DTSTART;VALUE=DATE:20260511
DTEND;VALUE=DATE:20260512
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:all-day-123
RECURRENCE-ID;VALUE=DATE:20260512
SUMMARY:Moved all-day
DTSTART:20260512T160000Z
DTEND:20260512T170000Z
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


def _vevents_from_body(body: str) -> list[icalendar.Event]:
    calendar = icalendar.Calendar.from_ical(body)
    return [cast(icalendar.Event, component) for component in calendar.walk("VEVENT")]


def test_calendar_client_exports_public_operations() -> None:
    assert {"fetch_events_range", "import_ics"}.issubset(calendar_client.__all__)


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
async def test_fetch_events_exposes_uid_and_calendar_slug() -> None:
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

    by_summary = {e["summary"]: e for e in events}
    assert by_summary["Standup"]["uid"] == "event1@test"
    assert by_summary["Standup"]["calendar_slug"] == "personal"
    assert by_summary["Memorial Day Observance"]["uid"] == "holiday1@test"
    assert by_summary["Memorial Day Observance"]["calendar_slug"] == "holidays"


@respx.mock
async def test_fetch_events_exposes_recurrence_id_for_recurring_instances() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    recurring_ical = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:recurring-123
SUMMARY:Daily standup
DTSTART:20260511T140000Z
DTEND:20260511T150000Z
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
END:VCALENDAR
"""
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=recurring_ical)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    )

    events = await fetch_events(_CONFIG, "week", now=_FIXED_NOW)

    standups = [event for event in events if event["uid"] == "recurring-123"]
    assert [event["recurrence_id"] for event in standups] == [
        "2026-05-11T14:00:00+00:00",
        "2026-05-12T14:00:00+00:00",
    ]


@respx.mock
async def test_fetch_events_exposes_date_recurrence_id_for_all_day_instances() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    recurring_ical = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VEVENT
UID:all-day-123
SUMMARY:Daily all-day
DTSTART;VALUE=DATE:20260511
DTEND;VALUE=DATE:20260512
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
END:VCALENDAR
"""
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=recurring_ical)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    )

    events = await fetch_events(_CONFIG, "week", now=_FIXED_NOW)

    all_day_events = [event for event in events if event["uid"] == "all-day-123"]
    assert [event["recurrence_id"] for event in all_day_events] == [
        "2026-05-11",
        "2026-05-12",
    ]


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
    mid_week = next(e for e in week_events if e["summary"] == "Mid-week")
    assert mid_week["uid"] == "wk@test"
    assert mid_week["calendar_slug"] == "personal"


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
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
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
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
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
async def test_create_event_writes_display_alarms() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
    route = respx.put("https://cal.example/dav/personal/event-123.ics").mock(
        return_value=httpx.Response(201)
    )

    await create_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Dentist",
        start="2026-05-11T14:00:00+00:00",
        end="2026-05-11T15:00:00+00:00",
        reminders=[60, 15, 15],
    )

    body = route.calls.last.request.content.decode()
    assert body.count("BEGIN:VALARM") == 2
    assert "ACTION:DISPLAY" in body
    assert "DESCRIPTION:Dentist" in body
    assert body.index("TRIGGER:-PT15M") < body.index("TRIGGER:-PT1H")


@respx.mock
async def test_create_event_without_reminders_has_no_alarm() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
    route = respx.put("https://cal.example/dav/personal/event-123.ics").mock(
        return_value=httpx.Response(201)
    )

    await create_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Dentist",
        start="2026-05-11T14:00:00+00:00",
        end="2026-05-11T15:00:00+00:00",
    )

    assert "BEGIN:VALARM" not in route.calls.last.request.content.decode()


async def test_create_event_rejects_negative_reminder() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        await create_event(
            _CONFIG,
            calendar_slug="personal",
            summary="Dentist",
            start="2026-05-11T14:00:00+00:00",
            end="2026-05-11T15:00:00+00:00",
            reminders=[-5],
        )


async def test_create_event_rejects_over_cap_reminder() -> None:
    with pytest.raises(ValueError, match="4 weeks"):
        await create_event(
            _CONFIG,
            calendar_slug="personal",
            summary="Dentist",
            start="2026-05-11T14:00:00+00:00",
            end="2026-05-11T15:00:00+00:00",
            reminders=[40321],
        )


@respx.mock
async def test_create_event_accepts_non_path_uid_with_safe_resource_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID("12345678-1234-5678-1234-567812345678"))
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
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
async def test_create_event_returns_error_when_supplied_uid_exists_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID("12345678-1234-5678-1234-567812345678"))
    report_route = respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_COMPLEX_UID_XML)
    )
    put_route = respx.put(
        "https://cal.example/dav/personal/12345678123456781234567812345678.ics"
    ).mock(return_value=httpx.Response(201))

    result = await create_event(
        _CONFIG,
        calendar_slug="personal",
        uid="2026/05/11:event_123@example.com",
        summary="Dentist",
        start="2026-05-11T10:00:00-04:00",
        end="2026-05-11T11:00:00-04:00",
    )

    assert result == {
        "error": "Event already exists: 2026/05/11:event_123@example.com",
        "uid": "2026/05/11:event_123@example.com",
        "href": "https://cal.example/dav/personal/complex-resource.ics",
    }
    assert report_route.called
    assert not put_route.called


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
async def test_update_event_replaces_reminders() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EVENT_WITH_ALARM_XML)
    )
    route = respx.put("https://cal.example/dav/personal/server-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Existing event",
        start="2026-05-11T14:00:00+00:00",
        end="2026-05-11T15:00:00+00:00",
        reminders=[30],
    )

    body = route.calls.last.request.content.decode()
    assert body.count("BEGIN:VALARM") == 1
    assert "TRIGGER:-PT30M" in body
    assert "TRIGGER:-PT15M" not in body


@respx.mock
async def test_update_event_clears_reminders_with_empty_list() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EVENT_WITH_ALARM_XML)
    )
    route = respx.put("https://cal.example/dav/personal/server-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Existing event",
        start="2026-05-11T14:00:00+00:00",
        end="2026-05-11T15:00:00+00:00",
        reminders=[],
    )

    assert "BEGIN:VALARM" not in route.calls.last.request.content.decode()


@respx.mock
async def test_update_event_preserves_reminders_when_omitted() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EVENT_WITH_ALARM_XML)
    )
    route = respx.put("https://cal.example/dav/personal/server-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="event-123",
        summary="Renamed event",
        start="2026-05-11T14:00:00+00:00",
        end="2026-05-11T16:00:00+00:00",
    )

    body = route.calls.last.request.content.decode()
    assert "TRIGGER:-PT15M" in body


@respx.mock
async def test_rsvp_event_updates_attendee_partstat_without_disabling_scheduling() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_INVITE_XML)
    )
    route = respx.put("https://cal.example/dav/personal/invite-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await rsvp_event(
        _CONFIG,
        calendar_slug="personal",
        uid="invite-123",
        attendee="mailto:dan@example.test",
        partstat="ACCEPTED",
    )

    assert result == {
        "uid": "invite-123",
        "href": "https://cal.example/dav/personal/invite-resource.ics",
        "partstat": "ACCEPTED",
        "updated": True,
    }
    request = route.calls.last.request
    assert request.headers["If-Match"] == "*"
    assert "x-nc-scheduling" not in {key.lower() for key in request.headers}
    body = request.content.decode()
    assert "ORGANIZER;CN=Emily:mailto:emily@example.test" in body
    assert "ATTENDEE;CN=Dan;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:dan@example.test" in body


@respx.mock
async def test_rsvp_event_finds_invite_across_writable_calendars() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    personal_report = respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_INVITE_XML)
    )
    holidays_report = respx.route(method="REPORT", url="https://cal.example/dav/holidays/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
    route = respx.put("https://cal.example/dav/personal/invite-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await rsvp_event(
        _CONFIG,
        uid="invite-123",
        attendee="mailto:dan@example.test",
        partstat="DECLINED",
    )

    assert result["calendar_slug"] == "personal"
    assert result["partstat"] == "DECLINED"
    assert personal_report.called
    assert not holidays_report.called
    body = route.calls.last.request.content.decode()
    assert "PARTSTAT=DECLINED" in body


@respx.mock
async def test_rsvp_event_updates_one_recurring_instance() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_RECURRING_INVITE_XML)
    )
    route = respx.put("https://cal.example/dav/personal/recurring-invite-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await rsvp_event(
        _CONFIG,
        calendar_slug="personal",
        uid="recurring-invite-123",
        attendee="mailto:dan@example.test",
        partstat="ACCEPTED",
        recurrence_id="2026-06-16T13:00:00+00:00",
    )

    assert result == {
        "uid": "recurring-invite-123",
        "href": "https://cal.example/dav/personal/recurring-invite-resource.ics",
        "partstat": "ACCEPTED",
        "updated": True,
        "recurrence_id": "2026-06-16T13:00:00+00:00",
    }
    request = route.calls.last.request
    assert request.headers["If-Match"] == "*"
    events = _vevents_from_body(request.content.decode())
    assert len(events) == 2
    master = next(event for event in events if event.get("RECURRENCE-ID") is None)
    override = next(event for event in events if event.get("RECURRENCE-ID") is not None)
    assert str(master.get("SUMMARY")) == "Emily appt"
    assert "RRULE" in master
    assert "RRULE" not in override
    assert override.get("RECURRENCE-ID").dt == datetime(2026, 6, 16, 13, 0, tzinfo=timezone.utc)
    assert override.get("DTSTART").dt == datetime(2026, 6, 16, 13, 0, tzinfo=timezone.utc)
    assert override.get("DTEND").dt == datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc)
    assert str(override.get("ATTENDEE")) == "mailto:dan@example.test"
    assert override.get("ATTENDEE").params["PARTSTAT"] == "ACCEPTED"
    assert str(override.get("ORGANIZER")) == "mailto:emily@example.test"


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
async def test_update_event_updates_one_recurring_instance() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_RECURRING_XML)
    )
    route = respx.put("https://cal.example/dav/personal/recurring-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="recurring-123",
        recurrence_id="2026-05-12T14:00:00+00:00",
        summary="Moved standup",
        start="2026-05-12T16:00:00+00:00",
        end="2026-05-12T17:00:00+00:00",
        description="One-off shift",
        location="Room 2",
    )

    assert result == {
        "uid": "recurring-123",
        "recurrence_id": "2026-05-12T14:00:00+00:00",
        "href": "https://cal.example/dav/personal/recurring-resource.ics",
        "updated": True,
    }
    request = route.calls.last.request
    assert request.headers["If-Match"] == "*"
    events = _vevents_from_body(request.content.decode())
    assert len(events) == 2
    master = next(event for event in events if event.get("RECURRENCE-ID") is None)
    override = next(event for event in events if event.get("RECURRENCE-ID") is not None)
    assert str(master.get("SUMMARY")) == "Daily standup"
    assert "RRULE" in master
    assert str(override.get("UID")) == "recurring-123"
    assert override.get("RECURRENCE-ID").dt == datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)
    assert str(override.get("SUMMARY")) == "Moved standup"
    assert override.get("DTSTART").dt == datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc)
    assert override.get("DTEND").dt == datetime(2026, 5, 12, 17, 0, tzinfo=timezone.utc)
    assert str(override.get("DESCRIPTION")) == "One-off shift"
    assert str(override.get("LOCATION")) == "Room 2"


@respx.mock
async def test_update_event_accepts_all_day_recurrence_id() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_ALL_DAY_RECURRING_XML)
    )
    route = respx.put("https://cal.example/dav/personal/all-day-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="all-day-123",
        recurrence_id="2026-05-12",
        summary="Moved all-day",
        start="2026-05-12T16:00:00+00:00",
        end="2026-05-12T17:00:00+00:00",
    )

    assert result == {
        "uid": "all-day-123",
        "recurrence_id": "2026-05-12",
        "href": "https://cal.example/dav/personal/all-day-resource.ics",
        "updated": True,
    }
    events = _vevents_from_body(route.calls.last.request.content.decode())
    override = next(event for event in events if event.get("RECURRENCE-ID") is not None)
    assert override.get("RECURRENCE-ID").dt == date(2026, 5, 12)
    assert str(override.get("SUMMARY")) == "Moved all-day"


@respx.mock
async def test_update_event_accepts_tzid_recurrence_id_from_listed_event() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    local_recurring_ical = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:local-recurring-123
SUMMARY:Local recurring
DTSTART;TZID=America/New_York:20260511T100000
DTEND;TZID=America/New_York:20260511T110000
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
END:VCALENDAR
"""
    report_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/local-recurring-resource.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[{local_recurring_ical}]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=local_recurring_ical)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    )
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=report_xml)
    )
    route = respx.put("https://cal.example/dav/personal/local-recurring-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    events = await fetch_events(_CONFIG, "week", now=_FIXED_NOW)
    recurrence_id = next(
        event["recurrence_id"]
        for event in events
        if event["uid"] == "local-recurring-123" and event["summary"] == "Local recurring"
    )
    result = await update_event(
        _CONFIG,
        calendar_slug="personal",
        uid="local-recurring-123",
        recurrence_id=recurrence_id,
        summary="Moved local recurring",
        start="2026-05-11T16:00:00+00:00",
        end="2026-05-11T17:00:00+00:00",
    )

    assert recurrence_id == "2026-05-11T10:00:00-04:00 (America/New_York)"
    assert result == {
        "uid": "local-recurring-123",
        "recurrence_id": recurrence_id,
        "href": "https://cal.example/dav/personal/local-recurring-resource.ics",
        "updated": True,
    }
    override = next(
        event
        for event in _vevents_from_body(route.calls.last.request.content.decode())
        if event.get("RECURRENCE-ID") is not None
    )
    assert override.get("RECURRENCE-ID").dt.isoformat() == "2026-05-11T10:00:00-04:00"
    assert str(override.get("RECURRENCE-ID").dt.tzinfo) == "America/New_York"
    assert str(override.get("SUMMARY")) == "Moved local recurring"


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


@respx.mock
async def test_delete_event_deletes_one_recurring_instance() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_RECURRING_OVERRIDE_XML)
    )
    put_route = respx.put("https://cal.example/dav/personal/recurring-resource.ics").mock(
        return_value=httpx.Response(204)
    )
    delete_route = respx.delete("https://cal.example/dav/personal/recurring-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await delete_event(
        _CONFIG,
        calendar_slug="personal",
        uid="recurring-123",
        recurrence_id="2026-05-12T14:00:00+00:00",
    )

    assert result == {
        "uid": "recurring-123",
        "recurrence_id": "2026-05-12T14:00:00+00:00",
        "href": "https://cal.example/dav/personal/recurring-resource.ics",
        "deleted": True,
    }
    assert not delete_route.called
    request = put_route.calls.last.request
    assert request.headers["If-Match"] == "*"
    events = _vevents_from_body(request.content.decode())
    assert len(events) == 1
    master = events[0]
    assert str(master.get("UID")) == "recurring-123"
    assert master.get("EXDATE").dts[0].dt == datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)


@respx.mock
async def test_delete_event_accepts_all_day_recurrence_id() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_ALL_DAY_RECURRING_OVERRIDE_XML)
    )
    put_route = respx.put("https://cal.example/dav/personal/all-day-resource.ics").mock(
        return_value=httpx.Response(204)
    )
    delete_route = respx.delete("https://cal.example/dav/personal/all-day-resource.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await delete_event(
        _CONFIG,
        calendar_slug="personal",
        uid="all-day-123",
        recurrence_id="2026-05-12",
    )

    assert result == {
        "uid": "all-day-123",
        "recurrence_id": "2026-05-12",
        "href": "https://cal.example/dav/personal/all-day-resource.ics",
        "deleted": True,
    }
    assert not delete_route.called
    events = _vevents_from_body(put_route.calls.last.request.content.decode())
    assert len(events) == 1
    assert events[0].get("EXDATE").dts[0].dt == date(2026, 5, 12)


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


_ICAL_INVITE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//External//
METHOD:REQUEST
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:20251102T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:invite-1@external.example
SUMMARY:Coffee chat
DTSTART;TZID=America/New_York:20260715T100000
DTEND;TZID=America/New_York:20260715T110000
ORGANIZER;CN=Alice:mailto:alice@external.example
ATTENDEE;CN=Dan;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:dan@example.com
END:VEVENT
END:VCALENDAR
"""

_REPORT_INVITE_FOUND_XML = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/personal/existing-invite.ics</d:href>
    <d:propstat>
      <d:prop>
        <cal:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:invite-1@external.example
SUMMARY:Coffee chat
DTSTART:20260715T140000Z
DTEND:20260715T150000Z
END:VEVENT
END:VCALENDAR
]]></cal:calendar-data>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


@respx.mock
async def test_fetch_events_range_returns_events_between() -> None:
    respx.route(method="PROPFIND", url="https://cal.example/dav/").mock(
        return_value=httpx.Response(207, text=_PROPFIND_XML)
    )
    respx.get("https://cal.example/dav/personal?export").mock(
        return_value=httpx.Response(200, text=_ICAL_PERSONAL)
    )
    respx.get("https://cal.example/dav/holidays?export").mock(
        return_value=httpx.Response(200, text=_ICAL_HOLIDAYS)
    )

    events = await fetch_events_range(
        _CONFIG,
        start="2026-05-25T00:00:00+00:00",
        end="2026-06-05T00:00:00+00:00",
    )

    assert [event["uid"] for event in events] == ["event2@test"]


@respx.mock
async def test_fetch_events_range_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="end must be after start"):
        await fetch_events_range(
            _CONFIG,
            start="2026-05-25T00:00:00+00:00",
            end="2026-05-25T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="366 days or less"):
        await fetch_events_range(
            _CONFIG,
            start="2026-01-01T00:00:00+00:00",
            end="2027-06-01T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="timezone offset"):
        await fetch_events_range(
            _CONFIG,
            start="2026-05-25T00:00:00",
            end="2026-06-05T00:00:00+00:00",
        )


@respx.mock
async def test_import_ics_creates_event_preserving_scheduling_properties() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
    route = respx.put("https://cal.example/dav/personal/invite-1@external.example.ics").mock(
        return_value=httpx.Response(201)
    )

    result = await import_ics(_CONFIG, calendar_slug="personal", ics_text=_ICAL_INVITE)

    assert result == {
        "uid": "invite-1@external.example",
        "href": "https://cal.example/dav/personal/invite-1@external.example.ics",
        "created": True,
        "updated": False,
    }
    request = route.calls.last.request
    assert request.headers["If-None-Match"] == "*"
    body = request.content.decode()
    assert "ORGANIZER" in body
    assert "ATTENDEE" in body
    assert "TZID:America/New_York" in body
    # CalDAV object resources must not carry the iTIP METHOD property.
    assert "METHOD:REQUEST" not in body


@respx.mock
async def test_import_ics_updates_existing_event_by_uid() -> None:
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_INVITE_FOUND_XML)
    )
    route = respx.put("https://cal.example/dav/personal/existing-invite.ics").mock(
        return_value=httpx.Response(204)
    )

    result = await import_ics(_CONFIG, calendar_slug="personal", ics_text=_ICAL_INVITE)

    assert result["created"] is False
    assert result["updated"] is True
    assert result["href"] == "https://cal.example/dav/personal/existing-invite.ics"
    assert "If-None-Match" not in route.calls.last.request.headers


@respx.mock
async def test_import_ics_rejects_invalid_payloads() -> None:
    with pytest.raises(ValueError):
        await import_ics(_CONFIG, calendar_slug="personal", ics_text="not an ics")
    with pytest.raises(ValueError, match="at least one VEVENT"):
        await import_ics(
            _CONFIG,
            calendar_slug="personal",
            ics_text="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n",
        )
    two_uids = _ICAL_INVITE.replace(
        "END:VCALENDAR",
        "BEGIN:VEVENT\nUID:other@external.example\nDTSTART:20260716T100000Z\nEND:VEVENT\nEND:VCALENDAR",
    )
    with pytest.raises(ValueError, match="exactly one event UID"):
        await import_ics(_CONFIG, calendar_slug="personal", ics_text=two_uids)
    respx.route(method="REPORT", url="https://cal.example/dav/personal/").mock(
        return_value=httpx.Response(207, text=_REPORT_EMPTY_XML)
    )
    respx.put("https://cal.example/dav/personal/invite-1@external.example.ics").mock(
        return_value=httpx.Response(201)
    )
    missing_uid = _ICAL_INVITE.replace(
        "END:VCALENDAR",
        "BEGIN:VEVENT\nDTSTART:20260716T100000Z\nDTEND:20260716T110000Z\nEND:VEVENT\nEND:VCALENDAR",
    )
    with pytest.raises(ValueError, match="exactly one event UID"):
        await import_ics(_CONFIG, calendar_slug="personal", ics_text=missing_uid)
    bare_event = """BEGIN:VEVENT
UID:invite-1@external.example
SUMMARY:Bare event
DTSTART:20260716T100000Z
DTEND:20260716T110000Z
END:VEVENT
"""
    with pytest.raises(ValueError, match="VCALENDAR"):
        await import_ics(_CONFIG, calendar_slug="personal", ics_text=bare_event)
