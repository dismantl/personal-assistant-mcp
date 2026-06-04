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
    @surface_tool_errors("calendar_create_event")
    async def calendar_create_event(
        calendar_slug: str,
        summary: str,
        start: str,
        end: str,
        uid: str | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Create a CalDAV event from ISO datetimes in a calendar slug."""
        return await caldav.create_event(
            caldav.CalDAVConfig.from_env(),
            calendar_slug=calendar_slug,
            uid=uid,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
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
    ) -> dict[str, Any]:
        """Replace a CalDAV event or recurrence instance using ISO datetimes."""
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


__all__ = ["register"]
