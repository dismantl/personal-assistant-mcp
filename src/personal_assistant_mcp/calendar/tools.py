"""MCP tool decorators for CalDAV calendar operations."""

from __future__ import annotations

from typing import Any

from . import client as caldav


def register(mcp: Any) -> None:
    """Attach calendar tools to the FastMCP server."""

    @mcp.tool()
    async def calendar_list() -> dict[str, Any]:
        """List active CalDAV calendars (includes subscribed, skips deleted)."""
        calendars = await caldav.list_calendars(caldav.CalDAVConfig.from_env())
        return {"calendars": calendars}

    @mcp.tool()
    async def calendar_today() -> dict[str, Any]:
        """Fetch events for the next 24 hours from the configured vault tz."""
        events = await caldav.fetch_events(caldav.CalDAVConfig.from_env(), "today")
        return {"events": events}

    @mcp.tool()
    async def calendar_week() -> dict[str, Any]:
        """Fetch events for the next 7 days from the configured vault tz."""
        events = await caldav.fetch_events(caldav.CalDAVConfig.from_env(), "week")
        return {"events": events}

    @mcp.tool()
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
    async def calendar_update_event(
        calendar_slug: str,
        uid: str,
        summary: str,
        start: str,
        end: str,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Replace a CalDAV event resource by UID using ISO datetimes."""
        return await caldav.update_event(
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
    async def calendar_delete_event(calendar_slug: str, uid: str) -> dict[str, Any]:
        """Delete a CalDAV event resource by calendar slug and UID."""
        return await caldav.delete_event(
            caldav.CalDAVConfig.from_env(),
            calendar_slug=calendar_slug,
            uid=uid,
        )


__all__ = ["register"]
