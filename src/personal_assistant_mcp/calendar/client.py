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
import copy
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypeGuard, cast
from urllib.parse import urljoin
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import defusedxml.ElementTree as ET  # noqa: N817 - defused stdlib-compatible alias
import httpx
import icalendar
import recurring_ical_events
from icalendar.prop import vRecur

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
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DISPLAY_TZ_SUFFIX = re.compile(r"^(?P<value>.+) \((?P<tz>[^)]+)\)$")
_PARTSTAT_ALIASES = {
    "accept": "ACCEPTED",
    "accepted": "ACCEPTED",
    "going": "ACCEPTED",
    "decline": "DECLINED",
    "declined": "DECLINED",
    "not-going": "DECLINED",
    "not_going": "DECLINED",
    "tentative": "TENTATIVE",
    "maybe": "TENTATIVE",
}
_ALLOWED_PARTSTATS = frozenset({"ACCEPTED", "DECLINED", "TENTATIVE"})
_MAX_REMINDER_MINUTES = 40320  # 4 weeks; typo guard, not a hard product limit


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


@dataclass(frozen=True)
class _EventResource:
    href: str
    calendar_data: str


@dataclass(frozen=True)
class _EventTimeValues:
    start: date | datetime
    end: date | datetime
    timezone_name: str | None


@dataclass(frozen=True)
class _SerializedEventUpdate:
    body: bytes
    include_timezone: bool
    timezone_name: str | None


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


def _event_reminders(component: icalendar.Event) -> list[int | str]:
    """Reminders as minutes-before-start ints; exotic triggers as raw strings."""
    minutes: list[int] = []
    others: list[str] = []
    for alarm in component.subcomponents:
        if getattr(alarm, "name", "") != "VALARM":
            continue
        trigger = alarm.get("TRIGGER")
        if trigger is None:
            continue
        value = trigger.dt
        related = str(trigger.params.get("RELATED", "START")).upper()
        if isinstance(value, timedelta) and related == "START":
            total_seconds = value.total_seconds()
            before_minutes = -total_seconds / 60
            if total_seconds <= 0 and before_minutes == int(before_minutes):
                minutes.append(int(before_minutes))
                continue
        others.append(trigger.to_ical().decode("utf-8"))

    result: list[int | str] = []
    result.extend(sorted(minutes))
    result.extend(sorted(others))
    return result


def _window(
    config: CalDAVConfig, kind: str, now: datetime | None = None
) -> tuple[datetime, datetime]:
    now_local = (now or datetime.now(timezone.utc)).astimezone(config.tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    days = 1 if kind == "today" else 7
    end_local = start_local + timedelta(days=days)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _parse_iso_datetime(value: str, field_name: str) -> datetime:
    match = _DISPLAY_TZ_SUFFIX.fullmatch(value)
    timezone_name = match["tz"] if match else None
    raw_value = match["value"] if match else value
    normalized = raw_value[:-1] + "+00:00" if raw_value.endswith("Z") else raw_value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 datetime string") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    if timezone_name is not None:
        try:
            parsed = parsed.astimezone(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"{field_name} has an unknown timezone suffix") from exc
    return parsed


def _display_timezone_name(value: str) -> str | None:
    match = _DISPLAY_TZ_SUFFIX.fullmatch(value)
    return match["tz"] if match else None


def _parse_recurrence_id(value: str) -> date | datetime:
    if _DATE_ONLY.fullmatch(value):
        return date.fromisoformat(value)
    return _parse_iso_datetime(value, "recurrence_id")


def _validate_rrule_text(rrule: str) -> str:
    clean_rrule = rrule.strip()
    try:
        parsed = vRecur.from_ical(clean_rrule)
    except ValueError as exc:
        raise ValueError("rrule must be a valid RRULE, e.g. FREQ=WEEKLY;COUNT=4") from exc
    if "FREQ" not in parsed:
        raise ValueError("rrule must be a valid RRULE, e.g. FREQ=WEEKLY;COUNT=4")
    if "COUNT" in parsed and "UNTIL" in parsed:
        raise ValueError("rrule must not contain both COUNT and UNTIL")
    for prop_name in ("COUNT", "INTERVAL"):
        if any(int(value) <= 0 for value in parsed.get(prop_name, [])):
            raise ValueError("rrule COUNT and INTERVAL must be positive integers")
    return clean_rrule


def _event_time_values(
    *,
    config: CalDAVConfig,
    start: str,
    end: str,
    rrule: str | None,
) -> _EventTimeValues:
    start_is_date = _DATE_ONLY.fullmatch(start) is not None
    end_is_date = _DATE_ONLY.fullmatch(end) is not None
    if start_is_date or end_is_date:
        if not (start_is_date and end_is_date):
            raise ValueError("start and end must both be all-day dates or both datetimes")
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if end_date <= start_date:
            raise ValueError("end must be after start")
        return _EventTimeValues(start=start_date, end=end_date, timezone_name=None)

    dtstart = _parse_iso_datetime(start, "start")
    dtend = _parse_iso_datetime(end, "end")
    if rrule is None:
        dtstart = dtstart.astimezone(timezone.utc)
        dtend = dtend.astimezone(timezone.utc)
        timezone_name = None
    else:
        timezone_name = _display_timezone_name(start) or _display_timezone_name(end)
        if timezone_name is None:
            timezone_name = config.timezone_name
        try:
            anchor_tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("calendar timezone is unknown") from exc
        dtstart = dtstart.astimezone(anchor_tz)
        dtend = dtend.astimezone(anchor_tz)

    if dtend <= dtstart:
        raise ValueError("end must be after start")
    return _EventTimeValues(start=dtstart, end=dtend, timezone_name=timezone_name)


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
        calendar = _parse_calendar(calendar_data)
    except ValueError:
        return False
    return any(str(component.get("UID", "")) == uid for component in _event_components(calendar))


def _parse_calendar(calendar_data: str | bytes) -> icalendar.Calendar:
    return cast(icalendar.Calendar, icalendar.Calendar.from_ical(calendar_data))


def _event_components(calendar: icalendar.Calendar) -> list[icalendar.Event]:
    return [cast(icalendar.Event, component) for component in calendar.walk("VEVENT")]


def _is_event_component(component: Any) -> TypeGuard[icalendar.Event]:
    return getattr(component, "name", "") == "VEVENT"


async def _find_event_resource_by_uid(
    config: CalDAVConfig,
    calendar_slug: str,
    uid: str,
    *,
    client: httpx.AsyncClient,
) -> _EventResource | None:
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
            return _EventResource(href=absolute_href, calendar_data=calendar_data)
    return None


async def _find_event_href_by_uid(
    config: CalDAVConfig,
    calendar_slug: str,
    uid: str,
    *,
    client: httpx.AsyncClient,
) -> str | None:
    resource = await _find_event_resource_by_uid(config, calendar_slug, uid, client=client)
    return resource.href if resource is not None else None


def _normalize_partstat(value: str) -> str:
    normalized = value.strip().upper()
    normalized = _PARTSTAT_ALIASES.get(value.strip().lower(), normalized)
    if normalized not in _ALLOWED_PARTSTATS:
        allowed = ", ".join(sorted(_ALLOWED_PARTSTATS))
        raise ValueError(f"partstat must be one of: {allowed}")
    return normalized


def _normalize_calendar_address(value: Any) -> str:
    address = str(value).strip().lower()
    if address.startswith("mailto:"):
        address = address[7:]
    return address


def _component_attendees(component: icalendar.Event) -> list[Any]:
    attendees = component.get("ATTENDEE")
    if attendees is None:
        return []
    if isinstance(attendees, list):
        return attendees
    return [attendees]


def _attendee_matches(value: Any, target: str) -> bool:
    return _normalize_calendar_address(value) == target


def _select_rsvp_event(
    calendar: icalendar.Calendar,
    *,
    uid: str,
    recurrence_id: date | datetime | None,
) -> icalendar.Event | None:
    matches = [
        component
        for component in _event_components(calendar)
        if str(component.get("UID", "")) == uid
    ]
    if recurrence_id is not None:
        return next(
            (
                component
                for component in matches
                if _recurrence_id_matches(component, recurrence_id)
            ),
            None,
        )
    master = next(
        (component for component in matches if component.get("RECURRENCE-ID") is None),
        None,
    )
    return master or (matches[0] if len(matches) == 1 else None)


def _build_rsvp_recurrence_override(
    master: icalendar.Event,
    recurrence_id: date | datetime,
) -> icalendar.Event:
    override = copy.deepcopy(master)
    for prop_name in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"):
        override.pop(prop_name, None)
    override.add("recurrence-id", recurrence_id)

    dtstart = master.get("DTSTART")
    dtend = master.get("DTEND")
    if dtstart is not None:
        override.pop("DTSTART", None)
        override.add("dtstart", recurrence_id)
    if dtstart is not None and dtend is not None:
        override.pop("DTEND", None)
        try:
            override.add("dtend", recurrence_id + (dtend.dt - dtstart.dt))
        except TypeError:
            override.add("dtend", dtend.dt)
    return override


def _update_attendee_partstat(
    calendar_data: str,
    *,
    uid: str,
    partstat: str,
    attendee: str | None,
    recurrence_id: date | datetime | None,
) -> tuple[bytes | None, dict[str, Any] | None]:
    calendar = _parse_calendar(calendar_data)
    component = _select_rsvp_event(calendar, uid=uid, recurrence_id=recurrence_id)
    if component is None and recurrence_id is not None:
        master = _find_master_event(calendar, uid)
        if master is not None:
            component = _build_rsvp_recurrence_override(master, recurrence_id)
            calendar.add_component(component)
    if component is None:
        return None, {"error": f"Event not found: {uid}", "uid": uid}

    attendees = _component_attendees(component)
    if not attendees:
        return None, {"error": "Event has no attendees to RSVP as", "uid": uid}

    target = _normalize_calendar_address(attendee) if attendee else None
    if target is None and len(attendees) == 1:
        selected = attendees[0]
    elif target is not None:
        selected = next((item for item in attendees if _attendee_matches(item, target)), None)
        if selected is None:
            return None, {"error": f"Attendee not found on event: {attendee}", "uid": uid}
    else:
        return None, {
            "error": "attendee is required when an event has multiple attendees",
            "uid": uid,
        }

    selected.params["PARTSTAT"] = partstat
    return calendar.to_ical(), None


def _build_reminder_alarms(minutes: list[int], summary: str) -> list[icalendar.Alarm]:
    """Build DISPLAY alarms firing ``n`` minutes before start, deduped and sorted."""
    description = summary.strip() or "Reminder"
    unique: list[int] = []
    for value in minutes:
        if value < 0:
            raise ValueError("reminders must be non-negative minutes before start")
        if value > _MAX_REMINDER_MINUTES:
            raise ValueError("reminders must be 40320 minutes (4 weeks) or less")
        if value not in unique:
            unique.append(value)

    alarms: list[icalendar.Alarm] = []
    for value in sorted(unique):
        alarm = icalendar.Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("trigger", timedelta(minutes=-value))
        alarm.add("description", description)
        alarms.append(alarm)
    return alarms


def _extract_alarms(component: icalendar.Event) -> list[icalendar.Alarm]:
    """Deep-copy the VALARM sub-components off an existing event."""
    return [
        cast(icalendar.Alarm, copy.deepcopy(sub))
        for sub in component.subcomponents
        if getattr(sub, "name", "") == "VALARM"
    ]


def _build_event_component(
    *,
    config: CalDAVConfig,
    uid: str,
    summary: str,
    start: str,
    end: str,
    recurrence_id: date | datetime | None = None,
    description: str | None = None,
    location: str | None = None,
    alarms: list[icalendar.Alarm] | None = None,
    rrule: str | None = None,
) -> icalendar.Event:
    safe_uid = _validate_uid_text(uid, "uid")
    clean_summary = summary.strip()
    if not clean_summary:
        raise ValueError("summary must not be empty")
    if rrule is not None and recurrence_id is not None:
        raise ValueError("rrule cannot be combined with recurrence_id")

    clean_rrule = _validate_rrule_text(rrule) if rrule is not None else None
    times = _event_time_values(config=config, start=start, end=end, rrule=clean_rrule)

    event = icalendar.Event()
    event.add("uid", safe_uid)
    event.add("summary", clean_summary)
    event.add("dtstart", times.start)
    event.add("dtend", times.end)
    event.add("dtstamp", datetime.now(timezone.utc))
    if recurrence_id is not None:
        event.add("recurrence-id", recurrence_id)
    if clean_rrule is not None:
        event.add("rrule", clean_rrule)
    if description is not None:
        event.add("description", description)
    if location is not None:
        event.add("location", location)
    for alarm in alarms or []:
        event.add_component(alarm)
    return event


def _build_event_ical(
    *,
    config: CalDAVConfig,
    uid: str,
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    alarms: list[icalendar.Alarm] | None = None,
    rrule: str | None = None,
) -> bytes:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//personal-assistant-mcp//calendar//EN")
    cal.add("version", "2.0")
    cal.add_component(
        _build_event_component(
            config=config,
            uid=uid,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            alarms=alarms,
            rrule=rrule,
        )
    )
    cal.add_missing_timezones()
    return cal.to_ical()


def _same_ical_value(left: Any, right: Any) -> bool:
    if isinstance(left, datetime) and isinstance(right, datetime):
        if left.tzinfo is not None and right.tzinfo is not None:
            return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)
    return left == right


def _component_values(component: icalendar.Event, prop_name: str) -> list[Any]:
    values = component.get(prop_name)
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]

    parsed: list[Any] = []
    for value in values:
        if hasattr(value, "dts"):
            parsed.extend(item.dt for item in value.dts)
        elif hasattr(value, "dt"):
            parsed.append(value.dt)
        else:
            parsed.append(value)
    return parsed


def _temporal_kind(value: Any) -> str | None:
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    return None


def _shift_temporal_value(value: Any, delta: timedelta) -> Any:
    if isinstance(value, tuple):
        return tuple(_shift_temporal_value(item, delta) for item in value)
    if isinstance(value, datetime):
        return value + delta
    if isinstance(value, date):
        return value + delta
    return value


def _shift_component_values(component: icalendar.Event, prop_name: str, delta: timedelta) -> None:
    values = component.get(prop_name)
    if values is None:
        return
    if not isinstance(values, list):
        values = [values]

    for value in values:
        if hasattr(value, "dts"):
            for item in value.dts:
                item.dt = _shift_temporal_value(item.dt, delta)
        elif hasattr(value, "dt"):
            value.dt = _shift_temporal_value(value.dt, delta)


def _recurrence_overrides(calendar: icalendar.Calendar, uid: str) -> list[icalendar.Event]:
    return [
        component
        for component in _event_components(calendar)
        if str(component.get("UID", "")) == uid and component.get("RECURRENCE-ID") is not None
    ]


def _shift_recurring_exception_metadata(
    calendar: icalendar.Calendar,
    *,
    uid: str,
    master: icalendar.Event,
    old_start: date | datetime,
    new_start: date | datetime,
) -> None:
    if _same_ical_value(old_start, new_start):
        return

    overrides = _recurrence_overrides(calendar, uid)
    if master.get("EXDATE") is None and master.get("RDATE") is None and not overrides:
        return

    old_kind = _temporal_kind(old_start)
    if old_kind != _temporal_kind(new_start):
        raise ValueError(
            "cannot change recurring event between all-day and timed while exceptions exist"
        )
    if old_kind == "datetime":
        if not isinstance(old_start, datetime) or not isinstance(new_start, datetime):
            raise TypeError("recurrence exception start kind mismatch")
        try:
            delta = new_start - old_start
        except TypeError as exc:
            raise ValueError(
                "cannot shift recurring exceptions between floating and timezone-aware times"
            ) from exc
    else:
        if not isinstance(old_start, date) or not isinstance(new_start, date):
            raise TypeError("recurrence exception start kind mismatch")
        delta = new_start - old_start

    _shift_component_values(master, "EXDATE", delta)
    _shift_component_values(master, "RDATE", delta)
    for component in overrides:
        recurrence_id = component.get("RECURRENCE-ID")
        if recurrence_id is not None:
            recurrence_id.dt = _shift_temporal_value(recurrence_id.dt, delta)


def _recurrence_id_matches(component: icalendar.Event, recurrence_id: date | datetime) -> bool:
    existing = component.get("RECURRENCE-ID")
    return existing is not None and _same_ical_value(existing.dt, recurrence_id)


def _find_master_event(calendar: icalendar.Calendar, uid: str) -> icalendar.Event | None:
    for component in _event_components(calendar):
        if str(component.get("UID", "")) == uid and component.get("RECURRENCE-ID") is None:
            return component
    return None


def _find_recurrence_override(
    calendar: icalendar.Calendar, uid: str, recurrence_id: date | datetime
) -> icalendar.Event | None:
    for component in _event_components(calendar):
        if str(component.get("UID", "")) == uid and _recurrence_id_matches(
            component, recurrence_id
        ):
            return component
    return None


def _remove_recurrence_override(
    calendar: icalendar.Calendar, uid: str, recurrence_id: date | datetime
) -> None:
    calendar.subcomponents = [
        component
        for component in calendar.subcomponents
        if not (
            _is_event_component(component)
            and str(component.get("UID", "")) == uid
            and _recurrence_id_matches(component, recurrence_id)
        )
    ]


def _build_recurring_instance_update_ical(
    config: CalDAVConfig,
    calendar_data: str,
    *,
    uid: str,
    recurrence_id: date | datetime,
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    reminders: list[int] | None = None,
) -> bytes | None:
    calendar = _parse_calendar(calendar_data)
    master = _find_master_event(calendar, uid)
    if master is None:
        return None

    if reminders is not None:
        alarms = _build_reminder_alarms(reminders, summary)
    else:
        source = _find_recurrence_override(calendar, uid, recurrence_id) or master
        alarms = _extract_alarms(source)

    _remove_recurrence_override(calendar, uid, recurrence_id)
    calendar.add_component(
        _build_event_component(
            config=config,
            uid=uid,
            recurrence_id=recurrence_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            alarms=alarms,
        )
    )
    return calendar.to_ical()


def _build_recurring_instance_delete_ical(
    calendar_data: str, *, uid: str, recurrence_id: date | datetime
) -> bytes | None:
    calendar = _parse_calendar(calendar_data)
    master = _find_master_event(calendar, uid)
    if master is None:
        return None

    _remove_recurrence_override(calendar, uid, recurrence_id)
    has_exdate = any(
        _same_ical_value(value, recurrence_id) for value in _component_values(master, "EXDATE")
    )
    if not has_exdate:
        master.add("exdate", recurrence_id)
    return calendar.to_ical()


def _rrule_text(component: icalendar.Event) -> str | None:
    rrule = component.get("RRULE")
    return rrule.to_ical().decode("utf-8") if rrule is not None else None


def _has_series_metadata(calendar: icalendar.Calendar, uid: str, master: icalendar.Event) -> bool:
    if any(master.get(prop_name) is not None for prop_name in ("RRULE", "EXDATE", "RDATE")):
        return True
    return any(
        str(component.get("UID", "")) == uid and component.get("RECURRENCE-ID") is not None
        for component in _event_components(calendar)
    )


def _replace_component_value(component: icalendar.Event, prop_name: str, value: Any) -> None:
    component.pop(prop_name, None)
    component.add(prop_name.lower(), value)


def _replace_component_alarms(
    component: icalendar.Event, alarms: list[icalendar.Alarm] | None
) -> None:
    if alarms is None:
        return
    component.subcomponents = [
        sub for sub in component.subcomponents if getattr(sub, "name", "") != "VALARM"
    ]
    for alarm in alarms:
        component.add_component(alarm)


def _build_whole_series_update_ical(
    config: CalDAVConfig,
    calendar: icalendar.Calendar,
    *,
    uid: str,
    master: icalendar.Event,
    summary: str,
    start: str,
    end: str,
    description: str | None,
    location: str | None,
    reminders: list[int] | None,
    rrule: str | None,
) -> _SerializedEventUpdate:
    clean_summary = summary.strip()
    if not clean_summary:
        raise ValueError("summary must not be empty")

    existing_rrule = _rrule_text(master)
    if rrule == "":
        next_rrule = None
    elif rrule is None:
        next_rrule = existing_rrule
    else:
        next_rrule = _validate_rrule_text(rrule)
    collapse_recurrence = rrule == ""

    old_dtstart = master.get("DTSTART")
    old_start = old_dtstart.dt if old_dtstart is not None else None
    times = _event_time_values(config=config, start=start, end=end, rrule=next_rrule)
    if not collapse_recurrence and old_start is not None:
        _shift_recurring_exception_metadata(
            calendar,
            uid=uid,
            master=master,
            old_start=old_start,
            new_start=times.start,
        )
    _replace_component_value(master, "SUMMARY", clean_summary)
    _replace_component_value(master, "DTSTART", times.start)
    _replace_component_value(master, "DTEND", times.end)
    _replace_component_value(master, "DTSTAMP", datetime.now(timezone.utc))

    master.pop("DESCRIPTION", None)
    if description is not None:
        master.add("description", description)
    master.pop("LOCATION", None)
    if location is not None:
        master.add("location", location)

    master.pop("RRULE", None)
    if next_rrule is not None:
        master.add("rrule", next_rrule)
    if collapse_recurrence:
        master.pop("EXDATE", None)
        master.pop("RDATE", None)
        calendar.subcomponents = [
            component
            for component in calendar.subcomponents
            if not (
                _is_event_component(component)
                and str(component.get("UID", "")) == uid
                and component.get("RECURRENCE-ID") is not None
            )
        ]

    alarms = _build_reminder_alarms(reminders, summary) if reminders is not None else None
    _replace_component_alarms(master, alarms)
    calendar.add_missing_timezones()
    return _SerializedEventUpdate(
        body=calendar.to_ical(),
        include_timezone=next_rrule is not None,
        timezone_name=times.timezone_name,
    )


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
    return await _fetch_events_between(config, start, end, http_client=http_client)


async def fetch_events_range(
    config: CalDAVConfig,
    *,
    start: str,
    end: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Return events between two ISO datetimes (max 366 days), sorted by start."""
    start_dt = _parse_iso_datetime(start, "start")
    end_dt = _parse_iso_datetime(end, "end")
    if end_dt <= start_dt:
        raise ValueError("end must be after start")
    if end_dt - start_dt > timedelta(days=366):
        raise ValueError("start to end must span 366 days or less")
    return await _fetch_events_between(config, start_dt, end_dt, http_client=http_client)


async def _fetch_events_between(
    config: CalDAVConfig,
    start: datetime,
    end: datetime,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
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

            ical_cal = _parse_calendar(response.text)
            rrules_by_uid = {
                str(component.get("UID", "")): rrule
                for component in _event_components(ical_cal)
                if component.get("RECURRENCE-ID") is None
                if (rrule := _rrule_text(component)) is not None
            }
            expanded = recurring_ical_events.of(ical_cal).between(start, end)
            for event in expanded:
                row: dict[str, Any] = {
                    "uid": str(event.get("UID", "")),
                    "summary": str(event.get("SUMMARY", "")),
                    "calendar": cal["name"],
                    "calendar_slug": cal["slug"],
                }
                if (dtstart := event.get("DTSTART")) is not None:
                    row["start"] = _format_ical_value(dtstart.dt)
                if (dtend := event.get("DTEND")) is not None:
                    row["end"] = _format_ical_value(dtend.dt)
                if (recurrence_id := event.get("RECURRENCE-ID")) is not None:
                    row["recurrence_id"] = _format_ical_value(recurrence_id.dt)
                if rrule := rrules_by_uid.get(row["uid"]):
                    row["rrule"] = rrule
                if (location := event.get("LOCATION")) is not None:
                    row["location"] = str(location)
                if (description := event.get("DESCRIPTION")) is not None:
                    text = str(description)
                    row["description"] = text[:200] + ("..." if len(text) > 200 else "")
                if reminders := _event_reminders(event):
                    row["reminders"] = reminders
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
    reminders: list[int] | None = None,
    rrule: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Create a CalDAV event resource without overwriting an existing UID."""
    has_supplied_uid = uid is not None
    event_uid = _validate_uid_text(uid, "uid") if uid is not None else uuid.uuid4().hex
    resource_id = _resource_id_for_uid(event_uid)
    event_timezone: str | None = None
    if rrule is not None:
        event_timezone = _event_time_values(
            config=config,
            start=start,
            end=end,
            rrule=_validate_rrule_text(rrule),
        ).timezone_name
    body = _build_event_ical(
        config=config,
        uid=event_uid,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location,
        alarms=_build_reminder_alarms(reminders or [], summary),
        rrule=rrule,
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

    result: dict[str, Any] = {"uid": event_uid, "href": href, "created": True}
    if rrule is not None:
        result["timezone"] = event_timezone
    return result


async def import_ics(
    config: CalDAVConfig,
    *,
    calendar_slug: str,
    ics_text: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Store a raw iCalendar object (e.g. an emailed invite) as-is, upserting by UID.

    Unlike ``create_event`` this preserves scheduling properties
    (ORGANIZER/ATTENDEE/VTIMEZONE), which the server needs to notify the
    organizer when the attendee's participation status later changes.
    """
    calendar = _parse_calendar(ics_text)
    if getattr(calendar, "name", "") != "VCALENDAR":
        raise ValueError("ics_text must contain a VCALENDAR")
    events = _event_components(calendar)
    if not events:
        raise ValueError("ics_text must contain at least one VEVENT")
    uids = [str(event.get("UID", "")).strip() for event in events]
    if any(not uid for uid in uids) or len(set(uids)) != 1:
        raise ValueError("ics_text must contain exactly one event UID")
    event_uid = _validate_uid_text(uids[0], "ics_text UID")
    # CalDAV object resources must not carry an iTIP METHOD property
    # (RFC 4791 §4.1); emailed invites arrive with METHOD:REQUEST.
    if calendar.get("METHOD") is not None:
        del calendar["METHOD"]
    body = calendar.to_ical()

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        existing_href = await _find_event_href_by_uid(
            config, calendar_slug, event_uid, client=client
        )
        created = existing_href is None
        href = existing_href or _event_href(config, calendar_slug, _resource_id_for_uid(event_uid))
        headers = {
            "Authorization": _auth_header(config),
            "Content-Type": "text/calendar; charset=utf-8",
        }
        if created:
            headers["If-None-Match"] = "*"
        response = await client.put(href, content=body, headers=headers)
        response.raise_for_status()
    finally:
        if own_client:
            await client.aclose()

    return {"uid": event_uid, "href": href, "created": created, "updated": not created}


async def update_event(
    config: CalDAVConfig,
    *,
    calendar_slug: str,
    uid: str,
    summary: str,
    start: str,
    end: str,
    recurrence_id: str | None = None,
    description: str | None = None,
    location: str | None = None,
    reminders: list[int] | None = None,
    rrule: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Replace a CalDAV event resource or one recurrence instance by UID."""
    if rrule is not None and recurrence_id is not None:
        raise ValueError("rrule cannot be combined with recurrence_id")
    event_uid = _validate_uid_text(uid, "uid")
    parsed_recurrence_id = (
        _parse_recurrence_id(recurrence_id) if recurrence_id is not None else None
    )
    recurrence_text = (
        _format_ical_value(parsed_recurrence_id) if parsed_recurrence_id is not None else None
    )
    update_timezone: tuple[bool, str | None] = (False, None)

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        resource = await _find_event_resource_by_uid(
            config, calendar_slug, event_uid, client=client
        )
        if resource is None:
            return {"error": f"Event not found: {event_uid}", "uid": event_uid}

        body: bytes | None
        if parsed_recurrence_id is None:
            calendar = _parse_calendar(resource.calendar_data)
            master = _find_master_event(calendar, event_uid)
            if master is not None and _has_series_metadata(calendar, event_uid, master):
                serialized = _build_whole_series_update_ical(
                    config,
                    calendar,
                    uid=event_uid,
                    master=master,
                    summary=summary,
                    start=start,
                    end=end,
                    description=description,
                    location=location,
                    reminders=reminders,
                    rrule=rrule,
                )
                body = serialized.body
                update_timezone = (serialized.include_timezone, serialized.timezone_name)
            else:
                if reminders is not None:
                    alarms = _build_reminder_alarms(reminders, summary)
                else:
                    alarms = _extract_alarms(master) if master is not None else []
                next_rrule = rrule if rrule not in (None, "") else None
                body = _build_event_ical(
                    config=config,
                    uid=event_uid,
                    summary=summary,
                    start=start,
                    end=end,
                    description=description,
                    location=location,
                    alarms=alarms,
                    rrule=next_rrule,
                )
                if next_rrule is not None:
                    update_timezone = (
                        True,
                        _event_time_values(
                            config=config,
                            start=start,
                            end=end,
                            rrule=_validate_rrule_text(next_rrule),
                        ).timezone_name,
                    )
        else:
            body = _build_recurring_instance_update_ical(
                config,
                resource.calendar_data,
                uid=event_uid,
                recurrence_id=parsed_recurrence_id,
                summary=summary,
                start=start,
                end=end,
                description=description,
                location=location,
                reminders=reminders,
            )
            if body is None:
                return {
                    "error": f"Recurring event not found: {event_uid}",
                    "uid": event_uid,
                    "recurrence_id": recurrence_text,
                }

        response = await client.put(
            resource.href,
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

    result: dict[str, Any] = {"uid": event_uid, "href": resource.href, "updated": True}
    if recurrence_text is not None:
        result["recurrence_id"] = recurrence_text
    include_timezone, timezone_name = update_timezone
    if include_timezone:
        result["timezone"] = timezone_name
    return result


async def rsvp_event(
    config: CalDAVConfig,
    *,
    uid: str,
    partstat: str,
    calendar_slug: str | None = None,
    attendee: str | None = None,
    recurrence_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Update this user's attendee status while preserving Nextcloud scheduling context."""
    event_uid = _validate_uid_text(uid, "uid")
    normalized_partstat = _normalize_partstat(partstat)
    parsed_recurrence_id = (
        _parse_recurrence_id(recurrence_id) if recurrence_id is not None else None
    )

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    discovered_slug: str | None = None
    try:
        if calendar_slug is not None:
            safe_slug = _validate_path_segment(calendar_slug, "calendar_slug")
            resource = await _find_event_resource_by_uid(
                config,
                safe_slug,
                event_uid,
                client=client,
            )
        else:
            resource = None
            safe_slug = None
            for calendar in await list_calendars(config, http_client=client):
                if calendar.get("subscribed"):
                    continue
                candidate_slug = str(calendar.get("slug") or "")
                if not candidate_slug:
                    continue
                resource = await _find_event_resource_by_uid(
                    config,
                    candidate_slug,
                    event_uid,
                    client=client,
                )
                if resource is not None:
                    safe_slug = candidate_slug
                    discovered_slug = candidate_slug
                    break
        if resource is None:
            return {"error": f"Event not found: {event_uid}", "uid": event_uid}

        body, error = _update_attendee_partstat(
            resource.calendar_data,
            uid=event_uid,
            attendee=attendee,
            partstat=normalized_partstat,
            recurrence_id=parsed_recurrence_id,
        )
        if error is not None:
            return error
        assert body is not None

        response = await client.put(
            resource.href,
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

    result: dict[str, Any] = {
        "uid": event_uid,
        "href": resource.href,
        "partstat": normalized_partstat,
        "updated": True,
    }
    if discovered_slug is not None:
        result["calendar_slug"] = discovered_slug
    if parsed_recurrence_id is not None:
        result["recurrence_id"] = _format_ical_value(parsed_recurrence_id)
    return result


async def delete_event(
    config: CalDAVConfig,
    *,
    calendar_slug: str,
    uid: str,
    recurrence_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Delete a CalDAV event resource or one recurrence instance by UID."""
    event_uid = _validate_uid_text(uid, "uid")
    parsed_recurrence_id = (
        _parse_recurrence_id(recurrence_id) if recurrence_id is not None else None
    )
    recurrence_text = (
        _format_ical_value(parsed_recurrence_id) if parsed_recurrence_id is not None else None
    )

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        resource = await _find_event_resource_by_uid(
            config, calendar_slug, event_uid, client=client
        )
        if resource is None:
            return {"error": f"Event not found: {event_uid}", "uid": event_uid}

        if parsed_recurrence_id is None:
            response = await client.delete(
                resource.href,
                headers={
                    "Authorization": _auth_header(config),
                    "If-Match": "*",
                },
            )
        else:
            body = _build_recurring_instance_delete_ical(
                resource.calendar_data, uid=event_uid, recurrence_id=parsed_recurrence_id
            )
            if body is None:
                return {
                    "error": f"Recurring event not found: {event_uid}",
                    "uid": event_uid,
                    "recurrence_id": recurrence_text,
                }
            response = await client.put(
                resource.href,
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

    result = {"uid": event_uid, "href": resource.href, "deleted": True}
    if recurrence_text is not None:
        result["recurrence_id"] = recurrence_text
    return result


__all__ = [
    "CalDAVConfig",
    "create_event",
    "delete_event",
    "fetch_events",
    "fetch_events_range",
    "import_ics",
    "list_calendars",
    "rsvp_event",
    "update_event",
]
