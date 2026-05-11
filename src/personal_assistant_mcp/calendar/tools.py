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


__all__ = ["register"]
