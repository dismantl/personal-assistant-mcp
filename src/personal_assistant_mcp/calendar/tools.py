"""MCP tool decorators for CalDAV calendar operations."""

from __future__ import annotations

from typing import Any

from ..tool_errors import surface_tool_errors
from . import client as caldav


def register(mcp: Any) -> None:
    """Attach calendar tools to the FastMCP server."""

    @mcp.tool()
    @surface_tool_errors("calendar_list")
    async def calendar_list() -> dict[str, Any]:
        """List active CalDAV calendars (includes subscribed, skips deleted)."""
        calendars = await caldav.list_calendars(caldav.CalDAVConfig.from_env())
        return {"calendars": calendars}

    @mcp.tool()
    @surface_tool_errors("calendar_today")
    async def calendar_today() -> dict[str, Any]:
        """Fetch events for the next 24 hours from the configured vault tz."""
        events = await caldav.fetch_events(caldav.CalDAVConfig.from_env(), "today")
        return {"events": events}

    @mcp.tool()
    @surface_tool_errors("calendar_week")
    async def calendar_week() -> dict[str, Any]:
        """Fetch events for the next 7 days from the configured vault tz."""
        events = await caldav.fetch_events(caldav.CalDAVConfig.from_env(), "week")
        return {"events": events}

    @mcp.tool()
    @surface_tool_errors("calendar_events_range")
    async def calendar_events_range(start: str, end: str) -> dict[str, Any]:
        """Fetch events between two ISO datetimes (max 366 days) across all calendars."""
        events = await caldav.fetch_events_range(
            caldav.CalDAVConfig.from_env(), start=start, end=end
        )
        return {"events": events, "start": start, "end": end}

    @mcp.tool()
    @surface_tool_errors("calendar_import_ics")
    async def calendar_import_ics(calendar_slug: str, ics_text: str) -> dict[str, Any]:
        """Import a raw iCalendar invite as-is (preserves organizer/attendees; upserts by UID)."""
        return await caldav.import_ics(
            caldav.CalDAVConfig.from_env(),
            calendar_slug=calendar_slug,
            ics_text=ics_text,
        )

    @mcp.tool()
    @surface_tool_errors("calendar_create_event")
    async def calendar_create_event(
        calendar_slug: str,
        summary: str,
        start: str,
        end: str,
        uid: str | None = None,
        description: str | None = None,
        location: str | None = None,
        reminders: list[int] | None = None,
        rrule: str | None = None,
    ) -> dict[str, Any]:
        """Create a CalDAV event, optionally as a recurring RRULE series.

        reminders: minutes before start for DISPLAY alarms, e.g. [15, 60].
        Omit or pass None for no reminders.
        rrule: raw RRULE string, e.g. every weekday
        FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR; every 2 weeks Mon/Wed
        FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE; last Friday monthly
        FREQ=MONTHLY;BYDAY=-1FR; yearly FREQ=YEARLY.
        For recurring timed events, add a display timezone suffix such as
        2026-05-11T09:00:00-04:00 (America/New_York). Without the suffix,
        the configured calendar timezone anchors the local recurrence pattern.
        All-day events use YYYY-MM-DD for start/end; DTEND is exclusive, so
        pass the day after the final all-day date.
        """
        return await caldav.create_event(
            caldav.CalDAVConfig.from_env(),
            calendar_slug=calendar_slug,
            uid=uid,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            reminders=reminders,
            rrule=rrule,
        )

    @mcp.tool()
    @surface_tool_errors("calendar_update_event")
    async def calendar_update_event(
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
    ) -> dict[str, Any]:
        """Replace a CalDAV event, whole series, or recurrence instance.

        reminders: minutes before start for DISPLAY alarms, e.g. [15, 60].
        Omit (None) to keep existing reminders, pass [] to clear them, or a
        list to replace them. This differs from description/location, which
        are dropped when omitted.
        rrule: None to preserve, "" to remove recurrence, or a raw RRULE to
        replace the whole-series rule. Examples: every weekday
        FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR; every 2 weeks Mon/Wed
        FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE; last Friday monthly
        FREQ=MONTHLY;BYDAY=-1FR; yearly FREQ=YEARLY.
        recurrence_id and rrule are mutually exclusive. Presence of
        recurrence_id updates one instance; absence updates the whole series.
        Recurring timed events use the display timezone suffix, e.g.
        2026-05-11T09:00:00-04:00 (America/New_York), or the configured
        calendar timezone by default. All-day events use YYYY-MM-DD with
        exclusive end dates.
        """
        return await caldav.update_event(
            caldav.CalDAVConfig.from_env(),
            calendar_slug=calendar_slug,
            uid=uid,
            summary=summary,
            start=start,
            end=end,
            recurrence_id=recurrence_id,
            description=description,
            location=location,
            reminders=reminders,
            rrule=rrule,
        )

    @mcp.tool()
    @surface_tool_errors("calendar_delete_event")
    async def calendar_delete_event(
        calendar_slug: str, uid: str, recurrence_id: str | None = None
    ) -> dict[str, Any]:
        """Delete a CalDAV event or recurrence instance by calendar slug and UID."""
        return await caldav.delete_event(
            caldav.CalDAVConfig.from_env(),
            calendar_slug=calendar_slug,
            uid=uid,
            recurrence_id=recurrence_id,
        )

    @mcp.tool()
    @surface_tool_errors("calendar_rsvp")
    async def calendar_rsvp(
        uid: str,
        partstat: str,
        calendar_slug: str | None = None,
        attendee: str | None = None,
        recurrence_id: str | None = None,
    ) -> dict[str, Any]:
        """RSVP to an existing CalDAV invitation by updating attendee status."""
        return await caldav.rsvp_event(
            caldav.CalDAVConfig.from_env(),
            uid=uid,
            partstat=partstat,
            calendar_slug=calendar_slug,
            attendee=attendee,
            recurrence_id=recurrence_id,
        )


__all__ = ["register"]
