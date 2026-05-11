"""MCP tool decorators for FreshRSS operations."""

from __future__ import annotations

from typing import Any

from . import client as freshrss


def register(mcp: Any) -> None:
    """Attach FreshRSS tools to the FastMCP server."""

    @mcp.tool()
    async def freshrss_unread() -> dict[str, Any]:
        """List unread FreshRSS item IDs from the last 7 days (cap 100)."""
        return await freshrss.unread(freshrss.FreshRSSConfig.from_env())

    @mcp.tool()
    async def freshrss_contents() -> dict[str, Any]:
        """Fetch full content payloads for all currently-unread items."""
        return await freshrss.contents(freshrss.FreshRSSConfig.from_env())


__all__ = ["register"]
